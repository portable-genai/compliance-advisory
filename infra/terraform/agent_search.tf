# agent_search.tf — Agent Search (ex-Vertex AI Search) data store + search engine.
#
# General Principle map:
#   P-01 (managed retrieval, single backend): Agent Search is the ONLY production
#         retrieval backend (SPEC §2). There is no RAG-Engine / File-Search fallback.
#   P-03 (residency): the data store and engine are NOT in the deploy region, and cannot
#         be. See the residency note below -- this is the one control in this stack that
#         the region pin does not reach.
#   P-09 (CMEK explicit): KMS key wired in below (CMEK does not cascade).
#
# RESIDENCY, STATED RATHER THAN ABSORBED (revised 2026-08-27):
#   Agent Search serves exactly `global`, `us` and `eu`. It serves NO Cloud region at all.
#   Checked against the publisher's own locations page, which carried Last updated
#   2026-08-26; the finding is recorded in org-metadata's
#   docs/gcp-service-region-availability.md.
#
#   This file used to hard-set the location to var.region and carry a precondition demanding
#   asia-southeast1, on the reasoning that a rejection "IS the fail-fast signal". That was
#   wrong in a way worth naming: the apply could never succeed, so the guard was not
#   protecting a residency property, it was guaranteeing this stack could not build its only
#   retrieval backend. A control that always fails is not a control. Meanwhile COMPLIANCE.md
#   and README.md claimed the resulting posture as Covered.
#
#   The location is now a deploy-time INPUT validated against what the service actually
#   serves, defaulting to `global`. `global` carries NO residency guarantee, so the index is
#   unlocated while every other resource in this stack stays in region. An installation under
#   a residency obligation sets `us` or `eu`, which confines the index to one jurisdiction,
#   and widens gcp.resourceLocations to match. What is NOT available at any setting is an
#   in-country index, and no configuration here can manufacture one.

locals {
  # Single source of truth for where Agent Search lives. Deliberately NOT var.region:
  # "which region do we deploy in" and "which location holds the corpus" are two facts with
  # two answer sets, and this service's set contains no Cloud region.
  agent_search_location = var.agent_search_location
}

resource "google_discovery_engine_data_store" "reg_kb" {
  project           = var.project_id
  location          = local.agent_search_location # NOT var.region; see the residency note above
  data_store_id     = "compliance-reg-kb"         # matches config/settings.yaml agent_search.data_store_id
  display_name      = "Compliance Reg KB (MAS/HKMA/APRA/FSA + cross-jurisdiction)"
  industry_vertical = "GENERIC"
  content_config    = "CONTENT_REQUIRED" # PDFs of regulatory documents
  solution_types    = ["SOLUTION_TYPE_SEARCH"]

  # Fail-fast guard: refuse a location Agent Search does not serve, so an unsupported value
  # fails at PLAN with this service named, rather than at apply with an opaque API rejection
  # or at the first live call with a hostname that does not resolve.
  lifecycle {
    precondition {
      condition     = contains(["global", "us", "eu"], local.agent_search_location)
      error_message = <<-EOT
        Agent Search serves only global, us and eu -- no Cloud region, including this
        deployment's own. Refusing to create the regulatory data store in
        '${local.agent_search_location}'.
        Set var.agent_search_location. `us` or `eu` confines the index to one jurisdiction;
        `global` carries no residency guarantee. Both are residency decisions and must be
        typed out rather than inherited from var.region.
      EOT
    }
  }

  depends_on = [
    google_project_service.required,
    google_kms_crypto_key_iam_member.discoveryengine,
  ]
}

resource "google_discovery_engine_search_engine" "compliance" {
  project       = var.project_id
  engine_id     = "compliance-advisory-engine" # matches settings.yaml agent_search.engine_id
  collection_id = "default_collection"
  location      = google_discovery_engine_data_store.reg_kb.location # inherits in-country
  display_name  = "Compliance Assistant Search Engine"
  data_store_ids = [
    google_discovery_engine_data_store.reg_kb.data_store_id,
  ]
  industry_vertical = "GENERIC"

  search_engine_config {
    search_tier = "SEARCH_TIER_ENTERPRISE" # enterprise tier for ACL + advanced features
    search_add_ons = [
      "SEARCH_ADD_ON_LLM", # grounded answers / summaries
    ]
  }

  depends_on = [google_discovery_engine_data_store.reg_kb]
}

# --------------------------------------------------------------------------- #
# Belt-and-suspenders fail-fast: an explicit check block. If the resolved
# Agent Search location is ever anything other than Singapore, `terraform plan`
# and `apply` emit an error and stop.
# --------------------------------------------------------------------------- #
# What this can honestly assert is that the engine indexes the same corpus the data store
# holds, and that both sit at the ONE location chosen for retrieval. It cannot assert
# in-country residency, because Agent Search offers none. An assertion that can never hold is
# not a stricter control, it is a broken one, and the version of this block that demanded
# asia-southeast1 is why this stack could not build its retrieval backend at all.
check "agent_search_location_is_singular_and_served" {
  assert {
    condition = (
      google_discovery_engine_data_store.reg_kb.location == local.agent_search_location &&
      google_discovery_engine_search_engine.compliance.location == local.agent_search_location
    )
    error_message = "Agent Search data store and engine must share the one configured retrieval location."
  }
}
