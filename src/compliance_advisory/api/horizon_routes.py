"""FastAPI router for regulatory horizon scanning (SPEC §7.5).

Mounts the horizon capability on the assistant's existing FastAPI app:

* ``POST /horizon/scan``                     — detect, assess, route and track changes.
* ``GET  /horizon/items``                    — the caller's tenant's tracked journey.
* ``GET  /horizon/items/{change_id}``        — one tracked change (fail-closed).
* ``POST /horizon/items/{change_id}/status`` — advance a tracked change.

Design constraints mirror the host app:

* **Import-safe.** Building the Container is deferred to request time via ``deps``.
* **Empty inputs map to 422, not 500.** A :class:`CorpusLedgerEmptyError` (the corpus has
  never been ingested) becomes an HTTP 422 with a clear detail.
* **Authorization is fail-closed and server-verified.** Every route reads the tenant from
  the verified :class:`~compliance_advisory.domain.identity.Principal`; the request body
  carries neither an actor nor a tenant. A change owned by another tenant returns **403**,
  not a 404 that would hide whether it exists, and a genuinely unknown change returns 404.
* The horizon pipeline is wired WITHOUT the guardrail / DLP ports (see deps): it reasons
  over published regulatory instruments and the bank's own implementation state.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, status

from ..domain.horizon import (
    CorpusLedgerEmptyError,
    HorizonScanService,
    ImplementationItemNotFoundError,
    ImplementationStatus,
    ImplementationTrackingService,
    TenantMismatchError,
)
from . import deps
from .horizon_schemas import (
    HorizonScanRequest,
    HorizonScanResponse,
    ImplementationItemModel,
    ImplementationItemsResponse,
    StatusUpdateRequest,
)
from .security import CurrentPrincipal

router = APIRouter(prefix="/horizon", tags=["horizon"])

ChangeId = Annotated[str, Path(min_length=1, max_length=256)]


@router.post("/scan", response_model=HorizonScanResponse)
def scan(
    request: HorizonScanRequest,
    principal: CurrentPrincipal,
    service: Annotated[HorizonScanService, Depends(deps.get_horizon_scan_service)],
) -> HorizonScanResponse:
    """Detect every regulatory change in the corpus and assess, route and track it."""
    try:
        result = service.scan(
            request.scope,
            principal.actor,
            regulator=request.regulator,
            tenant=principal.tenant,
        )
    except CorpusLedgerEmptyError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    return HorizonScanResponse.from_domain(result)


@router.get("/items", response_model=ImplementationItemsResponse)
def list_items(
    principal: CurrentPrincipal,
    service: Annotated[ImplementationTrackingService, Depends(deps.get_horizon_tracking_service)],
    open_only: bool = False,
) -> ImplementationItemsResponse:
    """List the tracked implementation journey for the verified principal's tenant."""
    return ImplementationItemsResponse.from_domain(
        service.list_items(principal.tenant, open_only=open_only)
    )


@router.get("/items/{change_id}", response_model=ImplementationItemModel)
def get_item(
    change_id: ChangeId,
    principal: CurrentPrincipal,
    service: Annotated[ImplementationTrackingService, Depends(deps.get_horizon_tracking_service)],
) -> ImplementationItemModel:
    """One tracked change. 403 when it belongs to another tenant, 404 when unknown."""
    return ImplementationItemModel.from_domain(_get(service, change_id, principal.tenant))


@router.post("/items/{change_id}/status", response_model=ImplementationItemModel)
def update_status(
    change_id: ChangeId,
    request: StatusUpdateRequest,
    principal: CurrentPrincipal,
    service: Annotated[ImplementationTrackingService, Depends(deps.get_horizon_tracking_service)],
) -> ImplementationItemModel:
    """Advance a tracked change, gated on the verified tenant.

    Closing a change as ``implemented`` or ``accepted_risk`` is consequential: it is
    audited as an escalation and routed to Hrz7 for maker-checker sign-off (rule R8).
    """
    try:
        new_status = ImplementationStatus(request.status)
    except ValueError as exc:
        allowed = ", ".join(s.value for s in ImplementationStatus)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"unknown status {request.status!r}; expected one of: {allowed}",
        ) from exc
    try:
        item = service.update_status(
            change_id,
            new_status,
            principal.actor,
            principal.tenant,
            note=request.note,
            control_ids=tuple(request.control_ids),
        )
    except TenantMismatchError as exc:
        raise _forbidden(exc) from exc
    except ImplementationItemNotFoundError as exc:
        raise _not_found(exc) from exc
    return ImplementationItemModel.from_domain(item)


# --------------------------------------------------------------------------- #
# Internals
# --------------------------------------------------------------------------- #
def _get(service: ImplementationTrackingService, change_id: str, tenant: str):  # type: ignore[no-untyped-def]
    try:
        return service.get_item(change_id, tenant)
    except TenantMismatchError as exc:
        raise _forbidden(exc) from exc
    except ImplementationItemNotFoundError as exc:
        raise _not_found(exc) from exc


def _forbidden(exc: Exception) -> HTTPException:
    """A cross-tenant access is an explicit 403 denial, never a disguised 404."""
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))


def _not_found(exc: Exception) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
