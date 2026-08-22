"""Control-inventory adapter (ControlInventoryPort) — live GCP posture.

Backs the domain ``ControlInventoryPort`` with three managed services on the Gemini
Enterprise Agent Platform:

* **Security Command Center** — security findings and the enablement state of
  built-in detectors / posture controls (the primary signal for ENABLED / DISABLED
  / MISCONFIGURED states).
* **Cloud Asset Inventory** — the realised configuration of org-policy constraints
  and resources (e.g. whether ``gcp.resourceLocations`` is set to Singapore).
* **Assured Workloads** — the sovereignty control package state for the scope's
  folder/workload (residency, personnel access, key management).

``list_controls`` returns the toolkit's catalog of GCP technical controls; ``observe``
reads each control's live state for a scope and returns ``ControlObservation`` rows the
domain coverage logic reasons over.

All Google Cloud SDK imports are lazy so the on-prem / test profile imports this module
without ``google-cloud-securitycenter`` / ``-asset`` / ``-assuredworkloads`` installed.
"""

from __future__ import annotations

from typing import Any

from ...config import Settings
from ...domain.control_mapping.models import (
    ControlFamily,
    ControlObservation,
    ControlState,
    GcpControl,
    utcnow,
)

# The toolkit's catalog of GCP technical controls. These are the cloud-side controls
# the toolkit reasons over; the config_ref names the org-policy constraint or the
# Terraform/asset resource type the control corresponds to.
_CONTROL_CATALOG: tuple[GcpControl, ...] = (
    GcpControl(
        id="vpc-sc-perimeter",
        name="VPC Service Controls perimeter",
        family=ControlFamily.VPC_SC,
        description="Service perimeter confining the data-plane APIs to in-country projects.",
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
        description="Sovereignty control package pinning data and personnel to the region.",
        config_ref="google_assured_workloads_workload",
    ),
    GcpControl(
        id="access-transparency",
        name="Access Transparency logging",
        family=ControlFamily.ACCESS_TRANSPARENCY,
        description="Near-real-time logs of provider administrative access to tenant data.",
        config_ref="accesstransparency.googleapis.com",
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
        description="Cloud Logging locked bucket with multi-year retention for audit.",
        config_ref="google_logging_project_bucket_config",
    ),
)

# SCC finding category -> the control it speaks to + the state a finding implies.
_STATE_BY_SCC_SEVERITY: dict[str, ControlState] = {
    "CRITICAL": ControlState.MISCONFIGURED,
    "HIGH": ControlState.MISCONFIGURED,
    "MEDIUM": ControlState.PARTIAL,
    "LOW": ControlState.PARTIAL,
}


class SccControlInventoryAdapter:
    """Observe live GCP control posture across SCC, Asset Inventory and Assured Workloads."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._posture = settings.posture
        self._scc: Any | None = None
        self._asset: Any | None = None
        self._assured: Any | None = None

    # ------------------------------------------------------------------ #
    # ControlInventoryPort
    # ------------------------------------------------------------------ #
    def list_controls(self) -> list[GcpControl]:
        """Return the catalog of GCP technical controls the toolkit knows about."""
        return list(_CONTROL_CATALOG)

    def observe(self, scope: str) -> list[ControlObservation]:
        """Read the live control posture for ``scope`` from the three sources."""
        observations: list[ControlObservation] = []
        observations.extend(self._observe_scc(scope))
        observations.extend(self._observe_assets(scope))
        observations.extend(self._observe_assured_workloads(scope))
        return observations

    # ------------------------------------------------------------------ #
    # Security Command Center
    # ------------------------------------------------------------------ #
    def _scc_client(self) -> Any:
        if self._scc is None:
            from google.cloud import securitycenter_v2  # lazy

            # verify: https://cloud.google.com/security-command-center/docs/reference/rest
            self._scc = securitycenter_v2.SecurityCenterClient()
        return self._scc

    def _observe_scc(self, scope: str) -> list[ControlObservation]:
        client = self._scc_client()
        parent = self._posture.scc_parent or scope
        observations: list[ControlObservation] = []
        # ACTIVE findings indicate a control gap; absence implies the control holds.
        request = {"parent": f"{parent}/sources/-", "filter": 'state="ACTIVE"'}
        for finding_result in client.list_findings(request=request):
            finding = getattr(finding_result, "finding", finding_result)
            control_id = self._control_for_category(getattr(finding, "category", ""))
            if control_id is None:
                continue
            severity = str(getattr(finding, "severity", "MEDIUM"))
            observations.append(
                ControlObservation(
                    control_id=control_id,
                    resource=str(getattr(finding, "resource_name", scope)),
                    state=_STATE_BY_SCC_SEVERITY.get(severity, ControlState.MISCONFIGURED),
                    detail=str(getattr(finding, "category", "")),
                    source="security_command_center",
                    observed_at=utcnow(),
                )
            )
        return observations

    @staticmethod
    def _control_for_category(category: str) -> str | None:
        """Map an SCC finding category to a known control id (best-effort)."""
        cat = (category or "").lower()
        if "encryption" in cat or "cmek" in cat or "kms" in cat:
            return "cmek-regional-key"
        if "logging" in cat or "audit" in cat:
            return "worm-audit-logging"
        if "perimeter" in cat or "vpc_sc" in cat or "vpc-sc" in cat:
            return "vpc-sc-perimeter"
        if "location" in cat or "residency" in cat:
            return "org-policy-resource-locations"
        return None

    # ------------------------------------------------------------------ #
    # Cloud Asset Inventory
    # ------------------------------------------------------------------ #
    def _asset_client(self) -> Any:
        if self._asset is None:
            from google.cloud import asset_v1  # lazy

            # verify: https://cloud.google.com/asset-inventory/docs/reference/rest
            self._asset = asset_v1.AssetServiceClient()
        return self._asset

    def _observe_assets(self, scope: str) -> list[ControlObservation]:
        client = self._asset_client()
        parent = self._posture.asset_scope or scope
        observations: list[ControlObservation] = []
        request = {
            "scope": parent,
            "asset_types": ["orgpolicy.googleapis.com/Policy"],
            "query": "name:resourceLocations",
        }
        for resource in client.search_all_resources(request=request):
            name = str(getattr(resource, "name", ""))
            state = (
                ControlState.ENABLED if "asia-southeast1" in name.lower() else ControlState.PARTIAL
            )
            observations.append(
                ControlObservation(
                    control_id="org-policy-resource-locations",
                    resource=name or scope,
                    state=state,
                    detail="resourceLocations policy",
                    source="cloud_asset_inventory",
                    observed_at=utcnow(),
                )
            )
        return observations

    # ------------------------------------------------------------------ #
    # Assured Workloads
    # ------------------------------------------------------------------ #
    def _assured_client(self) -> Any:
        if self._assured is None:
            from google.cloud import assuredworkloads_v1  # lazy

            # verify: https://cloud.google.com/assured-workloads/docs/reference/rest
            self._assured = assuredworkloads_v1.AssuredWorkloadsServiceClient()
        return self._assured

    def _observe_assured_workloads(self, scope: str) -> list[ControlObservation]:
        workload = self._posture.assured_workload
        if not workload:
            return []
        client = self._assured_client()
        result = client.get_workload(name=workload)
        compliant = bool(getattr(result, "compliance_status", None))
        return [
            ControlObservation(
                control_id="assured-workloads-sg",
                resource=workload,
                state=ControlState.ENABLED if compliant else ControlState.PARTIAL,
                detail=str(getattr(result, "compliance_regime", "")),
                source="assured_workloads",
                observed_at=utcnow(),
            )
        ]
