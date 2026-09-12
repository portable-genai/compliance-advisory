# apis.tf: enable exactly the managed services this stack's resources and the app use.
#
# General Principle map:
#   P-01 (managed-first / minimal surface): only the services the pinned stack (SPEC §3)
#         actually uses are enabled. The AlloyDB set is enabled only with enable_alloydb.
#   P-03 (residency): enabling these APIs is a prerequisite for the regional,
#         CMEK-protected resources defined in the sibling files.
#
# disable_on_destroy = false so a `terraform destroy` of this stack, or turning a toggle off,
# does not yank a platform API out from under other workloads in a shared project.

locals {
  required_services = concat(
    [
      "aiplatform.googleapis.com",           # Gemini Enterprise Agent Platform / Agent Runtime
      "discoveryengine.googleapis.com",      # Agent Search (ex-Vertex AI Search) retrieval
      "dlp.googleapis.com",                  # Sensitive Data Protection / DLP (PII redaction)
      "modelarmor.googleapis.com",           # Model Armor guardrail
      "logging.googleapis.com",              # Cloud Logging (audit bucket + audit logs)
      "cloudtrace.googleapis.com",           # Cloud Trace (OpenTelemetry spans)
      "firestore.googleapis.com",            # Freshness ledger + horizon tracker (gcp profile)
      "cloudscheduler.googleapis.com",       # Corpus freshness refresh trigger
      "artifactregistry.googleapis.com",     # Promoted image registry (artifact_registry.tf)
      "run.googleapis.com",                  # Cloud Run refresh job
      "secretmanager.googleapis.com",        # App secrets (no secrets in code, P-04)
      "cloudkms.googleapis.com",             # Regional CMEK key ring (P-09)
      "accesscontextmanager.googleapis.com", # VPC Service Controls perimeter (P-03)
      "assuredworkloads.googleapis.com",     # Assured Workloads (read by the posture adapter)
      "securitycenter.googleapis.com",       # Security Command Center (live posture findings)
      "cloudasset.googleapis.com",           # Cloud Asset Inventory (realised config)
      "pubsub.googleapis.com",               # Topic the Cloud Asset feed publishes to
      "iam.googleapis.com",                  # Service accounts / least-privilege IAM
      "orgpolicy.googleapis.com",            # Org Policy residency constraints (P-03)
    ],
    var.enable_alloydb ? [
      "alloydb.googleapis.com",           # AlloyDB ledger + tracker (platform profile)
      "servicenetworking.googleapis.com", # Private Service Access for AlloyDB
      "compute.googleapis.com",           # The VPC and PSA range AlloyDB peers into
    ] : [],
  )
}

resource "google_project_service" "required" {
  for_each = toset(local.required_services)

  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}
