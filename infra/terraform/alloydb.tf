# alloydb.tf: the AlloyDB store for the `platform` profile's ledger and tracker. OFF by default.
#
# Everything here exists only for AlloyDB, and every resource is counted on var.enable_alloydb:
# the VPC and Private Service Access peering, the cluster and its 2-vCPU primary. Their API
# enablements (apis.tf), CMEK grant (kms.tf), perimeter entry (vpc_sc.tf), CMEK org-policy
# entry (org_policy.tf) and client roles (iam.tf, agent_runtime.tf) follow the same variable.
#
# Why off: an AlloyDB primary bills by the hour whether or not a row moves, and nothing in a
# deployment can pause it. The `gcp` profile keeps the same two stores in Firestore
# (firestore.tf), which costs nothing while idle. The `platform` profile binds the AlloyDB
# adapters (config/settings.yaml), so an installation on that profile sets enable_alloydb =
# true and supplies alloydb_password.
#
# General Principle map:
#   P-03 (residency): cluster + primary instance pinned to var.region.
#   P-05 (private-only data plane): the instance has NO public IP; it is reachable only over
#         the VPC via Private Service Access (PSA), which ip_type=PRIVATE in settings honours.
#   P-09 (CMEK explicit): encryption_config names the regional key (CMEK does not cascade, so
#         AlloyDB needs its own key binding, granted in kms.tf).
#
# The corpus_freshness and horizon_tracking tables are created by the adapters, idempotently,
# on first use; here we provision the cluster and the PRIVATE primary.

# ---- VPC + Private Service Access range for the private AlloyDB endpoint ---- #
resource "google_compute_network" "compliance" {
  count                   = var.enable_alloydb ? 1 : 0
  name                    = var.vpc_network_name
  auto_create_subnetworks = false
  project                 = var.project_id

  depends_on = [google_project_service.required]
}

resource "google_compute_global_address" "alloydb_psa" {
  count         = var.enable_alloydb ? 1 : 0
  name          = "alloydb-psa-range"
  project       = var.project_id
  purpose       = "VPC_PEERING"
  address_type  = "INTERNAL"
  prefix_length = 16
  network       = google_compute_network.compliance[0].id
}

resource "google_service_networking_connection" "alloydb_psa" {
  count                   = var.enable_alloydb ? 1 : 0
  network                 = google_compute_network.compliance[0].id
  service                 = "servicenetworking.googleapis.com"
  reserved_peering_ranges = [google_compute_global_address.alloydb_psa[0].name]
}

# ----------------------------- AlloyDB cluster ------------------------------ #
resource "google_alloydb_cluster" "freshness" {
  count      = var.enable_alloydb ? 1 : 0
  cluster_id = "compliance-freshness"
  location   = var.region
  project    = var.project_id

  database_version = "POSTGRES_16"

  # PRIVATE data plane: peer the cluster into the VPC; no public surface (P-05).
  network_config {
    network = google_compute_network.compliance[0].id
  }

  # CMEK: explicit regional key (P-09). Does not cascade from any other resource.
  dynamic "encryption_config" {
    for_each = var.cmek_enabled ? [1] : []
    content {
      kms_key_name = one(google_kms_crypto_key.compliance[*].id)
    }
  }

  # Initial app user; the password is the sensitive per-tenant variable.
  initial_user {
    user     = "compliance_app" # matches config/settings.yaml alloydb.user
    password = var.alloydb_password
  }

  # Continuous backup, also CMEK-protected, kept in-region.
  continuous_backup_config {
    enabled              = true
    recovery_window_days = 14
    dynamic "encryption_config" {
      for_each = var.cmek_enabled ? [1] : []
      content {
        kms_key_name = one(google_kms_crypto_key.compliance[*].id)
      }
    }
  }

  depends_on = [
    google_service_networking_connection.alloydb_psa,
    google_kms_crypto_key_iam_member.alloydb,
  ]
}

# ------------------------- AlloyDB primary instance ------------------------- #
resource "google_alloydb_instance" "primary" {
  count         = var.enable_alloydb ? 1 : 0
  cluster       = google_alloydb_cluster.freshness[0].name
  instance_id   = "compliance-freshness-primary"
  instance_type = "PRIMARY"

  machine_config {
    cpu_count = 2 # small ledger workload; scale up if refresh volume grows
  }

  # PRIVATE only: no public IP. Connectivity is via the VPC/PSA above and the AlloyDB
  # connector from the app (settings ip_type=PRIVATE).
  network_config {
    enable_public_ip = false
  }

  depends_on = [google_service_networking_connection.alloydb_psa]
}

# The google/google-beta ~> 6.0 provider line has NO google_alloydb_database resource. The
# "compliance" database (settings.yaml alloydb.database) is created once over the PRIVATE
# endpoint, and the adapters create their tables idempotently on first use:
#   psql "host=<private-ip> user=compliance_app dbname=postgres" -c 'CREATE DATABASE compliance;'
