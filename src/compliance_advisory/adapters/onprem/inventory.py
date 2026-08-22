"""On-prem placeholder for ``ControlInventoryPort`` — Google Distributed Cloud target.

One of the reversibility (P-02, P-12) migration placeholders: in the managed profile
this port binds to the Security Command Center + Cloud Asset Inventory + Assured
Workloads adapter; switching ``profile`` to ``onprem`` rebinds it here. The adapter
constructs cleanly with **no external dependencies** and structurally satisfies the
same Protocol as the managed adapter, so the contract tests prove interface parity.
Both methods deliberately raise rather than returning an empty posture: an
unimplemented inventory must never let the toolkit assert coverage it cannot evidence,
so porting on-premise *must* supply a real posture source. Filling these bodies in is
the only change required.
"""

from __future__ import annotations

from ...config import Settings
from ...domain.control_mapping.models import ControlObservation, GcpControl

_MESSAGE = (
    "On-prem ControlInventoryPort adapter is a migration placeholder; implement against "
    "your on-premise platform. Core domain logic is unchanged."
)


class OnPremControlInventoryAdapter:
    """Placeholder control-inventory adapter for the on-prem profile."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def observe(self, scope: str) -> list[ControlObservation]:
        raise NotImplementedError(_MESSAGE)

    def list_controls(self) -> list[GcpControl]:
        raise NotImplementedError(_MESSAGE)
