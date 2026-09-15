# iam.tf: least-privilege service accounts for the serving API and the ingestion pipeline.
#
# General Principle map:
#   P-06 (least privilege / separation of duties): distinct identities for the app (serving),
#         the pipeline job (ingestion/freshness) and the agent runtime (agent_runtime.tf). Each
#         gets only the roles it needs; no shared "kitchen-sink" SA.
#   P-03 (residency): identities are project-scoped; data access is to in-region services.
#   P-09 (CMEK explicit): each SA that touches CMEK-encrypted data gets its own key binding.

# ------------------------------- App (serving) ------------------------------ #
resource "google_service_account" "app" {
  account_id   = "compliance-app"
  display_name = "Compliance Assistant app (serving / API)"
  project      = var.project_id

  depends_on = [google_project_service.required]
}

locals {
  # Serving path: read the corpus, call models + guardrail + DLP, read and write the ledger and
  # the horizon tracker, write audit + traces, read secrets. No write to the corpus.
  app_roles = concat(
    [
      "roles/aiplatform.user",
      "roles/discoveryengine.viewer", # query Agent Search only (read)
      "roles/dlp.user",               # deidentifyContent (P-04)
      # ...and READ the templates it is configured with. dlp.user grants the call and not
      # dlp.inspectTemplates.get, so an identity holding only dlp.user is refused the template
      # that says how to redact, at the first step of every request.
      "roles/dlp.reader",
      # Model Armor's screening call is a permission ON THE TEMPLATE
      # (modelarmor.templates.useToSanitizeUserPrompt), not a general API grant.
      "roles/modelarmor.user",
      "roles/datastore.user",    # Firestore ledger + horizon tracker (gcp profile)
      "roles/logging.logWriter", # write redacted audit events
      "roles/cloudtrace.agent",  # OpenTelemetry spans (content OFF)
      "roles/secretmanager.secretAccessor",
      "roles/run.invoker",
      # Control-mapping posture reads: the service observes control posture, it creates none.
      "roles/securitycenter.findingsViewer", # read SCC findings
      "roles/cloudasset.viewer",             # read realised config / org policy
      "roles/assuredworkloads.reader",       # read Assured Workloads status
      "roles/pubsub.subscriber",             # consume the asset-feed topic
    ],
    var.enable_alloydb ? ["roles/alloydb.client"] : [],
  )
}

resource "google_project_iam_member" "app" {
  for_each = toset(local.app_roles)
  project  = var.project_id
  role     = each.value
  member   = "serviceAccount:${google_service_account.app.email}"
}

# App uses the CMEK for envelope ops it performs directly.
resource "google_kms_crypto_key_iam_member" "app" {
  count         = var.cmek_enabled ? 1 : 0
  crypto_key_id = one(google_kms_crypto_key.compliance[*].id)
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = "serviceAccount:${google_service_account.app.email}"
}

# --------------------- Embedding host's runtime identity -------------------- #
# A portal that mounts this app runs the API under a service account of the PORTAL's making.
# That identity is the one the container actually authenticates as, so without these grants
# the deployed API starts, authenticates, and then fails on its first Firestore, DLP or Model
# Armor call, which reads as a broken application rather than a missing binding. Empty by
# default: an app deployed on its own needs none of this.
resource "google_project_iam_member" "additional_serving" {
  for_each = {
    for pair in setproduct(var.additional_serving_service_accounts, local.app_roles) :
    "${pair[0]}|${pair[1]}" => { email = pair[0], role = pair[1] }
  }
  project = var.project_id
  role    = each.value.role
  member  = "serviceAccount:${each.value.email}"
}

resource "google_kms_crypto_key_iam_member" "additional_serving" {
  for_each      = var.cmek_enabled ? toset(var.additional_serving_service_accounts) : toset([])
  crypto_key_id = one(google_kms_crypto_key.compliance[*].id)
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = "serviceAccount:${each.value}"
}

# ------------------------- Pipeline (ingestion job) ------------------------- #
resource "google_service_account" "pipeline" {
  account_id   = "compliance-pipeline"
  display_name = "Compliance corpus ingestion / freshness pipeline"
  project      = var.project_id

  depends_on = [google_project_service.required]
}

locals {
  # Ingestion path: WRITE to Agent Search, WRITE the freshness ledger, redact fetched documents
  # with the reviewed DLP templates, read secrets, write logs/traces.
  pipeline_roles = concat(
    [
      "roles/discoveryengine.editor", # import/ingest documents into the data store
      "roles/datastore.user",         # upsert the Firestore freshness ledger (gcp profile)
      "roles/dlp.user",               # redact fetched docs before indexing (P-04)
      "roles/dlp.reader",             # ...with the templates it is configured with
      "roles/secretmanager.secretAccessor",
      "roles/logging.logWriter",
      "roles/cloudtrace.agent",
    ],
    var.enable_alloydb ? ["roles/alloydb.client"] : [],
  )
}

resource "google_project_iam_member" "pipeline" {
  for_each = toset(local.pipeline_roles)
  project  = var.project_id
  role     = each.value
  member   = "serviceAccount:${google_service_account.pipeline.email}"
}

# Pipeline uses the CMEK (e.g. staging buckets, ledger writes).
resource "google_kms_crypto_key_iam_member" "pipeline" {
  count         = var.cmek_enabled ? 1 : 0
  crypto_key_id = one(google_kms_crypto_key.compliance[*].id)
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = "serviceAccount:${google_service_account.pipeline.email}"
}
