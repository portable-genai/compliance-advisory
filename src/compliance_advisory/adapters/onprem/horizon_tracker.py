"""On-prem placeholder for ``HorizonTrackerPort`` — the Google Distributed Cloud target.

One of the reversibility (P-02, P-12) migration placeholders: in the managed profile this
port binds to the AlloyDB tracking adapter; switching ``profile`` to ``onprem`` rebinds it
here. The adapter constructs cleanly with **no external dependencies** and structurally
satisfies the same Protocol as the managed adapter, so the contract tests prove interface
parity. Porting the horizon implementation journey to an on-premise relational store is
*only* a matter of filling these bodies in: the detection diff, the materiality policy and
the tracking service are unchanged.

The methods raise rather than returning empty results: silently reporting "no tracked
changes" would let a regulatory obligation disappear from the journey (P-06, rule R8).
"""

from __future__ import annotations

from ...config import Settings
from ...domain.horizon.models import ImplementationItem

_MESSAGE = (
    "On-prem HorizonTrackerPort adapter is a migration placeholder; implement against your "
    "on-premise platform. Core domain logic is unchanged."
)


class OnPremHorizonTrackerAdapter:
    """Placeholder horizon-tracking adapter for the on-prem profile."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def upsert(self, item: ImplementationItem) -> None:
        raise NotImplementedError(_MESSAGE)

    def get(self, change_id: str) -> ImplementationItem | None:
        raise NotImplementedError(_MESSAGE)

    def list(self, tenant: str) -> list[ImplementationItem]:
        raise NotImplementedError(_MESSAGE)
