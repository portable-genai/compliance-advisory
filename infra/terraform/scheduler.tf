# scheduler.tf — Cloud Scheduler job that refreshes the 7-day corpus.
#
# General Principle map:
#   P-07 (freshness / fetch-at-runtime): the regulatory corpus has a 7-day TTL
#         (settings.yaml corpus.ttl_days). This daily cron triggers an out-of-band
#         refresh of expiring sources so reads rarely have to re-fetch inline. It
#         POSTs to the app's /corpus/refresh route (which runs the same pipeline as
#         CorpusIngestionPort + CorpusLedgerPort), or invokes the Cloud Run job.
#   P-03 (residency): the scheduler and the Cloud Run job both run in asia-southeast1.
#
# Two wiring options are provided:
#   (a) HTTP target hitting POST {agent_runtime_refresh_url} on the running app.
#   (b) A Cloud Run *job* the scheduler can run instead (commented OIDC variant).
# The job runs daily at 02:00 Singapore time (low-traffic window).

# Dedicated SA the scheduler uses to authenticate to the app (OIDC).
resource "google_service_account" "scheduler" {
  account_id   = "compliance-freshness-cron"
  display_name = "C1 corpus freshness scheduler"
  project      = var.project_id

  depends_on = [google_project_service.required]
}

# The Cloud Run job that performs the refresh (image built/pushed by CI).
# verify: https://registry.terraform.io/providers/hashicorp/google/latest/docs/resources/cloud_run_v2_job
resource "google_cloud_run_v2_job" "freshness_refresh" {
  name     = "compliance-freshness-refresh"
  location = var.region # asia-southeast1 (P-03)
  project  = var.project_id

  template {
    template {
      service_account = google_service_account.pipeline.email

      containers {
        # Placeholder image; CI publishes the real one to Artifact Registry in-region.
        image   = "asia-southeast1-docker.pkg.dev/${var.project_id}/compliance/refresh:latest"
        command = ["python", "-m", "compliance_advisory.pipelines.refresh"]

        env {
          name  = "COMPLIANCE_PROFILE"
          value = "gcp"
        }
        env {
          name  = "GOOGLE_CLOUD_PROJECT"
          value = var.project_id
        }
        # Control-mapping posture settings merged in from the Rsk2 module (COMPLIANCE_*).
        # These replace the retired CONTROL_MAPPING_* vars; empty when org_id is unset.
        env {
          name  = "COMPLIANCE_SCC_PARENT"
          value = var.org_id != "" ? "organizations/${var.org_id}" : ""
        }
        env {
          name  = "COMPLIANCE_ASSURED_WORKLOAD"
          value = google_assured_workloads_workload.sg.name
        }
      }
    }
  }

  # The image is provisioned by CI; ignore drift on it here.
  lifecycle {
    ignore_changes = [template[0].template[0].containers[0].image]
  }

  depends_on = [google_project_service.required]
}

# Daily cron. Hits the app's POST /corpus/refresh endpoint via authenticated OIDC.
resource "google_cloud_scheduler_job" "freshness_refresh" {
  name        = "compliance-freshness-refresh"
  description = "Daily refresh of expiring regulatory sources (7-day TTL, P-07)."
  schedule    = "0 2 * * *" # 02:00 daily
  time_zone   = "Asia/Singapore"
  region      = var.region
  project     = var.project_id

  attempt_deadline = "320s"

  retry_config {
    retry_count = 3
  }

  http_target {
    http_method = "POST"
    # Falls back to the Cloud Run job's run endpoint if the app URL is unset.
    uri = var.agent_runtime_refresh_url != "" ? var.agent_runtime_refresh_url : (
      "https://${var.region}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${var.project_id}/jobs/${google_cloud_run_v2_job.freshness_refresh.name}:run"
    )

    headers = {
      "Content-Type" = "application/json"
    }
    body = base64encode(jsonencode({ reason = "scheduled-ttl-refresh" }))

    # Authenticate with the scheduler SA (the app/job validates the OIDC token).
    oidc_token {
      service_account_email = google_service_account.scheduler.email
      audience              = var.agent_runtime_refresh_url != "" ? var.agent_runtime_refresh_url : "https://${var.region}-run.googleapis.com/"
    }
  }

  depends_on = [google_cloud_run_v2_job.freshness_refresh]
}

# Let the scheduler SA invoke the Cloud Run job (the fallback path above).
resource "google_cloud_run_v2_job_iam_member" "scheduler_invoke" {
  name     = google_cloud_run_v2_job.freshness_refresh.name
  location = var.region
  project  = var.project_id
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.scheduler.email}"
}
