# variables.tf: the knobs. Everything else is a concrete in-region value.
#
# General Principle map:
#   P-03 (residency): `region` is SELECTED AT DEPLOY TIME and validated against
#         `allowed_regions`, the residency allowlist, so a caller fails fast rather
#         than deploying to an unvetted, out-of-jurisdiction region. Both default to
#         Singapore; deploying elsewhere means setting BOTH, which is the deliberate
#         residency review.
#   P-08 (auditability/retention): `worm_locked` and `retention_days` decide the audit
#         bucket, and the lock is irreversible, so it is never inherited.
#
# The optional controls default in two different ways, on purpose:
#
#   * A REVERSIBLE control defaults to its compliant setting, so a fork inherits the strict
#     posture, and a deployment that declines one says so in its own tfvars:
#     manage_org_policies, manage_audit_config, enable_vpc_sc, model_armor_full_capabilities,
#     model_armor_log_sanitize_operations, enable_posture_feed and the three Firestore
#     protections.
#   * A control that cannot be undone never arrives by default. worm_locked has no default at
#     all and refuses a plan until it is stated; enable_assured_workloads is off. So is
#     enable_alloydb, which is reversible but bills by the hour whether or not anyone uses it.

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

variable "org_id" {
  description = "Organization id: the SCC parent the posture adapter reads, and the parent of the Assured Workloads workload when enable_assured_workloads = true."
  type        = string
}

variable "billing_account" {
  description = "Billing account id. Required only when enable_assured_workloads = true."
  type        = string
  default     = ""
}

# --------------------------------------------------------------------------- #
# Audit log bucket (logging_worm.tf)
# --------------------------------------------------------------------------- #
variable "worm_locked" {
  description = <<-EOT
    Lock the audit log bucket (P-08). NO DEFAULT: a plan refuses until this is stated.

    ###########################################################################
    # LOCKING IS IRREVERSIBLE. A locked bucket's retention can never be       #
    # shortened and the bucket can never be deleted before every entry ages   #
    # out, not with project-owner rights and not by destroying this stack.    #
    ###########################################################################

    true is the posture of a system of record: the audit trail is Write-Once-Read-Many only
    when locked, and retention_days must then be at least 2557. false keeps the bucket and its
    retention editable and the stack destroyable, which is the posture of a reference or
    shared-project deployment and is NOT compliant for production.

    There is no default because either one would be a decision nobody made. A default of true
    locks a bucket for seven years on a deployment whose tfvars said nothing about it; a
    default of false hands a fork a mutable audit trail it never asked for.
  EOT
  type        = bool
  default     = null
  nullable    = true

  validation {
    condition     = var.worm_locked != null
    error_message = "worm_locked must be stated: true locks the audit bucket IRREVERSIBLY, false keeps it editable and the stack destroyable. Neither is inherited."
  }
}

variable "retention_days" {
  description = <<-EOT
    Audit log bucket retention, in days. At least 2557 (~7 years) when worm_locked = true,
    because a locked trail is the compliance record (P-08). Any value from 1 while the bucket
    stays unlocked: an unlocked window can be shortened or lengthened later, so it is an
    operational choice rather than a commitment.
  EOT
  type        = number
  default     = 2557

  validation {
    condition     = var.retention_days >= 1 && (var.worm_locked != true || var.retention_days >= 2557)
    error_message = "retention_days must be at least 1, and at least 2557 (~7 years) when worm_locked = true (P-08)."
  }
}

variable "manage_audit_config" {
  type        = bool
  default     = false
  description = <<-EOT
    Whether THIS stack writes the project's data-access audit configuration.

    False by default, and the default is the point. `google_project_iam_audit_config` is
    AUTHORITATIVE for the service it names, so a second stack declaring `allServices` does
    not add to that configuration, it REPLACES it, and a stack asking for DATA_READ and
    DATA_WRITE removes an ADMIN_READ a sibling enabled. Terraform reports that as a create
    rather than a change, because this stack holds no prior state for a resource that is
    nonetheless already live. Nearly every stack in this fleet carries this resource and one
    project hosts many of them, so a default of true is a race whose winner is whichever
    stack applied last.

    Data-access logs are also the highest-volume class Cloud Logging ingests, and nothing in
    the reference deployment reads them.

    Set true in exactly one stack per project, in that deployment's own tfvars, where the
    project genuinely wants data-access logging on.
  EOT
}

# --------------------------------------------------------------------------- #
# Project guardrails (org_policy.tf, vpc_sc.tf, assured_workloads.tf)
# --------------------------------------------------------------------------- #
variable "manage_org_policies" {
  type        = bool
  default     = true
  description = <<-EOT
    Whether THIS stack writes the project's Org Policies (gcp.resourceLocations,
    compute.vmExternalIpAccess, storage.uniformBucketLevelAccess and
    gcp.restrictNonCmekServices).

    True by default, because a fork deploying this app on its own project should inherit the
    residency guardrail rather than have to remember it. Set false where another stack in the
    same project already owns them: two stacks declaring the same project-level policy is a
    last-writer-wins race, and the loser is whichever application needed the wider boundary.

    That is not hypothetical. This stack derives the STRICTEST form from its own allowlist, so
    applying it into a shared project narrows `gcp.resourceLocations` to that region and breaks
    every sibling that reaches another one, and its restrictNonCmekServices refuses any
    sibling's non-CMEK resource in the services it names. Nothing in this stack's plan says so.
  EOT
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

  validation {
    condition     = !var.enable_vpc_sc || length(var.access_policy_id) > 0
    error_message = "enable_vpc_sc = true requires access_policy_id. Supply the org's Access Context Manager policy id, or set enable_vpc_sc = false where this project's perimeter is owned elsewhere."
  }
}

variable "enable_vpc_sc" {
  description = <<-EOT
    Create a VPC Service Controls perimeter around the AI/data APIs (P-03). True by default.

    Set false where another stack already owns this project's perimeter. A second REGULAR
    perimeter over the same project enforces where the established one may only observe, and
    enabling it before the resources in this stack exist denies the calls that create them.
  EOT
  type        = bool
  default     = true
}

variable "enable_assured_workloads" {
  description = <<-EOT
    Create an Assured Workloads REGIONS_CONTROL workload under the organization. Default false.

    It is the one resource in this stack that `terraform destroy` cannot simply undo: the
    workload creates a folder under the organization with its own compliance regime, which
    Google deletes only once it is empty and only on its own terms. A control like that never
    arrives because a deployment said nothing. Turn it on deliberately, with billing_account
    set, in a project that is meant to live under that regime. The posture adapter reads its
    status only when COMPLIANCE_ASSURED_WORKLOAD names it.
  EOT
  type        = bool
  default     = false
}

variable "enable_posture_feed" {
  description = <<-EOT
    Create the Cloud Asset Inventory project feed and the regional Pub/Sub topic it publishes
    to (scc_asset_feed.tf). True by default.

    The feed streams every org-policy, key, perimeter, log-bucket and workload change in the
    PROJECT, not just this application's. In a shared project that is every sibling's posture
    too, and nothing in this service subscribes to the topic today (the control inventory
    adapter reads Security Command Center and Asset Inventory directly), so a shared-project
    deployment may decline it.
  EOT
  type        = bool
  default     = true
}

# --------------------------------------------------------------------------- #
# Guardrail (model_armor.tf)
# --------------------------------------------------------------------------- #
variable "model_armor_full_capabilities" {
  type        = bool
  default     = true
  description = <<-EOT
    Whether the guardrail template asks for the capabilities that are not served in every
    region, today the malicious-URI filter.

    True by default, because a deployment should get the whole guardrail unless it has a reason
    not to. asia-southeast1 does not serve it, and Model Armor does not degrade: it refuses the
    template with CAPABILITY_NOT_SUPPORTED, so the stack does not deploy at all. A deployment
    there sets this false, which narrows the guardrail and is a disclosure to record, not a
    silent downgrade.
  EOT
}

variable "model_armor_log_sanitize_operations" {
  type        = bool
  default     = true
  description = <<-EOT
    Whether Model Armor writes sanitize operations to Cloud Logging. True by default.

    Those entries carry the screened prompt and response. Here that text has already been
    through DLP de-identification, and the entries are what show an operator why a request was
    blocked. A deployment that runs no locked audit bucket to hold them may decline it, as the
    sibling support stacks in a shared project do.
  EOT
}

# --------------------------------------------------------------------------- #
# Stores (firestore.tf, alloydb.tf)
# --------------------------------------------------------------------------- #
variable "firestore_cmek_enabled" {
  type        = bool
  default     = true
  description = <<-EOT
    Encrypt the Firestore database under this stack's regional CMEK key. Default true.

    Firestore CMEK is ALLOWLIST-GATED by Google. On a project that has not been admitted, an
    unconditional cmek_config does not degrade: the apply fails. A CMEK setting is also fixed
    when the database is created, so a deployment in a project awaiting the allowlist declines
    it here, before its first apply, and discloses the gap. Every other CMEK binding in this
    stack is unconditional.
  EOT
}

variable "firestore_delete_protection_enabled" {
  type        = bool
  default     = true
  description = <<-EOT
    Firestore delete protection on the ledger and tracker database. Default true.

    It refuses `terraform destroy` of the database, which is correct where the tracked
    implementation journey is a record someone relies on. A reference deployment that has
    stated it must stay destroyable declines it rather than holding two opposite postures.
  EOT
}

variable "firestore_pitr_enabled" {
  type        = bool
  default     = true
  description = <<-EOT
    Point-in-time recovery on the Firestore database. Default true.

    PITR bills continuous backup storage for a recovery window. The ledger can be rebuilt by a
    full corpus refresh; the tracked journey cannot. A deployment that has not rehearsed a
    restore and holds no production journey may decline it and say so.
  EOT
}

variable "enable_alloydb" {
  type        = bool
  default     = false
  description = <<-EOT
    Provision the AlloyDB store for the freshness ledger and the horizon tracker: a cluster, a
    2-vCPU primary, the VPC and Private Service Access peering it needs, its API enablements,
    its CMEK grant and its IAM. Default false.

    AlloyDB bills its primary by the hour whether or not a row moves, and nothing in a
    deployment can pause it. The `gcp` profile keeps both stores in Firestore instead
    (firestore.tf), which costs nothing while idle. Set true only for an installation that
    runs the `platform` profile, which binds the AlloyDB adapters (config/settings.yaml), and
    supply alloydb_password.
  EOT
}

variable "alloydb_password" {
  description = "Initial password for the AlloyDB application user. Required when enable_alloydb = true and unused otherwise (sensitive, per-tenant; prefer TF_VAR_alloydb_password)."
  type        = string
  default     = null
  sensitive   = true

  validation {
    condition     = !var.enable_alloydb || try(length(var.alloydb_password) > 0, false)
    error_message = "enable_alloydb = true requires alloydb_password."
  }
}

variable "vpc_network_name" {
  description = "Name of the VPC that hosts the private AlloyDB instance and PSA range (created only when enable_alloydb = true)."
  type        = string
  default     = "compliance-vpc"
}

# --------------------------------------------------------------------------- #
# Retrieval (agent_search.tf, org_policy.tf)
# --------------------------------------------------------------------------- #
variable "agent_search_location" {
  description = <<-EOT
    Where the Agent Search data store and engine live. DELIBERATELY NOT var.region.

    Agent Search serves exactly `global`, `us` and `eu` and no Cloud region, so this cannot
    track the deploy region: doing so is what made the apply impossible before 2026-08-27.

    `global` (the default) carries NO residency guarantee -- the index is unlocated. `us` and
    `eu` confine it to one jurisdiction and are the stronger choice under a residency
    obligation. Whichever is chosen, gcp.resourceLocations must be wide enough to permit it,
    and the residency claim must be stated at that width rather than at var.region's. The API
    reads the same value from COMPLIANCE_AGENT_SEARCH_LOCATION.
  EOT
  type        = string
  default     = "global"

  validation {
    condition     = contains(["global", "us", "eu"], var.agent_search_location)
    error_message = "agent_search_location must be one of global, us, eu -- the only locations Agent Search serves."
  }
}

variable "resource_location_values" {
  description = <<-EOT
    Value groups for the gcp.resourceLocations Org Policy. Empty (the default) derives the
    strictest form from the deploy region: that region and its sub-locations, nothing else.

    Widen it ONLY where a service this stack genuinely needs has no presence at single-region
    granularity, and treat the width as the residency claim rather than as plumbing. Agent
    Search serves `global`, `us` and `eu` and NO Cloud region at all, so a stack that writes
    this policy and creates a data store must widen it.

    Move to the smallest value group that still describes ONE JURISDICTION -- `in:us-locations`
    keeps every resource inside the United States -- and state the residency claim at that
    granularity rather than pretending it is still single-region. NEVER list an individual
    foreign region to unblock one service: that turns a jurisdiction boundary into a list of
    exceptions nobody can reason about.
  EOT
  type        = list(string)
  default     = []

  validation {
    condition     = alltrue([for value in var.resource_location_values : startswith(value, "in:") || startswith(value, "is:")])
    error_message = "Each value must be an Org Policy location value group (in:...) or a literal location (is:...)."
  }
}

# --------------------------------------------------------------------------- #
# Identities and jobs (iam.tf, scheduler.tf)
# --------------------------------------------------------------------------- #
variable "additional_serving_service_accounts" {
  type        = list(string)
  default     = []
  description = <<-EOT
    Service-account emails, other than this stack's own serving identity, that run this
    application's API and therefore need its roles and its key.

    Exists for embedding hosts. A portal that mounts this console runs the API under a runtime
    identity of the PORTAL's making, which this stack cannot know and the serving identity's
    own grants do not cover; without this the deployed API authenticates fine and then fails on
    its first Firestore, DLP or Model Armor call. Empty by default, because an app deployed on
    its own needs none. Fill it in on a second apply, once the portal has minted the identity.
  EOT
  validation {
    condition = alltrue([
      for email in var.additional_serving_service_accounts :
      can(regex("^[a-z0-9-]+@[a-z0-9-]+\\.iam\\.gserviceaccount\\.com$", email))
    ])
    error_message = "each additional_serving_service_accounts entry must be a service-account email."
  }
}

variable "corpus_refresh_image" {
  type        = string
  default     = ""
  description = <<-EOT
    Digest-pinned image the scheduled corpus refresh job runs, or "" for no scheduled refresh.

    The API image already carries the refresh entrypoint
    (`python -m compliance_advisory.pipelines.refresh_job`), so a deployment names the same
    reviewed digest it deploys the API from. Empty creates neither the Cloud Run job nor its
    Cloud Scheduler trigger: a job cannot be created from an image that does not exist, and a
    tag is a pointer that moves without a diff.
  EOT
  validation {
    condition     = var.corpus_refresh_image == "" || can(regex("@sha256:[0-9a-f]{64}$", var.corpus_refresh_image))
    error_message = "corpus_refresh_image must be empty or pinned by digest (...@sha256:<64 hex>)."
  }
}

variable "cloud_run_deletion_protection" {
  type        = bool
  default     = true
  description = "Deletion protection on the corpus refresh Cloud Run job. True by default; a reference stack that must stay destroyable sets false deliberately."
}

variable "cmek_enabled" {
  type        = bool
  default     = false
  description = <<-EOT
    Whether this stack creates its own Cloud KMS key ring and key and binds every store, log
    bucket and revision to it. False by default, and the default is the point: a key ring can
    never be deleted, a log bucket that has CMEK can never drop it, and registries and document
    stores take their key at creation. None of that changes an answer or a screen, and every
    resource is encrypted at rest with Google-managed keys regardless. A deployment with a
    customer whose data it must be able to shred, whose key access must be audited, or whose
    keys must live in an HSM sets this true in its own tfvars BEFORE its first apply. Flipping
    it off on a stack that already applied it is refused by the keys' prevent_destroy, which is
    the right answer: the stores it bound stay bound.
  EOT
}

variable "pii_redaction_enabled" {
  description = "Switch PII redaction on the corpus refresh job (COMPLIANCE_PII_REDACTION). A cheap runtime control: on in the reference, reversible, so it takes a default."
  type        = bool
  default     = true
}
