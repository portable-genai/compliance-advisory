# scheduler.tf: the scheduled corpus freshness refresh.
#
# General Principle map:
#   P-07 (freshness / fetch-at-runtime): the regulatory corpus has a 7-day TTL
#         (settings.yaml corpus.ttl_days). A daily Cloud Run job re-fetches expired and
#         never-ingested sources, redacts them, re-ingests them into Agent Search and writes
#         the freshness ledger, so reads rarely have to re-fetch inline.
#   P-03 (residency): the job and its trigger both run in var.region.
#
# Counted on var.corpus_refresh_image. The job runs the API image with the refresh entrypoint,
# so a deployment names the reviewed digest it deploys the API from; empty creates no job and
# no trigger, because a Cloud Run job cannot be created from an image that does not exist.
#
# The job writes the ledger through whatever the `gcp` profile binds (Firestore), as the
# pipeline identity, which holds datastore.user, the Agent Search editor role and the DLP roles
# (iam.tf). Horizon scanning reads that same ledger afterwards: run `compliance horizon scan`
# or POST /horizon/scan once a refresh has landed.

locals {
  corpus_refresh_enabled = var.corpus_refresh_image != ""
}

# The identity Cloud Scheduler uses to start the job.
resource "google_service_account" "scheduler" {
  count        = local.corpus_refresh_enabled ? 1 : 0
  account_id   = "compliance-freshness-cron"
  display_name = "Compliance corpus freshness scheduler"
  project      = var.project_id

  depends_on = [google_project_service.required]
}

resource "google_cloud_run_v2_job" "freshness_refresh" {
  count               = local.corpus_refresh_enabled ? 1 : 0
  name                = "compliance-freshness-refresh"
  location            = var.region
  project             = var.project_id
  deletion_protection = var.cloud_run_deletion_protection

  template {
    task_count = 1

    template {
      service_account = google_service_account.pipeline.email
      timeout         = "3600s"
      max_retries     = 1

      containers {
        image   = var.corpus_refresh_image
        command = ["python", "-m", "compliance_advisory.pipelines.refresh_job"]

        env {
          name  = "COMPLIANCE_PROFILE"
          value = "gcp"
        }
        env {
          name  = "GOOGLE_CLOUD_PROJECT"
          value = var.project_id
        }
        env {
          name  = "COMPLIANCE_AGENT_SEARCH_LOCATION"
          value = var.agent_search_location
        }
        env {
          name  = "COMPLIANCE_DLP_INSPECT_TEMPLATE"
          value = google_data_loss_prevention_inspect_template.compliance.id
        }
        env {
          name  = "COMPLIANCE_DLP_DEIDENTIFY_TEMPLATE"
          value = google_data_loss_prevention_deidentify_template.compliance.id
        }
      }
    }
  }

  depends_on = [
    google_project_service.required,
    google_project_iam_member.pipeline,
  ]
}

# Daily at 02:00 Singapore time. Cloud Scheduler starts the job through the Cloud Run Admin
# API, which takes an OAuth access token, not an OIDC identity token.
resource "google_cloud_scheduler_job" "freshness_refresh" {
  count       = local.corpus_refresh_enabled ? 1 : 0
  name        = "compliance-freshness-refresh"
  description = "Daily refresh of expired regulatory sources (7-day TTL, P-07)."
  schedule    = "0 2 * * *"
  time_zone   = "Asia/Singapore"
  region      = var.region
  project     = var.project_id

  attempt_deadline = "320s"

  retry_config {
    retry_count = 3
  }

  http_target {
    http_method = "POST"
    uri         = "https://run.googleapis.com/v2/projects/${var.project_id}/locations/${var.region}/jobs/${google_cloud_run_v2_job.freshness_refresh[0].name}:run"

    oauth_token {
      service_account_email = google_service_account.scheduler[0].email
      scope                 = "https://www.googleapis.com/auth/cloud-platform"
    }
  }

  depends_on = [google_cloud_run_v2_job_iam_member.scheduler_invoke]
}

# run.jobs.run on this job only.
resource "google_cloud_run_v2_job_iam_member" "scheduler_invoke" {
  count    = local.corpus_refresh_enabled ? 1 : 0
  name     = google_cloud_run_v2_job.freshness_refresh[0].name
  location = var.region
  project  = var.project_id
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.scheduler[0].email}"
}
