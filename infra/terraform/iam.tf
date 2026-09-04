# iam.tf — Least-privilege service accounts for the three C1 workloads.
#
# General Principle map:
#   P-06 (least privilege / separation of duties): three distinct identities —
#         the app (serving), the pipeline job (ingestion/freshness), and the agent
#         runtime (defined in agent_runtime.tf). Each gets only the roles it needs;
#         no shared "kitchen-sink" SA. This also supports maker-checker separation.
#   P-03 (residency): identities are project-scoped; data access is to in-region
#         services only.
#   P-09 (CMEK explicit): each SA that touches CMEK-encrypted data gets its own
#         cryptoKey use binding.
#
# (The Agent Runtime SA + its roles live in agent_runtime.tf; the scheduler SA +
#  Cloud Run job SA usage live in scheduler.tf. This file covers the app + pipeline.)

# ------------------------------- App (serving) ------------------------------ #
resource "google_service_account" "app" {
  account_id   = "compliance-app"
  display_name = "C1 Compliance Assistant app (serving / API)"
  project      = var.project_id

  depends_on = [google_project_service.required]
}

locals {
  # Serving path: read corpus, call models + guardrail + DLP, write audit + traces,
  # read the freshness ledger, read secrets. No write to the corpus (that's the pipeline).
  app_roles = [
    "roles/aiplatform.user",
    "roles/discoveryengine.viewer", # query Agent Search only (read)
    "roles/dlp.user",               # deidentifyContent (P-04)
    "roles/logging.logWriter",      # write redacted audit events to WORM sink
    "roles/cloudtrace.agent",       # OpenTelemetry spans (content OFF)
    "roles/alloydb.client",         # read freshness ledger (PRIVATE)
    "roles/secretmanager.secretAccessor",
    "roles/run.invoker",
    # Control-mapping posture reads (merged from the cloud control-mapping toolkit) — folded onto this single
    # serving SA; the merged service observes control posture, it does not create it.
    "roles/securitycenter.findingsViewer", # read SCC findings (posture)
    "roles/cloudasset.viewer",             # read realised config / org policy
    "roles/assuredworkloads.reader",       # read Assured Workloads status
    "roles/pubsub.subscriber",             # consume the asset-feed topic
  ]
}

resource "google_project_iam_member" "app" {
  for_each = toset(local.app_roles)
  project  = var.project_id
  role     = each.value
  member   = "serviceAccount:${google_service_account.app.email}"
}

# App uses the CMEK for envelope ops it performs directly.
resource "google_kms_crypto_key_iam_member" "app" {
  crypto_key_id = google_kms_crypto_key.compliance.id
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = "serviceAccount:${google_service_account.app.email}"
}

# ------------------------- Pipeline (ingestion job) ------------------------- #
resource "google_service_account" "pipeline" {
  account_id   = "compliance-pipeline"
  display_name = "C1 corpus ingestion / freshness pipeline"
  project      = var.project_id

  depends_on = [google_project_service.required]
}

locals {
  # Ingestion path: WRITE to Agent Search (import/ingest docs), WRITE the freshness
  # ledger, read secrets, write logs/traces. Editor on Discovery Engine to import.
  pipeline_roles = [
    "roles/discoveryengine.editor", # import/ingest documents into the data store
    "roles/alloydb.client",         # upsert the freshness ledger (PRIVATE)
    "roles/secretmanager.secretAccessor",
    "roles/logging.logWriter",
    "roles/cloudtrace.agent",
    "roles/dlp.user", # redact fetched docs before indexing (P-04)
  ]
}

resource "google_project_iam_member" "pipeline" {
  for_each = toset(local.pipeline_roles)
  project  = var.project_id
  role     = each.value
  member   = "serviceAccount:${google_service_account.pipeline.email}"
}

# Pipeline uses the CMEK (e.g. staging buckets, ledger writes).
resource "google_kms_crypto_key_iam_member" "pipeline" {
  crypto_key_id = google_kms_crypto_key.compliance.id
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = "serviceAccount:${google_service_account.pipeline.email}"
}
