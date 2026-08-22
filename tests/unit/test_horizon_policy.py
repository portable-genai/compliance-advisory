"""The deterministic horizon policy: applicability, materiality, ownership, review gate.

These tests are the guarantee behind the module's central claim: the consequential
judgements are pure code over config-owned numbers (B4). They prove the score is
reproducible, that it is driven by the CONFIG rather than by constants baked into the
engine, and that ownership routing and consequential bands always demand a human.
"""

from __future__ import annotations

from datetime import UTC, datetime

from compliance_advisory.config import Settings
from compliance_advisory.domain.horizon import (
    Applicability,
    ChangeKind,
    CorpusChange,
    HorizonPolicy,
    HorizonPolicyConfig,
    MaterialityBand,
)
from compliance_advisory.domain.models import DocType, Jurisdiction, Regulator

_T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _change(
    *,
    kind: ChangeKind = ChangeKind.CONTENT_REVISED,
    regulator: Regulator = Regulator.MAS,
    jurisdiction: Jurisdiction = Jurisdiction.SG,
    doc_type: DocType = DocType.GUIDELINE,
    topics: tuple[str, ...] = ("technology-risk", "cloud"),
) -> CorpusChange:
    return CorpusChange(
        id="mas-trm:content_revised:abc",
        source_id="mas-trm",
        kind=kind,
        regulator=regulator,
        jurisdiction=jurisdiction,
        title="MAS Technology Risk Management Guidelines",
        url="https://example.test/mas/trm",
        doc_type=doc_type,
        topics=topics,
        detected_at=_T0,
    )


# --------------------------------------------------------------------------- #
# Applicability
# --------------------------------------------------------------------------- #
def test_in_footprint_change_with_in_scope_topics_is_applicable() -> None:
    verdict = HorizonPolicy().assess_applicability(_change())
    assert verdict.applicability is Applicability.APPLICABLE
    assert set(verdict.matched_topics) == {"technology-risk", "cloud"}
    assert any("in the configured footprint" in r for r in verdict.reasons)


def test_in_footprint_change_without_in_scope_topics_is_conditional() -> None:
    verdict = HorizonPolicy().assess_applicability(_change(topics=("consumer-credit",)))
    assert verdict.applicability is Applicability.CONDITIONAL
    assert verdict.matched_topics == ()


def test_change_outside_the_declared_footprint_is_not_applicable() -> None:
    policy = HorizonPolicy(HorizonPolicyConfig(in_scope_jurisdictions=("SG",)))
    verdict = policy.assess_applicability(_change(jurisdiction=Jurisdiction.AU))
    assert verdict.applicability is Applicability.NOT_APPLICABLE
    assert any("outside the configured footprint" in r for r in verdict.reasons)


# --------------------------------------------------------------------------- #
# Materiality: pure, reproducible arithmetic
# --------------------------------------------------------------------------- #
def test_materiality_score_is_the_sum_of_its_named_drivers() -> None:
    policy = HorizonPolicy()
    change = _change()
    verdict = policy.assess_applicability(change)
    materiality = policy.score_materiality(change, verdict, open_control_gaps=2)

    # content_revised 32 + guideline 14 + 2 topics x 8 + 2 gaps x 7 = 76 -> HIGH
    assert materiality.score == 76
    assert sum(d.points for d in materiality.drivers) == materiality.score
    assert materiality.band is MaterialityBand.HIGH
    assert {d.name for d in materiality.drivers} == {
        "change_kind",
        "doc_type",
        "topic_overlap",
        "open_control_gaps",
    }


def test_materiality_is_deterministic_across_repeated_runs() -> None:
    policy = HorizonPolicy()
    change = _change()
    verdict = policy.assess_applicability(change)
    runs = [policy.score_materiality(change, verdict, open_control_gaps=1) for _ in range(5)]
    assert len({(r.score, r.band) for r in runs}) == 1


def test_open_control_gaps_raise_materiality() -> None:
    """The link into the control-mapping journey actually moves the number."""
    policy = HorizonPolicy()
    change = _change()
    verdict = policy.assess_applicability(change)

    clean = policy.score_materiality(change, verdict, open_control_gaps=0)
    exposed = policy.score_materiality(change, verdict, open_control_gaps=3)
    assert exposed.score > clean.score


def test_driver_caps_and_clamp_hold() -> None:
    policy = HorizonPolicy()
    change = _change(
        kind=ChangeKind.NEW_SOURCE,
        doc_type=DocType.STANDARD,
        topics=(
            "technology-risk",
            "cloud",
            "outsourcing",
            "incident-response",
            "model-risk",
        ),
    )
    verdict = policy.assess_applicability(change)
    materiality = policy.score_materiality(change, verdict, open_control_gaps=99)

    topic_driver = next(d for d in materiality.drivers if d.name == "topic_overlap")
    gap_driver = next(d for d in materiality.drivers if d.name == "open_control_gaps")
    assert topic_driver.points == 24  # capped
    assert gap_driver.points == 21  # capped
    assert materiality.score <= HorizonPolicy.MAX_SCORE


def test_conditional_applicability_is_discounted() -> None:
    policy = HorizonPolicy()
    change = _change(topics=("consumer-credit",))
    verdict = policy.assess_applicability(change)
    materiality = policy.score_materiality(change, verdict)

    penalty = next(d for d in materiality.drivers if d.name == "conditional_applicability")
    assert penalty.points < 0


def test_not_applicable_change_scores_zero() -> None:
    policy = HorizonPolicy(HorizonPolicyConfig(in_scope_regulators=("MAS",)))
    change = _change(regulator=Regulator.FSA, jurisdiction=Jurisdiction.JP)
    verdict = policy.assess_applicability(change)
    materiality = policy.score_materiality(change, verdict, open_control_gaps=5)

    assert materiality.score == 0
    assert materiality.band is MaterialityBand.LOW


# --------------------------------------------------------------------------- #
# The numbers are BANK-OWNED (B4), not baked into the engine
# --------------------------------------------------------------------------- #
def test_band_thresholds_come_from_config_not_from_code() -> None:
    change = _change()
    reference = HorizonPolicy()
    verdict = reference.assess_applicability(change)
    baseline = reference.score_materiality(change, verdict)
    # content_revised 32 + guideline 14 + 2 topics x 8 = 62, which clears the HIGH floor (58).
    assert baseline.score == 62
    assert baseline.band is MaterialityBand.HIGH

    # A bank that bands more severely gets a different verdict from the SAME arithmetic.
    strict = HorizonPolicy(
        HorizonPolicyConfig(band_thresholds={"critical": 60, "high": 40, "medium": 10, "low": 0})
    )
    raised = strict.score_materiality(change, strict.assess_applicability(change))
    assert raised.score == baseline.score  # same arithmetic
    assert raised.band is not baseline.band  # different bank policy, different band


def test_change_kind_weights_come_from_config() -> None:
    change = _change(kind=ChangeKind.VERSION_BUMP)
    louder = HorizonPolicy(HorizonPolicyConfig(change_kind_weights={"version_bump": 90}))
    quiet = HorizonPolicy()

    assert (
        louder.score_materiality(change, louder.assess_applicability(change)).score
        > quiet.score_materiality(change, quiet.assess_applicability(change)).score
    )


def test_settings_yaml_defaults_equal_the_reference_policy() -> None:
    """The shipped config must be the reference policy, not a silently different one."""
    settings = Settings.load("config/settings.yaml")
    reference = HorizonPolicyConfig()

    assert settings.horizon.band_thresholds == reference.band_thresholds
    assert settings.horizon.change_kind_weights == reference.change_kind_weights
    assert settings.horizon.doc_type_weights == reference.doc_type_weights
    assert settings.horizon.sla_days == reference.sla_days
    assert settings.horizon.topic_owners == reference.topic_owners
    assert settings.horizon.in_scope_topics == reference.in_scope_topics
    assert settings.horizon.default_owner == reference.default_owner


# --------------------------------------------------------------------------- #
# Ownership routing + the review gate
# --------------------------------------------------------------------------- #
def test_ownership_routes_on_the_first_matching_topic_rule() -> None:
    policy = HorizonPolicy()
    change = _change(topics=("outsourcing", "cloud"))
    verdict = policy.assess_applicability(change)
    owner = policy.route_owner(change, verdict, MaterialityBand.HIGH)

    assert owner is not None
    assert owner.owner == "third-party-risk-office"
    assert owner.matched_on == "outsourcing"
    assert owner.due_within_days == 60  # HIGH band SLA from config


def test_unmatched_topics_fall_back_to_the_configured_default_owner() -> None:
    policy = HorizonPolicy()
    change = _change(topics=("consumer-credit",))
    verdict = policy.assess_applicability(change)
    owner = policy.route_owner(change, verdict, MaterialityBand.LOW)

    assert owner is not None
    assert owner.owner == "compliance-office"


def test_not_applicable_change_gets_no_owner_and_no_review() -> None:
    policy = HorizonPolicy(HorizonPolicyConfig(in_scope_regulators=("MAS",)))
    change = _change(regulator=Regulator.FSA, jurisdiction=Jurisdiction.JP)
    verdict = policy.assess_applicability(change)
    owner = policy.route_owner(change, verdict, MaterialityBand.LOW)

    assert owner is None
    assert policy.requires_review(verdict, MaterialityBand.LOW, owner) is False


def test_every_routed_change_requires_human_review() -> None:
    """Ownership routing is itself a consequential call (P-06, rule R8)."""
    policy = HorizonPolicy()
    change = _change(kind=ChangeKind.VERSION_BUMP, doc_type=DocType.OTHER, topics=("cloud",))
    verdict = policy.assess_applicability(change)
    materiality = policy.score_materiality(change, verdict)
    owner = policy.route_owner(change, verdict, materiality.band)

    assert materiality.band is MaterialityBand.LOW  # even the quietest routed change
    assert policy.requires_review(verdict, materiality.band, owner) is True
