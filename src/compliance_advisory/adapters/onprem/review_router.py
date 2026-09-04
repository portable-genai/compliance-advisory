"""On-prem placeholder for ``ReviewRouterPort`` — the sovereign target.

One of the reversibility (P-02, P-12) migration placeholders: in the managed profile this port binds
to the human-review-console submission adapter; switching ``profile`` to ``onprem`` rebinds it here.
The adapter constructs cleanly with **no external dependencies** and structurally satisfies the same
Protocol as the managed adapter, so the contract tests prove interface parity. ``route``
deliberately raises rather than silently dropping the escalation: an unimplemented router must never
let a consequential answer bypass human review (P-06, rule R8), so porting on-premise *must* supply
a real hand-off to the sovereign review console. Filling this body in is the only change required.
"""

from __future__ import annotations

from ...config import Settings
from ...domain.control_mapping.models import EvidencePack
from ...domain.horizon.models import HorizonAssessment, ImplementationItem
from ...domain.models import Answer

_MESSAGE = (
    "On-prem ReviewRouterPort adapter is a migration placeholder; implement against your "
    "on-premise human-review console. Core domain logic is unchanged."
)


class OnPremReviewRouter:
    """Placeholder review-router adapter for the on-prem profile.

    Serves every R8 path (compliance :class:`Answer`, control-mapping
    :class:`EvidencePack`, horizon :class:`HorizonAssessment` and horizon
    :class:`ImplementationItem`); all fail-fast until the sovereign console is wired.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def route(
        self,
        answer: Answer | EvidencePack | HorizonAssessment | ImplementationItem,
        *,
        maker: str,
        tenant: str = "",
    ) -> None:
        raise NotImplementedError(_MESSAGE)
