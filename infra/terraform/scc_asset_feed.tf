# scc_asset_feed.tf: Cloud Asset Inventory project feed for the live control posture.
#
# Security Command Center supplies findings (the ENABLED / MISCONFIGURED signal) and is read
# directly by SccControlInventoryAdapter. This feed streams the realised configuration of the
# posture-relevant resources to a regional Pub/Sub topic.
#
# Counted on var.enable_posture_feed. The feed covers the whole PROJECT, so in a shared project
# it streams every sibling's policies, keys, perimeters and buckets too, and nothing in this
# service subscribes to the topic today; a shared-project deployment may decline it.
#
# General Principle map:
#   P-09 (defence in depth / continuous assurance): continuous posture, not a snapshot.
#   P-03 (residency): the topic persists messages in var.region only.

resource "google_pubsub_topic" "asset_feed" {
  count   = var.enable_posture_feed ? 1 : 0
  name    = "compliance-asset-feed"
  project = var.project_id

  message_storage_policy {
    allowed_persistence_regions = [var.region]
  }

  depends_on = [google_project_service.required]
}

resource "google_cloud_asset_project_feed" "posture" {
  count        = var.enable_posture_feed ? 1 : 0
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
      topic = google_pubsub_topic.asset_feed[0].id
    }
  }

  depends_on = [google_project_service.required]
}
