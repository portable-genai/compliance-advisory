# Compliance FAQ

For compliance, MLRO, and model-risk teams assessing the repo's regulatory posture.
Cross-references: [`COMPLIANCE.md`](../../COMPLIANCE.md) (the full principle-to-control map),
[`SPEC.md`](../../SPEC.md).

### Is this making regulatory decisions autonomously?

No. It is a **decision-support** assistant: every consequential output is proposed for human
review, never auto-executed. The consequential artifacts (control checklist, control test
cases, regulator questions) always set `requires_human_review=True`, and any escalated output
is routed to the Hrz7 Human-Review and Maker-Checker console via the shared `review-kit`
(rule R8). The deterministic engines produce a documented, replayable assessment; a qualified
human (analyst / compliance officer) disposes.

### How is PII handled? This answers over public guidance, so is there any?

The system answers over a **public regulatory corpus** and ingests no customer documents, so
the honest-gate PII concern that document-diligence verticals face does not arise here. As
defence-in-depth, redaction is still the **first** step of every request
(`ComplianceQAService._answer_inner`, before guardrail, retrieval, LLM and audit): the local
`LocalRegexRedactionAdapter` masks anything a user types into a question (SG NRIC/FIN, email,
SG phone) and the `gcp` profile uses DLP de-identify. The `AuditEvent` stores only the
redacted prompt and response, and the tracer span carries no question content. The runtime
guardrail (prompt-injection / jailbreak defence) is the sibling **Hrz1** gateway, consumed
rather than re-implemented.

The control-mapping module (`/map`, `/gaps`, `/evidence-pack`) runs a **separate**
path with a different posture: it reasons over the bank's own GCP control posture, not
customer data, so it runs **without** redaction and **without** the guardrail, by design
(R1 / P-04 = n/a for that module). See [`COMPLIANCE.md`](../../COMPLIANCE.md) section A0 for
the per-module split.

### How is the work auditable / reproducible?

Every assessment writes an immutable, already-redacted WORM `AuditEvent` with the decision
and the citation set. Every answer statement carries a source-and-page `Citation`, and the
consequential logic (review policy, freshness policy, citation mapping, severity roll-up) is
deterministic, so an auditor can recompute any review or freshness decision from the same
inputs. The enterprise WORM audit system is **Hrz5**; the in-repo hash-chained store is the
offline/local stand-in (see [security-faq.md](security-faq.md) for its exact tamper-evidence
limits).

### What is the model-risk story?

An offline eval gate (`eval/run_eval.py`) scores groundedness, citation accuracy,
faithfulness, and safety against a golden set, with a `--mode smoke | gate` split. The
enterprise promotion gate and model-risk harness are the sibling **Hrz4** system; this repo's
gate mirrors its thresholds (registered bundle `rsk1-compliance-advisory`, the
`remote_evaluation` platform adapter) so merges are guarded locally, and gate mode refuses to
run outside `COMPLIANCE_PROFILE=platform | gcp`. A fork must rebuild the golden set for its
own regulators, or the gate measures the wrong thing.

### Which regulators does this map to?

`COMPLIANCE.md` maps the internal P-01..P-12 and R1..R6 controls to concrete code with file
references. The reference corpus targets MAS (Singapore), HKMA (Hong Kong), APRA (Australia)
and FSA (Japan). To add or retarget a regulator, edit the source registry
(`pipelines/sources/registry.yaml`) and re-review with local counsel. At scale, the sibling
**Rsk2 control-mapping toolkit** generates and maintains crosswalks; a large estate should
integrate it rather than hand-maintain the tables. Note the repo does not yet carry an
explicit adopter-owned per-regulator crosswalk appendix (practices-audit check G2), which a
fork should add.

### Is data residency enforced?

Yes, at deploy time: a single in-country region (default `asia-southeast1` / Singapore),
validated to fail fast (the Terraform `region` variable rejects other regions), with regional
endpoints, a resource-location Org Policy allowlist, CMEK, and a VPC-SC perimeter. The
residency-violation CI gate is the sibling **Rsk4 residency validator**; the
exit/concentration-risk plan is **Rsk5**. This repo enforces residency in its own infra and
is one of the systems those tools reason about.

### Can we run it against a live regulatory corpus and expose answers to users today?

Not without your own legal, security, and model-risk sign-off. Every fixture and the demo
corpus are obviously-fictional (`tests/fixtures/sample_regs.py` states its sources are
"plausible but fictional", and golden data uses `example.org`), and the docs state
throughout that this is a reference build. The adoption checklist
([`docs/ADOPTING.md`](../ADOPTING.md) section 6) lists the steps that must precede any
live use: replace the source registry, own the freshness and review thresholds, wire your
IdP, and rebuild the eval golden set.

### Which compliance activities does it cover, and which does it not?

It covers grounded regulatory Q&A, control-checklist drafting, control test-case generation,
and regulator-question preparation over a public regulatory knowledge base. It does **not**
do case-level customer diligence (CDD/KYC, source-of-wealth), sanctions-hit disposition, or
transaction-monitoring alert triage; those are separate catalog systems (the
document-diligence verticals and the proposed FCC systems) that *consume* Rsk1 for their
regulatory checks rather than duplicating it. See [features-faq.md](features-faq.md) for
the boundary.
