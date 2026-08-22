"""The deterministic horizon policy: applicability, materiality, ownership (SPEC §7.2).

This module is the reason the horizon module can be put in front of a regulator. Every
consequential judgement is PURE CODE over a frozen, config-owned policy object:

* **Applicability** is a set membership test against the bank's declared regulated
  footprint (regulators, jurisdictions, in-scope topics), with the matched/unmatched
  reasons recorded.
* **Materiality** is an additive integer score built from named
  :class:`~.models.MaterialityDriver` contributions and banded by config-owned thresholds.
  The LLM never sees the score before it is computed and cannot change it afterwards.
* **Ownership** is a first-match lookup: topic rules, then regulator rules, then the
  configured default owner. Routing is deterministic, so the same change always lands in
  the same queue.

:class:`HorizonPolicyConfig` holds the numbers. Its defaults ARE the reference policy, and
``config/settings.yaml`` overrides them per bank (B4: bank-owned policy numbers live in
config, never in code). Nothing here imports a framework, a cloud SDK, or a port.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .models import (
    MATERIALITY_BAND_ORDER,
    Applicability,
    ChangeKind,
    CorpusChange,
    DocType,
    MaterialityBand,
    MaterialityDriver,
    OwnerAssignment,
)

#: Reference weights per change kind. A brand-new instrument and a republication carry the
#: most regulatory work; a pure version-label bump usually carries the least.
_DEFAULT_KIND_WEIGHTS: dict[str, int] = {
    ChangeKind.NEW_SOURCE.value: 40,
    ChangeKind.CONTENT_REVISED.value: 32,
    ChangeKind.WITHDRAWN.value: 24,
    ChangeKind.VERSION_BUMP.value: 12,
    ChangeKind.UNCHANGED.value: 0,
}

#: Reference weights per document type: a binding standard or notice outranks a consultation.
_DEFAULT_DOC_TYPE_WEIGHTS: dict[str, int] = {
    DocType.STANDARD.value: 22,
    DocType.NOTICE.value: 20,
    DocType.GUIDELINE.value: 14,
    DocType.CIRCULAR.value: 12,
    DocType.ADVISORY.value: 8,
    DocType.CONSULTATION.value: 6,
    DocType.OTHER.value: 4,
}

#: Reference band floors, evaluated strongest-first against the clamped score.
_DEFAULT_BAND_THRESHOLDS: dict[str, int] = {
    MaterialityBand.CRITICAL.value: 78,
    MaterialityBand.HIGH.value: 58,
    MaterialityBand.MEDIUM.value: 34,
    MaterialityBand.LOW.value: 0,
}

#: Reference remediation SLA (days) by band.
_DEFAULT_SLA_DAYS: dict[str, int] = {
    MaterialityBand.CRITICAL.value: 30,
    MaterialityBand.HIGH.value: 60,
    MaterialityBand.MEDIUM.value: 90,
    MaterialityBand.LOW.value: 180,
}

#: Reference topic -> accountable-owner routing table.
_DEFAULT_TOPIC_OWNERS: dict[str, str] = {
    "outsourcing": "third-party-risk-office",
    "third-party-risk": "third-party-risk-office",
    "cloud": "cloud-controls-office",
    "data-residency": "cloud-controls-office",
    "technology-risk": "ciso-office",
    "cyber-resilience": "ciso-office",
    "information-security": "ciso-office",
    "incident-response": "ciso-office",
    "operational-resilience": "operational-resilience-office",
    "business-continuity": "operational-resilience-office",
    "ai-governance": "model-risk-office",
    "model-risk": "model-risk-office",
    "it-governance": "compliance-office",
}

#: Reference regulator -> owner fallback when no topic rule matches.
_DEFAULT_REGULATOR_OWNERS: dict[str, str] = {}

#: Reference regulated footprint.
_DEFAULT_REGULATORS: tuple[str, ...] = ("MAS", "HKMA", "APRA", "FSA", "CROSS")
_DEFAULT_JURISDICTIONS: tuple[str, ...] = ("SG", "HK", "AU", "JP", "GLOBAL")
_DEFAULT_TOPICS: tuple[str, ...] = (
    "outsourcing",
    "third-party-risk",
    "cloud",
    "data-residency",
    "technology-risk",
    "cyber-resilience",
    "information-security",
    "incident-response",
    "operational-resilience",
    "business-continuity",
    "ai-governance",
    "model-risk",
    "it-governance",
)


@dataclass(frozen=True, slots=True)
class HorizonPolicyConfig:
    """Bank-owned horizon policy numbers (B4). Defaults equal the reference policy."""

    in_scope_regulators: tuple[str, ...] = _DEFAULT_REGULATORS
    in_scope_jurisdictions: tuple[str, ...] = _DEFAULT_JURISDICTIONS
    in_scope_topics: tuple[str, ...] = _DEFAULT_TOPICS
    change_kind_weights: dict[str, int] = field(default_factory=lambda: dict(_DEFAULT_KIND_WEIGHTS))
    doc_type_weights: dict[str, int] = field(
        default_factory=lambda: dict(_DEFAULT_DOC_TYPE_WEIGHTS)
    )
    topic_points: int = 8  # per in-scope topic the change touches
    max_topic_points: int = 24
    open_gap_points: int = 7  # per open control gap for the same regulator
    max_open_gap_points: int = 21
    conditional_penalty: int = 12  # subtracted when applicability is CONDITIONAL
    band_thresholds: dict[str, int] = field(default_factory=lambda: dict(_DEFAULT_BAND_THRESHOLDS))
    sla_days: dict[str, int] = field(default_factory=lambda: dict(_DEFAULT_SLA_DAYS))
    topic_owners: dict[str, str] = field(default_factory=lambda: dict(_DEFAULT_TOPIC_OWNERS))
    regulator_owners: dict[str, str] = field(
        default_factory=lambda: dict(_DEFAULT_REGULATOR_OWNERS)
    )
    default_owner: str = "compliance-office"
    #: Band at or above which a change is consequential enough to force human review.
    review_band: str = MaterialityBand.MEDIUM.value


@dataclass(frozen=True, slots=True)
class ApplicabilityVerdict:
    """The applicability decision plus the reasons that produced it."""

    applicability: Applicability
    reasons: tuple[str, ...]
    matched_topics: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MaterialityVerdict:
    """The materiality decision: a clamped integer score, its band, and its drivers."""

    score: int
    band: MaterialityBand
    drivers: tuple[MaterialityDriver, ...]


class HorizonPolicy:
    """Pure decision logic for horizon scanning. No I/O, no model, no side effects."""

    #: Materiality is reported on a 0..100 scale so bands are comparable across banks.
    MIN_SCORE = 0
    MAX_SCORE = 100

    def __init__(self, config: HorizonPolicyConfig | None = None) -> None:
        self.config = config or HorizonPolicyConfig()

    # ------------------------------------------------------------------ #
    # Applicability
    # ------------------------------------------------------------------ #
    def assess_applicability(self, change: CorpusChange) -> ApplicabilityVerdict:
        """Decide whether ``change`` applies to the configured regulated footprint."""
        cfg = self.config
        reasons: list[str] = []
        regulator = change.regulator.value
        jurisdiction = change.jurisdiction.value

        regulator_ok = regulator in cfg.in_scope_regulators
        jurisdiction_ok = jurisdiction in cfg.in_scope_jurisdictions
        matched = tuple(t for t in change.topics if t in cfg.in_scope_topics)

        if not regulator_ok:
            reasons.append(f"regulator {regulator} is outside the configured footprint")
        else:
            reasons.append(f"regulator {regulator} is in the configured footprint")
        if not jurisdiction_ok:
            reasons.append(f"jurisdiction {jurisdiction} is outside the configured footprint")
        else:
            reasons.append(f"jurisdiction {jurisdiction} is in the configured footprint")

        if not (regulator_ok and jurisdiction_ok):
            return ApplicabilityVerdict(Applicability.NOT_APPLICABLE, tuple(reasons), ())

        if matched:
            reasons.append("in-scope topics matched: " + ", ".join(matched))
            return ApplicabilityVerdict(Applicability.APPLICABLE, tuple(reasons), matched)

        reasons.append(
            "no in-scope topic matched; a reviewer must confirm whether the instrument "
            "reaches the bank's activities"
        )
        return ApplicabilityVerdict(Applicability.CONDITIONAL, tuple(reasons), ())

    # ------------------------------------------------------------------ #
    # Materiality (pure arithmetic; the LLM never produces this number)
    # ------------------------------------------------------------------ #
    def score_materiality(
        self,
        change: CorpusChange,
        verdict: ApplicabilityVerdict,
        open_control_gaps: int = 0,
    ) -> MaterialityVerdict:
        """Compute the materiality score and band from named, additive drivers.

        ``open_control_gaps`` is the count of currently-open control gaps for the same
        regulator, taken from the control-mapping journey: a change landing on an area the
        bank already fails is more material than the same change on a fully covered area.
        """
        cfg = self.config
        if verdict.applicability is Applicability.NOT_APPLICABLE:
            driver = MaterialityDriver(
                name="not_applicable",
                points=0,
                detail="outside the configured regulated footprint; scored zero by policy",
            )
            return MaterialityVerdict(0, MaterialityBand.LOW, (driver,))

        drivers: list[MaterialityDriver] = []

        kind_points = int(cfg.change_kind_weights.get(change.kind.value, 0))
        drivers.append(
            MaterialityDriver(
                name="change_kind",
                points=kind_points,
                detail=f"{change.kind.value} weighted {kind_points} by policy",
            )
        )

        doc_points = int(cfg.doc_type_weights.get(change.doc_type.value, 0))
        drivers.append(
            MaterialityDriver(
                name="doc_type",
                points=doc_points,
                detail=f"{change.doc_type.value} weighted {doc_points} by policy",
            )
        )

        topic_points = min(cfg.topic_points * len(verdict.matched_topics), cfg.max_topic_points)
        drivers.append(
            MaterialityDriver(
                name="topic_overlap",
                points=topic_points,
                detail=(
                    f"{len(verdict.matched_topics)} in-scope topic(s) "
                    f"[{', '.join(verdict.matched_topics) or 'none'}] at "
                    f"{cfg.topic_points} each, capped at {cfg.max_topic_points}"
                ),
            )
        )

        gaps = max(0, int(open_control_gaps))
        gap_points = min(cfg.open_gap_points * gaps, cfg.max_open_gap_points)
        drivers.append(
            MaterialityDriver(
                name="open_control_gaps",
                points=gap_points,
                detail=(
                    f"{gaps} open control gap(s) for {change.regulator.value} at "
                    f"{cfg.open_gap_points} each, capped at {cfg.max_open_gap_points}"
                ),
            )
        )

        if verdict.applicability is Applicability.CONDITIONAL:
            drivers.append(
                MaterialityDriver(
                    name="conditional_applicability",
                    points=-abs(cfg.conditional_penalty),
                    detail="applicability unconfirmed; policy discounts pending a human read",
                )
            )

        raw = sum(d.points for d in drivers)
        score = max(self.MIN_SCORE, min(self.MAX_SCORE, raw))
        return MaterialityVerdict(score, self.band_for(score), tuple(drivers))

    def band_for(self, score: int) -> MaterialityBand:
        """Band a clamped score against the config-owned floors, strongest first."""
        thresholds = self.config.band_thresholds
        for band in reversed(MATERIALITY_BAND_ORDER):
            floor = thresholds.get(band.value)
            if floor is not None and score >= int(floor):
                return band
        return MaterialityBand.LOW

    # ------------------------------------------------------------------ #
    # Ownership routing + review gate
    # ------------------------------------------------------------------ #
    def route_owner(
        self,
        change: CorpusChange,
        verdict: ApplicabilityVerdict,
        band: MaterialityBand,
    ) -> OwnerAssignment | None:
        """Assign the accountable owner. ``None`` when the change does not apply."""
        if verdict.applicability is Applicability.NOT_APPLICABLE:
            return None
        cfg = self.config
        due = int(cfg.sla_days.get(band.value, 0))

        for topic in change.topics:
            owner = cfg.topic_owners.get(topic)
            if owner:
                return OwnerAssignment(
                    owner=owner,
                    reason=f"topic rule '{topic}' routes to {owner}",
                    matched_on=topic,
                    due_within_days=due,
                )
        regulator_owner = cfg.regulator_owners.get(change.regulator.value)
        if regulator_owner:
            return OwnerAssignment(
                owner=regulator_owner,
                reason=(f"regulator rule '{change.regulator.value}' routes to {regulator_owner}"),
                matched_on=change.regulator.value,
                due_within_days=due,
            )
        return OwnerAssignment(
            owner=cfg.default_owner,
            reason=f"no topic or regulator rule matched; default owner {cfg.default_owner}",
            matched_on="",
            due_within_days=due,
        )

    def requires_review(
        self,
        verdict: ApplicabilityVerdict,
        band: MaterialityBand,
        owner: OwnerAssignment | None,
    ) -> bool:
        """Maker-checker gate (P-06, rule R8).

        Review is required when the change is routed to an owner at all (an ownership
        assignment is itself a consequential call a human must confirm), when its band
        reaches the configured review floor, or when applicability is CONDITIONAL and
        therefore explicitly unresolved. Only a NOT_APPLICABLE, unrouted change is closed
        without a reviewer.
        """
        if verdict.applicability is Applicability.NOT_APPLICABLE and owner is None:
            return False
        if owner is not None:
            return True
        return self.at_or_above_review_band(band)

    def at_or_above_review_band(self, band: MaterialityBand) -> bool:
        """Whether ``band`` reaches the configured review floor."""
        try:
            floor = MaterialityBand(self.config.review_band)
        except ValueError:  # pragma: no cover - defensive against a bad config value
            floor = MaterialityBand.MEDIUM
        return MATERIALITY_BAND_ORDER.index(band) >= MATERIALITY_BAND_ORDER.index(floor)
