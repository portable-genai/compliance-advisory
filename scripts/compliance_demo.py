"""Runnable demo of the C1 grounded-compliance flow (synthetic, fictional corpus).

Drives a single Compliance/Risk *use case* through the C1 pipeline and produces the
four cited artifacts the assistant exists to deliver:

  1. Answer               — a grounded, page-cited answer to a compliance question.
  2. ControlChecklist     — the controls the use case requires, with cited rationale.
  3. TestCase[]           — automated tests that verify each control.
  4. RegulatorQuestion[]  — the questions a regulator / CRO will ask, with cited answers.

Everything runs on the ``local`` profile: SQLite FTS5 over the built-in synthetic
MAS / HKMA / APRA corpus + a deterministic (no-network, no-SDK) LLM. No Google Cloud,
no API key. The retrieval / guardrail / redaction / maker-checker / WORM-audit path is
the real one — only the managed adapters are swapped for offline equivalents.

Run it::

    PYTHONPATH=src:tests python scripts/compliance_demo.py [out.json]

It prints a per-artifact summary and writes the full audit-view JSON (the four artifacts
plus the WORM audit trail recorded for the run) for the static renderer to turn into
audit-first HTML pages.
"""

from __future__ import annotations

import json
import os
import sys

# The local stores default to ~/.compliance_advisory/*.db; pin them to in-memory so the
# demo is hermetic and deterministic regardless of what is on the presenter's laptop.
os.environ.setdefault("COMPLIANCE_PROFILE", "local")
os.environ.setdefault("COMPLIANCE_LOCAL_DB", ":memory:")
os.environ.setdefault("COMPLIANCE_LOCAL_AUDIT", ":memory:")
os.environ.setdefault("COMPLIANCE_LOCAL_LEDGER", ":memory:")

from compliance_advisory.api import deps  # noqa: E402
from compliance_advisory.config import Settings, build_container  # noqa: E402
from compliance_advisory.domain.serialization import to_jsonable  # noqa: E402

# The synthetic scenario (clearly fictional). One use case, one question, several actors.
USE_CASE = "Onboard a public-cloud SaaS provider for core banking workloads (FICTIONAL)"
QUESTION = (
    "What cloud-outsourcing controls does MAS expect before onboarding a cloud "
    "service provider for a core banking workload?"
)
REGULATOR_FILTER = "MAS"  # left-rail regulator filter, as in the console
ANALYST = "analyst@bank.test"
REVIEWER = "mlro.lim@bank.test"

# The four artifacts, in the order the console tabs present them.
ARTIFACTS = [
    {
        "key": "answer",
        "label": "Answer — grounded, page-cited reply to the compliance question",
        "endpoint": "POST /ask",
    },
    {
        "key": "checklist",
        "label": "Control checklist — the controls this use case requires",
        "endpoint": "POST /checklist",
    },
    {
        "key": "testcases",
        "label": "Test cases — automated checks that verify each control",
        "endpoint": "POST /testcases",
    },
    {
        "key": "regulator_questions",
        "label": "Regulator questions — what a regulator / CRO will ask, answered",
        "endpoint": "POST /regulator-questions",
    },
]


def _container():  # type: ignore[no-untyped-def]
    return build_container(Settings.load())


def run() -> dict:
    """Run the four-artifact flow once and return the audit-view payload."""
    container = _container()
    settings = container.settings

    answer = deps.build_qa_service(container).answer(
        QUESTION, actor=ANALYST, filters={"regulator": REGULATOR_FILTER}
    )
    checklist = deps.build_checklist_service(container).build(USE_CASE, actor=ANALYST)
    testcases = deps.build_testcase_service(container).generate(USE_CASE, actor=ANALYST)
    regq = deps.build_regulator_question_service(container).generate(USE_CASE, actor=ANALYST)

    # The WORM audit trail every service wrote for this run (already PII-redacted).
    audit_trail = []
    if hasattr(container.audit, "read_all"):
        audit_trail = container.audit.read_all()

    return {
        "profile": settings.profile,
        "region": settings.region,
        "use_case": USE_CASE,
        "question": QUESTION,
        "regulator_filter": REGULATOR_FILTER,
        "actor": ANALYST,
        "reviewer": REVIEWER,
        "artifacts": {
            "answer": to_jsonable(answer),
            "checklist": to_jsonable(checklist),
            "testcases": to_jsonable(testcases),
            "regulator_questions": to_jsonable(regq),
        },
        "audit_trail": to_jsonable(audit_trail),
    }


def _n_cit(obj: dict) -> int:
    return len(obj.get("citations", []) or [])


def _print_summary(payload: dict) -> None:
    print(f"Profile {payload['profile']} · region {payload['region']} (offline, no cloud)\n")
    print(f"Use case: {payload['use_case']}")
    print(f"Question: {payload['question']}")
    print(f"Regulator filter: {payload['regulator_filter']}\n")

    a = payload["artifacts"]["answer"]
    print("-- Answer")
    print(
        f"   confidence {a['confidence']:.2f}   citations {_n_cit(a)}   "
        f"human-review {str(a['requires_human_review']).lower()}"
    )
    for c in a.get("citations", []):
        page = f" p.{c['page']}" if c.get("page") is not None else ""
        print(f"     cite [{c['source_id']}, {c['regulator']} {c['version']}{page}]")

    ck = payload["artifacts"]["checklist"]
    print(
        f"\n-- Control checklist  ({len(ck.get('items', []))} controls; "
        f"human-review {str(ck.get('requires_human_review')).lower()})"
    )
    for it in ck.get("items", []):
        print(
            f"     [{it['control_id']}] ({it['severity']}) {it['control']}  "
            f"({_n_cit(it)} citations)"
        )

    tcs = payload["artifacts"]["testcases"]
    print(f"\n-- Test cases  ({len(tcs)})")
    for tc in tcs:
        print(
            f"     [{tc['id']}] {tc['title']}  (verifies {tc['control_id']}; "
            f"{_n_cit(tc)} citations)"
        )

    rqs = payload["artifacts"]["regulator_questions"]
    print(f"\n-- Regulator questions  ({len(rqs)})")
    for q in rqs:
        print(f"     ({q['regulator']}) {q['question']}  ({_n_cit(q)} citations)")

    print(f"\n-- WORM audit trail  ({len(payload['audit_trail'])} records, PII-redacted)")
    for ev in payload["audit_trail"]:
        print(f"     {ev.get('action'):<20} {ev.get('decision'):<10} actor={ev.get('actor')}")


def main(out_path: str) -> None:
    payload = run()
    _print_summary(payload)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
    print(f"\nWrote audit-view JSON -> {out_path}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "compliance_demo.json")
