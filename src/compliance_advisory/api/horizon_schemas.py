"""Pydantic v2 request/response models for the horizon-scanning API routes.

These schemas mirror the frozen domain dataclasses in
:mod:`compliance_advisory.domain.horizon.models` one-for-one, so the HTTP boundary is a
thin, typed projection of the domain: enums serialise as their ``.value`` strings via
:func:`~compliance_advisory.domain.serialization.to_jsonable`.

Two deliberate shapes:

* the materiality **drivers** are on the wire, not just the score, so a caller can show a
  reviewer the arithmetic behind a band without a second request, and
* no request body carries an ``actor`` or a ``tenant``: both come from the server-verified
  :class:`~compliance_advisory.domain.identity.Principal`.

Nothing here imports Google Cloud, ADK, or any adapter.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from ..domain.horizon import models as m
from ..domain.serialization import to_jsonable


# --------------------------------------------------------------------------- #
# Requests
# --------------------------------------------------------------------------- #
class HorizonScanRequest(BaseModel):
    """Scan the regulatory horizon for a scope (a GCP scope or a use-case label)."""

    scope: str = Field(min_length=1, max_length=200)
    regulator: str | None = Field(default=None, max_length=16)


class StatusUpdateRequest(BaseModel):
    """Advance a tracked change. The tenant comes from the verified principal, not here."""

    status: str = Field(min_length=1, max_length=32)
    note: str = Field(default="", max_length=2000)
    control_ids: list[str] = Field(default_factory=list, max_length=50)


# --------------------------------------------------------------------------- #
# Projections
# --------------------------------------------------------------------------- #
class CitationModel(BaseModel):
    """Regulator-grade provenance for an assessment (mirror of Citation)."""

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


class CorpusChangeModel(BaseModel):
    """A detected corpus movement (mirror of CorpusChange)."""

    id: str
    source_id: str
    kind: str
    regulator: str
    jurisdiction: str
    title: str
    url: str
    doc_type: str
    topics: list[str] = Field(default_factory=list)
    previous_version: str = ""
    current_version: str = ""
    previous_checksum: str = ""
    current_checksum: str = ""
    detected_at: str
    detail: str = ""

    @classmethod
    def from_domain(cls, change: m.CorpusChange) -> CorpusChangeModel:
        raw = to_jsonable(change)
        raw.pop("citation", None)
        return cls(**raw)


class MaterialityDriverModel(BaseModel):
    """One additive contribution to the materiality score (mirror of MaterialityDriver)."""

    name: str
    points: int
    detail: str = ""

    @classmethod
    def from_domain(cls, driver: m.MaterialityDriver) -> MaterialityDriverModel:
        return cls(**to_jsonable(driver))


class OwnerAssignmentModel(BaseModel):
    """Deterministic ownership routing (mirror of OwnerAssignment)."""

    owner: str
    reason: str
    matched_on: str = ""
    due_within_days: int = 0

    @classmethod
    def from_domain(cls, owner: m.OwnerAssignment) -> OwnerAssignmentModel:
        return cls(**to_jsonable(owner))


class HorizonAssessmentModel(BaseModel):
    """A deterministically assessed, routed regulatory change (mirror of HorizonAssessment)."""

    id: str
    change: CorpusChangeModel
    applicability: str
    applicability_reasons: list[str] = Field(default_factory=list)
    materiality_score: int
    materiality_band: str
    drivers: list[MaterialityDriverModel] = Field(default_factory=list)
    owner: OwnerAssignmentModel | None = None
    citations: list[CitationModel] = Field(default_factory=list)
    narrative: str = ""
    requires_human_review: bool = True

    @classmethod
    def from_domain(cls, assessment: m.HorizonAssessment) -> HorizonAssessmentModel:
        return cls(
            id=assessment.id,
            change=CorpusChangeModel.from_domain(assessment.change),
            applicability=assessment.applicability.value,
            applicability_reasons=list(assessment.applicability_reasons),
            materiality_score=assessment.materiality_score,
            materiality_band=assessment.materiality_band.value,
            drivers=[MaterialityDriverModel.from_domain(d) for d in assessment.drivers],
            owner=(
                OwnerAssignmentModel.from_domain(assessment.owner)
                if assessment.owner is not None
                else None
            ),
            citations=[CitationModel.from_domain(c) for c in assessment.citations],
            narrative=assessment.narrative,
            requires_human_review=assessment.requires_human_review,
        )


class HorizonScanResponse(BaseModel):
    """The scan deliverable: assessed changes plus a band summary (mirror of HorizonScan)."""

    scope: str
    assessments: list[HorizonAssessmentModel] = Field(default_factory=list)
    band_summary: dict[str, int] = Field(default_factory=dict)
    generated_at: str
    requires_human_review: bool = True

    @classmethod
    def from_domain(cls, scan: m.HorizonScan) -> HorizonScanResponse:
        return cls(
            scope=scan.scope,
            assessments=[HorizonAssessmentModel.from_domain(a) for a in scan.assessments],
            band_summary=scan.band_summary,
            generated_at=to_jsonable(scan.generated_at),
            requires_human_review=scan.requires_human_review,
        )


class ImplementationItemModel(BaseModel):
    """The tracked implementation state of one change (mirror of ImplementationItem)."""

    change_id: str
    tenant: str
    source_id: str
    status: str
    owner: str = ""
    materiality_band: str
    due_within_days: int = 0
    control_ids: list[str] = Field(default_factory=list)
    note: str = ""
    updated_by: str = ""
    updated_at: str

    @classmethod
    def from_domain(cls, item: m.ImplementationItem) -> ImplementationItemModel:
        return cls(**to_jsonable(item))


class ImplementationItemsResponse(BaseModel):
    """A tenant's tracked implementation journey."""

    items: list[ImplementationItemModel] = Field(default_factory=list)

    @classmethod
    def from_domain(cls, items: list[m.ImplementationItem]) -> ImplementationItemsResponse:
        return cls(items=[ImplementationItemModel.from_domain(i) for i in items])
