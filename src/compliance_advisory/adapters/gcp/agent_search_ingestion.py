"""Agent Search ingestion adapter (CorpusIngestionPort).

Indexes fetched regulatory documents into the **Agent Search** (formerly Vertex
AI Search) data store on the **Gemini Enterprise Agent Platform**, using the
Discovery Engine ``DocumentService`` through a **regional** endpoint pinned to
``asia-southeast1`` (Singapore).

This is the write side of the 7-day fetch-at-runtime corpus: when the freshness
ledger reports a source as expired or missing, the pipeline re-fetches it and
calls :meth:`ingest` to (re-)index it before answering. Each document is stored
with ``struct_data`` carrying the provenance fields (``source_id``, ``regulator``,
``jurisdiction``, ``version``, ``url``) that the retrieval adapter reads back to
build regulator-grade citations.

All Google Cloud SDK imports are lazy so the on-prem / test profile imports this
module without ``google-cloud-discoveryengine`` installed.
"""

from __future__ import annotations

import base64
from typing import TYPE_CHECKING, Any

from ...config import Settings
from ...domain.models import FetchedDocument, IngestResult

if TYPE_CHECKING:  # pragma: no cover - typing only, never imported at runtime
    from google.cloud import discoveryengine_v1


class AgentSearchIngestionAdapter:
    """(Re-)index fetched documents into the Agent Search data store."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        cfg = settings.agent_search
        self._location = cfg.location
        self._data_store_id = cfg.data_store_id
        self._endpoint = f"{self._location}-discoveryengine.googleapis.com"
        self._client: Any | None = None

    # ------------------------------------------------------------------ #
    # Lazy client construction
    # ------------------------------------------------------------------ #
    def _get_client(self) -> discoveryengine_v1.DocumentServiceClient:
        if self._client is None:
            from google.api_core.client_options import ClientOptions
            from google.cloud import discoveryengine_v1

            self._client = discoveryengine_v1.DocumentServiceClient(
                client_options=ClientOptions(api_endpoint=self._endpoint),
            )
        return self._client

    def _branch_path(self) -> str:
        """Default branch of the data store's document collection."""
        return (
            f"projects/{self._settings.project_id}"
            f"/locations/{self._location}"
            f"/collections/default_collection"
            f"/dataStores/{self._data_store_id}"
            f"/branches/default_branch"
        )

    def _document_path(self, document_id: str) -> str:
        return f"{self._branch_path()}/documents/{document_id}"

    # ------------------------------------------------------------------ #
    # CorpusIngestionPort
    # ------------------------------------------------------------------ #
    def ingest(self, document: FetchedDocument) -> IngestResult:
        """Index ``document`` into Agent Search, replacing any prior version.

        The document is created inline (``create_document``); if it already
        exists, it is updated in place (``update_document``) so re-ingestion on
        expiry refreshes content and ``struct_data`` without duplicating.
        """
        from google.api_core.exceptions import AlreadyExists, GoogleAPICallError
        from google.cloud import discoveryengine_v1

        client = self._get_client()
        source = document.source
        document_id = source.id

        struct_data = {
            "source_id": source.id,
            "regulator": source.regulator.value,
            "jurisdiction": source.jurisdiction.value,
            "version": source.version,
            "url": source.url,
            "title": source.title,
            "doc_type": source.doc_type.value,
        }

        # Inline raw content for indexing. Binary payloads (e.g. PDFs) are stored
        # base64-encoded in the Document.content struct field.
        encoded = base64.b64encode(document.content).decode("ascii")
        content = discoveryengine_v1.Document.Content(
            mime_type=document.mime_type,
            raw_bytes=document.content,
        )

        doc = discoveryengine_v1.Document(
            id=document_id,
            struct_data=struct_data,
            content=content,
        )

        try:
            client.create_document(
                request=discoveryengine_v1.CreateDocumentRequest(
                    parent=self._branch_path(),
                    document=doc,
                    document_id=document_id,
                )
            )
            detail = "created"
        except AlreadyExists:
            doc.name = self._document_path(document_id)
            client.update_document(
                request=discoveryengine_v1.UpdateDocumentRequest(
                    document=doc,
                    allow_missing=True,
                )
            )
            detail = "updated"
        except GoogleAPICallError as exc:  # pragma: no cover - live-call path
            return IngestResult(
                source_id=source.id,
                document_id=document_id,
                chunks=0,
                ok=False,
                detail=f"ingest failed: {exc}",
            )

        # `encoded` is retained for callers/adapters that index via inline struct
        # rather than raw bytes; size acts as a coarse signal of indexed payload.
        return IngestResult(
            source_id=source.id,
            document_id=document_id,
            chunks=1,
            ok=True,
            detail=f"{detail} ({len(encoded)} b64 chars)",
        )

    def delete(self, source_id: str) -> None:
        """Remove the document indexed for ``source_id`` from the data store."""
        from google.api_core.exceptions import NotFound
        from google.cloud import discoveryengine_v1

        client = self._get_client()
        try:
            client.delete_document(
                request=discoveryengine_v1.DeleteDocumentRequest(
                    name=self._document_path(source_id),
                )
            )
        except NotFound:
            # Already absent — deletion is idempotent for the corpus pipeline.
            return
