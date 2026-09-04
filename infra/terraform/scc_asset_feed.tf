# scc_asset_feed.tf — Live control-posture sources: SCC + Cloud Asset Inventory feed.
#
# Merged in from the the cloud control-mapping toolkit control-mapping module. The compliance assistant reads the
# live control posture (not a point-in-time snapshot): Security Command Center supplies
# findings (the ENABLED / MISCONFIGURED signal) and a Cloud Asset Inventory feed streams
# the realised configuration of org-policy constraints and resources so the merged
# service always maps against current state.
#
# General Principle map:
#   P-09 (defence in depth / continuous assurance): continuous posture, not a snapshot.
#   P-03 (residency): the feed's Pub/Sub topic is regional (asia-southeast1).
#
# Security Command Center is enabled at the organization level out of band (it cannot be
# fully provisioned per-project here); this file wires the project-scoped asset feed the
# ControlInventory adapter consumes and documents the SCC parent the app reads.

# Regional Pub/Sub topic the Cloud Asset feed publishes change events to.
resource "google_pubsub_topic" "asset_feed" {
  name    = "compliance-asset-feed"
  project = var.project_id

  message_storage_policy {
    allowed_persistence_regions = [var.region] # Singapore-only persistence (P-03)
  }

  depends_on = [google_project_service.required]
}

# Stream org-policy + sovereignty-relevant resource changes to the topic. The
# SccControlInventoryAdapter reads SCC findings directly; this feed keeps the realised
# config (locations policy, KMS bindings, perimeter) continuously fresh.
resource "google_cloud_asset_project_feed" "posture" {
  project      = var.project_id
  feed_id      = "compliance-posture"
  content_type = "RESOURCE"

  asset_types = [
    "orgpolicy.googleapis.com/Policy",
    "cloudkms.googleapis.com/CryptoKey",
    "accesscontextmanager.googleapis.com/ServicePerimeter",
    "logging.googleapis.com/LogBucket",
    "assuredworkloads.googleapis.com/Workload",
  ]

  feed_output_config {
    pubsub_destination {
      topic = google_pubsub_topic.asset_feed.id
    }
  }

  depends_on = [google_project_service.required]
}
