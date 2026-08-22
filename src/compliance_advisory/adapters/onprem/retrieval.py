"""On-prem placeholder for ``RetrievalPort`` — the Google Distributed Cloud target.

This is one of the migration placeholders that make reversibility (P-02, P-12) a
*demonstrable* property rather than a claim. In the managed profile this port is bound
to the Agent Search adapter; switching ``profile`` to ``onprem`` rebinds it here. The
adapter constructs cleanly with **no external dependencies** and structurally satisfies
the same Protocol as the managed adapter, so the contract tests prove interface parity.
Every method is a deliberate ``NotImplementedError`` stub: porting C1 to an on-premise
platform (Google Distributed Cloud) is *only* a matter of filling these bodies in — the
core domain logic and the service callers are untouched.
"""

from __future__ import annotations

from ...config import Settings
from ...domain.models import RetrievalQuery, RetrievedPassage

_MESSAGE = (
    "On-prem RetrievalPort adapter is a migration placeholder; implement against your "
    "on-premise platform. Core domain logic is unchanged."
)


class OnPremRetrievalAdapter:
    """Placeholder retrieval adapter for the on-prem (Google Distributed Cloud) profile."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def retrieve(self, query: RetrievalQuery) -> list[RetrievedPassage]:
        raise NotImplementedError(_MESSAGE)
