"""Synthetic GCP controls, regulatory requirements + observations for deterministic tests.

Nothing here touches Google Cloud. The data mimics the shape of real GCP control
posture (VPC-SC, CMEK, Assured Workloads, ...) mapped to MAS / HKMA / APRA / FSA
obligations, with page-level citations as C2 requires, so unit tests can assert the
full mapping pipeline without any network or SDK. The text is invented; the source ids
/ titles are plausible but fictional and must not be treated as the real instruments.

The requirement ids deliberately equal the keys of the deterministic local mapper's
:data:`~compliance_advisory.adapters.local.control_mapping_seed.REQUIREMENT_CONTROL_MAP`,
and the control ids/states equal the local control-inventory seed, so the ported unit
suite drives the SAME offline mapping the CLI runs and can assert coverage per requirement.
"""

from __future__ import annotations

from datetime import UTC, datetime

from compliance_advisory.domain.control_mapping.models import (
    Citation,
    ControlFamily,
    ControlObservation,
    ControlState,
    GcpControl,
    Jurisdiction,
    RegRequirement,
    Regulator,
)

_OBSERVED_AT = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)


# --------------------------------------------------------------------------- #
# Known GCP controls (the ControlInventoryPort.list_controls catalog)
# --------------------------------------------------------------------------- #
SAMPLE_CONTROLS: tuple[GcpControl, ...] = (
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

CONTROLS_BY_ID: dict[str, GcpControl] = {c.id: c for c in SAMPLE_CONTROLS}


# --------------------------------------------------------------------------- #
# Regulatory requirements (the reg KB / RequirementSourcePort.fetch output)
# --------------------------------------------------------------------------- #
def _citation(
    source_id: str, regulator: Regulator, jurisdiction: Jurisdiction, page: int
) -> Citation:
    titles = {
        "mas-trm-guidelines": "MAS Technology Risk Management Guidelines",
        "apra-cps-234": "APRA CPS 234 Information Security",
        "hkma-cloud-circular": "HKMA Circular on Cloud Computing",
        "fsa-system-risk": "FSA Comprehensive Guidelines for System Risk Management",
    }
    return Citation(
        source_id=source_id,
        regulator=regulator,
        jurisdiction=jurisdiction,
        title=titles.get(source_id, source_id),
        url=f"https://example.test/{source_id}",
        version="2024",
        page=page,
        snippet="applicable supervisory obligation",
        score=0.9,
    )


SAMPLE_REQUIREMENTS: tuple[RegRequirement, ...] = (
    RegRequirement(
        id="mas-data-residency",
        regulator=Regulator.MAS,
        jurisdiction=Jurisdiction.SG,
        title="Data residency for material outsourcing",
        text=(
            "An institution should ensure regulated data remains within an approved "
            "jurisdiction and that residency is enforced for cloud-hosted workloads."
        ),
        citation=_citation("mas-trm-guidelines", Regulator.MAS, Jurisdiction.SG, page=42),
    ),
    RegRequirement(
        id="apra-encryption-at-rest",
        regulator=Regulator.APRA,
        jurisdiction=Jurisdiction.AU,
        title="Encryption of information assets",
        text=(
            "A regulated entity must protect information assets with encryption "
            "controls commensurate with their sensitivity, retaining control of keys."
        ),
        citation=_citation("apra-cps-234", Regulator.APRA, Jurisdiction.AU, page=15),
    ),
    RegRequirement(
        id="hkma-audit-trail",
        regulator=Regulator.HKMA,
        jurisdiction=Jurisdiction.HK,
        title="Immutable audit trail for cloud operations",
        text=(
            "Authorized institutions should maintain an immutable, retained audit "
            "trail of access to and operations on regulated cloud workloads."
        ),
        citation=_citation("hkma-cloud-circular", Regulator.HKMA, Jurisdiction.HK, page=7),
    ),
)

REQUIREMENTS_BY_ID: dict[str, RegRequirement] = {r.id: r for r in SAMPLE_REQUIREMENTS}
PRIMARY_REQUIREMENT: RegRequirement = SAMPLE_REQUIREMENTS[0]
PRIMARY_REQUIREMENT_ID: str = PRIMARY_REQUIREMENT.id


# --------------------------------------------------------------------------- #
# Observed control posture (ControlInventoryPort.observe output)
# --------------------------------------------------------------------------- #
# Residency controls ENABLED; the WORM audit log is DISABLED (drives a HKMA gap);
# CMEK is MISCONFIGURED (drives a PARTIAL/NONE coverage for the APRA requirement).
SAMPLE_OBSERVATIONS: tuple[ControlObservation, ...] = (
    ControlObservation(
        control_id="vpc-sc-perimeter",
        resource="projects/acme-sg-prod",
        state=ControlState.ENABLED,
        detail="perimeter compliance_sg active",
        source="security_command_center",
        observed_at=_OBSERVED_AT,
    ),
    ControlObservation(
        control_id="org-policy-resource-locations",
        resource="projects/acme-sg-prod",
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
        resource="projects/acme-sg-prod",
        state=ControlState.MISCONFIGURED,
        detail="key present but not bound to AlloyDB service agent",
        source="security_command_center",
        observed_at=_OBSERVED_AT,
    ),
    ControlObservation(
        control_id="worm-audit-logging",
        resource="projects/acme-sg-prod",
        state=ControlState.DISABLED,
        detail="no locked log bucket found",
        source="cloud_asset_inventory",
        observed_at=_OBSERVED_AT,
    ),
)


# --------------------------------------------------------------------------- #
# Scope used by the mapping / evidence / gap tests.
# --------------------------------------------------------------------------- #
SAMPLE_SCOPE: str = "projects/acme-sg-prod"

# Which controls the deterministic local mapper associates with each requirement
# (mirrors ``adapters.local.control_mapping_seed.REQUIREMENT_CONTROL_MAP``).
REQUIREMENT_CONTROL_MAP: dict[str, list[str]] = {
    "mas-data-residency": [
        "vpc-sc-perimeter",
        "org-policy-resource-locations",
        "assured-workloads-sg",
    ],
    "apra-encryption-at-rest": ["cmek-regional-key"],
    "hkma-audit-trail": ["worm-audit-logging"],
}
