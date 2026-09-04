# assured_workloads.tf — Sovereignty control package for the Singapore workload.
#
# Merged in from the the cloud control-mapping toolkit control-mapping module: the compliance assistant now also
# *observes* its own Assured Workloads compliance status as one of its control
# families (assured-workloads-sg). This is the resource that observation reads.
#
# General Principle map:
#   P-03 (data residency / sovereignty): Assured Workloads pins data location, support
#         personnel access and key management to the region.
#   P-09 (defence in depth): a sovereignty package layered on top of org policy + VPC-SC.
#
# Provisioned on the beta provider; the workload lives under the org's compliance folder.

resource "google_assured_workloads_workload" "sg" {
  provider = google-beta

  organization      = var.org_id
  location          = var.region # asia-southeast1 (Singapore) — pinned (P-03)
  display_name      = "compliance-advisory-sg"
  compliance_regime = "REGIONS_CONTROL" # regions-control package (data + personnel in-region)
  billing_account   = var.billing_account != "" ? "billingAccounts/${var.billing_account}" : null

  # Use the regional CMEK for resources created inside the workload (P-09).
  kms_settings {
    next_rotation_time = "2027-01-01T00:00:00Z"
    rotation_period    = "7776000s" # 90 days
  }

  labels = {
    system = "compliance-advisory"
    group  = "rsk"
    module = "control-mapping"
  }

  depends_on = [google_project_service.required]
}
