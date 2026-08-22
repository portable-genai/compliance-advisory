"""Horizon-scanning ports: the source catalog and the implementation tracker.

Two edges the horizon module needs that the assistant did not:

* :class:`RegSourceCatalogPort` supplies the regulator-grade metadata (regulator,
  jurisdiction, doc type, topics) for each ledger row. The freshness ledger knows *when* a
  source moved; the catalog knows *what it is*, and detection needs both.
* :class:`HorizonTrackerPort` persists the implementation journey for an assessed change.

``HorizonTrackerPort.get`` is deliberately tenant-agnostic: the tenant check belongs in the
domain (:class:`~compliance_advisory.domain.horizon.ImplementationTrackingService`) so a
cross-tenant read is answered with an explicit 403 denial instead of a 404 that hides
whether the row exists. ``list`` IS tenant-scoped, because a listing must never emit a row
the caller may not see even momentarily.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..domain.horizon.models import ImplementationItem
from ..domain.models import RegSource


@runtime_checkable
class RegSourceCatalogPort(Protocol):
    def sources(self) -> list[RegSource]:
        """Every regulatory source the deployment tracks, from the source registry."""
        ...

    def get(self, source_id: str) -> RegSource | None:
        """One source by its stable slug, or ``None`` when it is not registered."""
        ...


@runtime_checkable
class HorizonTrackerPort(Protocol):
    def upsert(self, item: ImplementationItem) -> None:
        """Create or replace the tracked implementation item for a change."""
        ...

    def get(self, change_id: str) -> ImplementationItem | None:
        """Look up one tracked item by change id, WITHOUT filtering by tenant."""
        ...

    def list(self, tenant: str) -> list[ImplementationItem]:
        """Every tracked item owned by ``tenant`` (tenant-scoped by construction)."""
        ...
