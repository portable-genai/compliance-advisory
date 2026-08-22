"""Fetch-at-runtime stage of the corpus pipeline.

The C1 knowledge base is **not** vendored. At runtime (and on the scheduled refresh
job) each :class:`~compliance_advisory.domain.models.RegSource` in the registry is
downloaded directly from the regulator's public site, checksummed, and handed on as a
:class:`~compliance_advisory.domain.models.FetchedDocument` for redaction and
ingestion into **Agent Search** (SPEC §2, "Reg KB data: fetch-at-runtime, 7-day TTL").

This module is deliberately framework-light: it depends only on ``httpx``, ``pyyaml``
and the pure domain models, so it imports cleanly under the on-prem/test profile with
no Google Cloud SDK installed.
"""

from __future__ import annotations

import hashlib
import ssl
from pathlib import Path

import httpx
import yaml

from ..domain.models import (
    DocType,
    FetchedDocument,
    Jurisdiction,
    RegSource,
    Regulator,
    utcnow,
)

# Polite client defaults: identify ourselves and never hang forever on a slow regulator
# site. The UA keeps the fetcher name visible but leads with the Mozilla/compatible
# convention: several regulator CDNs (MAS notably) answer a plain product-token UA with
# an 853 KB HTML interstitial instead of the PDF, and the compatible form is the honest
# way to be served the actual document.
_USER_AGENT = "Mozilla/5.0 (compatible; compliance-corpus-fetcher/0.1)"
_DEFAULT_TIMEOUT = httpx.Timeout(60.0, connect=10.0)
_MAX_REDIRECTS = 5

#: Operator drop-box for sources whose publishers gate direct downloads behind a
#: JS-driven repository (HKMA's Banking Regulatory Document Repository is one): save the
#: document from a browser as ``<source id>.pdf`` here and the pipeline ingests the file
#: instead of fetching the URL. Keeps the corpus real without scripting around a WAF.
MANUAL_SOURCE_DIR = Path(__file__).parent / "sources" / "manual"


class FetchContentError(RuntimeError):
    """The publisher answered, but not with the document the registry points at."""


def _ssl_context() -> ssl.SSLContext | bool:
    """System trust store when available; certifi otherwise.

    Some regulator sites (hkma.gov.hk) serve an incomplete certificate chain that
    certifi cannot complete but the OS trust store can. ``truststore`` uses the system
    verifier, so those hosts verify normally instead of failing closed.
    """
    try:
        import truststore

        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except ImportError:  # pragma: no cover - truststore is a declared dependency
        return True


# Fallback MIME types keyed on the URL/content-type suffix; most registry entries are PDFs.
_EXTENSION_MIME = {
    ".pdf": "application/pdf",
    ".html": "text/html",
    ".htm": "text/html",
    ".txt": "text/plain",
    ".json": "application/json",
    ".xml": "application/xml",
}
_DEFAULT_MIME = "application/octet-stream"


def load_registry(path: str | Path) -> list[RegSource]:
    """Parse the YAML source registry into typed :class:`RegSource` objects.

    The registry shape is ``{"sources": [ {RegSource-shaped dict}, ... ]}``. Enum fields
    (``regulator``, ``jurisdiction``, ``doc_type``) are coerced from their string values;
    ``topics`` is normalised to a tuple so the resulting dataclass stays hashable/frozen.
    """
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    entries = raw.get("sources", raw) if isinstance(raw, dict) else raw
    if not isinstance(entries, list):
        raise ValueError(f"registry at {path!s} must contain a list of sources")
    return [_to_source(entry) for entry in entries]


def _to_source(entry: dict) -> RegSource:
    """Build one :class:`RegSource` from a registry dict, validating enum members."""
    try:
        regulator = Regulator(entry["regulator"])
        jurisdiction = Jurisdiction(entry["jurisdiction"])
    except KeyError as exc:  # pragma: no cover - guards malformed registry entries
        raise ValueError(f"registry entry missing required field: {exc}") from exc
    doc_type_raw = entry.get("doc_type", DocType.OTHER.value)
    doc_type = doc_type_raw if isinstance(doc_type_raw, DocType) else DocType(doc_type_raw)
    return RegSource(
        id=str(entry["id"]),
        regulator=regulator,
        jurisdiction=jurisdiction,
        title=str(entry["title"]).strip(),
        url=str(entry["url"]).strip(),
        doc_type=doc_type,
        version=str(entry.get("version", "unknown")),
        published_date=entry.get("published_date"),
        topics=tuple(entry.get("topics", ()) or ()),
    )


def compute_checksum(content: bytes) -> str:
    """SHA-256 hex digest used as the freshness/version fingerprint in the ledger."""
    return hashlib.sha256(content).hexdigest()


def _resolve_mime(source: RegSource, response: httpx.Response) -> str:
    """Prefer the server's Content-Type; fall back to the URL extension."""
    header = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if header:
        return header
    suffix = Path(httpx.URL(source.url).path).suffix.lower()
    return _EXTENSION_MIME.get(suffix, _DEFAULT_MIME)


def _manual_file(source: RegSource) -> Path | None:
    """The operator-dropped file for ``source``, if one exists (any extension)."""
    if not MANUAL_SOURCE_DIR.is_dir():
        return None
    for path in sorted(MANUAL_SOURCE_DIR.glob(f"{source.id}.*")):
        if path.is_file():
            return path
    return None


def _check_is_document(source: RegSource, content: bytes, mime: str) -> None:
    """Refuse to hand on an HTML page where the registry promises a PDF.

    JS-gated repositories (and WAF interstitials) answer a ``.pdf`` URL with HTTP 200
    and an HTML shell; indexing that would poison the corpus with navigation chrome
    while the ledger records the source as FRESH. Failing here keeps the honest state:
    the source is FAILED until it can really be read.
    """
    if not source.url.lower().endswith(".pdf"):
        return
    if content[:5] == b"%PDF-":
        return
    head = content[:512].lstrip().lower()
    if "html" in mime or head.startswith((b"<!doctype", b"<html")):
        raise FetchContentError(
            f"{source.id}: publisher returned an HTML page instead of the PDF at "
            f"{source.url} (likely a JS-gated repository). Download it in a browser and "
            f"save it as {MANUAL_SOURCE_DIR / (source.id + '.pdf')} to ingest it."
        )


def fetch_source(
    source: RegSource,
    *,
    client: httpx.Client | None = None,
    timeout: httpx.Timeout = _DEFAULT_TIMEOUT,
) -> FetchedDocument:
    """Download one public regulatory document into a :class:`FetchedDocument`.

    An operator-dropped file under :data:`MANUAL_SOURCE_DIR` takes precedence over the
    network (the escape hatch for JS-gated publishers). Otherwise the URL is fetched,
    checksummed (SHA-256: the ledger's change-detection key), and sanity-checked so an
    HTML interstitial is never passed off as the document.

    A caller may pass a shared ``client`` (the refresh job reuses one connection pool
    across the whole registry); otherwise a short-lived client is created per call.

    Raises ``httpx.HTTPStatusError`` on a non-2xx response and
    :class:`FetchContentError` on a 200 that is not the promised document, so the ingest
    layer marks the source ``FAILED`` rather than indexing an error page.
    """
    manual = _manual_file(source)
    if manual is not None:
        content = manual.read_bytes()
        suffix_mime = _EXTENSION_MIME.get(manual.suffix.lower(), _DEFAULT_MIME)
        return FetchedDocument(
            source=source,
            content=content,
            mime_type="application/pdf" if content[:5] == b"%PDF-" else suffix_mime,
            fetched_at=utcnow(),
            checksum=compute_checksum(content),
        )

    owns_client = client is None
    client = client or new_client(timeout)
    try:
        response = client.get(source.url)
        response.raise_for_status()
        content = response.content
        mime = _resolve_mime(source, response)
        _check_is_document(source, content, mime)
        return FetchedDocument(
            source=source,
            content=content,
            mime_type=mime,
            fetched_at=utcnow(),
            checksum=compute_checksum(content),
        )
    finally:
        if owns_client:
            client.close()


def new_client(timeout: httpx.Timeout = _DEFAULT_TIMEOUT) -> httpx.Client:
    """Construct a polite, redirect-following client to share across a fetch batch."""
    return httpx.Client(
        headers={"User-Agent": _USER_AGENT},
        timeout=timeout,
        follow_redirects=True,
        max_redirects=_MAX_REDIRECTS,
        verify=_ssl_context(),
    )
