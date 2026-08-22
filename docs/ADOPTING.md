# Adopting this repo as your base

This repository is a **common base** that BFSI institutions (and other regulated
industries) fork to build their own grounded regulatory-Q&A assistants: a compliance
copilot that answers questions, drafts control checklists, generates control test cases,
and prepares regulator-question responses, every claim carrying a citation into a governed
regulatory knowledge base. It ships a reusable hexagonal core (a pure-stdlib domain, typed
ports, swappable adapter profiles, a green offline gate) plus a fully worked
MAS/HKMA/APRA/FSA regulatory-Q&A vertical you can keep, retarget to your regulators, or
learn from.

This guide is the step-by-step for making it yours. It has two halves: a **mechanical
rebrand** (one script) and the **human decisions** the script cannot make for you.

> Related reading: [`ARCHITECTURE.md`](../ARCHITECTURE.md), [`CONTRIBUTING.md`](../CONTRIBUTING.md)
> (adding a port / sub-service), the [`faq/`](faq/) directory.

---

## 1. What you keep vs what you rewrite

The domain is pure stdlib and speaks only to ports, so the reusable boundary is explicit:

| Layer | Where | For a new deployment / vertical |
|---|---|---|
| **Reusable core** (vertical-neutral) | the vertical-neutral types in `domain/models.py` (`Citation`, `AuditEvent`, `EvalReport`, `Severity`, `GuardrailVerdict`), the deterministic decision logic (`domain/hitl.py`, `domain/freshness_policy.py`, `domain/_grounded.py`), the ports (`ports/`), the hexagon wiring (`config.py` `Container`) | keep untouched |
| **Policy / config** (your numbers) | `corpus.ttl_days` (freshness window), the whole `horizon:` block (regulated footprint, materiality weights and caps, band thresholds, remediation SLAs, the topic and regulator owner tables, `review_band`), and the settings that thread into the domain policies | change by config, not code |
| **Vertical** (regulatory-Q&A artifacts) | the narrating services (`domain/qa_service.py`, `domain/checklist_service.py`, `domain/testcase_service.py`, `domain/regulator_questions_service.py`), `domain/prompts.py`, the source registry (`pipelines/sources/registry.yaml`), the local fixtures, the eval golden set, the UI views | retarget for your regulators or rewrite for your artifacts |

If your product is another grounded regulatory-Q&A vertical (a different regulator set, a
different jurisdiction), most of the core, the retrieval/grounding pipeline, and the
deterministic human-review and freshness logic transfer directly; you swap the source
registry and prompts, and retune the freshness and review thresholds.

> Note on the kernel split: unlike the document-diligence siblings, this repo does not yet
> carry a dedicated `domain/kernel.py`; the vertical-neutral types currently live alongside
> the vertical artifacts in `domain/models.py` (tracked as practices-audit check A7). A fork
> that wants a hard boundary can lift those types into their own module without touching any
> port.

## 2. Core-vs-adopter-owned files (so upstream merges stay mechanical)

Upstream keeps evolving these; avoid diverging from them so you can pull fixes cleanly:

- **Upstream-owned** (take our changes): the vertical-neutral domain types and decision
  logic, `ports/`, `tests/contract/`, the eval harness mechanics (`eval/run_eval.py`), the
  CI workflows, and the hexagon wiring (`config.py` `Container`).
- **Adopter-owned** (yours; expect to edit): `config/settings.yaml` *values*, the source
  registry (`pipelines/sources/registry.yaml`), the local fixtures
  (`tests/fixtures/sample_regs.py`), `adapters/onprem/*`, UI theming/branding, the golden
  eval dataset, and `COMPLIANCE.md` regulator rows.

Track upstream via git tags; rebase your adopter-owned
changes onto each release rather than merging `main` continuously.

## 3. The mechanical rebrand (one script)

`scripts/rename_fork.py` rewrites the package name (`compliance_advisory`), the CLI entry
point (`compliance`), the `COMPLIANCE_` env prefix, and the `compliance-advisory` resource
ids across the tree in one pass. Preview first, then apply:

```bash
# Preview (writes nothing):
python scripts/rename_fork.py --package acme_reg_qa --cli acme-reg \
    --env-prefix ACME --resource acme-reg-qa --dry-run

# Apply:
python scripts/rename_fork.py --package acme_reg_qa --cli acme-reg \
    --env-prefix ACME --resource acme-reg-qa --yes

# Then recreate the environment (the distribution name changed) and prove it is green:
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
make lint test eval
```

Add `--include-docs` to sweep Markdown prose too. The distribution name defaults to the new
resource stem; pass `--dist` to override.
The script deliberately does NOT touch the human decisions below.

## 4. The human decisions (the script can't make these)

1. **Region / residency.** The build selects a single in-country region at deploy time
   (`region:` in `config/settings.yaml`, the Terraform `region` variable validated against the
   `allowed_regions` residency allowlist), both defaulting to `asia-southeast1` (MAS /
   Singapore). Set BOTH to your in-country region and re-review the
   `infra/terraform/` residency controls (Org Policy allowlist, CMEK, VPC-SC, the WORM
   bucket). See [`docs/runbook.md`](runbook.md).
2. **Identity / IdP.** `local` uses seeded dev personas with no IdP; the secure profiles
   verify the IAP-injected signed assertion (auth configured ON the fronting service, not a
   login this repo implements). Wire your IdP / IAP configuration for the `gcp` / `platform`
   profiles. See [`docs/embedding-and-identity.md`](embedding-and-identity.md).
3. **Source registry / corpus.** `pipelines/sources/registry.yaml` lists the regulator
   sources fetched into the knowledge base. Replace it with your regulators and documents;
   the bundled `local` corpus fixtures are plausible but fictional and must not be treated
   as authoritative guidance.
4. **Freshness and review policy.** Own the numbers a compliance function cares about: the
   corpus freshness window (`corpus.ttl_days`) and the human-review thresholds
   (`domain/hitl.py`). The defaults are a reference, not your policy.
5. **Reference data is fictional.** Every fixture (`tests/fixtures/sample_regs.py`, the
   demo corpus) uses obviously-fake sources and `example.org` URLs. **Do not run against a
   live regulatory corpus, or expose answers to end users, without your own legal, security
   and model-risk sign-off.**
6. **Eval golden set.** Rebuild `eval/datasets/` and the rubrics for your regulators: a
   fork inherits a green gate that measures the WRONG thing until you do. The gate structure
   (groundedness, citation accuracy, faithfulness, safety) is generic; the golden cases are
   yours.
7. **Deployment posture.** Review the Dockerfile (digest-pinned base, non-root),
   `infra/terraform/`, and the loopback-by-default binding (`COMPLIANCE_ALLOW_INSECURE_DEMO`)
   before you expose anything.

## 5. Do not duplicate the platform

This repo is one system in a catalog of composable GRC systems. Several concerns it
*touches* are owned by sibling platform services, and you should integrate rather than
rebuild them (see [`docs/faq/features-faq.md`](faq/features-faq.md) for the full map):
the guardrail gateway (Hrz1), the governed knowledge base (Hrz2), the agent registry
(Hrz3), the AI-quality / eval gate (Hrz4), observability + WORM audit (Hrz5), the
Human-Review and Maker-Checker console (Hrz7), and the on-prem DLP gate (Rsk6). The
`platform` profile's adapters are already thin HTTP clients to those services.

## 6. Adoption checklist

- [ ] Ran `scripts/rename_fork.py`, recreated the venv, `make lint test eval` green.
- [ ] Set region + Terraform tfvars to your in-country region.
- [ ] Wired your IdP / IAP configuration for the secure profiles.
- [ ] Replaced the source registry and every synthetic corpus fixture.
- [ ] Owned the freshness window and review thresholds with your compliance function.
- [ ] Owned the `horizon:` policy with your compliance function: the regulated footprint
      (regulators, jurisdictions, in-scope topics), the materiality weights and band
      thresholds, the remediation SLAs, and the owner tables that name YOUR teams.
- [ ] Rebuilt the eval golden set + rubrics for your regulators.
- [ ] Reviewed the deploy posture (Dockerfile, Terraform, bind address).
- [ ] Decided which sibling platform services you integrate vs stub.
- [ ] Recorded your baseline upstream tag so you can take future fixes.
