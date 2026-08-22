# outputs.tf — Values the app/operators need to wire settings.yaml after apply.
#
# These map 1:1 onto config/settings.yaml / config.py fields so a deploy is just
# "apply, then export these into the runtime environment".

output "project_id" {
  description = "The deployment project id."
  value       = var.project_id
}

output "region" {
  description = "Pinned region (asia-southeast1, Singapore)."
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
  description = "Confirms Agent Search residency — must be asia-southeast1 (fail-fast)."
  value       = google_discovery_engine_data_store.reg_kb.location
}

# --------------------------------- KMS -------------------------------------- #
output "kms_key" {
  description = "Regional CMEK crypto key id (settings.yaml kms_key / COMPLIANCE_KMS_KEY)."
  value       = google_kms_crypto_key.compliance.id
}

# --------------------------------- AlloyDB ---------------------------------- #
output "alloydb_instance_uri" {
  description = "AlloyDB primary instance URI (settings.yaml alloydb.instance_uri)."
  value       = google_alloydb_instance.primary.name
}

output "alloydb_cluster" {
  description = "AlloyDB cluster resource name."
  value       = google_alloydb_cluster.freshness.name
}

# ------------------------- Control posture (Rsk2 merge) --------------------- #
output "scc_parent" {
  description = "SCC parent the app reads findings from (settings.yaml COMPLIANCE_SCC_PARENT)."
  value       = var.org_id != "" ? "organizations/${var.org_id}" : ""
}

output "asset_feed_topic" {
  description = "Pub/Sub topic the Cloud Asset feed publishes posture changes to."
  value       = google_pubsub_topic.asset_feed.id
}

output "assured_workload" {
  description = "Assured Workloads resource the app observes (settings.yaml COMPLIANCE_ASSURED_WORKLOAD)."
  value       = google_assured_workloads_workload.sg.name
}

# ------------------------------- WORM logging ------------------------------- #
output "log_bucket" {
  description = "Locked WORM audit log bucket id (settings.yaml logging.bucket)."
  value       = google_logging_project_bucket_config.worm_audit.id
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
  description = "DLP inspect template (settings.yaml dlp.inspect_template)."
  value       = google_data_loss_prevention_inspect_template.compliance.id
}

output "dlp_deidentify_template" {
  description = "DLP deidentify template (settings.yaml dlp.deidentify_template)."
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

output "scheduler_service_account" {
  description = "Corpus freshness scheduler service account email."
  value       = google_service_account.scheduler.email
}
