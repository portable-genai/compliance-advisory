"""Live retrieval adapter (RetrievalPort) — the REAL regulatory corpus only.

Same SQLite FTS5 store as the local adapter, with the live profile's two extra
guarantees:

1. **No fiction.** It never self-seeds the built-in fictional passages, and on
   construction it purges any ``example.test`` rows an earlier local run may have left
   in a shared index, so a live answer can only ever cite a real ingested instrument.
2. **Fail closed, with the fix in the message.** Retrieving from an empty index raises
   with the exact refresh command instead of silently answering ungrounded.
"""

from __future__ import annotations

from ...config import Settings
from ...domain.models import RetrievalQuery, RetrievedPassage
from ..local.retrieval import LocalFtsRetrievalAdapter

#: Every fictional built-in passage cites this host; nothing real ever does.
_FICTION_URL_PREFIX = "https://example.test/"

_EMPTY_HINT = (
    "the regulatory corpus is empty under the live profile: ingest the real "
    "instruments first with `python -m compliance_advisory.pipelines.refresh_job "
    "--full` (see the source registry under pipelines/sources/)"
)


class LiveCorpusRetrievalAdapter(LocalFtsRetrievalAdapter):
    """Serve only real ingested regulator documents; refuse to answer from nothing."""

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self._purge_fiction()

    # The base class consults settings.profile ("live" never seeds); this override makes
    # the invariant structural rather than configuration-dependent.
    def _maybe_seed(self) -> None:
        return

    def _purge_fiction(self) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM passages WHERE url LIKE ?", (f"{_FICTION_URL_PREFIX}%",)
            )
            self._conn.commit()

    def retrieve(self, query: RetrievalQuery) -> list[RetrievedPassage]:
        if self._is_empty():
            raise RuntimeError(_EMPTY_HINT)
        return super().retrieve(query)
