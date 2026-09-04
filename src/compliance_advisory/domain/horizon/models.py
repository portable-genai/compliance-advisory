"""Domain models for regulatory horizon scanning (SPEC §7).

Horizon scanning is the fourth capability of compliance-advisory, alongside the assistant, the
checklist family and the control-mapping module. It answers a different question from
"what does the regulation say": it answers **what just changed, does it apply to us, how
much does it matter, who owns it, and is it implemented yet**.

Everything here is pure stdlib. The regulatory taxonomy (:class:`Regulator`,
:class:`Jurisdiction`), :class:`Citation`, :class:`Severity` and the audit envelope are
the ONE canonical copy owned by :mod:`compliance_advisory.domain.models` and are
re-exported so horizon code keeps a single ``from .models import ...`` surface.

Two invariants this module exists to encode:

* the **materiality score and band are pure code** (:mod:`.policy`); the LLM narrates the
  rationale and never produces the number, and
* every assessment carries the :class:`Citation` of the corpus item that drove it, so a
  reviewer can trace a materiality call back to the published instrument.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from hex_service_kit import StrEnum

from ..models import (
    AuditEvent,
    Citation,
    Decision,
    DocType,
    Jurisdiction,
    RegSource,
    Regulator,
    Severity,
    utcnow,
)

__all__ = [
    # Re-exported canonical types (single source of truth in ..models).
    "AuditEvent",
    "Citation",
    "Decision",
    "DocType",
    "Jurisdiction",
    "RegSource",
    "Regulator",
    "Severity",
    "utcnow",
    # Horizon-specific types defined here.
    "ChangeKind",
    "CorpusChange",
    "Applicability",
    "MaterialityBand",
    "MATERIALITY_BAND_ORDER",
    "MaterialityDriver",
    "OwnerAssignment",
    "HorizonAssessment",
    "HorizonScan",
    "ImplementationStatus",
    "OPEN_IMPLEMENTATION_STATES",
    "ImplementationItem",
]


# --------------------------------------------------------------------------- #
# Detected corpus change
# --------------------------------------------------------------------------- #
class ChangeKind(StrEnum):
    """What kind of corpus movement the freshness ledger diff revealed."""

    NEW_SOURCE = "new_source"  # first successful ingest of a source
    CONTENT_REVISED = "content_revised"  # checksum moved: the instrument was republished
    VERSION_BUMP = "version_bump"  # publisher version label moved, content identical
    WITHDRAWN = "withdrawn"  # the source stopped resolving (FAILED / MISSING)
    UNCHANGED = "unchanged"  # re-fetched, byte-identical: not a horizon event


@dataclass(frozen=True, slots=True)
class CorpusChange:
    """One detected movement in the regulatory corpus, with its provenance.

    ``id`` is a stable, content-derived key (``<source_id>:<kind>:<checksum-prefix>``) so a
    re-scan of the same ledger state produces the same change ids and implementation
    tracking is idempotent rather than duplicating rows on every scan.
    """

    id: str
    source_id: str
    kind: ChangeKind
    regulator: Regulator
    jurisdiction: Jurisdiction
    title: str
    url: str
    doc_type: DocType = DocType.OTHER
    topics: tuple[str, ...] = ()
    previous_version: str = ""
    current_version: str = ""
    previous_checksum: str = ""
    current_checksum: str = ""
    detected_at: datetime = field(default_factory=utcnow)
    citation: Citation | None = None
    detail: str = ""


# --------------------------------------------------------------------------- #
# Deterministic assessment
# --------------------------------------------------------------------------- #
class Applicability(StrEnum):
    """Whether a detected change applies to the bank's regulated footprint."""

    APPLICABLE = "applicable"  # in-scope regulator + jurisdiction + at least one topic
    CONDITIONAL = "conditional"  # in-scope perimeter, no in-scope topic: needs a human read
    NOT_APPLICABLE = "not_applicable"  # outside the configured regulated footprint


class MaterialityBand(StrEnum):
    """The banded materiality verdict, derived from the pure score."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


#: Weakest -> strongest. Comparisons use the index, never the string.
MATERIALITY_BAND_ORDER: tuple[MaterialityBand, ...] = (
    MaterialityBand.LOW,
    MaterialityBand.MEDIUM,
    MaterialityBand.HIGH,
    MaterialityBand.CRITICAL,
)


@dataclass(frozen=True, slots=True)
class MaterialityDriver:
    """One additive contribution to the materiality score, named and evidenced.

    The drivers are the audit trail for the number: their ``points`` sum (clamped) IS the
    score, so a reviewer can reconstruct the arithmetic without rerunning the service.
    """

    name: str  # e.g. "change_kind", "doc_type", "topic_overlap", "open_control_gaps"
    points: int
    detail: str = ""


@dataclass(frozen=True, slots=True)
class OwnerAssignment:
    """Deterministic ownership routing for an applicable change."""

    owner: str  # the accountable team/queue, from config
    reason: str  # which rule matched (topic, regulator, or the configured default)
    matched_on: str = ""  # the topic / regulator key that selected the owner
    due_within_days: int = 0  # SLA from config, by materiality band


@dataclass(frozen=True, slots=True)
class HorizonAssessment:
    """A detected change, deterministically assessed and routed.

    ``materiality_score`` and ``materiality_band`` come from :mod:`.policy` (pure code over
    config-owned thresholds). ``narrative`` is the ONLY field the LLM writes, and it is
    advisory prose: dropping it changes nothing about the decision.
    """

    change: CorpusChange
    applicability: Applicability
    applicability_reasons: tuple[str, ...]
    materiality_score: int
    materiality_band: MaterialityBand
    drivers: tuple[MaterialityDriver, ...]
    owner: OwnerAssignment | None = None
    citations: tuple[Citation, ...] = ()
    narrative: str = ""
    requires_human_review: bool = True

    @property
    def id(self) -> str:
        """The assessment key is the change key (one live assessment per change)."""
        return self.change.id


@dataclass(frozen=True, slots=True)
class HorizonScan:
    """The horizon-scanning deliverable for one scope: assessed, routed changes.

    Always ``requires_human_review=True``: a scan proposes an applicability and materiality
    view of the regulatory horizon, and a qualified human disposes (P-06, rule R8).
    """

    scope: str
    assessments: tuple[HorizonAssessment, ...] = ()
    generated_at: datetime = field(default_factory=utcnow)
    requires_human_review: bool = True

    @property
    def band_summary(self) -> dict[str, int]:
        """Counts keyed by materiality band value, zero-filled."""
        counts = {band.value: 0 for band in MaterialityBand}
        for assessment in self.assessments:
            counts[assessment.materiality_band.value] += 1
        return counts


# --------------------------------------------------------------------------- #
# Implementation tracking (the hand-off into the compliance / control-mapping journey)
# --------------------------------------------------------------------------- #
class ImplementationStatus(StrEnum):
    """Where an assessed change sits in the bank's implementation journey."""

    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    IMPLEMENTED = "implemented"
    ACCEPTED_RISK = "accepted_risk"
    NOT_APPLICABLE = "not_applicable"


#: Statuses that still represent outstanding regulatory work.
OPEN_IMPLEMENTATION_STATES: frozenset[ImplementationStatus] = frozenset(
    {ImplementationStatus.NOT_STARTED, ImplementationStatus.IN_PROGRESS}
)


@dataclass(frozen=True, slots=True)
class ImplementationItem:
    """The tracked state of one assessed change, partitioned by tenant.

    ``tenant`` is the verified principal's tenant, never a client-supplied value: it is the
    object-level authorization key, so a read or a status update from another tenant is
    denied (403) rather than silently served.

    ``control_ids`` is the link into the existing control-mapping journey: the GCP controls
    (and therefore the evidence pack rows) a reviewer says close this change.
    """

    change_id: str
    tenant: str
    source_id: str
    status: ImplementationStatus = ImplementationStatus.NOT_STARTED
    owner: str = ""
    materiality_band: MaterialityBand = MaterialityBand.LOW
    due_within_days: int = 0
    control_ids: tuple[str, ...] = ()
    note: str = ""
    updated_by: str = ""
    updated_at: datetime = field(default_factory=utcnow)

    @property
    def is_open(self) -> bool:
        return self.status in OPEN_IMPLEMENTATION_STATES
