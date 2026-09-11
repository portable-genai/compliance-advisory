# posture.tftest.hcl: the control surface each variable produces, proved at plan.
#
# Mock providers, so no credentials and no cloud call: `make tf-test`. Every assertion reads a
# value Terraform knows at plan time (a count, a literal, a derived name), never a computed id,
# because a mock provider cannot resolve those and an assertion over an unknown proves nothing.

mock_provider "google" {
  mock_data "google_project" {
    defaults = {
      number = "123456789012"
    }
  }
}

mock_provider "google-beta" {}

variables {
  project_id = "fictional-compliance-sg"
  org_id     = "123456789012"
}

# --------------------------------------------------------------------------- #
# Nothing irreversible is inherited
# --------------------------------------------------------------------------- #
run "an_unstated_lock_is_refused" {
  command = plan

  variables {
    enable_vpc_sc = false
  }

  expect_failures = [var.worm_locked]
}

run "a_locked_bucket_keeps_the_seven_year_floor" {
  command = plan

  variables {
    enable_vpc_sc  = false
    worm_locked    = true
    retention_days = 30
  }

  expect_failures = [var.retention_days]
}

run "a_stated_lock_is_the_lock_that_applies" {
  command = plan

  variables {
    enable_vpc_sc = false
    worm_locked   = true
  }

  assert {
    condition     = google_logging_project_bucket_config.worm_audit.locked == true && google_logging_project_bucket_config.worm_audit.retention_days == 2557
    error_message = "worm_locked = true must lock the audit bucket at the seven-year floor."
  }
}

run "assured_workloads_needs_a_billing_account" {
  command = plan

  variables {
    enable_vpc_sc            = false
    worm_locked              = false
    enable_assured_workloads = true
  }

  expect_failures = [google_assured_workloads_workload.sg]
}

run "a_perimeter_needs_its_access_policy" {
  command = plan

  variables {
    worm_locked = false
  }

  expect_failures = [var.access_policy_id]
}

run "a_refresh_image_must_be_pinned_by_digest" {
  command = plan

  variables {
    enable_vpc_sc        = false
    worm_locked          = false
    corpus_refresh_image = "asia-southeast1-docker.pkg.dev/fictional/compliance/api:latest"
  }

  expect_failures = [var.corpus_refresh_image]
}

# --------------------------------------------------------------------------- #
# Defaults: no hourly store, nothing irreversible, reversible controls strict
# --------------------------------------------------------------------------- #
run "defaults_create_no_alloydb_and_keep_every_reversible_control" {
  command = plan

  variables {
    enable_vpc_sc = false
    worm_locked   = false
  }

  assert {
    condition = (
      length(google_alloydb_cluster.freshness) == 0 &&
      length(google_alloydb_instance.primary) == 0 &&
      length(google_compute_network.compliance) == 0 &&
      length(google_compute_global_address.alloydb_psa) == 0 &&
      length(google_service_networking_connection.alloydb_psa) == 0 &&
      length(google_kms_crypto_key_iam_member.alloydb) == 0
    )
    error_message = "No AlloyDB resource, network or grant may exist unless enable_alloydb = true."
  }

  assert {
    condition = (
      !contains(keys(google_project_service.required), "alloydb.googleapis.com") &&
      !contains(keys(google_project_service.required), "servicenetworking.googleapis.com") &&
      !contains(keys(google_project_iam_member.app), "roles/alloydb.client") &&
      !contains(keys(google_project_iam_member.pipeline), "roles/alloydb.client") &&
      !contains(keys(google_project_iam_member.agent_runtime), "roles/alloydb.client")
    )
    error_message = "The AlloyDB APIs and client roles exist only for AlloyDB."
  }

  assert {
    condition     = length(google_assured_workloads_workload.sg) == 0
    error_message = "An Assured Workloads folder must never arrive by default."
  }

  assert {
    condition = (
      google_firestore_database.compliance.name == "compliance-advisory-asia-southeast1" &&
      google_firestore_database.compliance.location_id == var.region &&
      google_firestore_database.compliance.type == "FIRESTORE_NATIVE"
    )
    error_message = "The ledger and tracker database is the named, regional Firestore Native one the adapters bind to."
  }

  assert {
    condition = (
      google_firestore_index.composite["horizon_tracking:tenant,change_id"].database == google_firestore_database.compliance.name &&
      google_firestore_index.composite["horizon_tracking:tenant,change_id"].collection == "horizon_tracking"
    )
    error_message = "The tracker's tenant listing needs its composite index on the adapters' database."
  }

  assert {
    condition = (
      length(google_firestore_database.compliance.cmek_config) == 1 &&
      google_firestore_database.compliance.delete_protection_state == "DELETE_PROTECTION_ENABLED" &&
      google_firestore_database.compliance.point_in_time_recovery_enablement == "POINT_IN_TIME_RECOVERY_ENABLED"
    )
    error_message = "Firestore CMEK, delete protection and PITR default on; a deployment declines them in its tfvars."
  }

  assert {
    condition = (
      length(google_org_policy_policy.resource_locations) == 1 &&
      length(google_org_policy_policy.restrict_cmek_projects) == 1 &&
      length(google_project_iam_audit_config.data_access) == 1 &&
      strcontains(google_logging_project_sink.audit_to_worm.filter, "cloudaudit.googleapis.com") &&
      length(google_model_armor_template.compliance_guardrail.filter_config[0].malicious_uri_filter_settings) == 1 &&
      google_model_armor_template.compliance_guardrail.template_metadata[0].log_sanitize_operations == true &&
      length(google_cloud_asset_project_feed.posture) == 1
    )
    error_message = "Every reversible control keeps its strict default; only a deployment's tfvars declines one."
  }

  assert {
    condition     = length(google_cloud_run_v2_job.freshness_refresh) == 0 && length(google_cloud_scheduler_job.freshness_refresh) == 0
    error_message = "No refresh job without a digest-pinned image to run."
  }

  assert {
    condition     = output.alloydb_instance_uri == null && output.assured_workload == null
    error_message = "A declined resource's output is null, so declined and forgotten are distinguishable."
  }
}

# --------------------------------------------------------------------------- #
# The shared-project deployment: every decline its siblings make, stated
# --------------------------------------------------------------------------- #
run "shared_project_declines" {
  command = plan

  variables {
    worm_locked                         = false
    retention_days                      = 30
    manage_org_policies                 = false
    manage_audit_config                 = false
    enable_vpc_sc                       = false
    model_armor_full_capabilities       = false
    model_armor_log_sanitize_operations = false
    enable_posture_feed                 = false
    firestore_cmek_enabled              = false
    firestore_delete_protection_enabled = false
    firestore_pitr_enabled              = false
    agent_search_location               = "us"
    additional_serving_service_accounts = ["journey-a-compli-abc123@fictional-compliance-sg.iam.gserviceaccount.com"]
    corpus_refresh_image                = "asia-southeast1-docker.pkg.dev/fictional-compliance-sg/compliance/api@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    cloud_run_deletion_protection       = false
  }

  assert {
    condition = (
      length(google_org_policy_policy.resource_locations) == 0 &&
      length(google_org_policy_policy.no_external_ip) == 0 &&
      length(google_org_policy_policy.uniform_bucket_access) == 0 &&
      length(google_org_policy_policy.restrict_cmek_projects) == 0
    )
    error_message = "manage_org_policies = false must write no project Org Policy."
  }

  assert {
    condition = (
      length(google_project_iam_audit_config.data_access) == 0 &&
      !strcontains(google_logging_project_sink.audit_to_worm.filter, "cloudaudit.googleapis.com")
    )
    error_message = "manage_audit_config = false must neither replace the project audit config nor route siblings' audit logs."
  }

  assert {
    condition     = length(google_access_context_manager_service_perimeter.compliance) == 0
    error_message = "enable_vpc_sc = false must create no second perimeter."
  }

  assert {
    condition = (
      length(google_model_armor_template.compliance_guardrail.filter_config[0].malicious_uri_filter_settings) == 0 &&
      google_model_armor_template.compliance_guardrail.template_metadata[0].log_sanitize_operations == false
    )
    error_message = "The Model Armor capabilities asia-southeast1 does not serve, and sanitize logging, must be declinable."
  }

  assert {
    condition = (
      google_logging_project_bucket_config.worm_audit.locked == false &&
      google_logging_project_bucket_config.worm_audit.retention_days == 30
    )
    error_message = "A declined lock leaves the bucket editable at the stated retention."
  }

  assert {
    condition = (
      length(google_firestore_database.compliance.cmek_config) == 0 &&
      length(google_kms_crypto_key_iam_member.firestore) == 0 &&
      length(google_project_service_identity.firestore) == 0 &&
      google_firestore_database.compliance.delete_protection_state == "DELETE_PROTECTION_DISABLED" &&
      google_firestore_database.compliance.point_in_time_recovery_enablement == "POINT_IN_TIME_RECOVERY_DISABLED" &&
      google_firestore_database.compliance.location_id == var.region
    )
    error_message = "Firestore CMEK, delete protection and PITR must each be declinable, and the database stays in region."
  }

  assert {
    condition     = length(google_pubsub_topic.asset_feed) == 0 && length(google_cloud_asset_project_feed.posture) == 0
    error_message = "enable_posture_feed = false must create no project-wide feed."
  }

  assert {
    condition = (
      contains(keys(google_project_iam_member.additional_serving), "journey-a-compli-abc123@fictional-compliance-sg.iam.gserviceaccount.com|roles/datastore.user") &&
      contains(keys(google_project_iam_member.additional_serving), "journey-a-compli-abc123@fictional-compliance-sg.iam.gserviceaccount.com|roles/modelarmor.user") &&
      contains(keys(google_project_iam_member.additional_serving), "journey-a-compli-abc123@fictional-compliance-sg.iam.gserviceaccount.com|roles/dlp.reader") &&
      contains(keys(google_kms_crypto_key_iam_member.additional_serving), "journey-a-compli-abc123@fictional-compliance-sg.iam.gserviceaccount.com")
    )
    error_message = "The portal-minted API identity must receive the serving roles and the key."
  }

  assert {
    condition     = google_discovery_engine_data_store.reg_kb.location == "us"
    error_message = "The data store must be created where the deployment said."
  }

  assert {
    condition = (
      length(google_cloud_run_v2_job.freshness_refresh) == 1 &&
      google_cloud_run_v2_job.freshness_refresh[0].location == var.region &&
      google_cloud_run_v2_job.freshness_refresh[0].deletion_protection == false &&
      google_cloud_run_v2_job.freshness_refresh[0].template[0].template[0].containers[0].image == var.corpus_refresh_image &&
      one([for env in google_cloud_run_v2_job.freshness_refresh[0].template[0].template[0].containers[0].env : env.value if env.name == "COMPLIANCE_PROFILE"]) == "gcp" &&
      one([for env in google_cloud_run_v2_job.freshness_refresh[0].template[0].template[0].containers[0].env : env.value if env.name == "COMPLIANCE_AGENT_SEARCH_LOCATION"]) == "us"
    )
    error_message = "The refresh job runs the reviewed digest in region, on the gcp profile, against the data store's location."
  }

  assert {
    condition = (
      length(google_cloud_scheduler_job.freshness_refresh) == 1 &&
      google_cloud_scheduler_job.freshness_refresh[0].http_target[0].uri == "https://run.googleapis.com/v2/projects/fictional-compliance-sg/locations/asia-southeast1/jobs/compliance-freshness-refresh:run" &&
      length(google_cloud_scheduler_job.freshness_refresh[0].http_target[0].oauth_token) == 1 &&
      length(google_cloud_scheduler_job.freshness_refresh[0].http_target[0].oidc_token) == 0
    )
    error_message = "The trigger must call the Cloud Run Admin API's :run with an OAuth token; an OIDC token is refused there."
  }

  assert {
    condition     = length(google_alloydb_cluster.freshness) == 0 && length(google_assured_workloads_workload.sg) == 0
    error_message = "The shared-project deployment creates no AlloyDB and no Assured Workloads folder."
  }
}

# --------------------------------------------------------------------------- #
# The other side of each toggle
# --------------------------------------------------------------------------- #
run "alloydb_needs_its_password" {
  command = plan

  variables {
    enable_vpc_sc  = false
    worm_locked    = false
    enable_alloydb = true
  }

  expect_failures = [var.alloydb_password]
}

run "alloydb_selected_for_the_platform_profile" {
  command = plan

  variables {
    enable_vpc_sc    = false
    worm_locked      = false
    enable_alloydb   = true
    alloydb_password = "fictional-not-a-secret"
  }

  assert {
    condition = (
      length(google_alloydb_cluster.freshness) == 1 &&
      length(google_alloydb_instance.primary) == 1 &&
      google_alloydb_cluster.freshness[0].location == var.region &&
      length(google_compute_network.compliance) == 1 &&
      length(google_service_networking_connection.alloydb_psa) == 1 &&
      length(google_kms_crypto_key_iam_member.alloydb) == 1
    )
    error_message = "enable_alloydb = true must create the regional cluster, its primary, its network and its key grant."
  }

  assert {
    condition = (
      contains(keys(google_project_service.required), "alloydb.googleapis.com") &&
      contains(keys(google_project_service.required), "servicenetworking.googleapis.com") &&
      contains(keys(google_project_iam_member.app), "roles/alloydb.client") &&
      contains(keys(google_project_iam_member.pipeline), "roles/alloydb.client") &&
      contains(google_org_policy_policy.restrict_cmek_projects[0].spec[0].rules[0].values[0].denied_values, "alloydb.googleapis.com")
    )
    error_message = "AlloyDB's APIs, client roles and CMEK requirement arrive with it."
  }
}

run "assured_workloads_when_chosen" {
  command = plan

  variables {
    enable_vpc_sc            = false
    worm_locked              = false
    enable_assured_workloads = true
    billing_account          = "000000-000000-000000"
  }

  assert {
    condition     = length(google_assured_workloads_workload.sg) == 1 && google_assured_workloads_workload.sg[0].location == var.region
    error_message = "enable_assured_workloads = true must create the regional workload."
  }
}
