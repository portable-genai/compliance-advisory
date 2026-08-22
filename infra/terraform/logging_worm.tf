# logging_worm.tf — WORM audit trail: locked Cloud Logging bucket + sink + audit config.
#
# General Principle map:
#   P-08 (immutable audit / WORM): the audit log is routed to a Cloud Logging bucket
#         whose retention is var.retention_days (~7 years) and whose `locked = true`
#         makes it Write-Once-Read-Many. The audit adapter (cloud_logging_audit)
#         writes already-redacted AuditEvents here.
#   P-03 (residency): bucket location is asia-southeast1.
#   P-09 (CMEK explicit): the bucket is CMEK-encrypted (logging SA key binding in kms.tf).
#   P-04 (no raw PII in logs): only redacted prompts/responses are written (enforced
#         in the app); DATA_READ audit logging is enabled to record every read.
#
# ############################################################################ #
# # WARNING — LOCKING IS IRREVERSIBLE.                                        # #
# # Setting `locked = true` below permanently prevents reducing retention or  # #
# # deleting this bucket for the full retention window. You CANNOT undo it,   # #
# # not even with project-owner rights. Confirm retention_days before apply.  # #
# # To trial without locking, set locked = false (NOT compliant for prod).    # #
# ############################################################################ #

resource "google_logging_project_bucket_config" "worm_audit" {
  project        = var.project_id
  location       = var.region                 # asia-southeast1 (P-03)
  bucket_id      = "compliance-advisory-worm" # matches settings.yaml logging.bucket
  description    = "WORM audit bucket for C1 compliance assistant (locked, ~7y retention)."
  retention_days = var.retention_days # 2557 (~7 years) by default

  # IRREVERSIBLE — see WARNING banner above. WORM compliance requires this true.
  locked = true

  # CMEK on the log bucket (P-09) — explicit, does not cascade.
  cmek_settings {
    kms_key_name = google_kms_crypto_key.compliance.id
  }

  depends_on = [
    google_project_service.required,
    google_kms_crypto_key_iam_member.logging,
  ]
}

# Route the audit log stream into the locked WORM bucket.
resource "google_logging_project_sink" "audit_to_worm" {
  project     = var.project_id
  name        = "compliance-audit-to-worm"
  description = "Routes the compliance-advisory-audit log to the locked WORM bucket."

  # Destination is the locked logging bucket created above.
  destination = "logging.googleapis.com/${google_logging_project_bucket_config.worm_audit.id}"

  # Capture this app's audit log + all Cloud Audit Logs (admin/data access).
  filter = <<-EOT
    logName="projects/${var.project_id}/logs/compliance-advisory-audit"
    OR logName:"cloudaudit.googleapis.com"
  EOT

  unique_writer_identity = true
}

# --------------------------------------------------------------------------- #
# Enable Data Access audit logs (DATA_READ) so every read of the corpus, the
# ledger, and the audit store itself is itself audited (P-08). ADMIN_READ and
# DATA_WRITE are on by default; we add DATA_READ explicitly.
# --------------------------------------------------------------------------- #
resource "google_project_iam_audit_config" "data_access" {
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
