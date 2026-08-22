"""TestCaseService — automated test-case generation (SPEC §5).

For a banking use case, retrieves the relevant controls and generates ``TestCase``
objects, each mapped to a control, with ordered steps, a single expected_result, an
``automated_check`` (pseudocode / SQL / rego an engineer can implement), and
page-level citations. Test cases are consequential output and are produced through
the maker-checker pipeline (a human reviews before they are relied upon).

Mirrors the grounded skeleton: redact -> screen(INPUT) -> retrieve -> generate
(structured) -> map source_ids back to retrieved Citations -> audit. Pure domain
code — no Google Cloud / ADK imports.
"""

from __future__ import annotations

from contextlib import nullcontext
from typing import Any

from . import _grounded as g
from .errors import GuardrailBlockedError, RetrievalEmptyError
from .hitl import HumanReviewPolicy
from .models import (
    AuditEvent,
    Citation,
    Decision,
    Direction,
    GuardrailVerdict,
    RetrievedPassage,
    TestCase,
)
from .prompts import _CITATION_RULES, TESTCASE_SYSTEM, TESTCASE_USER

_TESTCASE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "title": {"type": "string"},
                    "control_id": {"type": "string"},
                    "steps": {"type": "array", "items": {"type": "string"}},
                    "expected_result": {"type": "string"},
                    "automated_check": {"type": "string"},
                    "used_source_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": [
                    "id",
                    "title",
                    "control_id",
                    "steps",
                    "expected_result",
                ],
            },
        }
    },
    "required": ["items"],
}


class TestCaseService:
    """Generate automated test cases for a use case. Signature fixed by SPEC §5."""

    def __init__(
        self,
        retrieval: Any,
        llm: Any,
        guardrail: Any,
        redaction: Any,
        tracer: Any,
        audit: Any,
        review_policy: HumanReviewPolicy | None = None,
    ) -> None:
        self._retrieval = retrieval
        self._llm = llm
        self._guardrail = guardrail
        self._redaction = redaction
        self._tracer = tracer
        self._audit = audit
        self._review = review_policy or HumanReviewPolicy()

    def generate(self, use_case: str, actor: str) -> list[TestCase]:
        """Generate test cases for ``use_case`` (consequential, human-reviewed)."""
        span = self._tracer.span("testcases.generate", action="testcases", actor=actor)
        with span if span is not None else nullcontext():
            return self._generate_inner(use_case, actor)

    def _generate_inner(self, use_case: str, actor: str) -> list[TestCase]:
        redacted = self._redaction.redact(use_case).text

        in_verdict: GuardrailVerdict = self._guardrail.screen(redacted, Direction.INPUT)
        if not in_verdict.allowed:
            self._write_audit(actor, redacted, "", Decision.BLOCKED)
            raise GuardrailBlockedError(
                in_verdict.reason or "test-case request blocked by guardrail"
            )
        screened = in_verdict.sanitized_text or redacted

        query = f"controls to test and verify for: {screened}"
        passages: list[RetrievedPassage] = g.retrieve_passages(self._retrieval, query)
        if not passages:
            self._write_audit(actor, redacted, "", Decision.ESCALATED)
            raise RetrievalEmptyError(
                f"no regulatory passages retrieved for use case: {use_case!r}"
            )

        passage_block = g.render_passages(passages)
        system = TESTCASE_SYSTEM.format(citation_rules=_CITATION_RULES)
        user = TESTCASE_USER.format(use_case=screened, passages=passage_block)
        request = g.build_llm_request(
            system_instruction=system,
            user_content=user,
            model=None,
            response_schema=_TESTCASE_SCHEMA,
        )
        response = self._llm.generate(request)
        g.maybe_record_usage(self._tracer, response)

        parsed = g.parse_structured(response)
        test_cases = self._build_test_cases(parsed, passages)
        self._audit_test_cases(actor, redacted, test_cases)
        return test_cases

    def _build_test_cases(
        self, parsed: dict[str, Any], passages: list[RetrievedPassage]
    ) -> list[TestCase]:
        raw_items = parsed.get("items")
        if not isinstance(raw_items, list):
            return []
        out: list[TestCase] = []
        for i, raw in enumerate(raw_items):
            if not isinstance(raw, dict):
                continue
            title = str(raw.get("title") or "").strip()
            if not title:
                continue
            steps = tuple(g.as_str_list(raw.get("steps")))
            automated = raw.get("automated_check")
            out.append(
                TestCase(
                    id=str(raw.get("id") or f"tc-{i + 1}").strip(),
                    title=title,
                    control_id=str(raw.get("control_id") or "").strip(),
                    steps=steps,
                    expected_result=str(raw.get("expected_result") or "").strip(),
                    automated_check=(str(automated).strip() if automated else None),
                    citations=g.citations_for_source_ids(
                        g.as_str_list(raw.get("used_source_ids")), passages
                    ),
                )
            )
        return out

    # ------------------------------------------------------------------ #
    # Audit
    # ------------------------------------------------------------------ #
    def _audit_test_cases(
        self, actor: str, redacted_prompt: str, test_cases: list[TestCase]
    ) -> None:
        summary = "; ".join(tc.title for tc in test_cases)
        citations = tuple(c for tc in test_cases for c in tc.citations)
        self._write_audit(
            actor,
            redacted_prompt,
            summary,
            Decision.ESCALATED,  # consequential -> human checker (P-06)
            citations=citations,
            metadata={"n_test_cases": str(len(test_cases))},
        )

    def _write_audit(
        self,
        actor: str,
        redacted_prompt: str,
        redacted_response: str,
        decision: Decision,
        citations: tuple[Citation, ...] = (),
        metadata: dict[str, str] | None = None,
    ) -> None:
        event = AuditEvent(
            action="testcases",
            actor=actor,
            decision=decision,
            redacted_prompt=redacted_prompt,
            redacted_response=redacted_response,
            citations=citations,
            metadata=metadata or {},
        )
        try:
            self._audit.record(event)
        except Exception:  # noqa: BLE001 - audit must not crash the request
            return
