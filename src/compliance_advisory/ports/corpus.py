"""Corpus ports — the 7-day fetch-at-runtime freshness model.

Documents live in **Agent Search**; the freshness ledger lives in **AlloyDB**.
On a read, fresh sources (< TTL) are served from the store; expired sources are
re-fetched and re-ingested before answering. A scheduled job refreshes expiring
sources out of band.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from ..domain.models import FetchedDocument, FreshnessRecord, IngestResult


@runtime_checkable
class CorpusLedgerPort(Protocol):
    def get(self, source_id: str) -> FreshnessRecord | None: ...

    def upsert(self, record: FreshnessRecord) -> None: ...

    def list_expired(self, now: datetime | None = None) -> list[FreshnessRecord]:
        """Return ledger records whose ``expires_at`` is in the past."""
        ...

    def all(self) -> list[FreshnessRecord]: ...


@runtime_checkable
class CorpusIngestionPort(Protocol):
    def ingest(self, document: FetchedDocument) -> IngestResult:
        """Index a fetched document into the retrieval store (Agent Search)."""
        ...

    def delete(self, source_id: str) -> None: ...
