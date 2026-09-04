# `compliance-advisory`: Terraform (Singapore-resident, sovereign deploy)

This module provisions the full **Singapore-resident** managed stack for the `compliance-advisory`. Region is **pinned to `asia-southeast1`** for every resource;
only `project_id` and a few genuinely per-tenant values are variables.

It maps directly to the pinned stack in `SPEC.md §3`:

| Concern | Resource(s) |
|---|---|
| Retrieval (Agent Search) | `agent_search.tf` |
| Reasoning/Triage models + Runtime | `agent_runtime.tf` (engine deployed via SDK) |
| Guardrail (Model Armor) | `model_armor.tf` |
| PII redaction (DLP) | `dlp.tf` |
| WORM audit (locked Cloud Logging bucket) | `logging_worm.tf` |
| Freshness ledger (AlloyDB, PRIVATE, CMEK) | `alloydb.tf` |
| Tracing (Cloud Trace) | enabled in `apis.tf`; spans from the app |
| Corpus freshness cron | `scheduler.tf` |
| CMEK | `kms.tf` |
| Residency controls | `org_policy.tf`, `vpc_sc.tf` |
| Least-privilege identities | `iam.tf`, `agent_runtime.tf` |
| Control-mapping posture sources (SCC + Cloud Asset Inventory feed) | `scc_asset_feed.tf` |
| Control-mapping sovereignty boundary (Assured Workloads) | `assured_workloads.tf` |

The control-mapping module reads the bank's own posture (Security Command Center
findings and a Cloud Asset Inventory feed) and pins it to an Assured Workloads
boundary. It adds no second Cloud Run service, runtime service account or CMEK key
set: the posture-read roles bind onto the single app service account in `iam.tf`,
and it stays inside the one residency perimeter in `vpc_sc.tf`.

## Prerequisites

1. **Terraform** >= 1.9 and the **Google** + **google-beta** providers `~> 6.0`
   (auto-installed by `terraform init`).
2. A **GCP project** with billing linked, and an **Organization** (Org Policy and
   VPC Service Controls are org-scoped).
3. An **Access Context Manager policy** for the org (only if `enable_vpc_sc = true`):
   ```bash
   gcloud access-context-manager policies create \
     --organization=ORG_ID --title="sg-residency"
   ```
   Pass its numeric id as `access_policy_id`.
4. IAM on the runner: roles to create the resources here, e.g.
   `roles/owner` or a tailored set including `roles/resourcemanager.projectIamAdmin`,
   `roles/orgpolicy.policyAdmin`, `roles/accesscontextmanager.policyAdmin`,
   `roles/cloudkms.admin`, `roles/discoveryengine.admin`, `roles/alloydb.admin`,
   `roles/logging.admin`.
5. Quota/availability for **Agent Search**, **Model Armor**, and **AlloyDB** in
   `asia-southeast1`.

## Apply

```bash
cp terraform.tfvars.example terraform.tfvars   # then edit ids; keep region=asia-southeast1
export TF_VAR_alloydb_password='<strong-secret>'   # prefer env over the tfvars file

terraform init
terraform plan
terraform apply
```

Recommended **two-phase** apply when using VPC Service Controls (see deploy-order
caveat below):

```bash
terraform apply -var='enable_vpc_sc=false'   # 1) build resources
# 2) add your operator/CI identity to a VPC-SC access level
terraform apply -var='enable_vpc_sc=true'    # 3) enforce the perimeter
```

After apply, deploy the Agent Runtime engine out-of-band (it has no first-class
Terraform resource) and wire the outputs into `config/settings.yaml`:

```bash
terraform output    # copy data_store_id, kms_key, alloydb_instance_uri, templates...
```

## ⚠️ Fail-fast region note (Agent Search)

`compliance-advisory` uses **Agent Search as the only production retrieval backend** and is a
**Singapore-resident** system. The Terraform is written to **fail fast** if Agent
Search cannot be provisioned in `asia-southeast1`:

- `agent_search.tf` sets `location = asia-southeast1` directly on the data store,
  with a `lifecycle.precondition` **and** a top-level `check` block re-asserting it.
- The `region` variable is validated to be exactly `asia-southeast1`.

If the Discovery Engine API/provider in your environment rejects the regional
value, **that rejection is the intended fail-fast signal**: Agent Search is not yet
regionally available for a sovereign deploy here, and the apply must NOT proceed to
a non-Singapore fallback. There is deliberately **no** RAG-Engine / File-Search
fallback path.

## ⚠️ WORM lock is irreversible

`logging_worm.tf` sets `locked = true` on the audit log bucket. Once applied you
**cannot** reduce retention or delete the bucket for the retention window
(`retention_days`, default 2557 ≈ 7 years), not even as project owner. Confirm
`retention_days` before applying. For a non-prod trial, set `locked = false`
(not compliant for production).

## ⚠️ VPC-SC deploy-order caveat

Enabling the perimeter before resources exist (or before your runner identity is
inside an access level) will cause API calls to be denied and the apply to fail.
Use the two-phase apply above, and consider VPC-SC **dry-run** mode first.

## CMEK does not cascade

All data-bearing services (AlloyDB, Agent Search, Agent Runtime, Logging) get an
**explicit** binding to the single regional CMEK in `kms.tf`. A CMEK on one
resource does not automatically protect data handed to another service, every
service is wired individually. Removing a binding silently drops a service back to
Google-managed keys; `org_policy.tf` adds `restrictNonCmekServices` as a backstop.
