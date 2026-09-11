# vpc_sc.tf: VPC Service Controls perimeter around the AI/data plane.
#
# General Principle map:
#   P-03 (residency + exfiltration control): a service perimeter draws a logical boundary
#         around the sovereignty-critical APIs, so data cannot be read across it to a project
#         outside the boundary.
#   P-01 (least surface): only the services this app uses are inside the perimeter, and the
#         AlloyDB API only when AlloyDB exists.
#
# Guarded by var.enable_vpc_sc (count = 0 when false). A deployment into a project whose
# perimeter another stack already owns declines it: a second REGULAR perimeter over the same
# project enforces where the established posture may only observe.
#
# DEPLOY-ORDER CAVEAT:
#   The perimeter blocks API calls from outside it. Enabling it BEFORE the resources in the
#   other files exist (or before the Terraform runner / CI identity is in an access level)
#   denies those calls and fails the apply. Apply with enable_vpc_sc = false first, add the
#   operator identity to an access level, then re-apply with enable_vpc_sc = true. Consider the
#   perimeter's dry-run mode before enforcing it.

locals {
  perimeter_restricted_services = concat(
    [
      "aiplatform.googleapis.com",
      "discoveryengine.googleapis.com",
      "dlp.googleapis.com",
      "modelarmor.googleapis.com",
      "logging.googleapis.com",
      "cloudtrace.googleapis.com",
      "firestore.googleapis.com",
      "cloudkms.googleapis.com",
      "secretmanager.googleapis.com",
      "storage.googleapis.com",
      # The control-posture plane, kept inside the boundary so posture data stays in it.
      "securitycenter.googleapis.com",
      "cloudasset.googleapis.com",
      "assuredworkloads.googleapis.com",
      "pubsub.googleapis.com",
    ],
    var.enable_alloydb ? ["alloydb.googleapis.com"] : [],
  )
}

resource "google_access_context_manager_service_perimeter" "compliance" {
  count = var.enable_vpc_sc ? 1 : 0

  parent = "accessPolicies/${var.access_policy_id}"
  name   = "accessPolicies/${var.access_policy_id}/servicePerimeters/compliance_sg"
  title  = "compliance_sg"

  perimeter_type = "PERIMETER_TYPE_REGULAR"

  status {
    # Confine the project's sovereignty-critical APIs to this perimeter.
    resources = ["projects/${data.google_project.this.number}"]

    restricted_services = local.perimeter_restricted_services

    # Allow VPC-internal use of every restricted API from inside the boundary.
    vpc_accessible_services {
      enable_restriction = true
      allowed_services   = local.perimeter_restricted_services
    }
  }

  depends_on = [google_project_service.required]
}
