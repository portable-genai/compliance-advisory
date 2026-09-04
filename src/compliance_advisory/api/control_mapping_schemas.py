"""Pydantic v2 request/response models for the control-mapping API routes.

These schemas mirror the frozen domain dataclasses in
:mod:`compliance_advisory.domain.control_mapping.models` one-for-one, so the HTTP boundary is a
thin, typed projection of the domain. The ``/map``, ``/evidence-pack`` and ``/gaps`` shapes are
preserved UNCHANGED from the standalone C2 toolkit: an external consumer (architecture-validator,
the architecture validator) POSTs ``/evidence-pack`` and depends on this shape.

Nothing here imports Google Cloud, ADK, or any adapter: the API layer depends only on the
domain models, the ports, and the orchestration services — never on a concrete adapter.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from ..domain.control_mapping import models as m
from ..domain.serialization import to_jsonable

# --------------------------------------------------------------------------- #
# Shared projections
# --------------------------------------------------------------------------- #


class CitationModel(BaseModel):
    """Regulator-grade provenance attached to a requirement (mirror of Citation)."""

    source_id: str
    regulator: str
    jurisdiction: str
    title: str
    url: str
    version: str = "unknown"
    page: int | None = None
    snippet: str = ""
    score: float | None = None

    @classmethod
    def from_domain(cls, citation: m.Citation) -> CitationModel:
        return cls(**to_jsonable(citation))


class GcpControlModel(BaseModel):
    """A GCP technical control (mirror of GcpControl)."""

    id: str
    name: str
    family: str
    description: str
    config_ref: str = ""

    @classmethod
    def from_domain(cls, control: m.GcpControl) -> GcpControlModel:
        return cls(**to_jsonable(control))


class RegRequirementModel(BaseModel):
    """A regulatory obligation (mirror of RegRequirement)."""

    id: str
    regulator: str
    jurisdiction: str
    title: str
    text: str
    citation: CitationModel

    @classmethod
    def from_domain(cls, requirement: m.RegRequirement) -> RegRequirementModel:
        return cls(**to_jsonable(requirement))


class ControlObservationModel(BaseModel):
    """An observed control state (mirror of ControlObservation)."""

    control_id: str
    resource: str
    state: str
    detail: str = ""
    source: str = "security_command_center"
    observed_at: str

    @classmethod
    def from_domain(cls, obs: m.ControlObservation) -> ControlObservationModel:
        return cls(**to_jsonable(obs))


# --------------------------------------------------------------------------- #
# Requests
# --------------------------------------------------------------------------- #


class MapRequest(BaseModel):
    """Inbound control-mapping request.

    Note: there is deliberately no ``actor`` field. The audit actor is the server-verified
    :class:`~compliance_advisory.domain.identity.Principal` resolved by the IdentityPort,
    never a client-supplied value (which would be spoofable). See ``api/security.py``.
    """

    scope: str = Field(..., min_length=1, description="Regulator or use-case / resource scope.")
    regulator: str | None = Field(default=None, description="Optional single-regulator filter.")


class ScopeRequest(BaseModel):
    """Inbound request for the evidence-pack and gap generators.

    Shared by /evidence-pack and /gaps: each is scoped to a regulator or use case. Like
    :class:`MapRequest`, it carries no ``actor``: the audit actor comes from the verified
    Principal, not from the request body.
    """

    scope: str = Field(..., min_length=1, description="Regulator or use-case / resource scope.")
    regulator: str | None = Field(default=None, description="Optional single-regulator filter.")


# --------------------------------------------------------------------------- #
# Artifact responses
# --------------------------------------------------------------------------- #


class ControlMappingModel(BaseModel):
    """One requirement mapped to controls with a coverage verdict (mirror)."""

    requirement: RegRequirementModel
    controls: list[GcpControlModel] = Field(default_factory=list)
    observations: list[ControlObservationModel] = Field(default_factory=list)
    coverage: str
    rationale: str
    citations: list[CitationModel] = Field(default_factory=list)
    requires_human_review: bool = False

    @classmethod
    def from_domain(cls, mapping: m.ControlMapping) -> ControlMappingModel:
        return cls(
            requirement=RegRequirementModel.from_domain(mapping.requirement),
            controls=[GcpControlModel.from_domain(c) for c in mapping.controls],
            observations=[ControlObservationModel.from_domain(o) for o in mapping.observations],
            coverage=mapping.coverage.value,
            rationale=mapping.rationale,
            citations=[CitationModel.from_domain(c) for c in mapping.citations],
            requires_human_review=mapping.requires_human_review,
        )


class MappingsResponse(BaseModel):
    """A scope's control mappings."""

    scope: str
    mappings: list[ControlMappingModel] = Field(default_factory=list)

    @classmethod
    def from_domain(cls, scope: str, mappings: list[m.ControlMapping]) -> MappingsResponse:
        return cls(scope=scope, mappings=[ControlMappingModel.from_domain(mp) for mp in mappings])


class ControlGapModel(BaseModel):
    """A requirement whose controls are missing or misconfigured (mirror of ControlGap)."""

    requirement: RegRequirementModel
    missing_controls: list[str] = Field(default_factory=list)
    severity: str
    remediation: str
    citations: list[CitationModel] = Field(default_factory=list)

    @classmethod
    def from_domain(cls, gap: m.ControlGap) -> ControlGapModel:
        return cls(
            requirement=RegRequirementModel.from_domain(gap.requirement),
            missing_controls=[f.value for f in gap.missing_controls],
            severity=gap.severity.value,
            remediation=gap.remediation,
            citations=[CitationModel.from_domain(c) for c in gap.citations],
        )


class GapsResponse(BaseModel):
    """A scope's control gaps."""

    scope: str
    gaps: list[ControlGapModel] = Field(default_factory=list)

    @classmethod
    def from_domain(cls, scope: str, gaps: list[m.ControlGap]) -> GapsResponse:
        return cls(scope=scope, gaps=[ControlGapModel.from_domain(g) for g in gaps])


class EvidencePackResponse(BaseModel):
    """The regulator-grade evidence pack (mirror of EvidencePack)."""

    scope: str
    mappings: list[ControlMappingModel] = Field(default_factory=list)
    gaps: list[ControlGapModel] = Field(default_factory=list)
    coverage_summary: dict[str, int] = Field(default_factory=dict)
    generated_at: str
    requires_human_review: bool = True

    @classmethod
    def from_domain(cls, pack: m.EvidencePack) -> EvidencePackResponse:
        return cls(
            scope=pack.scope,
            mappings=[ControlMappingModel.from_domain(mp) for mp in pack.mappings],
            gaps=[ControlGapModel.from_domain(g) for g in pack.gaps],
            coverage_summary=dict(pack.coverage_summary),
            generated_at=to_jsonable(pack.generated_at),
            requires_human_review=pack.requires_human_review,
        )
