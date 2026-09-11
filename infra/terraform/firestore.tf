# firestore.tf: the scale-to-zero store for the freshness ledger and the horizon tracker.
#
# The `gcp` profile binds CorpusLedgerPort and HorizonTrackerPort to Firestore Native
# (adapters/gcp/firestore_ledger.py and adapters/gcp/firestore_horizon_tracker.py). Firestore
# bills by the operation and the stored byte, so a deployment nobody is using costs nothing
# here. The AlloyDB store those ports used to bind billed a primary by the hour; alloydb.tf now
# creates it only when enable_alloydb is set, for the `platform` profile.
#
# A NAMED database, one per deploy region. A project holds one immutable `(default)` database
# whose location is fixed when it is created, and this stack is applied into projects that
# already run other applications. So it creates `compliance-advisory-<region>`, and the
# adapters derive the same name from the same region (adapters/gcp/_firestore.py).
# tests/contract/test_firestore_provisioning.py holds the two together.
#
# Indexes. Firestore maintains single-field indexes itself. A query over more than one field
# needs a composite index, and without it the query fails with FAILED_PRECONDITION when it
# RUNS: on the deployment, the first time someone opens the tracked journey, and nowhere else.
# The list below is exactly the composite queries the adapters run, and the provisioning test
# compares it with the queries the adapters actually issue, in both directions.
#
# CMEK, delete protection and point-in-time recovery all default ON and are each a variable a
# deployment may decline (variables.tf). Firestore CMEK is allowlist-gated by Google and fixed
# at creation, so it is decided before the first apply.

locals {
  # Must match database_for() in adapters/gcp/_firestore.py; a contract test holds them together.
  firestore_database = "compliance-advisory-${var.region}"

  # One entry per composite query the adapters run, fields in query order.
  firestore_composite_indexes = [
    # FirestoreHorizonTrackerAdapter.list(): tenant equality, ordered by change_id.
    { collection = "horizon_tracking", fields = ["tenant", "change_id"] },
  ]
}

resource "google_firestore_database" "compliance" {
  project     = var.project_id
  name        = local.firestore_database
  location_id = var.region
  type        = "FIRESTORE_NATIVE"

  dynamic "cmek_config" {
    for_each = var.firestore_cmek_enabled ? [google_kms_crypto_key.compliance.id] : []
    content {
      kms_key_name = cmek_config.value
    }
  }

  delete_protection_state = (
    var.firestore_delete_protection_enabled
    ? "DELETE_PROTECTION_ENABLED"
    : "DELETE_PROTECTION_DISABLED"
  )

  point_in_time_recovery_enablement = (
    var.firestore_pitr_enabled
    ? "POINT_IN_TIME_RECOVERY_ENABLED"
    : "POINT_IN_TIME_RECOVERY_DISABLED"
  )

  # DELETE rather than the provider's ABANDON, so a stack that has declined delete protection
  # is destroyable in fact. Delete protection, not this, is the guard: while it is enabled a
  # destroy of the database is refused.
  deletion_policy = "DELETE"

  depends_on = [
    google_project_service.required,
    google_kms_crypto_key_iam_member.firestore,
  ]
}

resource "google_firestore_index" "composite" {
  for_each = {
    for index in local.firestore_composite_indexes :
    "${index.collection}:${join(",", index.fields)}" => index
  }

  project     = var.project_id
  database    = google_firestore_database.compliance.name
  collection  = each.value.collection
  query_scope = "COLLECTION"

  dynamic "fields" {
    for_each = each.value.fields
    content {
      field_path = fields.value
      order      = "ASCENDING"
    }
  }
}
