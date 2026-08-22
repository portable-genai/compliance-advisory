# variables.tf — The only knobs. Everything else is a concrete in-region value.
#
# General Principle map:
#   P-03 (residency): `region` is SELECTED AT DEPLOY TIME and validated against
#         `allowed_regions`, the residency allowlist, so a caller fails fast rather
#         than deploying to an unvetted, out-of-jurisdiction region. Both default to
#         Singapore; deploying elsewhere means setting BOTH, which is the deliberate
#         residency review.
#   P-08 (auditability/retention): `retention_days` is a Terraform variable (the
#         WORM bucket lock is irreversible, so retention must be deliberate).
#
# Per the build contract, ONLY project_id and a couple of genuinely per-tenant
# values (org/billing ids, the AlloyDB app password, the VPC-SC toggle) are
# variables. All service identifiers, locations, and template names are concrete.

variable "project_id" {
  description = "Target GCP project id (required). Single-tenant, Singapore-resident."
  type        = string
}

variable "allowed_regions" {
  description = <<-EOT
    Residency allowlist: the regions this deployment may be created in (P-03). The region is
    chosen at deploy time (var.region) and validated against this list to FAIL FAST, so an
    operator cannot accidentally deploy to an unvetted region. The list also generates the
    gcp.resourceLocations Org Policy, so the allowlist cannot be enforced in one place and
    forgotten in the other. Extending it is the deliberate residency review point: confirm the
    regional service availability and your residency obligations there first.
  EOT
  type        = list(string)
  default     = ["asia-southeast1"]

  validation {
    condition     = length(var.allowed_regions) > 0
    error_message = "allowed_regions must list at least one residency-approved region."
  }
}

variable "region" {
  description = <<-EOT
    Deployment region, SELECTED AT DEPLOY TIME. Keeps the Singapore default below but is
    overridable. Validated against var.allowed_regions so an unapproved region fails fast at
    `terraform plan` rather than deploying data out of jurisdiction (P-03).
  EOT
  type        = string
  default     = "asia-southeast1"

  validation {
    condition     = contains(var.allowed_regions, var.region)
    error_message = "region must be one of var.allowed_regions (residency allowlist). Add it there first if that region is approved for this workload (P-03)."
  }
}

variable "zone" {
  description = "Default zone within Singapore for zonal resources."
  type        = string
  default     = "asia-southeast1-a"
}

variable "retention_days" {
  description = "WORM audit-log retention in days. Default ~7 years. Lock is irreversible."
  type        = number
  default     = 2557 # ~7 years; mirrors config/settings.yaml logging.retention_days

  validation {
    condition     = var.retention_days >= 2557
    error_message = "Compliance retention must be at least 2557 days (~7 years) (P-08)."
  }
}

variable "org_id" {
  description = "Organization id — required for Org Policy and Access Context Manager."
  type        = string
}

variable "billing_account" {
  description = "Billing account id (used by Assured Workloads / FinOps tagging)."
  type        = string
  default     = ""
}

variable "access_policy_id" {
  description = <<-EOT
    Existing Access Context Manager policy id (numeric, no prefix) for the org.
    Required when enable_vpc_sc = true; the service perimeter is created under it.
    Create once per org with:
      gcloud access-context-manager policies create \
        --organization=ORG_ID --title="sg-residency"
  EOT
  type        = string
  default     = ""
}

variable "alloydb_password" {
  description = "Initial password for the AlloyDB application user (sensitive, per-tenant)."
  type        = string
  sensitive   = true
}

variable "vpc_network_name" {
  description = "Name of the VPC that hosts the private AlloyDB instance and PSA range."
  type        = string
  default     = "compliance-vpc"
}

variable "enable_vpc_sc" {
  description = "Create the VPC Service Controls perimeter around the AI/data APIs (P-03)."
  type        = bool
  default     = true
}

variable "agent_runtime_refresh_url" {
  description = <<-EOT
    HTTPS endpoint the freshness scheduler POSTs to (the deployed app's
    /corpus/refresh route on Cloud Run / Agent Runtime). Set after the app
    is deployed; the Cloud Scheduler job below targets it (7-day TTL refresh).
  EOT
  type        = string
  default     = ""
}
