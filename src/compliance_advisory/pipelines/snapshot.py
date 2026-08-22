"""Posture-snapshot pipeline — summarise the live control posture for a scope.

A small, cloud-agnostic helper that drives the ``ControlInventoryPort`` to read the live
posture and roll it up into a per-family summary (how many controls are ENABLED vs. not).
It is the C2 analogue of a freshness/ingest pipeline: an operational job an operator runs
(or a scheduler triggers) to record the posture over time, independent of a mapping
request.

Pure domain orchestration: it talks only to the port and the domain models, so it runs
under any profile (the GCP adapter reads SCC/Asset/Assured Workloads; the onprem stub
raises until implemented). No Google Cloud / framework imports here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..domain.control_mapping.models import (
    SATISFYING_STATES,
    ControlFamily,
    ControlObservation,
    utcnow,
)


@dataclass(frozen=True, slots=True)
class FamilySnapshot:
    """Rolled-up posture for one control family at a scope."""

    family: ControlFamily
    total: int
    satisfied: int  # observations in a satisfying (ENABLED) state

    @property
    def fully_satisfied(self) -> bool:
        return self.total > 0 and self.satisfied == self.total


@dataclass(frozen=True, slots=True)
class PostureSnapshot:
    """A point-in-time roll-up of the observed posture for a scope."""

    scope: str
    families: tuple[FamilySnapshot, ...] = ()
    observations: tuple[ControlObservation, ...] = ()
    taken_at: str = field(default_factory=lambda: utcnow().isoformat())


def _family_for(control_id: str, controls: list[Any]) -> ControlFamily:
    for ctrl in controls:
        if ctrl.id == control_id:
            return ctrl.family
    return ControlFamily.OTHER


def snapshot_posture(control_inventory: Any, scope: str) -> PostureSnapshot:
    """Read the live posture for ``scope`` and roll it up per control family.

    Args:
      control_inventory: an object satisfying ``ControlInventoryPort``.
      scope: the project / org / folder resource the posture is observed for.

    Returns:
      A :class:`PostureSnapshot` with one :class:`FamilySnapshot` per family seen.
    """
    observations = list(control_inventory.observe(scope) or [])
    controls = list(control_inventory.list_controls() or [])

    totals: dict[ControlFamily, int] = {}
    satisfied: dict[ControlFamily, int] = {}
    for obs in observations:
        family = _family_for(obs.control_id, controls)
        totals[family] = totals.get(family, 0) + 1
        if obs.state in SATISFYING_STATES:
            satisfied[family] = satisfied.get(family, 0) + 1

    families = tuple(
        FamilySnapshot(family=family, total=total, satisfied=satisfied.get(family, 0))
        for family, total in sorted(totals.items(), key=lambda kv: kv[0].value)
    )
    return PostureSnapshot(
        scope=scope,
        families=families,
        observations=tuple(observations),
    )
