"""Unit tests for the two domain policies (SPEC §5).

* HumanReviewPolicy.requires_review(confidence, severity, artifact_kind) -> bool (P-06)
* FreshnessPolicy(ttl_days) -> .expires_at(fetched_at), .is_stale(record)

SPEC §5 behaviour: every generated answer and consequential artifact requires review;
confidence and severity can only raise the review level, never remove the checker.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from tests.conftest import load_policy

from compliance_advisory.domain.models import (
    FreshnessRecord,
    FreshnessStatus,
    Severity,
)

# Artifact kinds the maker-checker gate never auto-releases.
CONSEQUENTIAL_KINDS = ("checklist", "testcases", "regulator_questions")


# --------------------------------------------------------------------------- #
# HumanReviewPolicy
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("kind", CONSEQUENTIAL_KINDS)
def test_consequential_artifacts_always_require_review(kind: str):
    policy = load_policy("HumanReviewPolicy")()
    # Even maximally confident, low-severity -> a consequential artifact still reviews.
    assert (
        policy.requires_review(confidence=0.99, severity=Severity.LOW, artifact_kind=kind) is True
    )


def test_low_confidence_answer_requires_review():
    policy = load_policy("HumanReviewPolicy")()
    assert (
        policy.requires_review(confidence=0.1, severity=Severity.LOW, artifact_kind="answer")
        is True
    )


def test_high_severity_answer_requires_review():
    policy = load_policy("HumanReviewPolicy")()
    assert (
        policy.requires_review(confidence=0.95, severity=Severity.CRITICAL, artifact_kind="answer")
        is True
    )


def test_confident_low_severity_answer_still_requires_review():
    policy = load_policy("HumanReviewPolicy")()
    assert (
        policy.requires_review(confidence=0.95, severity=Severity.LOW, artifact_kind="answer")
        is True
    )


def test_answer_with_no_severity_and_high_confidence_still_requires_review():
    policy = load_policy("HumanReviewPolicy")()
    # severity may be None when no cited topic is high-risk.
    assert policy.requires_review(confidence=0.9, severity=None, artifact_kind="answer") is True


def test_deliberate_policy_override_changes_non_consequential_behavior():
    from compliance_advisory.domain.hitl import HumanReviewPolicy
    from compliance_advisory.domain.policy import CompliancePolicyConfig

    configured = CompliancePolicyConfig(
        always_review_artifacts=("checklist",),
        answer_confidence_floor=0.8,
    )
    policy = HumanReviewPolicy.from_policy(configured)
    assert policy.requires_review(0.81, Severity.LOW, "memo") is False
    assert policy.requires_review(0.79, Severity.LOW, "memo") is True


def test_review_all_answers_is_an_immutable_floor_and_severity_is_validated():
    from compliance_advisory.domain.policy import CompliancePolicyConfig

    with pytest.raises(ValueError, match="immutable production safety floor"):
        CompliancePolicyConfig(review_all_answers=False)
    with pytest.raises(ValueError, match="invalid values"):
        CompliancePolicyConfig(high_severities=("severe",))


def test_settings_defaults_equal_reference_policy(tmp_path, monkeypatch):
    from compliance_advisory.config import Settings
    from compliance_advisory.domain.policy import CompliancePolicyConfig

    monkeypatch.setenv("COMPLIANCE_PROFILE", "local")
    settings = Settings.load()
    assert settings.policy == CompliancePolicyConfig()


# --------------------------------------------------------------------------- #
# FreshnessPolicy — 7-day TTL
# --------------------------------------------------------------------------- #
def _record(
    expires_at: datetime,
    status: FreshnessStatus = FreshnessStatus.FRESH,
) -> FreshnessRecord:
    fetched = expires_at - timedelta(days=7)
    return FreshnessRecord(
        source_id="mas-trm-guidelines",
        url="https://example.test/mas/trm",
        version="2021",
        fetched_at=fetched,
        expires_at=expires_at,
        checksum="abc",
        status=status,
    )


def test_expires_at_is_ttl_days_after_fetch():
    policy = load_policy("FreshnessPolicy")(ttl_days=7)
    fetched = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
    expires = policy.expires_at(fetched)
    assert expires == fetched + timedelta(days=7)


def test_is_stale_true_for_expired_record():
    policy = load_policy("FreshnessPolicy")(ttl_days=7)
    expired = _record(datetime.now(UTC) - timedelta(days=1))
    assert policy.is_stale(expired) is True


def test_is_stale_false_for_fresh_record():
    policy = load_policy("FreshnessPolicy")(ttl_days=7)
    fresh = _record(datetime.now(UTC) + timedelta(days=3))
    assert policy.is_stale(fresh) is False


def test_is_stale_true_for_non_fresh_status():
    policy = load_policy("FreshnessPolicy")(ttl_days=7)
    # Even with a future expiry, a FAILED/EXPIRED/MISSING status is stale.
    failed = _record(datetime.now(UTC) + timedelta(days=3), status=FreshnessStatus.FAILED)
    assert policy.is_stale(failed) is True


def test_record_is_fresh_helper_consistent_with_policy():
    # The model's own is_fresh() and the policy's is_stale() must agree.
    policy = load_policy("FreshnessPolicy")(ttl_days=7)
    fresh = _record(datetime.now(UTC) + timedelta(days=2))
    assert fresh.is_fresh() is True
    assert policy.is_stale(fresh) is False


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
