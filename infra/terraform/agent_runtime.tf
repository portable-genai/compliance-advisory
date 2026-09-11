# agent_runtime.tf: identity + CMEK for the Agent Runtime (reasoningEngine) deploy.
#
# General Principle map:
#   P-01 (managed runtime only): hosting is Agent Runtime (ex-Agent Engine), the single
#         managed runtime in SPEC §2. We do NOT self-host the agent.
#   P-02 (ports/adapters): the engine is deployed by the SDK/CLI
#         (google-cloud-aiplatform[agent_engines,adk]); Terraform only provisions the
#         identity and CMEK so the rest of the infra is in place before the SDK deploy runs.
#   P-09 (CMEK explicit): the aiplatform service agent's key binding lives in kms.tf.
#
# WHY NO google_*_reasoning_engine RESOURCE: as of the pinned provider line there is no
# first-class Terraform resource for an Agent Runtime / reasoningEngine instance; it is created
# out-of-band by the ADK deploy step. We provision its runtime service account instead.
# verify: https://cloud.google.com/vertex-ai/generative-ai/docs/agent-engine/deploy

# Runtime service account the deployed reasoningEngine runs as.
resource "google_service_account" "agent_runtime" {
  account_id   = "compliance-agent-runtime"
  display_name = "Compliance Agent Runtime (reasoningEngine) identity"
  project      = var.project_id

  depends_on = [google_project_service.required]
}

# Least-privilege runtime bindings: call Vertex/Agent Platform, read Agent Search, call Model
# Armor + DLP, read and write the ledger and tracker, write audit logs + traces.
locals {
  agent_runtime_roles = concat(
    [
      "roles/aiplatform.user",        # invoke Gemini models / Agent Runtime
      "roles/discoveryengine.viewer", # query Agent Search at serving time
      "roles/dlp.user",               # deidentifyContent (PII redaction, P-04)
      "roles/dlp.reader",             # read the configured DLP templates
      "roles/modelarmor.user",        # sanitize against the guardrail template
      "roles/datastore.user",         # Firestore ledger + horizon tracker (gcp profile)
      "roles/logging.logWriter",      # write to the audit log
      "roles/cloudtrace.agent",       # export OpenTelemetry spans
    ],
    var.enable_alloydb ? ["roles/alloydb.client"] : [],
  )
}

resource "google_project_iam_member" "agent_runtime" {
  for_each = toset(local.agent_runtime_roles)

  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.agent_runtime.email}"
}

# Let the runtime SA use the CMEK directly for any envelope encryption it performs.
resource "google_kms_crypto_key_iam_member" "agent_runtime" {
  crypto_key_id = google_kms_crypto_key.compliance.id
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = "serviceAccount:${google_service_account.agent_runtime.email}"
}

# Deploy the engine after apply, and record the resource id it prints into
# COMPLIANCE_AGENT_ENGINE (settings.yaml agent_engine.resource_name):
#
#   python -m compliance_advisory.adapters.gcp.agent_runtime deploy \
#     --project=<project> --region=<region> \
#     --service-account=<agent_runtime_service_account output> --kms-key=<kms_key output>
