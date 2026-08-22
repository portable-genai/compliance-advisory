"""ControlInventoryPort — the live GCP control posture + the known control catalog.

``observe`` reads the live posture for a scope; ``list_controls`` returns the catalog
of GCP controls the control-mapping capability reasons over. Primary GCP adapter:
Security Command Center + Cloud Asset Inventory + Assured Workloads (all SDK imports
lazy). There is no remote-platform variant: the posture is read directly from the
bank's own GCP organisation. On-prem migration swaps in a placeholder adapter with no
change to callers.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..domain.control_mapping.models import ControlObservation, GcpControl


@runtime_checkable
class ControlInventoryPort(Protocol):
    def observe(self, scope: str) -> list[ControlObservation]:
        """Read the live control posture for ``scope`` (project / org / folder)."""
        ...

    def list_controls(self) -> list[GcpControl]:
        """Return the catalog of GCP technical controls the toolkit knows about."""
        ...
