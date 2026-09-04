# Portability FAQ

For architecture, cloud-governance, and exit-planning teams. The claim this repo makes is
"no vendor lock-in, demonstrably", and it is designed to be *shown*, not asserted.
Cross-references: [`ARCHITECTURE.md`](../../ARCHITECTURE.md),
[`docs/onprem-migration.md`](../onprem-migration.md).

### What does "portable" actually mean here?

The whole stack migrates by a one-line profile change with no domain edits. The pure-domain
core speaks only to `typing.Protocol` ports; four adapter families implement them; and
`config/settings.yaml` binds one adapter per port per profile. Run
`PYTHONPATH=src python scripts/portability_demo.py` for the executable proof (four acts:
one-line profile swap, port parity across the SDK-free profiles, offline pipeline breadth
including hard-fail on empty retrieval, and identity portability). Exit code 0 means every
check passed.

### How does the profile switch work?

Setting `COMPLIANCE_PROFILE` (or `profile:` in the settings) rebinds the entire stack:

- `local`: a WORKING offline stack (SQLite FTS5 retrieval, deterministic LLM, regex
  redaction, hash-chained audit). No Google Cloud SDK. The default for dev/test/CI.
- `gcp`: real managed services (Agent Search retrieval, Gemini, Model Armor guardrail, DLP,
  Cloud Logging WORM, Cloud Trace, Gen AI Evals, AlloyDB freshness ledger).
- `platform`: thin HTTP clients delegating to the sibling horizontal-platform and
  de-risking services.
- `onprem`: fail-fast Google Distributed Cloud placeholders that still satisfy every
  Protocol (the sovereign-exit target); a primary command exits non-zero by design.

No `domain/` code changes across any of these. The contract test
(`tests/contract/test_port_parity.py`) proves both `local` and `onprem` construct and
satisfy every port with no cloud SDK installed.

### How do we get our data out?

The audit trail is a hash chain built on the shared `hex_service_kit.audit.HashChainedAuditLog`
(`LocalAppendOnlyAuditAdapter`): it exports to JSON Lines and reloads into a fresh store with
the chain re-verified line by line (`verify_chain()`), so the exit story for the audit trail
is "copy the JSONL file", not "migrate a product". Answers, checklists and the other
artifacts serialize to plain JSON via the shared `to_jsonable`, so case outputs are portable
open documents, not opaque blobs.

### Is on-prem / sovereign deployment real or aspirational?

The `onprem` adapters are deliberate fail-fast placeholders that nonetheless satisfy every
Protocol and construct with a single `Settings` arg, so the *interface contract* for a
sovereign migration is proven and enforced by CI today (the eval gate runs on
`COMPLIANCE_PROFILE=onprem`). The actual on-prem implementations are the migration work,
scoped in [`docs/onprem-migration.md`](../onprem-migration.md). This repo is not the
sovereign-exit *planner* (that is the sibling **the exit-and-portability planner exit-portability planner**: APRA CPS
230, MAS/HKMA outsourcing); this repo is one of the systems whose exit that planner reasons
about.

### Does residency compromise portability?

No. Residency is a deploy-time pin (a single in-country region, `asia-southeast1` by
default, plus the Org Policy resource-location allowlist, CMEK, and VPC-SC), and portability
is the ability to change *where* the stack runs by configuration. They are orthogonal. The
region is validated to fail fast (the Terraform `region` variable rejects anything but the
pinned region), and a second region is a tfvars change, not a fork. Residency enforcement
infra overlaps with the sibling **the data-residency validator residency validator** (a CI gate for region
violations), which a fork should run rather than re-implement.

### Does the knowledge base lock us in?

No. Retrieval is a port (`ports/retrieval.py`) with a managed adapter (Agent Search under
`gcp`) and an offline adapter (SQLite FTS5 under `local`). The governed enterprise knowledge
base is the sibling `enterprise-knowledge-base` system, consumed through the same port; a fork can point the
port at `enterprise-knowledge-base`, at managed Agent Search, or at the offline index without any domain change. The
regulatory corpus itself is defined by data (`pipelines/sources/registry.yaml`), not code.

### What is NOT yet fully portable?

The managed freshness ledger (`AlloyDBLedgerAdapter`) and the managed session/memory stores
are cloud-backed under `gcp`; the `local` in-process stores and the `onprem` placeholders
prove parity, but the production managed equivalents are the migration work. Everything in
the offline four-artifact pipeline is exercised across `local` and `gcp`.
