# kms.tf: the regional customer-managed encryption key (CMEK).
#
# General Principle map:
#   P-09 (CMEK does NOT cascade): a CMEK on one resource does not protect data that resource
#         hands to another service. Each managed service must be told to use this key
#         explicitly, so ONE regional key ring + crypto key lives here and every resource that
#         supports CMEK names it in its own file, with its service agent granted below.
#   P-03 (residency): the key ring is regional, never a global or multi-region key.

resource "google_kms_key_ring" "compliance" {
  name     = "compliance-advisory-ring"
  location = var.region

  depends_on = [google_project_service.required]
}

resource "google_kms_crypto_key" "compliance" {
  name     = "compliance-advisory-cmek"
  key_ring = google_kms_key_ring.compliance.id

  purpose         = "ENCRYPT_DECRYPT"
  rotation_period = "7776000s" # 90 days

  # Software-level protection. Switch to "HSM" if FIPS/CC HSM is mandated.
  version_template {
    algorithm        = "GOOGLE_SYMMETRIC_ENCRYPTION"
    protection_level = "SOFTWARE"
  }

  lifecycle {
    # A destroyed key is unrecoverable and would strand all CMEK-encrypted data.
    prevent_destroy = true
  }
}

# --------------------------------------------------------------------------- #
# Each service agent that encrypts with this key needs its OWN binding (P-09).
# --------------------------------------------------------------------------- #
data "google_project" "this" {
  project_id = var.project_id
}

# AlloyDB service agent, only when AlloyDB exists.
resource "google_kms_crypto_key_iam_member" "alloydb" {
  count         = var.enable_alloydb ? 1 : 0
  crypto_key_id = google_kms_crypto_key.compliance.id
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = "serviceAccount:service-${data.google_project.this.number}@gcp-sa-alloydb.iam.gserviceaccount.com"
}

# Firestore service agent, only when the database is encrypted with this key. Asked for rather
# than spelled out, because the agent exists only once the API has provisioned it.
resource "google_project_service_identity" "firestore" {
  count    = var.firestore_cmek_enabled ? 1 : 0
  provider = google-beta
  project  = var.project_id
  service  = "firestore.googleapis.com"

  depends_on = [google_project_service.required]
}

resource "google_kms_crypto_key_iam_member" "firestore" {
  count         = var.firestore_cmek_enabled ? 1 : 0
  crypto_key_id = google_kms_crypto_key.compliance.id
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = "serviceAccount:${google_project_service_identity.firestore[0].email}"
}

# Discovery Engine (Agent Search) service agent.
resource "google_kms_crypto_key_iam_member" "discoveryengine" {
  crypto_key_id = google_kms_crypto_key.compliance.id
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = "serviceAccount:service-${data.google_project.this.number}@gcp-sa-discoveryengine.iam.gserviceaccount.com"
}

# Vertex AI / Agent Runtime service agent.
resource "google_kms_crypto_key_iam_member" "aiplatform" {
  crypto_key_id = google_kms_crypto_key.compliance.id
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = "serviceAccount:service-${data.google_project.this.number}@gcp-sa-aiplatform.iam.gserviceaccount.com"
}

# Cloud Logging service agent (CMEK on the audit bucket).
resource "google_kms_crypto_key_iam_member" "logging" {
  crypto_key_id = google_kms_crypto_key.compliance.id
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = "serviceAccount:service-${data.google_project.this.number}@gcp-sa-logging.iam.gserviceaccount.com"
}
