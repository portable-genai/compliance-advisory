"""Human-in-the-loop (maker-checker) policy — General Principle P-06.

C1 is a *decision-support* assistant, never an autonomous approver. P-06 (maker
checker) requires that a human reviews any consequential or low-assurance output
before it is relied upon. This module centralises that gate so every service applies
identical rules and the threshold is auditable in one place.

Policy (SPEC §5):
* Consequential artifacts — control checklists and test cases — **always** require
  human review: they drive control design and assurance, so a maker (the assistant)
  proposes and a checker (a qualified human) disposes.
* Every grounded answer requires review. This is an immutable safety floor; confidence
  and severity can increase the review level but configuration cannot remove the checker.
* Regulator/CRO question sets are consequential too (they shape supervisory
  engagement) and require review.
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import Severity
from .policy import CompliancePolicyConfig


@dataclass(frozen=True, slots=True)
class HumanReviewPolicy:
    """Maker-checker gate (P-06). Pure decision logic; no side effects.

    Args:
        answer_confidence_floor: confidence below this value escalates the review level.
        review_all_answers: the immutable maker-checker floor. Construction refuses
            ``False`` through :class:`CompliancePolicyConfig`.
    """

    answer_confidence_floor: float = 0.6
    review_all_answers: bool = True
    always_review_artifacts: frozenset[str] = frozenset(
        {"answer", "checklist", "testcases", "testcase", "regulator_questions"}
    )
    high_severities: frozenset[Severity] = frozenset({Severity.HIGH, Severity.CRITICAL})

    @classmethod
    def from_policy(cls, policy: CompliancePolicyConfig) -> HumanReviewPolicy:
        """Build the pure review engine from the adopter-owned policy bundle."""
        return cls(
            answer_confidence_floor=policy.answer_confidence_floor,
            review_all_answers=policy.review_all_answers,
            always_review_artifacts=frozenset(
                value.lower() for value in policy.always_review_artifacts
            ),
            high_severities=frozenset(Severity(value.lower()) for value in policy.high_severities),
        )

    def requires_review(
        self,
        confidence: float,
        severity: Severity | None,
        artifact_kind: str,
    ) -> bool:
        """Decide whether ``artifact_kind`` must be routed to a human checker.

        Args:
            confidence: model/self-critique confidence in [0.0, 1.0].
            severity: highest severity among the artifact's cited topics, if any.
            artifact_kind: one of "answer", "checklist", "testcases",
                "regulator_questions" (case-insensitive).

        Returns:
            True if a human must review before the output is relied upon.
        """
        kind = artifact_kind.strip().lower()

        # Consequential artifacts are always maker-checker gated.
        if kind in self.always_review_artifacts:
            return True

        if kind == "answer" and self.review_all_answers:
            return True

        # Answers: low confidence forces review.
        if confidence < self.answer_confidence_floor:
            return True

        # Answers: any high/critical-severity cited topic forces review.
        return severity is not None and severity in self.high_severities
