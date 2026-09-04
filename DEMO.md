# Demo guide - `compliance-advisory`

Step-by-step scripts for demoing `compliance-advisory` four ways:

- **Demo A - Grounded compliance, four cited artifacts** (the headline flow): for one
  Compliance / Risk use case the assistant produces a grounded **Answer**, a **Control
  checklist**, **Test cases** that verify each control, and the **Regulator questions** a
  CRO / supervisor will ask - every claim cited to a regulator source and page, every
  consequential output gated for maker-checker review, every interaction written to a WORM
  audit trail. Runs **fully offline** (no cloud, no API key).
- **Demo B - The same flow on the managed GCP stack**: the identical four artifacts
  produced against real Agent Search / Gemini / Model Armor / DLP in `asia-southeast1`,
  driven through the REST API and the Next.js console.
- **Demo C - Control mapping and the evidence pack** (the control-mapping module): for one
  deployment scope, map each regulatory requirement to the GCP control(s) that satisfy it,
  read the observed posture, and assemble the regulator-grade **evidence pack** with its
  coverage summary and gaps - on the **same** shared reg KB the assistant cites, and
  **always** flagged for human review. Runs **fully offline** too.

- **Demo E - Regulatory horizon scanning** (the "what just changed" flow): diff the corpus
  freshness ledger, then for each detected movement show the deterministic applicability
  verdict, the materiality score with the arithmetic that produced it, the accountable
  owner, the citation, and the tracked implementation journey through to closure. Runs
  **fully offline** too.

- **Demo D - The REAL corpus under the `live` profile** (the audience-facing demo): the
  same four artifacts grounded on the **real public instruments** (MAS TRM / Outsourcing /
  FEAT, APRA CPS 230 / CPG 230 / CPS 234, JFSA AI discussion papers, BCBS operational
  resilience, NIST AI RMF) fetched from the regulators' own sites, with generation by
  the Gemini API. Audience questions are typed live; audience documents are added
  through the corpus upload (template downloadable in the UI).

> Demo A / B / C run on a synthetic **fictional** corpus (clearly-invented MAS / HKMA /
> APRA passages). Do not rely on it as the real instruments, and do not run against live
> customer data without your own legal, security and model-risk sign-off. Demo D never
> serves the fictional passages: the live profile purges them and fails closed until the
> real corpus is ingested.

### Demo D in three commands

```bash
# 1. Ingest the real corpus (per-page citations; re-runs are no-ops within the 7-day TTL).
#    HKMA gates its PDFs behind a JS repository: the job prints a manual-drop path for
#    those four sources (save the PDF from a browser; everything else ingests directly).
python -m compliance_advisory.pipelines.refresh_job --full

# 2. There is no local model server to start. Every model call in this profile is the
#    Gemini API, because the corpus itself is fetched from the regulators' own sites and
#    the profile cannot be kept current without leaving the data centre.

# 3. Serve under the live profile (generation needs a GCP project + application-default
#    credentials).
GOOGLE_CLOUD_PROJECT=<project> COMPLIANCE_PROFILE=live python -m compliance_advisory.api.app
```

The banner at the top of every UI page states the runtime and the answering model.

Then ask anything through the UI or `POST /ask`; answers cite real source ids, real page
numbers and the regulators' own URLs. To add an audience document:
`GET /corpus/upload-template` (CSV of the form fields) and `POST /corpus/documents`
(multipart: file + title), or use the "Add a document" panel under Corpus freshness in
the UI.

---

## 0. Prerequisites

| Need | Demo A (local) | Demo B (GCP) | Notes |
|------|:--:|:--:|-------|
| `git` | yes | yes | clone the repo |
| **Python 3.12+** | yes | yes | the package pins `>=3.12` |
| Node.js 18.18+ and npm | for the UI | for the UI | only if you show the browser console |
| **Playwright** (`pip install playwright` + `playwright install chromium`) | for the guided walkthrough | no | Demo A's presenter walkthrough only |
| A GCP project and `gcloud` | no | yes | billing enabled; `asia-southeast1` available |
| Terraform | no | yes | provisions Agent Search, AlloyDB, DLP, WORM bucket, CMEK |
| Cloud KMS key (regional) | no | yes | CMEK; set `COMPLIANCE_KMS_KEY` |

Install / setup references (read these once):

- Local install and profiles -> [README 4.1 `local`](README.md#41-local-profile-a-working-offline-laptop-stack-the-dev--test-default)
- GCP install and deploy -> [README 4.3 `gcp`](README.md#43-gcp-profile-real-managed-stack-in-asia-southeast1) and [`docs/runbook.md`](docs/runbook.md#1-deploy-ordered-steps)
- Running the surfaces (API / CLI / UI) -> [README 5](README.md#5-running-the-three-surfaces)
- The demo scripts -> [`scripts/README.md`](scripts/README.md)
- The UI console -> [`ui/README.md`](ui/README.md)
- Config (`${ENV_VAR}` resolved at load) -> [`config/settings.yaml`](config/settings.yaml)

---

## 1. Common setup (both demos)

```bash
git clone https://github.com/portable-genai/compliance-advisory.git
cd compliance-advisory

python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"          # core + dev tooling (NO google-cloud-* packages)

# Sanity check the offline stack before presenting:
export COMPLIANCE_PROFILE=local
make lint test                   # ruff + mypy + pytest (all local, no cloud)
```

See [README 4.1](README.md#41-local-profile-a-working-offline-laptop-stack-the-dev--test-default) for details.

---

## 2. Demo A - Grounded compliance, four cited artifacts (local, offline)

The flow uses the in-process `local` stack - SQLite FTS5 over the built-in synthetic
corpus plus a deterministic LLM - so it needs **no Google Cloud and no API key**, ideal
for a laptop demo. Four ways to present it, in order of polish.

### 2.1 Guided, presenter-controlled walkthrough (recommended)

A real browser opens; the script narrates each step and **waits for you to press Enter**
before performing it, so you control the pace. (One-time: `pip install playwright &&
playwright install chromium`.)

```bash
# Terminal 1 - the live demo server (http://localhost:8122)
source .venv/bin/activate
PYTHONPATH=src:tests python scripts/compliance_demo_server.py

# Terminal 2 - the guided walkthrough (a Chrome window opens)
source .venv/bin/activate
python scripts/compliance_demo_playwright.py
```

You'll step through, pressing Enter each time:

1. **Answer** - the analyst asks a MAS cloud-outsourcing question; a grounded answer comes
   back with a confidence meter, page-level citations (MAS TRM Guidelines p.42) and a
   HUMAN REVIEW REQUIRED banner.
2. **Control checklist** - the controls the use case requires, each with a severity, cited
   rationale and regulator + page citation chips.
3. **Test cases** - an automated verification test per control (steps, expected result,
   an executable check), cited to source.
4. **Regulator questions** - the questions a regulator / CRO will ask, each with why it is
   asked and a cited model answer.
5. **WORM audit trail** - every interaction (ask / checklist / testcases /
   regulator-questions) written PII-redacted to the append-only audit store.

**What to point at on screen:** the confidence meter and the citation chips (regulator +
version + page + source link = the proof), the maker-checker review banner above every
consequential artifact, and the audit table at the end. Full options (`SLOWMO_MS`,
`HEADLESS`, `CHROME_PATH`, …) are in [`scripts/README.md`](scripts/README.md).

### 2.2 Manual, click-through (no Playwright)

Drive the demo server yourself, or click through the real console.

The demo server (one process, no Node):

```bash
PYTHONPATH=src:tests python scripts/compliance_demo_server.py     # http://localhost:8122
```

Open `http://localhost:8122` and click **Next ▶** to reveal each artifact, **Restart** to
reset. Same five steps as above.

The real Next.js console talking to the local API:

```bash
# Terminal 1 - the API on the local profile (no cloud)
make run-api PROFILE=local            # FastAPI on :8080

# Terminal 2 - the console, built and served the way it ships (its default API is :8000)
cd ui && npm install
export NEXT_PUBLIC_API_BASE=http://localhost:8080   # inlined at build time, read again at run time
npm run build && npm run start                      # http://localhost:3000
```

Every demo runs against a production build, never a development server. `make run-ui` is the
developer loop with hot reload, and it is not what a presenter shows.

Open `http://localhost:3000`, pick the **MAS** regulator filter, ask the cloud-outsourcing
question, then switch the **Answer | Checklist | Test Cases | Regulator Questions** tabs.
The right rail shows the guardrail verdict, confidence, PII tallies and the human-review
banner; the corpus table reads `/corpus/status`.

> The console's default backend is `http://localhost:8000`, but `make run-api` serves on
> **:8080** - set `NEXT_PUBLIC_API_BASE=http://localhost:8080` (above) so the health pill
> goes green.

### 2.3 Static artifacts (slides / screenshots)

Generate the audit-first pages and JSON without a browser:

```bash
PYTHONPATH=src:tests python scripts/compliance_demo.py compliance_demo.json     # prints the per-artifact summary
PYTHONPATH=src:tests python scripts/render_compliance_ui.py compliance_demo.json ./out
# -> ./out/answer.html, checklist.html, testcases.html, regulator-questions.html, audit.html
```

### 2.4 One-shot via the CLI (quick variant)

If you only want to show one cited artifact at the terminal:

```bash
export COMPLIANCE_PROFILE=local
compliance ask "What cloud-outsourcing controls does MAS expect before onboarding a cloud provider?" --regulator MAS
# or any of:  compliance checklist "..."   /   compliance testcases "..."   /   compliance regulator-questions "..."
```

Or the bundled end-to-end smoke target:

```bash
make smoke-local
```

---

## 3. Demo B - The same flow on the managed GCP stack

Shows the identical four artifacts produced against **real managed services** in
`asia-southeast1`. Follow [`docs/runbook.md`](docs/runbook.md#1-deploy-ordered-steps) for
the authoritative deploy steps; the short version:

### 3.1 GCP setup

```bash
source .venv/bin/activate
pip install -e ".[gcp,dev]"                 # adds google-adk, google-genai, discoveryengine, dlp, ...

export GOOGLE_CLOUD_PROJECT=your-sg-project
export COMPLIANCE_PROFILE=gcp
export COMPLIANCE_KMS_KEY="projects/.../locations/asia-southeast1/keyRings/.../cryptoKeys/..."
export COMPLIANCE_ALLOYDB_URI="projects/.../locations/asia-southeast1/clusters/.../instances/..."
gcloud auth application-default login
```

### 3.2 Provision infra (one-time)

```bash
make tf-plan          # review the plan - the WORM bucket retention lock is IRREVERSIBLE
cd terraform && terraform apply && cd ..
```

Details and gotchas (region fail-fast, key rotation, retention): [`docs/runbook.md`](docs/runbook.md).

### 3.3 Run and show

```bash
make run-api          # FastAPI on :8080, profile=gcp
```

Then demo any surface ([README 5](README.md#5-running-the-three-surfaces)):

```bash
# REST - the grounded answer. No actor in the body: the audit actor is resolved
# server-side from the verified identity (IAP assertion in gcp; seeded persona via
# the X-Dev-Persona header in local). See docs/embedding-and-identity.md.
curl -s localhost:8080/ask -H 'content-type: application/json' -d '{
  "question": "What cloud-outsourcing controls does MAS expect before onboarding a cloud provider?",
  "filters": {"regulator": "MAS"}
}' | python -m json.tool

# REST - the consequential artifacts for a use case
curl -s localhost:8080/checklist -H 'content-type: application/json' -d '{
  "use_case": "Onboard a public-cloud SaaS provider for core banking workloads"
}' | python -m json.tool
# (likewise POST /testcases and POST /regulator-questions)

# Corpus freshness, agent card, health, seeded dev personas (local profile only)
curl -s localhost:8080/corpus/status | python -m json.tool
curl -s localhost:8080/.well-known/agent-card.json | python -m json.tool
curl -s localhost:8080/healthz
curl -s localhost:8080/personas | python -m json.tool
```

Or the browser console (talks to the API on :8080) - see [`ui/README.md`](ui/README.md):

```bash
cd ui && npm install
export NEXT_PUBLIC_API_BASE=http://localhost:8080
npm run build && npm run start                             # http://localhost:3000
```

**What to highlight:** every claim carries a regulator + version + **page** citation; PII
is redacted before any model / index / audit call; consequential artifacts are **always**
maker-checker gated; everything stays in `asia-southeast1` with CMEK
([README 8](README.md#8-security--residency-posture)).

---

## 3b. Demo C - Control mapping and the evidence pack (local, offline)

The control-mapping module is exposed over the REST API (`/map`, `/gaps`, `/evidence-pack`),
additive alongside the assistant routes. It runs on the same `local` profile with **no
Google Cloud**: the requirement source binds in-process to the same SQLite FTS5 reg KB the
assistant uses, and the observed posture comes from a canned deterministic posture (the
`local` control-inventory adapter). The scope below is a **fictional** project id.

```bash
# Terminal 1 - the API on the local profile (no cloud)
make run-api PROFILE=local            # FastAPI on :8080
```

```bash
# Terminal 2 - drive the three control-mapping routes. No actor in the body: the audit
# actor is resolved server-side from the verified identity (seeded persona via the
# X-Dev-Persona header in local). Empty requirements / unobservable posture -> HTTP 422.

# 1. Map each requirement for the scope to the GCP control(s) that satisfy it:
curl -s localhost:8080/map -H 'content-type: application/json' -d '{
  "scope": "projects/acme-sg-prod",
  "regulator": "MAS"
}' | python -m json.tool

# 2. The gaps only (missing / misconfigured controls, with severity + remediation):
curl -s localhost:8080/gaps -H 'content-type: application/json' -d '{
  "scope": "projects/acme-sg-prod"
}' | python -m json.tool

# 3. The regulator-grade evidence pack (mappings + posture + gaps + coverage summary),
#    always flagged requires_human_review=true:
curl -s localhost:8080/evidence-pack -H 'content-type: application/json' -d '{
  "scope": "projects/acme-sg-prod"
}' | python -m json.tool
```

**What to point at:** each `ControlMapping` carries a `Coverage` verdict (FULL / PARTIAL /
NONE) **computed server-side** from which mapped controls are observed ENABLED, not taken on
the model's word; every mapping cites the same regulator source and page the assistant
cites (one shared reg KB); the evidence pack is **always** human-review gated (maker-checker,
P-06) and routes to `human-review-console` (R8). The mapping module runs **without** guardrail or DLP steps by
design, it reasons over the bank's own control posture and carries no customer PII (see
[`COMPLIANCE.md`](COMPLIANCE.md) section A0).

> The same three routes exist on the managed `gcp` stack (Demo B setup), where the posture
> is read live from Security Command Center + Cloud Asset Inventory + Assured Workloads
> instead of the canned local posture. **External consumer:** `architecture-validator` (architecture validator)
> POSTs `/evidence-pack` to this service with the same shape.

---

## 3c. Demo E - Regulatory horizon scanning (local, offline)

The horizon module is exposed over the REST API (`/horizon/*`) and the CLI (`compliance
horizon`). It reads the SAME freshness ledger the corpus pipeline already writes, so the
demo is: refresh the corpus, then ask what changed.

```bash
# Terminal 1 - the API on the local profile (no cloud)
make run-api PROFILE=local            # FastAPI on :8080
```

```bash
# Terminal 2 - populate the ledger, then scan the horizon.
COMPLIANCE_PROFILE=local compliance corpus refresh

curl -s localhost:8080/horizon/scan -H 'content-type: application/json' -d '{
  "scope": "projects/acme-sg-prod"
}' | python -m json.tool

# The tracked journey for the caller's tenant, then close one change against the GCP
# controls that evidence it (this is the link into the control-mapping journey):
curl -s localhost:8080/horizon/items | python -m json.tool
curl -s localhost:8080/horizon/items/<change-id>/status \
  -H 'content-type: application/json' -d '{
    "status": "implemented",
    "note": "closed by the CMEK rollout",
    "control_ids": ["cmek-keys", "vpc-sc-perimeter"]
  }' | python -m json.tool
```

The same flow on the CLI, which prints the arithmetic inline:

```bash
COMPLIANCE_PROFILE=local compliance horizon scan "projects/acme-sg-prod"
COMPLIANCE_PROFILE=local compliance horizon track --open-only
```

**What to point at:**

- **The drivers are on the wire.** Each assessment carries the named contributions
  (`change_kind`, `doc_type`, `topic_overlap`, `open_control_gaps`) whose sum IS the
  materiality score. A reviewer reconstructs the number without rerunning the scan.
- **The model did not produce the number.** Applicability, score, band and owner are
  computed and audited before the model is called; the model is handed them as facts to
  explain. The `narrative` field is the only thing it writes, and on the offline `local`
  profile the deterministic stand-in often writes nothing at all: the scan still returns
  every decision, its arithmetic and its citation. That is the point.
- **Every assessment is cited** to the instrument that drove it, exactly like the other two
  families.
- **Ownership routing is itself consequential.** Any routed change sets
  `requires_human_review` and goes to the `human-review-console` maker-checker console (R8).
- **The numbers are the bank's.** Show `horizon:` in `config/settings.yaml` and change
  `band_thresholds` or `topic_owners` live: same arithmetic, different verdict, no code
  change (B4).
- **Tenant isolation is fail-closed.** Repeat the item read with
  `-H 'X-Dev-Persona: other-tenant'` and get a **403**, not an empty list and not a 404.

---

## 4. Talking points

- **Citations are the product.** A compliance answer a CRO / regulator cannot trace to a
  source page is worthless, so every claim carries `source_id`, regulator, version and
  page, with a link to the instrument.
- **One question, four artifacts.** The same use case yields the answer, the controls, the
  tests that verify the controls, and the questions a supervisor will ask - the full
  audit-ready package, not a chatbot reply.
- **One reg KB, ask to evidence pack.** Control mapping is a module of this service, not a
  separate tool: the assistant answers "what does the regulation require?" and the mapping
  module answers "do our cloud controls actually satisfy it, and where are the gaps?" over
  the **same** cited knowledge base. The evidence pack is the auditor deliverable, always
  human-reviewed.
- **Posture split is intentional.** The mapping module runs without guardrail or DLP by
  design (it reasons over the bank's own control posture, no customer PII); the assistant
  path keeps its full guardrail + DLP posture. Both feed the one `human-review-console` review contract.
- **Corpus freshness became compliance work.** The 7-day TTL kept answers current; horizon
  scanning makes the change itself the unit of work. The ledger carries the generation each
  ingest supersedes, so "the regulator republished CPS 230" becomes an assessed, scored,
  owned and tracked obligation rather than a silent re-index, on the same store with no
  shadow copy.
- **The consequential number is pure code.** Materiality is an additive total of named
  drivers over thresholds the bank owns in config. The model writes the rationale and is
  given the decisions as facts. That is the difference between a defensible materiality
  call and a plausible-sounding one.
- **Guardrails hold.** Redact-before-everything (P-04), guardrail screen on input and
  output, maker-checker on every consequential artifact (P-06), WORM audit, single-region
  + CMEK residency.
- **No vendor lock-in.** The same domain code runs on the managed GCP stack, the offline
  `local` stack, or the fail-fast `onprem` placeholders - switching is one env var
  (`COMPLIANCE_PROFILE`), proving ports-and-adapters reversibility (P-02).

---

## 5. Troubleshooting and cleanup

| Symptom | Fix |
|---------|-----|
| `python3.12: command not found` | Install Python 3.12+; the package pins `>=3.12`. |
| Playwright: "executable doesn't exist" | `playwright install chromium`, or set `CHROME_PATH=/path/to/chrome`. |
| No display for the headed walkthrough | Use 2.2 (manual browser) on a machine with a display, or `HEADLESS=1 DEMO_AUTO=1 python scripts/compliance_demo_playwright.py` to self-run. |
| "Cannot reach the demo server" | Start 2.1 Terminal 1 first; or set `DEMO_URL` if you changed `--port`. |
| Console health pill shows "down" | Set `NEXT_PUBLIC_API_BASE=http://localhost:8080` (the console defaults to :8000; the API serves on :8080). |
| Port 8122 / 8080 / 3000 in use | `python scripts/compliance_demo_server.py --port 9000` (then `DEMO_URL=http://127.0.0.1:9000`); API port via `make run-api API_PORT=...`. |
| CLI exits 2 with "not available under profile 'onprem'" | You're on `COMPLIANCE_PROFILE=onprem` (fail-fast placeholders). Use `local` (Demo A) or `gcp` (Demo B). |
| GCP deploy / region / VPC-SC errors | See [`docs/runbook.md`](docs/runbook.md). |

**Stop / clean up:** Ctrl-C the demo server, `make run-api` and the console. The local
stores are in-memory for the demo scripts, so nothing persists. For GCP, scale the
deployment to zero or remove the app SA's model-access role
([runbook kill-switch](docs/runbook.md#kill-switch)) - the audit trail stays intact.
`make clean` removes local caches and artefacts.
