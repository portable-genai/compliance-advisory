"""On-prem placeholder for ``CorpusIngestionPort`` — the Google Distributed Cloud target.

One of the reversibility (P-02, P-12) migration placeholders: in the managed profile
this port binds to the Agent Search ingestion adapter; switching ``profile`` to
``onprem`` rebinds it here. The adapter constructs cleanly with **no external
dependencies** and structurally satisfies the same Protocol as the managed adapter, so
the contract tests prove interface parity. Porting document ingestion to an on-premise
retrieval store is *only* a matter of filling these bodies in — the domain
fetch-at-runtime pipeline is unchanged.
"""

from __future__ import annotations

from ...config import Settings
from ...domain.models import FetchedDocument, IngestResult

_MESSAGE = (
    "On-prem CorpusIngestionPort adapter is a migration placeholder; implement against "
    "your on-premise platform. Core domain logic is unchanged."
)


class OnPremIngestionAdapter:
    """Placeholder corpus-ingestion adapter for the on-prem (Google Distributed Cloud) profile."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def ingest(self, document: FetchedDocument) -> IngestResult:
        raise NotImplementedError(_MESSAGE)

    def delete(self, source_id: str) -> None:
        raise NotImplementedError(_MESSAGE)
