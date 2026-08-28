# org_policy.tf — Org Policy constraints enforcing Singapore residency.
#
# General Principle map:
#   P-03 (data residency, defence in depth): even if someone hand-edits a resource,
#         these org policies REJECT the creation of resources outside Singapore.
#         gcp.resourceLocations is the master residency control; the rest harden
#         the project (no public IPs on VMs, uniform bucket access, restrict
#         external IPs) so data and compute stay in-country and private (P-05).
#
# Scoped to the project via google_project. To enforce org-wide, move these to an
# org-level google_org_policy_policy with parent = "organizations/${var.org_id}".
# verify: https://registry.terraform.io/providers/hashicorp/google/latest/docs/resources/org_policy_policy

# Master residency policy: GENERATED from var.allowed_regions, so the allowlist that gates
# var.region at plan time is the same list the Org Policy enforces at create time.
resource "google_org_policy_policy" "resource_locations" {
  name   = "projects/${var.project_id}/policies/gcp.resourceLocations"
  parent = "projects/${var.project_id}"

  spec {
    rules {
      values {
        # e.g. in:asia-southeast1-locations confines resources to the Singapore region.
        # var.resource_location_values overrides this only where a required service has no
        # single-region presence (Agent Search has none at all; Document AI has none until
        # in-region access is granted). See that variable: widening is a jurisdiction
        # statement, not an exception list.
        allowed_values = length(var.resource_location_values) > 0 ? var.resource_location_values : [for r in var.allowed_regions : "in:${r}-locations"]
      }
    }
  }

  depends_on = [google_project_service.required]
}

# Disable VM external IPs — keep the data plane private (P-05).
resource "google_org_policy_policy" "no_external_ip" {
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
  name   = "projects/${var.project_id}/policies/storage.uniformBucketLevelAccess"
  parent = "projects/${var.project_id}"

  spec {
    rules {
      enforce = "TRUE"
    }
  }

  depends_on = [google_project_service.required]
}

# Restrict which CMEK projects can be used — keep crypto in this project/region.
resource "google_org_policy_policy" "restrict_cmek_projects" {
  name   = "projects/${var.project_id}/policies/gcp.restrictNonCmekServices"
  parent = "projects/${var.project_id}"

  spec {
    rules {
      # Require CMEK for the data-bearing services (no Google-managed-key fallback).
      values {
        denied_values = [
          "alloydb.googleapis.com",
          "discoveryengine.googleapis.com",
          "logging.googleapis.com",
        ]
      }
    }
  }

  depends_on = [google_project_service.required]
}
