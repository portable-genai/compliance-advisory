"""Domain models for the control-mapping capability (merged from system C2).

This is the heart of the control-mapping hexagon. It has **no dependency on Google
Cloud, ADK, FastAPI, or any framework** — only the Python standard library. Every
adapter speaks in terms of these types, which is what lets the managed-service stack
be swapped for an on-premise one without touching domain logic (P-02).

The regulatory taxonomy (:class:`Regulator`, :class:`Jurisdiction`,
:data:`REGULATOR_JURISDICTION`), the citation/generation/audit/eval/governance types,
and :class:`Severity` are the ONE canonical copy owned by the parent
:mod:`compliance_advisory.domain.models` — they are re-exported here so control-mapping
code keeps a single ``from .models import ...`` surface while the assistant and the
control-mapping capability share one taxonomy. Only the control-mapping-specific types
(GCP controls, observed posture, coverage, mappings, gaps, evidence packs) are defined
below.

The control-mapping pipeline handles the bank's own cloud control posture, not
customer/PII data, so there is deliberately no guardrail or PII-redaction model here
(R1 / P-04 = N/A; see COMPLIANCE.md).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from hex_service_kit import StrEnum

from ..models import (
    REGULATOR_JURISDICTION as REGULATOR_JURISDICTION,
)

# Canonical shared taxonomy + envelope types — one home in the parent domain models.
from ..models import (
    AgentCard,
    AgentSkill,
    AuditEvent,
    Citation,
    Decision,
    EvalMetricResult,
    EvalReport,
    Jurisdiction,
    LlmMessage,
    LlmRequest,
    LlmResponse,
    Regulator,
    Severity,
    ThinkingLevel,
    TokenUsage,
    ToolSpec,
    utcnow,
)

__all__ = [
    # Re-exported canonical types (single source of truth in ..models).
    "AgentCard",
    "AgentSkill",
    "AuditEvent",
    "Citation",
    "Decision",
    "EvalMetricResult",
    "EvalReport",
    "Jurisdiction",
    "LlmMessage",
    "LlmRequest",
    "LlmResponse",
    "REGULATOR_JURISDICTION",
    "Regulator",
    "Severity",
    "ThinkingLevel",
    "TokenUsage",
    "ToolSpec",
    "utcnow",
    # Control-mapping-specific types defined here.
    "ControlFamily",
    "GcpControl",
    "RegRequirement",
    "ControlState",
    "SATISFYING_STATES",
    "ControlObservation",
    "Coverage",
    "ControlMapping",
    "ControlGap",
    "EvidencePack",
]


# --------------------------------------------------------------------------- #
# GCP controls & regulatory requirements
# --------------------------------------------------------------------------- #
class ControlFamily(StrEnum):
    """The families of GCP technical control the toolkit reasons over."""

    VPC_SC = "vpc_sc"  # VPC Service Controls perimeter
    CMEK = "cmek"  # Customer-managed encryption keys
    ASSURED_WORKLOADS = "assured_workloads"  # Sovereignty control package
    ACCESS_TRANSPARENCY = "access_transparency"  # Provider-access logging
    SOVEREIGN_CONTROLS = "sovereign_controls"  # Sovereign / data-boundary controls
    ORG_POLICY = "org_policy"  # Organization Policy constraints
    IAM = "iam"  # Identity & access management
    VPC = "vpc"  # Networking / private connectivity
    LOGGING = "logging"  # Audit logging (WORM, retention)
    ENCRYPTION = "encryption"  # Encryption in transit / at rest
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class GcpControl:
    """A GCP technical control the toolkit can map a requirement to."""

    id: str  # stable slug, e.g. "vpc-sc-perimeter"
    name: str
    family: ControlFamily
    description: str
    # The org-policy constraint or service config the control maps to,
    # e.g. "gcp.resourceLocations" or "google_kms_crypto_key".
    config_ref: str = ""


@dataclass(frozen=True, slots=True)
class RegRequirement:
    """One regulatory obligation, sourced from the reg KB (compliance-advisory retrieval)."""

    id: str  # stable slug, e.g. "mas-trm-data-residency"
    regulator: Regulator
    jurisdiction: Jurisdiction
    title: str
    text: str
    citation: Citation


# --------------------------------------------------------------------------- #
# Observed control posture (live GCP state)
# --------------------------------------------------------------------------- #
class ControlState(StrEnum):
    """Observed state of a control on a GCP resource/scope."""

    ENABLED = "enabled"
    PARTIAL = "partial"
    DISABLED = "disabled"
    MISCONFIGURED = "misconfigured"
    UNKNOWN = "unknown"


#: Control states that count as the control being effectively in place.
SATISFYING_STATES: frozenset[ControlState] = frozenset({ControlState.ENABLED})


@dataclass(frozen=True, slots=True)
class ControlObservation:
    """A single observation of a control's state, read from the live posture."""

    control_id: str
    resource: str  # the GCP resource / scope observed (project, org, folder, ...)
    state: ControlState
    detail: str = ""
    # Source of the observation, e.g. "security_command_center" |
    # "cloud_asset_inventory" | "assured_workloads".
    source: str = "security_command_center"
    observed_at: datetime = field(default_factory=utcnow)


# --------------------------------------------------------------------------- #
# Coverage & the three cited artifacts
# --------------------------------------------------------------------------- #
class Coverage(StrEnum):
    """How fully the observed controls satisfy a requirement."""

    FULL = "full"
    PARTIAL = "partial"
    NONE = "none"


@dataclass(frozen=True, slots=True)
class ControlMapping:
    """One regulatory requirement mapped to the GCP control(s) that satisfy it.

    Carries a ``Coverage`` verdict (FULL | PARTIAL | NONE), a rationale, the
    regulator citation(s) for the obligation, and the supporting control
    observation(s) read from the live posture.
    """

    requirement: RegRequirement
    controls: tuple[GcpControl, ...]
    observations: tuple[ControlObservation, ...]
    coverage: Coverage
    rationale: str
    citations: tuple[Citation, ...] = ()
    requires_human_review: bool = False


@dataclass(frozen=True, slots=True)
class ControlGap:
    """A requirement whose controls are missing or misconfigured.

    Each gap carries a severity, remediation guidance, and the requirement's
    citations so an auditor can trace the gap back to the obligation.
    """

    requirement: RegRequirement
    missing_controls: tuple[ControlFamily, ...]
    severity: Severity
    remediation: str
    citations: tuple[Citation, ...] = ()


@dataclass(frozen=True, slots=True)
class EvidencePack:
    """The auditor/regulator deliverable: a per-scope coverage + gap bundle.

    Always ``requires_human_review=True`` — an evidence pack is consequential and a
    qualified human (maker-checker, P-06) signs it off before it reaches a regulator.
    """

    scope: str  # regulator or use-case the pack is scoped to
    mappings: tuple[ControlMapping, ...]
    gaps: tuple[ControlGap, ...]
    coverage_summary: dict[str, int]  # counts keyed by Coverage value
    generated_at: datetime = field(default_factory=utcnow)
    requires_human_review: bool = True
