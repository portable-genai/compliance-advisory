"""The real-corpus pipeline: fetch guards, page-preserving redaction, live profile, upload.

These tests pin the behaviours that make the corpus REAL rather than fictional:

* the fetcher refuses an HTML page passed off as a PDF (JS-gated repositories) and
  prefers an operator-dropped file for exactly those publishers;
* redaction operates on extracted page text, never on PDF bytes (the byte-level pass
  silently corrupted every PDF), and page boundaries survive to the citations;
* the ``live`` profile never seeds or serves the fictional built-in corpus and fails
  closed (with the refresh command) rather than answering from an empty index;
* the corpus upload endpoint ingests an audience-provided document with page-level
  citations, and the upload template is downloadable.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from tests.conftest import LOOPBACK_PEER

from compliance_advisory.adapters.live.retrieval import LiveCorpusRetrievalAdapter
from compliance_advisory.adapters.local.document import LocalDocumentParser
from compliance_advisory.adapters.local.retrieval import (
    LocalFtsRetrievalAdapter,
    LocalIngestionAdapter,
)
from compliance_advisory.api import deps
from compliance_advisory.api.app import app
from compliance_advisory.config import Container, LocalSettings, Settings
from compliance_advisory.domain.models import (
    FetchedDocument,
    Jurisdiction,
    RegSource,
    Regulator,
    RetrievalQuery,
    utcnow,
)
from compliance_advisory.pipelines import fetch as fetch_mod
from compliance_advisory.pipelines import ingest as ingest_mod
from compliance_advisory.pipelines import textract

_PDF_SOURCE = RegSource(
    id="test-source",
    regulator=Regulator.MAS,
    jurisdiction=Jurisdiction.SG,
    title="Test Guideline",
    url="https://regulator.example/test-guideline.pdf",
)


def _settings(profile: str, db_path: str = ":memory:") -> Settings:
    base = Settings.load("config/settings.yaml")
    return Settings(
        profile=profile,
        adapters=base.adapters,
        corpus=base.corpus,
        local=LocalSettings(db_path=db_path, audit_path=":memory:", ledger_path=":memory:"),
    )


# --------------------------------------------------------------------------- #
# Fetch guards
# --------------------------------------------------------------------------- #
def _client_returning(body: bytes, content_type: str) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body, headers={"content-type": content_type})

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_html_answer_for_a_pdf_url_is_refused_with_the_manual_drop_hint() -> None:
    client = _client_returning(b"<!DOCTYPE html><html>repository shell</html>", "text/html")
    with pytest.raises(fetch_mod.FetchContentError, match="manual"):
        fetch_mod.fetch_source(_PDF_SOURCE, client=client)


def test_a_real_pdf_answer_passes_the_guard() -> None:
    client = _client_returning(b"%PDF-1.7 not really but the magic matches", "application/pdf")
    document = fetch_mod.fetch_source(_PDF_SOURCE, client=client)
    assert document.mime_type == "application/pdf"
    assert document.checksum


def test_operator_dropped_file_wins_over_the_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / f"{_PDF_SOURCE.id}.pdf").write_bytes(b"%PDF-1.7 dropped")
    monkeypatch.setattr(fetch_mod, "MANUAL_SOURCE_DIR", tmp_path)

    def explode(request: httpx.Request) -> httpx.Response:
        raise AssertionError("the network must not be consulted when a manual file exists")

    client = httpx.Client(transport=httpx.MockTransport(explode))
    document = fetch_mod.fetch_source(_PDF_SOURCE, client=client)
    assert document.content == b"%PDF-1.7 dropped"
    assert document.mime_type == "application/pdf"


# --------------------------------------------------------------------------- #
# Page-preserving redaction (the PDF-corruption regression)
# --------------------------------------------------------------------------- #
def test_ingest_redacts_text_not_bytes_and_keeps_page_numbers() -> None:
    settings = _settings("local")
    container = Container(settings)
    document = FetchedDocument(
        source=_PDF_SOURCE,
        content=b"Page one requires due diligence.\fPage two requires an exit strategy.",
        mime_type="text/plain",
        fetched_at=utcnow(),
        checksum="abc",
    )
    outcome = ingest_mod.ingest_fetched(container, document)
    assert outcome.action == "ingested"
    assert outcome.chunks == 2

    # The pages landed as distinct citations in the ingestion adapter's index.
    ingestion = container.ingestion
    assert isinstance(ingestion, LocalIngestionAdapter)
    hits = ingestion._retrieval.retrieve(  # noqa: SLF001 - same-package store access
        RetrievalQuery(text="exit strategy", top_k=3)
    )
    assert any(h.citation.source_id == "test-source" and h.citation.page == 2 for h in hits)


def test_document_parser_splits_page_broken_text() -> None:
    parser = LocalDocumentParser(_settings("local"))
    extract = parser.parse(
        FetchedDocument(
            source=_PDF_SOURCE,
            content=b"first\fsecond\fthird",
            mime_type=textract.PAGED_TEXT_MIME,
        )
    )
    assert extract.pages == ("first", "second", "third")


def test_paged_text_round_trip() -> None:
    assert textract.split_pages(textract.PAGE_BREAK.join(["a", "b"])) == ("a", "b")
    assert textract.to_paged_text(b"plain body", "text/plain") == "plain body"


# --------------------------------------------------------------------------- #
# Live profile: never fictional, fail closed when empty
# --------------------------------------------------------------------------- #
def test_local_profile_seeds_fiction_but_live_profile_does_not(tmp_path: Path) -> None:
    local = LocalFtsRetrievalAdapter(_settings("local", str(tmp_path / "local.db")))
    assert local.retrieve(RetrievalQuery(text="cloud", top_k=3)), "local self-seeds"

    live_settings = _settings("live", str(tmp_path / "live.db"))
    live = LiveCorpusRetrievalAdapter(live_settings)
    with pytest.raises(RuntimeError, match="refresh_job"):
        live.retrieve(RetrievalQuery(text="cloud", top_k=3))


def test_live_adapter_purges_fictional_rows_from_a_shared_index(tmp_path: Path) -> None:
    db = str(tmp_path / "shared.db")
    # A prior local run seeded the fictional corpus (example.test URLs) into this index.
    local = LocalFtsRetrievalAdapter(_settings("local", db))
    assert any(
        h.citation.url.startswith("https://example.test/")
        for h in local.retrieve(RetrievalQuery(text="cloud", top_k=5))
    )
    # Add one real row so the live index is not empty after the purge.
    real = FetchedDocument(
        source=_PDF_SOURCE,
        content=b"A financial institution should assess cloud concentration risk.",
        mime_type="text/plain",
    )
    LocalIngestionAdapter(_settings("local", db)).ingest(real)

    live = LiveCorpusRetrievalAdapter(_settings("live", db))
    hits = live.retrieve(RetrievalQuery(text="cloud", top_k=10))
    assert hits, "the real row must survive the purge"
    assert all(not h.citation.url.startswith("https://example.test/") for h in hits)


# --------------------------------------------------------------------------- #
# Corpus upload + template (the audience-data path)
# --------------------------------------------------------------------------- #
@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("COMPLIANCE_PROFILE", "local")
    monkeypatch.setenv("COMPLIANCE_LOCAL_DB", ":memory:")
    monkeypatch.setenv("COMPLIANCE_LOCAL_AUDIT", ":memory:")
    monkeypatch.setenv("COMPLIANCE_LOCAL_LEDGER", ":memory:")
    deps.get_container.cache_clear()
    try:
        with TestClient(app, client=LOOPBACK_PEER) as test_client:
            yield test_client
    finally:
        deps.get_container.cache_clear()


def test_upload_template_is_downloadable_csv(client: TestClient) -> None:
    response = client.get("/corpus/upload-template")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment" in response.headers["content-disposition"]
    header = response.text.splitlines()[0]
    assert header == "field,required,example,notes"


def test_upload_ingests_a_document_with_page_citations(client: TestClient) -> None:
    response = client.post(
        "/corpus/documents",
        files={"file": ("policy.txt", b"page one\fpage two", "text/plain")},
        data={"title": "Example Internal Policy", "version": "2026-07"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["ok"] is True
    assert body["chunks"] == 2
    assert body["source_id"].startswith("upload-example-internal-policy")

    status = client.get("/corpus/status").json()
    assert any(r["source_id"] == body["source_id"] for r in status["records"])


def test_upload_rejects_an_unknown_regulator(client: TestClient) -> None:
    response = client.post(
        "/corpus/documents",
        files={"file": ("policy.txt", b"text", "text/plain")},
        data={"title": "Example Internal Policy", "regulator": "NOPE"},
    )
    assert response.status_code == 422


def test_upload_rejects_an_empty_file(client: TestClient) -> None:
    response = client.post(
        "/corpus/documents",
        files={"file": ("policy.txt", b"", "text/plain")},
        data={"title": "Example Internal Policy"},
    )
    assert response.status_code == 422
