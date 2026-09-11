# org_policy.tf: project Org Policy constraints enforcing residency. Declinable.
#
# General Principle map:
#   P-03 (data residency, defence in depth): even if someone hand-edits a resource, these
#         policies REJECT resources outside the allowlist. gcp.resourceLocations is the master
#         residency control; the rest harden the project (no VM external IPs, uniform bucket
#         access, CMEK required for the data-bearing services).
#
# Every policy here is counted on var.manage_org_policies. They are project-level and
# last-writer-wins: in a project whose policies another stack already owns, applying these
# narrows that stack's boundary to this one's and breaks whichever application needed it
# wider. Such a deployment sets manage_org_policies = false and inherits the project's policy.
# verify: https://registry.terraform.io/providers/hashicorp/google/latest/docs/resources/org_policy_policy

# Master residency policy: GENERATED from var.allowed_regions, so the allowlist that gates
# var.region at plan time is the same list the Org Policy enforces at create time.
resource "google_org_policy_policy" "resource_locations" {
  count  = var.manage_org_policies ? 1 : 0
  name   = "projects/${var.project_id}/policies/gcp.resourceLocations"
  parent = "projects/${var.project_id}"

  spec {
    rules {
      values {
        # e.g. in:asia-southeast1-locations confines resources to the Singapore region.
        # var.resource_location_values overrides this only where a required service has no
        # single-region presence (Agent Search has none at all). See that variable: widening is
        # a jurisdiction statement, not an exception list.
        allowed_values = length(var.resource_location_values) > 0 ? var.resource_location_values : [for r in var.allowed_regions : "in:${r}-locations"]
      }
    }
  }

  depends_on = [google_project_service.required]
}

# Disable VM external IPs: keep the data plane private (P-05).
resource "google_org_policy_policy" "no_external_ip" {
  count  = var.manage_org_policies ? 1 : 0
  name   = "projects/${var.project_id}/policies/compute.vmExternalIpAccess"
  parent = "projects/${var.project_id}"

  spec {
    rules {
      deny_all = "TRUE"
    }
  }

  depends_on = [google_project_service.required]
}

# Require uniform bucket-level access (no per-object ACL exfiltration paths).
resource "google_org_policy_policy" "uniform_bucket_access" {
  count  = var.manage_org_policies ? 1 : 0
  name   = "projects/${var.project_id}/policies/storage.uniformBucketLevelAccess"
  parent = "projects/${var.project_id}"

  spec {
    rules {
      enforce = "TRUE"
    }
  }

  depends_on = [google_project_service.required]
}

# Require CMEK for the data-bearing services (no Google-managed-key fallback). AlloyDB is named
# only when this stack creates it. Firestore is not named: its CMEK is allowlist-gated by
# Google, so requiring it would refuse the database on any project not yet admitted.
resource "google_org_policy_policy" "restrict_cmek_projects" {
  count  = var.manage_org_policies ? 1 : 0
  name   = "projects/${var.project_id}/policies/gcp.restrictNonCmekServices"
  parent = "projects/${var.project_id}"

  spec {
    rules {
      values {
        denied_values = concat(
          ["discoveryengine.googleapis.com", "logging.googleapis.com"],
          var.enable_alloydb ? ["alloydb.googleapis.com"] : [],
        )
      }
    }
  }

  depends_on = [google_project_service.required]
}
