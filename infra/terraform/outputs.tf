# outputs.tf: the values the app and the embedding host need after apply.
#
# Each maps onto a config/settings.yaml field or a COMPLIANCE_* variable, so a deploy is
# "apply, then pass these into the runtime environment". An output for a resource a toggle did
# not create is null rather than absent, so a consumer can tell "declined" from "forgotten".

output "project_id" {
  description = "The deployment project id."
  value       = var.project_id
}

output "region" {
  description = "The deploy region (settings.yaml region)."
  value       = var.region
}

# ------------------------------ Agent Search -------------------------------- #
output "data_store_id" {
  description = "Agent Search data store id (settings.yaml agent_search.data_store_id)."
  value       = google_discovery_engine_data_store.reg_kb.data_store_id
}

output "search_engine_id" {
  description = "Agent Search engine id (settings.yaml agent_search.engine_id)."
  value       = google_discovery_engine_search_engine.compliance.engine_id
}

output "agent_search_location" {
  description = "Where the data store lives: global, us or eu. Pass as COMPLIANCE_AGENT_SEARCH_LOCATION."
  value       = google_discovery_engine_data_store.reg_kb.location
}

# ------------------------------- Firestore ---------------------------------- #
output "firestore_database" {
  description = "The Firestore database holding the ledger and tracker. The adapters derive the same name from the region, so nothing needs to pass it."
  value       = google_firestore_database.compliance.name
}

# ---------------------------- Artifact Registry ----------------------------- #
output "image_registry" {
  description = <<-EOT
    The Docker repository both images are pushed to, as the prefix a tag or a digest is appended
    to: `<region>-docker.pkg.dev/<project>/compliance-advisory`. The API is `.../api`, the console
    `.../ui`, and `corpus_refresh_image` names a digest under `.../api`.
  EOT
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.images.repository_id}"
}

# --------------------------------- KMS -------------------------------------- #
output "kms_key" {
  description = "Regional CMEK crypto key id (settings.yaml kms_key / COMPLIANCE_KMS_KEY)."
  value       = one(google_kms_crypto_key.compliance[*].id)
}

# --------------------------------- AlloyDB ---------------------------------- #
output "alloydb_instance_uri" {
  description = "AlloyDB primary instance URI (COMPLIANCE_ALLOYDB_URI, platform profile); null unless enable_alloydb."
  value       = one(google_alloydb_instance.primary[*].name)
}

output "alloydb_cluster" {
  description = "AlloyDB cluster resource name; null unless enable_alloydb."
  value       = one(google_alloydb_cluster.freshness[*].name)
}

# ------------------------------ Control posture ----------------------------- #
output "scc_parent" {
  description = "SCC parent the posture adapter reads findings from (COMPLIANCE_SCC_PARENT)."
  value       = "organizations/${var.org_id}"
}

output "asset_feed_topic" {
  description = "Pub/Sub topic the Cloud Asset feed publishes to; null unless enable_posture_feed."
  value       = one(google_pubsub_topic.asset_feed[*].id)
}

output "assured_workload" {
  description = "Assured Workloads resource the posture adapter observes (COMPLIANCE_ASSURED_WORKLOAD); null unless enable_assured_workloads."
  value       = one(google_assured_workloads_workload.sg[*].name)
}

# -------------------------------- Audit logging ----------------------------- #
output "log_bucket" {
  description = "Audit log bucket id (settings.yaml logging.bucket)."
  value       = google_logging_project_bucket_config.worm_audit.id
}

output "log_bucket_locked" {
  description = "Whether the audit bucket is locked. true is irreversible."
  value       = google_logging_project_bucket_config.worm_audit.locked
}

output "audit_sink_writer_identity" {
  description = "Sink writer identity (grant it bucket access if cross-project)."
  value       = google_logging_project_sink.audit_to_worm.writer_identity
}

# ------------------------- Model Armor / DLP -------------------------------- #
output "model_armor_template" {
  description = "Model Armor template id (settings.yaml model_armor.template_id)."
  value       = google_model_armor_template.compliance_guardrail.template_id
}

output "dlp_inspect_template" {
  description = "DLP inspect template (COMPLIANCE_DLP_INSPECT_TEMPLATE)."
  value       = google_data_loss_prevention_inspect_template.compliance.id
}

output "dlp_deidentify_template" {
  description = "DLP deidentify template (COMPLIANCE_DLP_DEIDENTIFY_TEMPLATE)."
  value       = google_data_loss_prevention_deidentify_template.compliance.id
}

# ----------------------------- Service accounts ----------------------------- #
output "app_service_account" {
  description = "Serving/API service account email."
  value       = google_service_account.app.email
}

output "pipeline_service_account" {
  description = "Ingestion/freshness pipeline service account email."
  value       = google_service_account.pipeline.email
}

output "agent_runtime_service_account" {
  description = "Agent Runtime (reasoningEngine) service account email."
  value       = google_service_account.agent_runtime.email
}

output "corpus_refresh_job" {
  description = "The scheduled corpus refresh Cloud Run job; null unless corpus_refresh_image is set."
  value       = one(google_cloud_run_v2_job.freshness_refresh[*].name)
}
