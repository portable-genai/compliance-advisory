"""Local ReviewRouterPort: enqueue the routed review to an in-memory outbox (no live
human-review-console).

Exercises the R8 routing path offline: an escalated item is converted to a review and enqueued (the
same transactional outbox the platform adapter flushes to human-review-console), so tests and the
offline demo can assert that an escalation is routed without a running console.

The one router serves EVERY escalation path against the single human-review-console contract: an
escalated compliance :class:`Answer` (the assistant's Q&A path), an :class:`EvidencePack` (the
control-mapping capability's always-review deliverable), a :class:`HorizonAssessment` (ownership
routing plus a consequential materiality call) and an :class:`ImplementationItem` (a tracked change
closed as implemented or accepted risk). It dispatches on the payload type.
"""

from __future__ import annotations

from review_kit import InMemoryOutbox

from ...config import Settings
from ...domain.control_mapping.models import EvidencePack
from ...domain.horizon.models import HorizonAssessment, ImplementationItem
from ...domain.models import Answer
from .._review_payload import (
    answer_to_review,
    assessment_to_review,
    implementation_to_review,
    pack_to_review,
)


class LocalReviewRouter:
    """Record routed reviews in an in-memory outbox for the SDK-free ``local`` profile."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._outbox = InMemoryOutbox()

    def route(
        self,
        answer: Answer | EvidencePack | HorizonAssessment | ImplementationItem,
        *,
        maker: str,
        tenant: str = "",
    ) -> None:
        if isinstance(answer, EvidencePack):
            self._outbox.enqueue(
                pack_to_review(answer, maker=maker, tenant=tenant),
                actor="control-mapping",
            )
            return
        if isinstance(answer, HorizonAssessment):
            self._outbox.enqueue(
                assessment_to_review(answer, maker=maker, tenant=tenant),
                actor="horizon-scanning",
            )
            return
        if isinstance(answer, ImplementationItem):
            self._outbox.enqueue(
                implementation_to_review(answer, maker=maker, tenant=tenant),
                actor="horizon-scanning",
            )
            return
        self._outbox.enqueue(
            answer_to_review(answer, maker=maker, tenant=tenant), actor="compliance-advisory"
        )

    @property
    def outbox(self) -> InMemoryOutbox:
        """Expose the outbox for inspection in tests and the demo."""
        return self._outbox
