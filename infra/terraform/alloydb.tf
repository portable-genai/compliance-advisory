# alloydb.tf — AlloyDB freshness ledger (the 7-day fetch-at-runtime corpus ledger).
#
# General Principle map:
#   P-03 (residency): cluster + primary instance pinned to asia-southeast1.
#   P-05 (private-only data plane): the instance has NO public IP; it is reachable
#         only over the VPC via Private Service Access (PSA). ip_type=PRIVATE in
#         config/settings.yaml is honoured here at the infra layer.
#   P-09 (CMEK explicit): encryption_config.kms_key_name set to the regional key
#         (CMEK does not cascade — AlloyDB needs its own key binding, granted in kms.tf).
#
# The freshness ledger table (corpus_freshness) is created by the app/migration,
# not Terraform; here we provision the cluster, the PRIVATE primary, and the DB.

# ---- VPC + Private Service Access range for the private AlloyDB endpoint ---- #
resource "google_compute_network" "compliance" {
  name                    = var.vpc_network_name
  auto_create_subnetworks = false
  project                 = var.project_id

  depends_on = [google_project_service.required]
}

resource "google_compute_global_address" "alloydb_psa" {
  name          = "alloydb-psa-range"
  project       = var.project_id
  purpose       = "VPC_PEERING"
  address_type  = "INTERNAL"
  prefix_length = 16
  network       = google_compute_network.compliance.id
}

resource "google_service_networking_connection" "alloydb_psa" {
  network                 = google_compute_network.compliance.id
  service                 = "servicenetworking.googleapis.com"
  reserved_peering_ranges = [google_compute_global_address.alloydb_psa.name]
}

# ----------------------------- AlloyDB cluster ------------------------------ #
resource "google_alloydb_cluster" "freshness" {
  cluster_id = "compliance-freshness"
  location   = var.region # asia-southeast1 (P-03)
  project    = var.project_id

  database_version = "POSTGRES_16"

  # PRIVATE data plane: peer the cluster into the VPC; no public surface (P-05).
  network_config {
    network = google_compute_network.compliance.id
  }

  # CMEK — explicit regional key (P-09). Does not cascade from any other resource.
  encryption_config {
    kms_key_name = google_kms_crypto_key.compliance.id
  }

  # Initial app user; password is the sensitive per-tenant variable.
  initial_user {
    user     = "compliance_app" # matches config/settings.yaml alloydb.user
    password = var.alloydb_password
  }

  # Continuous backup, also CMEK-protected, kept in-region.
  continuous_backup_config {
    enabled              = true
    recovery_window_days = 14
    encryption_config {
      kms_key_name = google_kms_crypto_key.compliance.id
    }
  }

  depends_on = [
    google_service_networking_connection.alloydb_psa,
    google_kms_crypto_key_iam_member.alloydb,
  ]
}

# ------------------------- AlloyDB primary instance ------------------------- #
resource "google_alloydb_instance" "primary" {
  cluster       = google_alloydb_cluster.freshness.name
  instance_id   = "compliance-freshness-primary"
  instance_type = "PRIMARY"

  machine_config {
    cpu_count = 2 # small ledger workload; scale up if refresh volume grows
  }

  # PRIVATE only: do NOT enable Public IP. Connectivity is via the VPC/PSA above
  # and the AlloyDB Auth Proxy / connector from the app (settings ip_type=PRIVATE).
  network_config {
    enable_public_ip = false
  }

  depends_on = [google_service_networking_connection.alloydb_psa]
}

# --------------------------- The freshness database ------------------------- #
# NOTE: the google/google-beta ~> 6.0 provider line has NO google_alloydb_database
# resource. The "compliance" database (settings.yaml alloydb.database) and the
# corpus_freshness table are therefore created by the app's first migration, which
# runs over the PRIVATE endpoint via the AlloyDB connector. Wiring it here would be
# the only thing reaching into the data plane, which we deliberately leave to the app.
# verify: https://registry.terraform.io/providers/hashicorp/google/latest/docs/resources/alloydb_instance
#
# Bootstrap (run once after apply, from inside the VPC / via the Auth Proxy):
#   psql "host=<private-ip> user=compliance_app dbname=postgres" \
#     -c 'CREATE DATABASE compliance;'
# or let the app's Alembic/SQLAlchemy migration create it on first start.
