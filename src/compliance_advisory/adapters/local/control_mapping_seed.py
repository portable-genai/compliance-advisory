"""Built-in synthetic control posture for the ``local`` profile (control-mapping capability).

The known GCP control catalog and a live control posture, so the local control-inventory
adapter can run the whole control-mapping pipeline out of the box and the end-to-end demo
returns a real cited evidence pack with no external posture source. The text is invented;
the source ids / titles are plausible but fictional and must not be treated as the real
instruments.

The regulatory obligations themselves are NOT seeded here: the merged assistant has ONE
regulatory knowledge base, so control-mapping requirements come from Rsk1's shared local
retrieval corpus (:mod:`compliance_advisory.adapters.local._seed`) via the in-process
:class:`~compliance_advisory.adapters.requirements.RetrievalRequirementSourceAdapter`.
The default local scope is :data:`SEED_SCOPE`; the posture is keyed to it.
"""

from __future__ import annotations

from datetime import UTC, datetime

from ...domain.control_mapping.models import (
    ControlFamily,
    ControlObservation,
    ControlState,
    GcpControl,
)

_OBSERVED_AT = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)

#: The default scope the built-in posture is observed for (an obviously-fictional project).
SEED_SCOPE = "projects/acme-sg-prod"


# --------------------------------------------------------------------------- #
# Known GCP controls (ControlInventoryPort.list_controls catalog)
# --------------------------------------------------------------------------- #
SEED_CONTROLS: tuple[GcpControl, ...] = (
    GcpControl(
        id="vpc-sc-perimeter",
        name="VPC Service Controls perimeter",
        family=ControlFamily.VPC_SC,
        description="Service perimeter confining data-plane APIs to in-country projects.",
        config_ref="google_access_context_manager_service_perimeter",
    ),
    GcpControl(
        id="cmek-regional-key",
        name="Regional customer-managed encryption key",
        family=ControlFamily.CMEK,
        description="Regional CMEK encrypting data at rest in asia-southeast1.",
        config_ref="google_kms_crypto_key",
    ),
    GcpControl(
        id="assured-workloads-sg",
        name="Assured Workloads (Singapore regions control package)",
        family=ControlFamily.ASSURED_WORKLOADS,
        description="Sovereignty control package pinning data and personnel to region.",
        config_ref="google_assured_workloads_workload",
    ),
    GcpControl(
        id="org-policy-resource-locations",
        name="Resource-location org policy",
        family=ControlFamily.ORG_POLICY,
        description="gcp.resourceLocations constraint allowing only asia-southeast1.",
        config_ref="gcp.resourceLocations",
    ),
    GcpControl(
        id="worm-audit-logging",
        name="Locked WORM audit log bucket",
        family=ControlFamily.LOGGING,
        description="Cloud Logging locked bucket with ~7-year retention for audit.",
        config_ref="google_logging_project_bucket_config",
    ),
)


# --------------------------------------------------------------------------- #
# Observed control posture (ControlInventoryPort.observe output)
# --------------------------------------------------------------------------- #
# Residency controls ENABLED; the WORM audit log is DISABLED (drives an HKMA gap);
# CMEK is MISCONFIGURED (drives NONE coverage for the APRA requirement).
SEED_OBSERVATIONS: tuple[ControlObservation, ...] = (
    ControlObservation(
        control_id="vpc-sc-perimeter",
        resource=SEED_SCOPE,
        state=ControlState.ENABLED,
        detail="perimeter compliance_sg active",
        source="security_command_center",
        observed_at=_OBSERVED_AT,
    ),
    ControlObservation(
        control_id="org-policy-resource-locations",
        resource=SEED_SCOPE,
        state=ControlState.ENABLED,
        detail="in:asia-southeast1-locations",
        source="cloud_asset_inventory",
        observed_at=_OBSERVED_AT,
    ),
    ControlObservation(
        control_id="assured-workloads-sg",
        resource="folders/12345",
        state=ControlState.ENABLED,
        detail="Singapore regions package",
        source="assured_workloads",
        observed_at=_OBSERVED_AT,
    ),
    ControlObservation(
        control_id="cmek-regional-key",
        resource=SEED_SCOPE,
        state=ControlState.MISCONFIGURED,
        detail="key present but not bound to AlloyDB service agent",
        source="security_command_center",
        observed_at=_OBSERVED_AT,
    ),
    ControlObservation(
        control_id="worm-audit-logging",
        resource=SEED_SCOPE,
        state=ControlState.DISABLED,
        detail="no locked log bucket found",
        source="cloud_asset_inventory",
        observed_at=_OBSERVED_AT,
    ),
)


# --------------------------------------------------------------------------- #
# Requirement -> control mapping (candidate control set per requirement)
# --------------------------------------------------------------------------- #
# Which controls a deterministic local mapping run associates with each requirement.
# The service recomputes Coverage from the observations above, so this only supplies
# the candidate control set. Kept for the offline/deterministic mapping wiring.
REQUIREMENT_CONTROL_MAP: dict[str, list[str]] = {
    "mas-data-residency": [
        "vpc-sc-perimeter",
        "org-policy-resource-locations",
        "assured-workloads-sg",
    ],
    "apra-encryption-at-rest": ["cmek-regional-key"],
    "hkma-audit-trail": ["worm-audit-logging"],
}
