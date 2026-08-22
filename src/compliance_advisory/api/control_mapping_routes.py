"""FastAPI router for the control-mapping capability (merged from system C2 into C1).

Mounts the three cited control-mapping artifacts on the assistant's existing FastAPI app:

* ``POST /map``            — map each requirement for a scope to the GCP control(s).
* ``POST /evidence-pack``  — assemble the regulator-grade, always-human-reviewed pack.
* ``POST /gaps``           — the requirements whose controls are missing / misconfigured.

These routes DO NOT collide with the assistant's own surface (``/ask``, ``/checklist``,
``/testcases``, ``/regulator-questions``). The ``/evidence-pack`` shape is preserved
unchanged for its external consumer (Rsk3, the architecture validator).

Design constraints mirror the host app:

* **Import-safe.** Building the Container is deferred to request time via ``deps``.
* **Empty inputs map to 422, not 500.** A :class:`RequirementsEmptyError` or
  :class:`PostureUnavailableError` becomes an HTTP 422 with a clear detail.
* The audit actor is the server-verified principal, never a client-asserted value.
* The pipeline is wired WITHOUT the guardrail / DLP ports (posture split; see deps).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from ..domain.control_mapping import (
    ControlMappingService,
    EvidencePackService,
    GapAnalysisService,
    PostureUnavailableError,
    RequirementsEmptyError,
)
from . import deps
from .control_mapping_schemas import (
    EvidencePackResponse,
    GapsResponse,
    MappingsResponse,
    MapRequest,
    ScopeRequest,
)
from .security import CurrentPrincipal

router = APIRouter(tags=["control-mapping"])


def _unprocessable(exc: Exception) -> HTTPException:
    """Translate a domain input error into a 422 with a clear detail."""
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))


@router.post("/map", response_model=MappingsResponse)
def map_controls(
    request: MapRequest,
    principal: CurrentPrincipal,
    service: Annotated[ControlMappingService, Depends(deps.get_mapping_service)],
) -> MappingsResponse:
    """Map each requirement for a scope to the GCP control(s) that satisfy it."""
    try:
        mappings = service.map(request.scope, principal.actor, regulator=request.regulator)
    except (RequirementsEmptyError, PostureUnavailableError) as exc:
        raise _unprocessable(exc) from exc
    return MappingsResponse.from_domain(request.scope, mappings)


@router.post("/evidence-pack", response_model=EvidencePackResponse)
def evidence_pack(
    request: ScopeRequest,
    principal: CurrentPrincipal,
    service: Annotated[EvidencePackService, Depends(deps.get_evidence_service)],
) -> EvidencePackResponse:
    """Assemble the regulator-grade evidence pack for a scope (always human-reviewed)."""
    try:
        pack = service.build(
            request.scope,
            principal.actor,
            regulator=request.regulator,
            tenant=principal.tenant,
        )
    except (RequirementsEmptyError, PostureUnavailableError) as exc:
        raise _unprocessable(exc) from exc
    return EvidencePackResponse.from_domain(pack)


@router.post("/gaps", response_model=GapsResponse)
def gaps(
    request: ScopeRequest,
    principal: CurrentPrincipal,
    service: Annotated[GapAnalysisService, Depends(deps.get_gap_service)],
) -> GapsResponse:
    """Identify the requirements for a scope whose controls are missing or misconfigured."""
    try:
        result = service.analyze(request.scope, principal.actor, regulator=request.regulator)
    except (RequirementsEmptyError, PostureUnavailableError) as exc:
        raise _unprocessable(exc) from exc
    return GapsResponse.from_domain(request.scope, result)
