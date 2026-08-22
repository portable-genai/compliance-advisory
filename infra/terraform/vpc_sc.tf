# vpc_sc.tf — VPC Service Controls perimeter around the AI/data plane.
#
# General Principle map:
#   P-03 (residency + exfiltration control): a service perimeter draws a logical
#         boundary around the sovereignty-critical APIs (Vertex/Agent Platform,
#         Agent Search, DLP, Logging, AlloyDB, KMS, Secret Manager, Storage). Data
#         cannot be read across the boundary to a non-Singapore project, which is
#         what stops the regulatory corpus and audit log from leaving the country.
#   P-01 (least surface): only the services C1 uses are inside the perimeter.
#
# Guarded by var.enable_vpc_sc so non-prod/dev applies can skip it (count = 0).
#
# DEPLOY-ORDER CAVEAT:
#   The perimeter blocks API calls from outside it. If you enable this BEFORE the
#   resources in the other files are created (or before your Terraform runner /
#   CI identity is added to the perimeter's access levels), those API calls will
#   be denied and the apply will fail. Recommended order:
#     1. Apply everything with enable_vpc_sc = false.
#     2. Add your operator/CI identity to an access level.
#     3. Re-apply with enable_vpc_sc = true to enforce the boundary.
#   Tightening a live perimeter can also break the SDK-based Agent Runtime deploy
#   if that runner is not inside the perimeter. # verify: VPC-SC dry-run mode.

locals {
  perimeter_restricted_services = [
    "aiplatform.googleapis.com",
    "discoveryengine.googleapis.com",
    "dlp.googleapis.com",
    "modelarmor.googleapis.com",
    "logging.googleapis.com",
    "cloudtrace.googleapis.com",
    "alloydb.googleapis.com",
    "cloudkms.googleapis.com",
    "secretmanager.googleapis.com",
    "storage.googleapis.com",
    # Control-posture plane merged from the Rsk2 control-mapping module — kept inside
    # the sovereignty boundary so posture data cannot leave the country (P-03).
    "securitycenter.googleapis.com",
    "cloudasset.googleapis.com",
    "assuredworkloads.googleapis.com",
    "pubsub.googleapis.com",
  ]
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
