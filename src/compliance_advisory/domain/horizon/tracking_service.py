"""ImplementationTrackingService — track a regulatory change to closure (SPEC §7.4).

Horizon scanning is only useful if somebody closes the loop, so every assessed change
opens a tracked :class:`~.models.ImplementationItem` that a compliance officer moves through
``not_started -> in_progress -> implemented`` (or ``accepted_risk``), linking the GCP
controls from the control-mapping journey that evidence the closure.

Authorization is fail-closed and server-verified. Every read and every write is gated on
the VERIFIED principal's tenant, which the API threads in from the
:class:`~compliance_advisory.domain.identity.Principal` and never from the request body.
A cross-tenant read raises :class:`~.errors.TenantMismatchError` (an explicit 403), so the
denial is visible rather than disguised as a 404.

A closure that ends the bank's exposure without implementing the change (``accepted_risk``) or
asserts it is done (``implemented``) is consequential, so it routes to human-review-console (rule
R8).

Pure domain code — no Google Cloud / ADK / FastAPI imports.
"""

from __future__ import annotations

import contextlib
from contextlib import nullcontext
from dataclasses import replace
from typing import Any

from .errors import ImplementationItemNotFoundError, TenantMismatchError
from .models import (
    AuditEvent,
    Decision,
    ImplementationItem,
    ImplementationStatus,
    utcnow,
)

#: Status transitions a human cannot make unilaterally: both assert the bank's regulatory
#: position, so both go to the maker-checker console (rule R8).
_REVIEWABLE_STATUSES: frozenset[ImplementationStatus] = frozenset(
    {ImplementationStatus.IMPLEMENTED, ImplementationStatus.ACCEPTED_RISK}
)


class ImplementationTrackingService:
    """Read and advance the implementation state of assessed regulatory changes."""

    def __init__(
        self,
        tracker: Any,
        tracer: Any,
        audit: Any,
        review_router: Any = None,
    ) -> None:
        self._tracker = tracker
        self._tracer = tracer
        self._audit = audit
        self._review_router = review_router

    # ------------------------------------------------------------------ #
    # Reads (tenant-scoped, fail-closed)
    # ------------------------------------------------------------------ #
    def list_items(self, tenant: str, open_only: bool = False) -> list[ImplementationItem]:
        """Every tracked item owned by ``tenant``. Never crosses the tenant boundary."""
        items = list(self._tracker.list(tenant) or [])
        # Defense in depth: filter again in the domain so a lax adapter cannot leak a row.
        items = [item for item in items if item.tenant == tenant]
        if open_only:
            items = [item for item in items if item.is_open]
        return sorted(items, key=lambda i: i.change_id)

    def get_item(self, change_id: str, tenant: str) -> ImplementationItem:
        """One tracked item, or a fail-closed error.

        The port lookup is deliberately tenant-agnostic and the tenant check lives HERE, in
        the domain, so the denial is explicit: a genuinely missing item raises
        :class:`ImplementationItemNotFoundError` (404) while an item owned by another tenant
        raises :class:`TenantMismatchError` (403), never a disguised 404.
        """
        item = self._tracker.get(change_id)
        if item is None:
            raise ImplementationItemNotFoundError(f"no tracked change '{change_id}'")
        if item.tenant != tenant:
            raise TenantMismatchError(
                f"change '{change_id}' belongs to another tenant; access denied"
            )
        return item

    # ------------------------------------------------------------------ #
    # Write
    # ------------------------------------------------------------------ #
    def update_status(
        self,
        change_id: str,
        status: ImplementationStatus,
        actor: str,
        tenant: str,
        note: str = "",
        control_ids: tuple[str, ...] = (),
    ) -> ImplementationItem:
        """Advance one tracked change, gated on the verified tenant.

        ``control_ids`` links the closure to the GCP controls the control-mapping journey
        already evidences, so an auditor can walk from "the regulator changed this" to "here
        is the control that answers it" without leaving the repo.
        """
        span = self._tracer.span(
            "horizon.update_status", action="horizon_update_status", actor=actor
        )
        with span if span is not None else nullcontext():
            current = self.get_item(change_id, tenant)  # raises 403/404 before any write
            updated = replace(
                current,
                status=status,
                note=note or current.note,
                control_ids=tuple(control_ids) or current.control_ids,
                updated_by=actor,
                updated_at=utcnow(),
            )
            self._tracker.upsert(updated)
            escalated = status in _REVIEWABLE_STATUSES
            self._audit_update(actor, updated, escalated)
            if escalated:
                self._route(updated, actor, tenant)
            return updated

    # ------------------------------------------------------------------ #
    # Audit + R8 routing
    # ------------------------------------------------------------------ #
    def _audit_update(self, actor: str, item: ImplementationItem, escalated: bool) -> None:
        event = AuditEvent(
            action="horizon_update_status",
            actor=actor,
            decision=Decision.ESCALATED if escalated else Decision.ALLOWED,
            redacted_prompt=f"change_id={item.change_id}",
            redacted_response=f"status={item.status.value}",
            metadata={
                "tenant": item.tenant,
                "source_id": item.source_id,
                "materiality_band": item.materiality_band.value,
                "control_ids": ",".join(item.control_ids),
                "requires_human_review": str(escalated).lower(),
            },
        )
        with contextlib.suppress(Exception):
            self._audit.record(event)

    def _route(self, item: ImplementationItem, actor: str, tenant: str) -> None:
        if self._review_router is None:
            return
        with contextlib.suppress(Exception):
            self._review_router.route(item, maker=actor, tenant=tenant)
