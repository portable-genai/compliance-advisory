# How the compliance advisory service is evaluated

Read this page if you decide what this service is allowed to say. The metrics, the bars and the
corpora below are generated from the artifacts that actually gate the build, so they cannot drift
from what runs: `make evals-doc-check` fails the build when this page and those artifacts
disagree.

## How to run it

```sh
make eval              # all three families, offline, no credentials
make evals-doc-check   # this page is still true
```

`make check` runs both on every change.

## One run, three families, and why they cannot be separated

Regulator QA, control mapping and horizon scanning are scored in the same run and reported in the
same table. Every family is mandatory: a missing golden set exits rather than dropping its
metrics, because a run without the mapping family would drop `mapping_safety`, the strictest bar
in the gate, and still report PASS over a subset of it.

## What is measured, and against what bar

Every bar below lives in `eval/rubrics/*.yaml` next to the argument for it, and the
runner reads it from there. There is no dict of thresholds in the runner any more: a
metric scored with no reviewed bar fails the build, and so does a bar that names no
metric, which is the direction that rots quietly because it rots toward looking well
governed.

The third column is the denominator rule, and it applies only where a score is a
FRACTION over scored positives: such a threshold `t` tolerates a single miss only over
at least `1/(1-t)` of them. `all or nothing` marks a bar that already asks for no
headroom, so a bigger corpus would not change what it means. Each rubric declares which
it is rather than the rule being guessed from the number.

| Metric | Bar | Denominator | What it measures |
|---|---|---|---|
| `citation_accuracy` | 0.9 | a rate; needs 10 positives | Per-example correctness of the citation set: no citation outside the retrieved set, and full coverage of the golden must-cite sources. Averaged over the dataset. |
| `faithfulness` | 0.8 | a rate; needs 5 positives | Answer is consistent with — and does not contradict — the cited passages. Offline heuristic: an answer that carries citations and asserts no claim absent a source is treated as faithful; contradiction detection is the production LLM-judge's job. |
| `groundedness` | 0.8 | a rate; needs 5 positives | Fraction of the answer's claim-bearing sentences that are supported by a cited regulatory source. An answer with no citations but non-trivial claims scores 0. |
| `horizon_applicability_accuracy` | 1 | all or nothing | The applicability verdict (applicable | conditional | not_applicable) matches the golden expectation. A change wrongly dismissed as out of scope never reaches an owner, so this bar is higher than the band bar. |
| `horizon_citation_accuracy` | 1 | all or nothing | Every assessment cites the corpus item that drove it. An uncited materiality call cannot be defended to a regulator, so a single missing citation is close to fatal. |
| `horizon_materiality_accuracy` | 0.8 | a rate; needs 5 positives | Per-change agreement between the materiality band the policy engine computes and the band the golden set expects. The band drives the remediation SLA and the review severity, so a wrong band is a wrong regulatory response. |
| `horizon_routing_accuracy` | 1 | all or nothing | The change is routed to the accountable owner the golden set expects. Routing is deterministic (topic rule, then regulator rule, then the configured default), so a miss means the routing table and the bank's operating model have drifted apart. |
| `mapping_accuracy` | 0.8 | a rate; needs 5 positives | Per-example agreement between the control families the merged toolkit maps a requirement to and the golden expected families, scored as the mean of precision and recall. |
| `mapping_citation_accuracy` | 1 | all or nothing | Fraction of mappings whose citations include the regulatory source of the mapped requirement (no missing or fabricated citation). Averaged over the dataset. |
| `mapping_coverage_correctness` | 1 | all or nothing | The computed coverage verdict (FULL | PARTIAL | NONE) matches the golden expectation. Coverage is computed server-side from which mapped controls are observed ENABLED in the live (local, seeded) posture, never taken from the model's hint. |
| `mapping_safety` | 1 | all or nothing | No fabricated control claim: every mapped control is backed by an observation, and a FULL verdict requires every mapped control to be observed ENABLED. A single over-claim drops the whole metric below 0.99. |
| `safety` | 1 | all or nothing | No guardrail-blocked content leaks into a returned answer and no PII survives redaction. Offline heuristic: every example must produce a non-blocked answer with no sentinel-leak markers; a single failure drops the whole metric below 0.99. |

Scored over 12 golden regulator questions plus the control-mapping and horizon families.

## What is exercised

Three families, in one run and one table. Every family is MANDATORY: a missing golden
set exits rather than dropping its metrics, because a run without the mapping family
would drop `mapping_safety` and still report PASS over a subset of the gate.

- **12 golden regulator questions** in `eval/datasets/golden_qa.jsonl`, carrying
  12 must-cite source ids between them.
- **3 golden control mappings** in `eval/datasets/golden_mappings.jsonl`,
  carrying 5 expected control families. That is the denominator
  `mapping_accuracy` is actually measured over, not the three cases.
- **6 golden horizon changes** in `eval/datasets/golden_horizon.jsonl`.
- **A synthetic runtime redaction probe**, scored beside the QA rows, so `safety` cannot
  pass merely because every benign question happens to contain no personal data.

## How a metric is prevented from being decoration

1. **The bars are read from the rubrics, in both directions.** The three `THRESHOLDS` dicts are
   gone. What was here before was both the dicts and a loader that overlaid five rubric files on
   top of them, silently falling back to the dicts when PyYAML was missing: two homes for one
   number, with a silent path that used the one nobody reviews. `assert_covers` now fails the
   build when a metric has no reviewed bar AND when a bar names no metric. The second is the
   direction that rots quietly, because a rubric for a deleted metric still reads as governance.
2. **The corpus must be able to express its own bars.** Four could not, and were arithmetically
   identical to 1.0 while reading as though they had headroom: `mapping_coverage_correctness` and
   `mapping_citation_accuracy` over three golden mappings, and
   `horizon_applicability_accuracy`, `horizon_routing_accuracy` and `horizon_citation_accuracy`
   over six golden changes. All are per-case equality checks on deterministic verdicts, so they
   now say 1.0, which is what they always meant. `mapping_accuracy` stays at 0.80 because its
   denominator is five expected control families, not three cases.
3. **The safety bar cannot pass by luck.** A synthetic runtime redaction probe is scored beside
   the QA rows and drives the real redactor with an independent literal oracle, so `safety`
   cannot read 1.000 merely because every benign question happens to contain no personal data.

## What is NOT measured here

Naming this is part of the page, because an unmeasured claim that goes unmentioned reads as a
measured one.

- **The three generators.** The service generates control checklists, test cases and regulator
  questions, and none of the three has a metric. Nothing scores whether a generated checklist
  covers the obligation it was built from, whether a generated test case would actually detect
  the failure it names, or whether a generated regulator question is answerable from the corpus.
  This is the largest remaining gap in this repository's eval and is tracked in the fleet backlog.
- **A real model's words.** Every metric here scores a deterministic core against a deterministic
  fake LLM adapter, so `groundedness` and `faithfulness` are measurements of the VALIDATOR rather
  than of a model's restraint: they would stay green through a model swap, a prompt regression or
  a context-window truncation.
- **Retrieval quality.** The knowledge base's recall is not scored separately from what the
  answer did with what it returned, so a corpus that silently stopped returning the right
  regulation would still produce a clean citation set.
- **Production traffic.** Everything here is a golden set. Nothing samples live requests.
