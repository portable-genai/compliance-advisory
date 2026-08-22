"""Ingest + freshness stage of the corpus pipeline.

This is where the three corpus concerns meet (SPEC §2, §5):

1. **Fetch** the public document (``pipelines.fetch``).
2. **Redact** it defensively through the :class:`PIIRedactionPort` before it is indexed
   — these are public documents, but running every byte through Sensitive Data
   Protection / DLP demonstrates the P-04 "minimise data" control end to end and means a
   stray PII string in a consultation annex never reaches the store or an audit trail.
3. **Ingest** the redacted document into **Agent Search** via :class:`CorpusIngestionPort`,
   then **record freshness** in the **AlloyDB** ledger (:class:`CorpusLedgerPort`) with an
   ``expires_at`` computed by :class:`FreshnessPolicy` from ``settings.corpus.ttl_days``.

On a read, the QA / API flow can call :func:`ensure_fresh` for any source it is about to
cite: if the ledger record is missing or stale (> TTL) the source is re-fetched and
re-ingested before the answer is assembled; if it is still fresh the call is a no-op and
the answer is served straight from the store. The scheduled Cloud Run job
(``pipelines.refresh_job``) calls :func:`refresh_expired` (or :func:`refresh_all`) out of
band so reads almost never pay the re-fetch cost.

The module depends only on the pure domain models, the ports, and ``pipelines.fetch`` —
no Google Cloud SDK — so it imports under the on-prem/test profile.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ..config import Container
from ..domain.horizon import carry_forward
from ..domain.models import (
    FreshnessRecord,
    FreshnessStatus,
    RegSource,
    utcnow,
)
from . import fetch as fetch_mod
from . import textract

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Freshness policy (SPEC §5). The TTL window is the single knob; everything else
# in the 7-day fetch-at-runtime model derives from it.
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class FreshnessPolicy:
    """Turns ``settings.corpus.ttl_days`` into concrete expiry / staleness decisions."""

    ttl_days: int = 7

    @property
    def ttl(self) -> timedelta:
        return timedelta(days=self.ttl_days)

    def expires_at(self, fetched_at: datetime) -> datetime:
        """The instant a document fetched at ``fetched_at`` becomes stale."""
        return fetched_at + self.ttl

    def is_stale(self, record: FreshnessRecord | None, now: datetime | None = None) -> bool:
        """A missing, failed, or past-``expires_at`` record is stale and must be refreshed."""
        if record is None:
            return True
        now = now or utcnow()
        return not record.is_fresh(now)


# --------------------------------------------------------------------------- #
# Result types — small, JSON-friendly summaries the refresh job logs and returns.
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class SourceOutcome:
    """Outcome of (re-)ingesting a single source."""

    source_id: str
    status: FreshnessStatus
    action: str  # "ingested" | "skipped-fresh" | "failed"
    document_id: str = ""
    chunks: int = 0
    detail: str = ""


@dataclass(frozen=True, slots=True)
class RefreshSummary:
    """Aggregate outcome of a refresh pass over a set of sources."""

    outcomes: tuple[SourceOutcome, ...] = field(default_factory=tuple)

    @property
    def ingested(self) -> int:
        return sum(1 for o in self.outcomes if o.action == "ingested")

    @property
    def skipped(self) -> int:
        return sum(1 for o in self.outcomes if o.action == "skipped-fresh")

    @property
    def failed(self) -> int:
        return sum(1 for o in self.outcomes if o.action == "failed")

    @property
    def total(self) -> int:
        return len(self.outcomes)


# --------------------------------------------------------------------------- #
# Registry helpers
# --------------------------------------------------------------------------- #
def load_sources(container: Container) -> list[RegSource]:
    """Load the source registry referenced by ``settings.corpus.registry_path``."""
    return fetch_mod.load_registry(container.settings.corpus.registry_path)


def _index_sources(container: Container) -> dict[str, RegSource]:
    return {s.id: s for s in load_sources(container)}


def _policy(container: Container) -> FreshnessPolicy:
    return FreshnessPolicy(ttl_days=container.settings.corpus.ttl_days)


# --------------------------------------------------------------------------- #
# Core operations
# --------------------------------------------------------------------------- #
def ingest_source(container: Container, source: RegSource) -> SourceOutcome:
    """Fetch -> redact (P-04) -> ingest into Agent Search -> upsert freshness ledger.

    On any failure (download error, ingestion error) a ``FAILED`` record is written to
    the ledger so the next :func:`ensure_fresh` / :func:`refresh_expired` pass retries it,
    and the failure is surfaced in the returned :class:`SourceOutcome` rather than raised.
    """
    try:
        document = fetch_mod.fetch_source(source)
        return ingest_fetched(container, document)
    except Exception as exc:  # noqa: BLE001 - any source must not abort the whole batch
        logger.exception("failed to ingest %s", source.id)
        return _record_failure(container, source, "", str(exc))


def ingest_fetched(container: Container, document) -> SourceOutcome:  # type: ignore[no-untyped-def]
    """Redact (P-04) -> ingest -> upsert freshness ledger for an already-fetched document.

    Shared by :func:`ingest_source` (the registry pipeline) and the API's corpus-upload
    path, so an uploaded internal policy goes through exactly the same redaction and
    provenance discipline as a fetched public instrument.
    """
    source = document.source
    policy = _policy(container)
    try:
        # Extract page-level text FIRST, then redact the text (P-04). Redaction must
        # never touch the raw bytes of a binary document: a latin-1 -> regex -> utf-8
        # round trip silently corrupts every PDF stream, which is how the corpus once
        # indexed "%PDF-1.6 ..." garbage as page 1 of a prudential standard.
        paged_text = textract.to_paged_text(document.content, document.mime_type)
        redaction = container.redaction.redact(paged_text)
        if redaction.redacted:
            logger.info(
                "redacted %d PII finding(s) from %s before ingest",
                sum(f.count for f in redaction.findings),
                source.id,
            )
        # Hand on de-identified page-broken text; the store never sees raw PII.
        redacted_doc = _with_content(
            document, redaction.text.encode("utf-8"), textract.PAGED_TEXT_MIME
        )

        result = container.ingestion.ingest(redacted_doc)
        if not result.ok:
            return _record_failure(container, source, document.checksum, result.detail)

        record = FreshnessRecord(
            source_id=source.id,
            url=source.url,
            version=source.version,
            fetched_at=document.fetched_at,
            expires_at=policy.expires_at(document.fetched_at),
            checksum=document.checksum,
            status=FreshnessStatus.FRESH,
        )
        # Roll the generation this ingest supersedes into the record before writing, so the
        # ONE ledger stays diffable for horizon scanning (no shadow store). A byte-identical
        # re-fetch leaves the diff base untouched, so a real change stays visible until it
        # is scanned.
        record = carry_forward(_current_record(container, source.id), record)
        container.ledger.upsert(record)
        logger.info(
            "ingested %s -> document_id=%s chunks=%d expires_at=%s",
            source.id,
            result.document_id,
            result.chunks,
            record.expires_at.isoformat(),
        )
        return SourceOutcome(
            source_id=source.id,
            status=FreshnessStatus.FRESH,
            action="ingested",
            document_id=result.document_id,
            chunks=result.chunks,
            detail=result.detail,
        )
    except Exception as exc:  # noqa: BLE001 - any source must not abort the whole batch
        logger.exception("failed to ingest %s", source.id)
        return _record_failure(container, source, "", str(exc))


def ensure_fresh(container: Container, source_id: str) -> SourceOutcome:
    """Re-ingest ``source_id`` iff its ledger record is missing or stale; else skip.

    This is the read-path hook (SPEC §2): the QA / checklist / test-case flows call it for
    each source they are about to cite so a regulator never receives an answer grounded in
    a document older than the TTL. A fresh source returns immediately without a network
    round-trip.
    """
    sources = _index_sources(container)
    source = sources.get(source_id)
    if source is None:
        return SourceOutcome(
            source_id=source_id,
            status=FreshnessStatus.MISSING,
            action="failed",
            detail=f"source '{source_id}' is not in the registry",
        )

    record = container.ledger.get(source_id)
    if not _policy(container).is_stale(record):
        logger.debug("source %s is fresh; serving from store", source_id)
        return SourceOutcome(
            source_id=source_id,
            status=FreshnessStatus.FRESH,
            action="skipped-fresh",
            detail="ledger record fresh within TTL",
        )
    return ingest_source(container, source)


def refresh_all(container: Container) -> RefreshSummary:
    """Re-ingest **every** source in the registry (used by the job's ``--full`` mode)."""
    outcomes = [ingest_source(container, source) for source in load_sources(container)]
    return RefreshSummary(outcomes=tuple(outcomes))


def refresh_expired(container: Container, now: datetime | None = None) -> RefreshSummary:
    """Refresh only sources that are expired in the ledger **or** never ingested.

    This is the default scheduled-job behaviour: it unions the ledger's expired records
    with any registry sources that have no ledger record yet, so a newly added registry
    entry is picked up on the next run without a full reingest.
    """
    sources = _index_sources(container)
    known_ids = {r.source_id for r in container.ledger.all()}

    expired_ids = {r.source_id for r in container.ledger.list_expired(now)}
    missing_ids = set(sources) - known_ids
    target_ids = sorted(expired_ids | missing_ids)

    outcomes: list[SourceOutcome] = []
    for source_id in target_ids:
        source = sources.get(source_id)
        if source is None:
            # In the ledger but no longer in the registry: nothing to re-fetch.
            logger.warning(
                "expired ledger source %s is no longer in the registry; skipping",
                source_id,
            )
            continue
        outcomes.append(ingest_source(container, source))
    return RefreshSummary(outcomes=tuple(outcomes))


# --------------------------------------------------------------------------- #
# Internals
# --------------------------------------------------------------------------- #
def _record_failure(
    container: Container,
    source: RegSource,
    checksum: str,
    detail: str,
) -> SourceOutcome:
    """Write a FAILED freshness record (already-expired) so the next pass retries it."""
    now = utcnow()
    try:
        failed = FreshnessRecord(
            source_id=source.id,
            url=source.url,
            version=source.version,
            fetched_at=now,
            expires_at=now,  # already expired => eligible for the next refresh
            checksum=checksum,
            status=FreshnessStatus.FAILED,
        )
        # A source that stopped resolving is itself a horizon event (WITHDRAWN), so the
        # superseded generation is carried forward here too.
        container.ledger.upsert(carry_forward(_current_record(container, source.id), failed))
    except Exception:  # noqa: BLE001 - ledger write failure must not mask the original error
        logger.exception("failed to record FAILED ledger entry for %s", source.id)
    return SourceOutcome(
        source_id=source.id,
        status=FreshnessStatus.FAILED,
        action="failed",
        detail=detail,
    )


def _current_record(container: Container, source_id: str) -> FreshnessRecord | None:
    """The ledger's current record for a source, or None (never fatal to an ingest)."""
    try:
        return container.ledger.get(source_id)
    except Exception:  # noqa: BLE001 - a ledger read failure must not abort the ingest
        logger.warning("could not read the current ledger record for %s", source_id)
        return None


def _with_content(document, content: bytes, mime_type: str):  # type: ignore[no-untyped-def]
    """Return a copy of ``document`` carrying the redacted text content."""
    from dataclasses import replace

    return replace(document, content=content, mime_type=mime_type)
