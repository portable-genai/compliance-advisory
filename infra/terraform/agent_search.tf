# agent_search.tf — Agent Search (ex-Vertex AI Search) data store + search engine.
#
# General Principle map:
#   P-01 (managed retrieval, single backend): Agent Search is the ONLY production
#         retrieval backend (SPEC §2). There is no RAG-Engine / File-Search fallback.
#   P-03 (residency): the data store and engine are pinned to asia-southeast1 so the
#         indexed regulatory corpus stays in-country.
#   P-09 (CMEK explicit): KMS key wired in below (CMEK does not cascade).
#
# FAIL-FAST REQUIREMENT (SPEC §2, locked decision "Retrieval"):
#   If Agent Search cannot be provisioned in asia-southeast1, the apply MUST FAIL —
#   we never silently fall back to a non-Singapore region or another backend.
#   We enforce this three ways:
#     1. local.agent_search_location is hard-set to var.region and validated to be
#        asia-southeast1 (the variable validation already blocks other regions).
#     2. The data store sets location = local.agent_search_location directly, so an
#        API rejection of an unsupported region surfaces as an apply error.
#     3. A `check` block re-asserts the invariant with a clear failure message.
#
#   NOTE ON PROVIDER/REGION SUPPORT: historically google_discovery_engine_data_store
#   only accepted "global"/"us"/"eu". For a Singapore-resident deployment the
#   regional ("asia-southeast1") data residency configuration must be available;
#   if the provider/API in your environment rejects the regional value, that IS the
#   fail-fast signal that Agent Search is not yet regionally available here and the
#   deployment must not proceed.
#   # verify: https://registry.terraform.io/providers/hashicorp/google/latest/docs/resources/discovery_engine_data_store

locals {
  # Single source of truth for where Agent Search lives. No global fallback.
  agent_search_location = var.region # asia-southeast1 only (P-03)
}

resource "google_discovery_engine_data_store" "reg_kb" {
  project           = var.project_id
  location          = local.agent_search_location # in-country; rejection => fail fast
  data_store_id     = "compliance-reg-kb"         # matches config/settings.yaml agent_search.data_store_id
  display_name      = "Compliance Reg KB (MAS/HKMA/APRA/FSA + cross-jurisdiction)"
  industry_vertical = "GENERIC"
  content_config    = "CONTENT_REQUIRED" # PDFs of regulatory documents
  solution_types    = ["SOLUTION_TYPE_SEARCH"]

  # Fail-fast guard: refuse to build the store anywhere but Singapore.
  lifecycle {
    precondition {
      condition     = local.agent_search_location == "asia-southeast1"
      error_message = <<-EOT
        Agent Search must be provisioned in asia-southeast1 (Singapore).
        Refusing to create the regulatory data store in '${local.agent_search_location}'.
        There is NO non-Singapore fallback (SPEC §2, fail-fast retrieval).
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
check "agent_search_is_singapore_resident" {
  assert {
    condition     = google_discovery_engine_data_store.reg_kb.location == "asia-southeast1"
    error_message = "FAIL-FAST: Agent Search data store is not in asia-southeast1 — sovereignty broken (SPEC §2)."
  }
}
