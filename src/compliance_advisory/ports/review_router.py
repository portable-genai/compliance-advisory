"""ReviewRouterPort: the boundary that routes an escalated compliance answer to human-review-console
(rule R8).

The compliance Q&A pipeline (SPEC §5) is decision-support: a low-confidence or high-severity answer
sets ``requires_human_review`` (maker-checker, P-06). Rule R8 says a producer that sets that flag
MUST route the item to the human-review-console Human-Review & Maker-Checker Console rather than
terminate the escalation in a per-repo boolean. This port is that hand-off. The domain stays pure:
the adapter (not this port) depends on the shared ``review-kit`` client and does the S2S submission.
``tenant`` is a call parameter because :class:`Answer` carries no tenant field; the API threads the
verified principal's tenant through.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..domain.control_mapping.models import EvidencePack
from ..domain.horizon.models import HorizonAssessment, ImplementationItem
from ..domain.models import Answer

#: Everything this repo can escalate. One port, one human-review-console contract, four payload
#: shapes:
#: a compliance answer, a control-mapping evidence pack, an assessed regulatory change
#: (horizon), and the closure of a tracked change (horizon). Adapters dispatch by type.
Escalatable = Answer | EvidencePack | HorizonAssessment | ImplementationItem


@runtime_checkable
class ReviewRouterPort(Protocol):
    def route(self, answer: Escalatable, *, maker: str, tenant: str = "") -> None:
        """Route an escalated item to human-review-console for human review (idempotent per item is
        ideal).
        """
        ...
