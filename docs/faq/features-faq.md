# Features FAQ

For product, compliance, and delivery teams: what this assistant does, what is
deterministic vs LLM, and, importantly, where its responsibilities **stop** and a sibling
catalog system takes over. Cross-references: [`README.md`](../../README.md),
[`DEMO.md`](../../DEMO.md).

### What does Rsk1 actually produce?

Four grounded artifacts over a public regulatory knowledge base (MAS/HKMA/APRA/FSA):

- a cited **answer** to a compliance question (`compliance ask`),
- a **control checklist** for a use case (`compliance checklist`),
- **control test cases** that verify each control (`compliance testcases`), and
- a **regulator-question** response pack (`compliance regulator-questions`).

Every claim carries a source-and-page `Citation` back into the retrieved regulatory
passages, and every assessment writes an already-redacted WORM `AuditEvent`.

### What is deterministic vs done by the LLM?

The consequential decisions are **deterministic and replayable** (pure stdlib,
unit-tested): the human-review policy (`HumanReviewPolicy.requires_review` in
`domain/hitl.py`), the corpus freshness policy (`FreshnessPolicy.is_stale` in
`domain/freshness_policy.py`), the citation mapping (used-source-ids back to retrieved
citations, `domain/_grounded.py`), and the severity roll-up. The LLM only **drafts and
narrates** (the answer text, the checklist and test-case wording) and **classifies /
triages** (routing, retrieval-need). An auditor can recompute every review and freshness
decision without the model. This is by design (the "deterministic domain service" pattern).

### Is anything auto-approved or auto-executed?

No. This is a **decision-support** assistant: it proposes, a qualified human disposes.
Consequential artifacts (the control checklist, test cases, regulator questions) always set
`requires_human_review=True`, and any escalated output is routed to the Hrz7 Human-Review
and Maker-Checker console via the shared `review-kit` (rule R8), not left as a per-repo
boolean. Note that a plain `ask` answer computes its review flag from confidence and
severity, so a high-confidence, low-severity answer can return with the flag `False`;
nothing auto-executes regardless (it is an advisory assistant).

### What happens on empty retrieval?

The consequential generators (checklist, test cases, regulator questions) hard-fail on
empty retrieval rather than inventing content (proven in `scripts/portability_demo.py`).
A plain `ask` deliberately **degrades** instead: it returns a low-confidence, zero-citation,
`requires_human_review=True` refusal rather than raising, so the caller gets a safe "I could
not ground an answer" rather than an exception. The refusal fabricates nothing.

### Which capabilities does this repo own vs integrate from the catalog?

This is one system in a catalog of composable GRC systems. It **owns** the grounded
regulatory-Q&A domain logic and its artifacts, plus the control-mapping module
(control mappings, gaps, and evidence packs). It **integrates** (via the `platform`
profile's HTTP adapters) several cross-cutting concerns owned by sibling platform systems.
Do not rebuild these in a fork:

| Concern | Owned by (catalog id / repo) | Rsk1's role |
|---|---|---|
| Runtime guardrail: PII redaction, prompt-injection / jailbreak defense | **Hrz1** `agent-guardrail-gateway` | consumes it on the input + output screen |
| Governed RAG / knowledge base with citations | **Hrz2** `enterprise-knowledge-base` | retrieves grounded passages from it (the `gcp` profile uses managed Agent Search) |
| Agent registry, versioning, identity | **Hrz3** `agent-registry` | publishes its A2A AgentCard for discovery |
| AI-quality / eval / model-risk promotion gate | **Hrz4** `model-quality-gate` | its eval metrics gate promotion; the offline gate mirrors it (bundle `rsk1-compliance-advisory`) |
| Observability + immutable WORM prompt/response audit | **Hrz5** `agent-observability` | writes audit events to it; traces spans through it |
| Human-Review and Maker-Checker console | **Hrz7** (`review-kit`) | routes every escalated output to it (R8) |
| On-prem, CPU-only DLP scrub before egress | **Rsk6** `onprem-dlp` | the sovereign-DLP option behind the redaction port |

So the guardrail, knowledge base, audit sink, eval platform and review console are
*dependencies*, not features of this repo.

### How does this relate to the document-diligence systems in the catalog?

Rsk1 is the horizontal regulatory-Q&A assistant that the document-diligence verticals
(for example the CDD/SoW agent) *consume* for their regulatory compliance checks. They own
the case-level diligence logic; Rsk1 owns the grounded regulatory reasoning and control
mapping. Check [the organization's repository index](https://github.com/portable-genai)
before building a capability that may already have a home.

### Can I use this for a different regulator set or jurisdiction?

Yes, that is the point of the ports-and-adapters split. The reusable core (grounding,
citations, the deterministic review and freshness logic, audit, eval, maker-checker routing)
transfers directly. You swap the source registry (`pipelines/sources/registry.yaml`) and the
prompts, and retune the freshness and review thresholds. See
[`docs/ADOPTING.md`](../ADOPTING.md) and [adoption-faq.md](adoption-faq.md).

### How do I see it working?

`make demo` runs the offline four-artifact flow and renders a static audit-first HTML view
into `./out` (one command, no cloud, no API key). `make demo-server` runs a live,
presenter-controlled server on the real `local` stack (port 8088). Everything runs on
synthetic, fictional regulatory content.
