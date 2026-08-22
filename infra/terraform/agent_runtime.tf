# agent_runtime.tf — Identity + CMEK for the Agent Runtime (reasoningEngine) deploy.
#
# General Principle map:
#   P-01 (managed runtime only): hosting is Agent Runtime (ex-Agent Engine), the
#         single managed runtime in SPEC §2. We do NOT self-host the agent.
#   P-02 (ports/adapters): the engine binary is deployed by the SDK/CLI
#         (google-cloud-aiplatform[agent_engines,adk]); Terraform only provisions
#         the *identity*, CMEK, and a data reference so the rest of the infra
#         (IAM, perimeter, KMS) is in place before the SDK deploy runs.
#   P-09 (CMEK explicit): the aiplatform service agent's key binding lives in kms.tf.
#
# WHY NO google_*_reasoning_engine RESOURCE: as of the pinned provider line there is
# no first-class Terraform resource for an Agent Runtime / reasoningEngine instance;
# it is created out-of-band by the ADK deploy step. We therefore provision its
# runtime service account and a data reference, and document the deploy command.
# verify: https://cloud.google.com/vertex-ai/generative-ai/docs/agent-engine/deploy

# Runtime service account the deployed reasoningEngine runs as.
resource "google_service_account" "agent_runtime" {
  account_id   = "compliance-agent-runtime"
  display_name = "C1 Agent Runtime (reasoningEngine) identity"
  project      = var.project_id

  depends_on = [google_project_service.required]
}

# Least-privilege runtime bindings: call Vertex/Agent Platform, read Agent Search,
# call Model Armor + DLP, write audit logs + traces, use the regional CMEK.
locals {
  agent_runtime_roles = [
    "roles/aiplatform.user",           # invoke Gemini models / Agent Runtime
    "roles/discoveryengine.viewer",    # query Agent Search at serving time
    "roles/dlp.user",                  # deidentifyContent (PII redaction, P-04)
    "roles/logging.logWriter",         # write to the audit log (routed to WORM bucket)
    "roles/cloudtrace.agent",          # export OpenTelemetry spans
    "roles/alloydb.client",            # connect to the freshness ledger (PRIVATE)
    "roles/aiplatform.modelArmorUser", # call Model Armor sanitize endpoints
  ]
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

# Placeholder data reference for the engine that the SDK deploys out-of-band.
# Populate COMPLIANCE_AGENT_ENGINE (settings.yaml agent_engine.resource_name) with
# the resource id printed by the deploy step. Deploy command (run after apply):
#
#   python -m compliance_advisory.adapters.gcp.agent_runtime deploy \
#     --project=${var.project_id} --region=asia-southeast1 \
#     --service-account=${google_service_account.agent_runtime.email} \
#     --kms-key=${google_kms_crypto_key.compliance.id}
#
# (The display name "compliance-advisory" matches settings.yaml agent_engine.display_name.)
