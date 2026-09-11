# assured_workloads.tf: the Assured Workloads sovereignty package. OFF by default.
#
# The control-mapping module OBSERVES an Assured Workloads status as one of its control
# families; this is the resource that observation can read. It is counted on
# var.enable_assured_workloads, which defaults to false, because it is the one resource here a
# `terraform destroy` cannot simply undo: the workload creates a folder under the organization,
# under its own compliance regime, that Google removes only once it is empty and on its own
# terms. A control like that is turned on deliberately, never inherited.
#
# General Principle map:
#   P-03 (data residency / sovereignty): Assured Workloads pins data location, support
#         personnel access and key management to the region.
#   P-09 (defence in depth): a sovereignty package layered on top of org policy + VPC-SC.

resource "google_assured_workloads_workload" "sg" {
  count    = var.enable_assured_workloads ? 1 : 0
  provider = google-beta

  organization      = var.org_id
  location          = var.region
  display_name      = "compliance-advisory-sg"
  compliance_regime = "REGIONS_CONTROL" # data + personnel in-region
  billing_account   = "billingAccounts/${var.billing_account}"

  kms_settings {
    next_rotation_time = "2027-01-01T00:00:00Z"
    rotation_period    = "7776000s" # 90 days
  }

  labels = {
    system = "compliance-advisory"
    module = "control-mapping"
  }

  lifecycle {
    precondition {
      condition     = var.billing_account != ""
      error_message = "enable_assured_workloads = true requires billing_account: the workload's folder is billed to it."
    }
  }

  depends_on = [google_project_service.required]
}
