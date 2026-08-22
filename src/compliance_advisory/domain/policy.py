"""Bank-owned policy for the assistant path.

The reference values are defaults here and are repeated in ``config/settings.yaml`` so an
adopter changes policy through configuration without editing orchestration code.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CompliancePolicyConfig:
    """Consequential review and severity policy for grounded answers."""

    answer_confidence_floor: float = 0.6
    review_all_answers: bool = True
    always_review_artifacts: tuple[str, ...] = (
        "answer",
        "checklist",
        "testcases",
        "testcase",
        "regulator_questions",
    )
    high_severities: tuple[str, ...] = ("high", "critical")
    high_risk_topics: tuple[str, ...] = (
        "operational-resilience",
        "outsourcing",
        "incident",
        "cyber",
        "aml",
        "sanctions",
        "third-party",
    )

    def __post_init__(self) -> None:
        if not 0.0 <= self.answer_confidence_floor <= 1.0:
            raise ValueError("policy.answer_confidence_floor must be between 0 and 1")
        if not self.review_all_answers:
            raise ValueError("policy.review_all_answers is an immutable production safety floor")
        for name, values in (
            ("always_review_artifacts", self.always_review_artifacts),
            ("high_severities", self.high_severities),
            ("high_risk_topics", self.high_risk_topics),
        ):
            if not values or any(not value.strip() for value in values):
                raise ValueError(f"policy.{name} must contain non-empty values")
        allowed_severities = {"low", "medium", "high", "critical"}
        invalid = {value.lower() for value in self.high_severities} - allowed_severities
        if invalid:
            raise ValueError(f"policy.high_severities contains invalid values: {sorted(invalid)}")
