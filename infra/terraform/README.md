# `compliance-advisory`: Terraform support stack

This module provisions the managed services `compliance-advisory` binds under its `gcp` profile,
in one project and one region (`region`, validated against `allowed_regions`). It does not deploy
the API or the console: an embedding host such as `journey-portal` runs those from digest-pinned
images, and this stack supplies what they call.

| Concern | Resource(s) | File |
|---|---|---|
| Retrieval | Agent Search data store + engine at `agent_search_location` (`global`, `us` or `eu`) | `agent_search.tf` |
| Freshness ledger + horizon tracker (`gcp` profile) | Firestore Native database `compliance-advisory-<region>` and its one composite index | `firestore.tf` |
| Freshness ledger + horizon tracker (`platform` profile) | AlloyDB cluster, primary, VPC and PSA peering. **Off** unless `enable_alloydb` | `alloydb.tf` |
| Guardrail | Model Armor template | `model_armor.tf` |
| PII redaction | DLP inspect and de-identify templates | `dlp.tf` |
| Audit | Cloud Logging bucket and sink; the project audit config | `logging_worm.tf` |
| Scheduled corpus refresh | Cloud Run job and Cloud Scheduler trigger, when `corpus_refresh_image` is set | `scheduler.tf` |
| CMEK | One regional key ring and key, one grant per service agent | `kms.tf` |
| Identities | Serving, pipeline and agent-runtime service accounts; grants for an embedding host's identity | `iam.tf`, `agent_runtime.tf` |
| Residency guardrails | Org Policies, VPC-SC perimeter | `org_policy.tf`, `vpc_sc.tf` |
| Control-posture sources | Cloud Asset feed and its Pub/Sub topic; Assured Workloads (opt-in) | `scc_asset_feed.tf`, `assured_workloads.tf` |

## The optional controls, and how each defaults

A reversible control defaults to its strict setting, so a fork inherits it, and a deployment that
declines one says so in its own tfvars. A control that cannot be undone never arrives by default.

| Variable | Default | Setting the other value |
|---|---|---|
| `worm_locked` | **none: the plan refuses until it is stated** | `true` locks the audit bucket irreversibly and needs `retention_days >= 2557`; `false` keeps it editable and the stack destroyable |
| `enable_assured_workloads` | `false` | `true` creates a workload folder under the organization that `terraform destroy` cannot simply remove |
| `enable_alloydb` | `false` | `true` creates an AlloyDB primary that bills by the hour, for the `platform` profile |
| `manage_org_policies` | `true` | `false` where another stack owns the project's Org Policies |
| `manage_audit_config` | `true` | `false` where another stack owns the project's audit config; the sink then routes only this app's own log |
| `enable_vpc_sc` | `true` | `false` where another stack owns the project's perimeter |
| `model_armor_full_capabilities` | `true` | `false` in a region that does not serve the malicious-URI filter, such as `asia-southeast1` |
| `model_armor_log_sanitize_operations` | `true` | `false` where no locked bucket holds the screened text |
| `enable_posture_feed` | `true` | `false` where a project-wide feed would stream siblings' posture that nothing here reads |
| `firestore_cmek_enabled` | `true` | `false` on a project Google has not admitted to Firestore CMEK; fixed when the database is created |
| `firestore_delete_protection_enabled`, `firestore_pitr_enabled` | `true` | `false` for a stack that must stay destroyable and holds no journey it needs to recover |
| `cloud_run_deletion_protection` | `true` | `false` for a destroyable stack |

[`tests/posture.tftest.hcl`](tests/posture.tftest.hcl) proves each row at plan against mock
providers, including that an unstated lock refuses to plan: `make tf-test`.

## Idle cost

With `enable_alloydb = false` nothing here bills by the hour. While nobody uses the service what
remains is storage and a handful of fixed items: the CMEK key version, Agent Search index storage,
audit log storage beyond the included retention, Firestore storage for a few hundred small
documents, and, when the refresh is scheduled, one Cloud Scheduler job and one Cloud Run job
execution a day.

## Apply

```bash
cp terraform.tfvars.example terraform.tfvars   # then state every control in the table above
terraform init -backend-config=bucket=<state-bucket> -backend-config=prefix=compliance-advisory
terraform plan
terraform apply
```

Beside an embedding host, in two passes, because the host mints the API's runtime identity:

1. Apply this stack with `additional_serving_service_accounts = []`.
2. Build and push both images: the API from the root `Dockerfile`, the console from
   `ui/Dockerfile` with `NEXT_PUBLIC_BASE_PATH=/apps/compliance-advisory` and
   `NEXT_PUBLIC_API_BASE=/apps/compliance-advisory/api`.
3. Apply the host, which creates the Cloud Run services and their identities.
4. Apply this stack again with the host's API identity in `additional_serving_service_accounts`,
   and the API image digest in `corpus_refresh_image` to schedule the corpus refresh. Without
   step 4 the API starts, authenticates, and fails on its first Firestore, DLP or Model Armor call.

With `enable_vpc_sc = true`, apply with it `false` first, add the operator identity to an access
level, then re-apply with it `true`: a perimeter created before the resources denies the calls
that create them.

## Region and residency

`region` is validated against `allowed_regions`, and every regional resource uses it. Agent Search
is the exception no region setting reaches: it serves `global`, `us` and `eu` and no Cloud region,
so `agent_search_location` is a separate input. A project whose residency policy refuses `global`
sets `us` or `eu`, and the API's `COMPLIANCE_AGENT_SEARCH_LOCATION` must name the same value.
Where this stack writes `gcp.resourceLocations`, widen `resource_location_values` to admit that
location; `in:us-locations` keeps one jurisdiction.

## CMEK does not cascade

Every data-bearing service that supports CMEK is told to use the one regional key in its own file,
and its service agent gets its own grant in `kms.tf`: the audit bucket, Firestore when
`firestore_cmek_enabled`, AlloyDB when `enable_alloydb`, Vertex AI and Discovery Engine. The Agent
Search data store itself is created without a key, so with `manage_org_policies = true` the
`gcp.restrictNonCmekServices` policy, which names Discovery Engine, refuses it. That combination
has not been applied anywhere; a stack that owns its project's policies has to resolve it first.
