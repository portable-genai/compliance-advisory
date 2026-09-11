# logging_worm.tf: the audit log bucket, its sink, and the project audit config.
#
# General Principle map:
#   P-08 (immutable audit / WORM): the audit log is routed to a Cloud Logging bucket whose
#         retention is var.retention_days. With var.worm_locked = true the bucket is locked and
#         the trail is Write-Once-Read-Many. The audit adapter (cloud_logging_audit) writes
#         already-redacted AuditEvents.
#   P-03 (residency): bucket location is var.region.
#   P-09 (CMEK explicit): the bucket is CMEK-encrypted (logging SA key binding in kms.tf).
#   P-04 (no raw PII in logs): only redacted prompts/responses are written (enforced in the app).
#
# ############################################################################ #
# # LOCKING IS IRREVERSIBLE.                                                  # #
# # With worm_locked = true the retention can never be reduced and the bucket # #
# # never deleted for the full retention window, not even as project owner.   # #
# # worm_locked therefore has NO default: a plan refuses until it is stated.  # #
# ############################################################################ #

resource "google_logging_project_bucket_config" "worm_audit" {
  project        = var.project_id
  location       = var.region
  bucket_id      = "compliance-advisory-worm" # matches settings.yaml logging.bucket
  description    = "Audit bucket for the compliance assistant (locked only when worm_locked = true)."
  retention_days = var.retention_days

  # IRREVERSIBLE when true. Stated by every deployment; never inherited (variables.tf).
  locked = var.worm_locked

  # CMEK on the log bucket (P-09): explicit, does not cascade.
  cmek_settings {
    kms_key_name = google_kms_crypto_key.compliance.id
  }

  depends_on = [
    google_project_service.required,
    google_kms_crypto_key_iam_member.logging,
  ]
}

locals {
  app_audit_log_filter = "logName=\"projects/${var.project_id}/logs/compliance-advisory-audit\""

  # The project's Cloud Audit Logs join the sink only when this stack owns the project's audit
  # configuration. In a shared project they are every sibling's too, and routing a copy of
  # them into this application's bucket is scope, and ingestion cost, it has no claim to.
  audit_sink_filter = var.manage_audit_config ? join(" OR ", [
    local.app_audit_log_filter,
    "logName:\"cloudaudit.googleapis.com\"",
  ]) : local.app_audit_log_filter
}

# Route the audit log stream into the audit bucket.
resource "google_logging_project_sink" "audit_to_worm" {
  project     = var.project_id
  name        = "compliance-audit-to-worm"
  description = "Routes the compliance-advisory-audit log to the audit bucket."

  destination = "logging.googleapis.com/${google_logging_project_bucket_config.worm_audit.id}"
  filter      = local.audit_sink_filter

  unique_writer_identity = true
}

# --------------------------------------------------------------------------- #
# Data Access audit logs (DATA_READ, DATA_WRITE, ADMIN_READ), so every read of the corpus, the
# ledger and the audit store is itself audited (P-08).
#
# AUTHORITATIVE for `allServices`: it REPLACES the project's audit config rather than adding to
# it, and Terraform shows that as a harmless create because this stack holds no state for a
# resource that is already live. Counted on var.manage_audit_config for that reason.
# --------------------------------------------------------------------------- #
resource "google_project_iam_audit_config" "data_access" {
  count   = var.manage_audit_config ? 1 : 0
  project = var.project_id
  service = "allServices"

  audit_log_config {
    log_type = "DATA_READ"
  }
  audit_log_config {
    log_type = "DATA_WRITE"
  }
  audit_log_config {
    log_type = "ADMIN_READ"
  }
}
