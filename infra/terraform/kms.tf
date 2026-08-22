# kms.tf — Regional Customer-Managed Encryption Keys (CMEK) in Singapore.
#
# General Principle map:
#   P-09 (CMEK does NOT cascade): a CMEK on one resource does not automatically
#         protect data that resource hands to another service. Each managed
#         service (AlloyDB, Agent Search, Agent Runtime, Logging, Secret Manager,
#         DLP outputs) must be told to use this key explicitly. We therefore keep
#         ONE regional key ring + crypto key here and wire it into every resource
#         that supports CMEK in its own file. If a service in the path does not
#         support CMEK, that is a residency gap to document, not to assume away.
#   P-03 (residency): the key ring location is asia-southeast1 — a regional key,
#         never the global/multi-region key. Regional CMEK is what pins crypto
#         material in-country.

resource "google_kms_key_ring" "compliance" {
  name     = "compliance-advisory-ring"
  location = var.region # asia-southeast1 — regional, in-country key material (P-03)

  depends_on = [google_project_service.required]
}

resource "google_kms_crypto_key" "compliance" {
  name     = "compliance-advisory-cmek"
  key_ring = google_kms_key_ring.compliance.id

  purpose         = "ENCRYPT_DECRYPT"
  rotation_period = "7776000s" # 90 days — periodic rotation for key hygiene

  # Software-level protection. Switch to "HSM" if FIPS/CC HSM is mandated; HSM
  # is available in asia-southeast1.
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
# Grant each service agent the right to use the key. CMEK does not cascade
# (P-09): every service that encrypts with this key needs its OWN binding here.
# --------------------------------------------------------------------------- #
data "google_project" "this" {
  project_id = var.project_id
}

# AlloyDB service agent.
resource "google_kms_crypto_key_iam_member" "alloydb" {
  crypto_key_id = google_kms_crypto_key.compliance.id
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = "serviceAccount:service-${data.google_project.this.number}@gcp-sa-alloydb.iam.gserviceaccount.com"
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

# Cloud Logging service agent (CMEK on the WORM bucket).
resource "google_kms_crypto_key_iam_member" "logging" {
  crypto_key_id = google_kms_crypto_key.compliance.id
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = "serviceAccount:service-${data.google_project.this.number}@gcp-sa-logging.iam.gserviceaccount.com"
}
