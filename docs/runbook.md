# Runbook: `compliance-advisory`

Operational guide for deploying and running `compliance-advisory` on the `gcp` profile in `asia-southeast1`.
This is a reference build; adapt thresholds, IAM, and approvals to your own change-management
and model-risk processes before any production use.

Authoritative stack and decisions: [`SPEC.md`](../SPEC.md). Architecture:
[`ARCHITECTURE.md`](../ARCHITECTURE.md). Controls: [`COMPLIANCE.md`](../COMPLIANCE.md).

---

## 0. Prerequisites

- A Google Cloud project in **`asia-southeast1`** with billing enabled and Org Policy
  permitting the Gemini Enterprise Agent Platform, Agent Search, Firestore, Model Armor,
  DLP, and Cloud Logging in that region.
- `gcloud` authenticated: `gcloud auth application-default login`.
- Terraform ≥ 1.7; Python 3.12; `pip install -e ".[gcp,dev]"`.
- Environment:
  ```bash
  export GOOGLE_CLOUD_PROJECT=your-sg-project
  export COMPLIANCE_PROFILE=gcp
  export COMPLIANCE_KMS_KEY="projects/$GOOGLE_CLOUD_PROJECT/locations/asia-southeast1/keyRings/.../cryptoKeys/..."
  export COMPLIANCE_AGENT_SEARCH_LOCATION=us   # where the data store lives: global | us | eu
  ```

---

## 1. Deploy: ordered steps

> **Order matters.** Provision and validate everything *before* locking the audit bucket.
> Locking is irreversible (see §3).

1. **Plan & apply infrastructure.**
   ```bash
   make tf-plan                       # review the plan
   cd infra/terraform && terraform apply   # Agent Search, Firestore, KMS, the audit bucket, and the controls the tfvars keep
   ```
   Terraform **fails fast** on an Agent Search location the service does not serve, this is the
   region guard (P-01). Do not relax it to a global endpoint; a global endpoint gives no
   residency guarantee.

2. **The ledger needs no schema step.** On `gcp` the freshness ledger and the horizon tracker live
   in the Firestore database `compliance-advisory-<region>`, and the apply creates it with the one
   composite index the tracker's tenant listing needs. On `platform` (AlloyDB, with
   `enable_alloydb = true`), create the `compliance` database once; the adapters create their
   tables idempotently on first use.

3. **Deploy the ADK agent to Agent Runtime.** Build and deploy the `reasoningEngine`
   (ex-Agent Engine) in-region; record the resource id into `COMPLIANCE_AGENT_ENGINE`
   (`agent_engine.resource_name`). The grounding `google_search` tool deploys as its own
   sub-agent (one built-in tool per agent).

4. **Seed the corpus.** Run the fetch-at-runtime pipeline once to populate Agent Search from
   `src/compliance_advisory/pipelines/sources/registry.yaml` and write initial
   `FreshnessRecord`s (TTL = `corpus.ttl_days`, default 7 days) into the ledger.

5. **Run the eval gate.** `make eval` must pass (groundedness, citation accuracy,
   faithfulness, safety) before promotion (P-08 / `model-quality-gate`). A non-zero exit blocks the release.

6. **Lock the log bucket, LAST, if you lock it at all.** `worm_locked` has no default, so the plan
   refuses until the tfvars state it. Apply with `false` until everything above is verified, then
   re-apply with `true` (and `retention_days` of at least 2557). **This is irreversible.** See §3.

7. **Start the API.** `make run-api` (or deploy the API container, see
   [`Dockerfile`](../Dockerfile), which installs `.[gcp]`).

---

## 2. Region fail-fast behaviour

The deploy region is a Terraform input (`region`) validated against `allowed_regions`, the
residency allowlist; both default to `asia-southeast1`, so an unset deploy stays in Singapore
and any other region means setting both, which is the deliberate residency review. Everything
else follows the selected region:

- **Terraform** refuses to provision if `agent_search_location` is not one Agent Search serves (no
  silent fallback to a global endpoint, and no RAG-Engine / File-Search production
  fallback, Agent Search is the only production retrieval backend).
- **Runtime** targets regional service endpoints (e.g. the Model Armor host
  `modelarmor.asia-southeast1.rep.googleapis.com`) and per-service regional CMEK. A
  misconfiguration that would route a REGIONAL service to a global endpoint should fail loudly
  rather than silently weaken residency (P-01).

**Agent Search is the documented exception, not a misconfiguration to escalate.** It serves
`global`, `us` and `eu` and no Cloud region, so its location is a separate deploy-time input
(`agent_search_location`) and cannot be in-country at any setting. If a plan errors naming it,
the fix is to choose among those three deliberately — `us` or `eu` to confine the index to one
jurisdiction, `global` to accept an unlocated index — and to widen `gcp.resourceLocations` to
permit the choice. Do NOT "work around it" by pointing another regional service at a global
endpoint; that is the failure this guard exists to catch.

---

## 3. WORM audit bucket: locking & retention

- The audit sink is a Cloud Logging **locked bucket**; retention is `2557` days (~7 years),
  set via the `retention_days` Terraform variable, and locked only by `worm_locked = true`.
- **Locking is irreversible.** Once locked, the bucket and its retention cannot be deleted or
  shortened for the retention window. Lock it **last** in the deploy, only after you have
  confirmed log routing, redaction, and field shape are correct.
- Records are written **already redacted** (`redacted_prompt` / `redacted_response`), so PII
  never lands in the WORM store (P-04 + P-07).

---

## 4. Operational notes

### Identity refusals: reading the status

| Symptom | Cause | Action |
|---|---|---|
| `401 {"detail":"authentication required"}` on every route taking a principal, while `/healthz`, `/personas` and the agent card answer 200 through the same proxy | The assertion is not arriving under a name this service reads. Behind an embedding host the serverless frontend strips the reserved `x-goog-*` namespace, so the host's copy of `x-goog-iap-jwt-assertion` never reaches the container and the forwarded `x-portal-iap-assertion` is the only name left | This service reads both (`docs/embedding-and-identity.md` 3.2). If the symptom persists, the deployed IMAGE predates that fix: check the running revision's image digest BEFORE changing configuration, because the audience and the reviewed maps are not the cause and editing them will not help. |
| `403` naming the caller, not `401` | The caller authenticated and this deployment admits them nothing: an allowlist that does not name them, or a tenant the reviewed maps decline to resolve | Add the exact address to `COMPLIANCE_IAP_MACHINE_TENANTS_JSON`, or the sign-in domain to `COMPLIANCE_IAP_TENANT_DOMAINS_JSON`. No credential change helps; see `docs/embedding-and-identity.md` 3.3. |
| A service caller gets `200` and an empty result set | It authenticated and resolved to an EMPTY tenant, which every tenant-scoped read fails closed on. An invisible row is indistinguishable from an empty dataset | Map its exact address in `COMPLIANCE_IAP_MACHINE_TENANTS_JSON`. A machine carries no `hd`, so no domain rule can give it a tenant. |
| `503` naming a variable | Nobody can authenticate here: `COMPLIANCE_IAP_AUDIENCE` unset or set-and-empty, or `google-auth` absent from the image | Set the variable to the IAP-protected resource path, or deploy the image built with the `[gcp]` extra. |

### Key rotation (CMEK, P-10)
- Rotate the regional Cloud KMS key on your standard cadence. The log bucket, the Firestore
  database (when Firestore CMEK is enabled) and AlloyDB (when enabled) reference the key version; rotation re-encrypts new writes. Keep old key
  versions enabled for the retention window so existing ciphertext (incl. WORM logs) stays
  readable.
- Update `COMPLIANCE_KMS_KEY` only if the key *resource* changes (not on version rotation).

### Retention
- Audit: `retention_days`, enforced as WORM only when `worm_locked = true` (then irreversible).
- Freshness ledger: rows are upserted in place; expired sources are refreshed, not deleted,
  so the version history is auditable. Prune only per your data-retention policy.

### Corpus refresh
- Inline: a query that needs a stale/missing source triggers re-fetch + re-ingest before
  answering (so answers are never built on expired regulation).
- Scheduled: the Cloud Run job `compliance-freshness-refresh` runs `pipelines.refresh_job` from
  the API image daily at 02:00 Singapore time, started by Cloud Scheduler. `infra/terraform/scheduler.tf`
  creates both once the deployment names the API image's digest in `corpus_refresh_image`; it
  runs as the pipeline identity and writes the ledger the `gcp` profile binds. Without that
  variable nothing schedules it: run it by hand at least daily so most reads hit fresh data
  within the 7-day TTL.

### Horizon scanning
- **What it reads:** the SAME freshness ledger the corpus refresh writes. Each record also
  carries the generation it supersedes (`previous_version`, `previous_checksum`,
  `previous_fetched_at`, `previous_status`); `domain/horizon/carry_forward` writes them
  inside the ingest upsert. No separate store to back up or restore.
- **Schema migration:** both ledger adapters add the four columns idempotently on startup
  (`ALTER TABLE ... ADD COLUMN IF NOT EXISTS` on AlloyDB, a `PRAGMA table_info` check on
  SQLite; a Firestore document written before the fields existed reads them as empty). An
  existing deployment needs no manual migration; rows ingested before the
  upgrade simply have an empty diff base and report as `new_source` on the first scan.
- **When to scan:** after each scheduled corpus refresh. `POST /horizon/scan` (or
  `compliance horizon scan <scope>`) is idempotent: change ids are content-derived, and a
  re-scan updates the existing tracked item rather than opening a duplicate. A human-set
  implementation status is never overwritten by a re-scan.
- **An empty ledger returns HTTP 422**, not an empty scan: it means the corpus has never
  been ingested. Run `compliance corpus refresh --full` first.
- **Changing the policy:** every threshold, weight, owner and SLA is in
  `config/settings.yaml` under `horizon:` and takes effect on the next scan with no
  redeploy of the domain. Changing `band_thresholds` or `topic_owners` re-bands and
  re-routes future scans; already-tracked items keep their recorded owner and band until
  the next scan refreshes them (a human-set status still survives).
- **Tracking store:** the Firestore `horizon_tracking` collection on `gcp` (its tenant listing
  uses the composite index `infra/terraform/firestore.tf` declares), an AlloyDB table on
  `platform`, SQLite on `local`. Rows are tenant-partitioned; a cross-tenant read or
  write is refused with 403.

### Kill-switch
- **Disable grounding:** set `COMPLIANCE_GROUNDING_ENABLED=false` (or `grounding_enabled:
  false`) to cut public-web grounding instantly; the grounding sub-agent then returns no web
  citations and the pipeline skips the grounding step.
- **Hard stop:** to take the assistant offline, scale the API to zero and/or undeploy the
  Agent Runtime `reasoningEngine`. The WORM audit bucket and ledger persist independently, so
  no audit history is lost.
- **Pause escalations:** every escalation, horizon assessments included, routes to
  `human-review-console` through the one `ReviewRouterPort`. Set
  `COMPLIANCE_REVIEW_ROUTING=off` to stop the submissions; the ESCALATED audit rows are still
  written, so nothing is lost, and every response reports `review_routing: "off"` so the user
  is told the item is not queued. Unsetting `HUMAN_REVIEW_URL` no longer pauses anything:
  under `gcp` or `platform` with routing on, the process refuses to boot without it.
- **Runtime controls:** `COMPLIANCE_GUARDRAIL`, `COMPLIANCE_PII_REDACTION` and
  `COMPLIANCE_REVIEW_ROUTING` each switch one cheap control, read in three states: unset is on,
  `true`/`false` (or `on`/`off`) wins, and an emptied or unrecognised value refuses at boot. A
  process with any of them off logs one warning at startup naming each. A response built from
  input that redaction changed carries `input_redacted: true`, and the console says so.
- **Block a category:** tighten the Model Armor template (`model_armor.template_id`) to deny
  the offending category; screening applies on the next request with no redeploy.

### Health & observability
- `AgentRuntimePort.health()` is the liveness/readiness probe for the hosted agent.
- Traces go to Cloud Trace via OpenTelemetry with **message-content capture OFF**, spans
  carry structure and token usage (FinOps) but never prompt/response text (part of the P-04
  data-minimisation posture).
- Token/cost metrics are emitted via `record_token_usage` for FinOps dashboards.

---

## 5. Rollback

- **Application:** redeploy the previous container image / Agent Runtime revision. The domain
  is stateless; sessions/memory persist in their stores.
- **Infrastructure:** `terraform apply` a prior known-good plan. **Exception:** the locked
  audit bucket cannot be rolled back or shortened, that is by design (P-07).
- **Eval-gate failure:** never promote past a failing `make eval`. Fix the regression (or the
  golden dataset, with review) and re-run; do not bypass the gate.
