"""Unit tests for the posture-snapshot pipeline (merged from C2; no Google Cloud SDK)."""

from __future__ import annotations

from tests.fixtures import sample_controls

from compliance_advisory.domain.control_mapping.models import ControlFamily
from compliance_advisory.pipelines.snapshot import snapshot_posture

SCOPE = sample_controls.SAMPLE_SCOPE


def test_snapshot_rolls_up_per_family(control_inventory):
    snap = snapshot_posture(control_inventory, SCOPE)

    assert snap.scope == SCOPE
    assert snap.observations  # all the fixture observations are captured
    by_family = {fs.family: fs for fs in snap.families}

    # VPC-SC observed once, ENABLED -> fully satisfied.
    assert by_family[ControlFamily.VPC_SC].fully_satisfied is True
    # CMEK observed once, MISCONFIGURED -> not satisfied.
    assert by_family[ControlFamily.CMEK].satisfied == 0
    # Logging observed once, DISABLED -> not satisfied.
    assert by_family[ControlFamily.LOGGING].fully_satisfied is False


def test_snapshot_empty_posture(empty_inventory):
    snap = snapshot_posture(empty_inventory, SCOPE)
    assert snap.families == ()
    assert snap.observations == ()
