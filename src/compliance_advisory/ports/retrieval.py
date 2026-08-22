"""RetrievalPort — grounded passage retrieval over the regulatory knowledge base.

Primary GCP adapter: **Agent Search** (formerly Vertex AI Search) on the
Gemini Enterprise Agent Platform, pinned to a single in-country region.
On-prem migration swaps this for a placeholder adapter with no change to callers.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..domain.models import RetrievalQuery, RetrievedPassage


@runtime_checkable
class RetrievalPort(Protocol):
    def retrieve(self, query: RetrievalQuery) -> list[RetrievedPassage]:
        """Return ranked passages with regulator-grade citations for ``query``."""
        ...
