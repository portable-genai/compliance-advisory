# Runbook: `compliance-advisory`

Operational guide for deploying and running `compliance-advisory` on the `gcp` profile in `asia-southeast1`.
This is a reference build; adapt thresholds, IAM, and approvals to your own change-management
and model-risk processes before any production use.

Authoritative stack and decisions: [`SPEC.md`](../SPEC.md). Architecture:
[`ARCHITECTURE.md`](../ARCHITECTURE.md). Controls: [`COMPLIANCE.md`](../COMPLIANCE.md).

---

## 0. Prerequisites

- A Google Cloud project in **`asia-southeast1`** with billing enabled and Org Policy
  permitting the Gemini Enterprise Agent Platform, Agent Search, AlloyDB, Model Armor,
  DLP, and Cloud Logging in that region.
- `gcloud` authenticated: `gcloud auth application-default login`.
- Terraform ≥ 1.7; Python 3.12; `pip install -e ".[gcp,dev]"`.
- Environment:
  ```bash
  export GOOGLE_CLOUD_PROJECT=your-sg-project
  export COMPLIANCE_PROFILE=gcp
  export COMPLIANCE_KMS_KEY="projects/$GOOGLE_CLOUD_PROJECT/locations/asia-southeast1/keyRings/.../cryptoKeys/..."
  export COMPLIANCE_ALLOYDB_URI="projects/$GOOGLE_CLOUD_PROJECT/locations/asia-southeast1/clusters/.../instances/..."
  ```

---

## 1. Deploy: ordered steps

> **Order matters.** Provision and validate everything *before* locking the audit bucket.
> Locking is irreversible (see §3).

1. **Plan & apply infrastructure.**
   ```bash
   make tf-plan                       # review the plan
   cd terraform && terraform apply    # provisions Agent Search, AlloyDB, KMS, log bucket (UNLOCKED), VPC-SC
   ```
   Terraform **fails fast** on an Agent Search location the service does not serve, this is the
   region guard (P-01). Do not relax it to a global endpoint; a global endpoint gives no
   residency guarantee.

2. **Create the AlloyDB freshness schema.** The `corpus_freshness` table (per
   `alloydb.table`) backs `CorpusLedgerPort`. Apply the schema migration shipped with the
   `pipelines/` tooling against `COMPLIANCE_ALLOYDB_URI`.

3. **Deploy the ADK agent to Agent Runtime.** Build and deploy the `reasoningEngine`
   (ex-Agent Engine) in-region; record the resource id into `COMPLIANCE_AGENT_ENGINE`
   (`agent_engine.resource_name`). The grounding `google_search` tool deploys as its own
   sub-agent (one built-in tool per agent).

4. **Seed the corpus.** Run the fetch-at-runtime pipeline once to populate Agent Search from
   `src/compliance_advisory/pipelines/sources/registry.yaml` and write initial
   `FreshnessRecord`s (TTL = `corpus.ttl_days`, default 7 days) into the AlloyDB ledger.

5. **Run the eval gate.** `make eval` must pass (groundedness, citation accuracy,
   faithfulness, safety) before promotion (P-08 / `model-quality-gate`). A non-zero exit blocks the release.

6. **Lock the log bucket, LAST.** Only after everything above is verified, lock the Cloud
   Logging WORM bucket (retention `logging.retention_days = 2557`). **This is irreversible.**
   See §3.

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
  set via the `logging.retention_days` Terraform variable.
- **Locking is irreversible.** Once locked, the bucket and its retention cannot be deleted or
  shortened for the retention window. Lock it **last** in the deploy, only after you have
  confirmed log routing, redaction, and field shape are correct.
- Records are written **already redacted** (`redacted_prompt` / `redacted_response`), so PII
  never lands in the WORM store (P-04 + P-07).

---

## 4. Operational notes

### Key rotation (CMEK, P-10)
- Rotate the regional Cloud KMS key on your standard cadence. Agent Search, AlloyDB, and the
  log bucket reference the key version; rotation re-encrypts new writes. Keep old key
  versions enabled for the retention window so existing ciphertext (incl. WORM logs) stays
  readable.
- Update `COMPLIANCE_KMS_KEY` only if the key *resource* changes (not on version rotation).

### Retention
- Audit: `2557` days, enforced by the locked bucket (irreversible).
- Freshness ledger: rows are upserted in place; expired sources are refreshed, not deleted,
  so the version history is auditable. Prune only per your data-retention policy.

### Corpus refresh
- Inline: a query that needs a stale/missing source triggers re-fetch + re-ingest before
  answering (so answers are never built on expired regulation).
- Scheduled: a background job calls `CorpusLedgerPort.list_expired()` and refreshes expiring
  sources out of band. **Nothing schedules it.** The GitHub Actions cron that documented this
  never ran, because Actions were disabled organization-wide at the time, and the file was
  removed rather than left standing as a control nobody was performing. GitHub Actions has been
  the fleet's live CI since 2026-09-02, but this cron has not been re-added, so run it by hand at
  least daily, or wire a Cloud Scheduler job, so most reads hit fresh data within the 7-day TTL.

### Horizon scanning
- **What it reads:** the SAME freshness ledger the corpus refresh writes. Each record also
  carries the generation it supersedes (`previous_version`, `previous_checksum`,
  `previous_fetched_at`, `previous_status`); `domain/horizon/carry_forward` writes them
  inside the ingest upsert. No separate store to back up or restore.
- **Schema migration:** both ledger adapters add the four columns idempotently on startup
  (`ALTER TABLE ... ADD COLUMN IF NOT EXISTS` on AlloyDB, a `PRAGMA table_info` check on
  SQLite). An existing deployment needs no manual migration; rows ingested before the
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
- **Tracking store:** AlloyDB `horizon_tracking` on `gcp` (created idempotently beside the
  freshness ledger), SQLite on `local`. Rows are tenant-partitioned; a cross-tenant read or
  write is refused with 403.

### Kill-switch
- **Disable grounding:** set `COMPLIANCE_GROUNDING_ENABLED=false` (or `grounding_enabled:
  false`) to cut public-web grounding instantly; the grounding sub-agent then returns no web
  citations and the pipeline skips the grounding step.
- **Hard stop:** to take the assistant offline, scale the API to zero and/or undeploy the
  Agent Runtime `reasoningEngine`. The WORM audit bucket and ledger persist independently, so
  no audit history is lost.
- **Pause horizon escalations:** horizon assessments route to `human-review-console` through the same
  `ReviewRouterPort` as every other escalation. Unsetting `HUMAN_REVIEW_URL` stops the
  submissions; the ESCALATED audit rows are still written, so nothing is lost, and the
  scans keep returning their decisions.
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
