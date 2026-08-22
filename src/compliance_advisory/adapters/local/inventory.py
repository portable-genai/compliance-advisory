"""Local control-inventory adapter (ControlInventoryPort) — canned GCP posture.

The ``local`` profile's SDK-free stand-in for **Security Command Center + Cloud Asset
Inventory + Assured Workloads**: a deterministic, seedable in-process control catalog
and observed posture. ``observe`` returns the built-in synthetic posture (residency
controls ENABLED, the WORM audit log DISABLED, CMEK MISCONFIGURED), and ``list_controls``
returns the known GCP control catalog. There is no Google emulator for the posture
sources, so this path is unconditional.

It is seedable (``seed_observations`` / ``seed_controls``) so the test suite stays
deterministic and the offline CLI run grounds the mapping out of the box.
"""

from __future__ import annotations

from ...config import Settings
from ...domain.control_mapping.models import ControlObservation, GcpControl
from .control_mapping_seed import SEED_CONTROLS, SEED_OBSERVATIONS


class LocalControlInventoryAdapter:
    """In-process control inventory: canned posture + catalog, seedable and deterministic."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._observations: list[ControlObservation] = list(SEED_OBSERVATIONS)
        self._controls: list[GcpControl] = list(SEED_CONTROLS)

    # ------------------------------------------------------------------ #
    # Seeding
    # ------------------------------------------------------------------ #
    def seed_observations(
        self, observations: tuple[ControlObservation, ...] | list[ControlObservation]
    ) -> int:
        """Replace the observed posture with ``observations`` (deterministic test/CLI seed)."""
        self._observations = list(observations)
        return len(self._observations)

    def seed_controls(self, controls: tuple[GcpControl, ...] | list[GcpControl]) -> int:
        """Replace the known control catalog with ``controls``."""
        self._controls = list(controls)
        return len(self._controls)

    # ------------------------------------------------------------------ #
    # ControlInventoryPort
    # ------------------------------------------------------------------ #
    def observe(self, scope: str) -> list[ControlObservation]:
        """Return the live control posture for ``scope`` (the canned synthetic posture)."""
        return list(self._observations)

    def list_controls(self) -> list[GcpControl]:
        """Return the catalog of GCP technical controls the toolkit knows about."""
        return list(self._controls)
