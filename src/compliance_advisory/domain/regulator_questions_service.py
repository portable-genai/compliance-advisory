"""RegulatorQuestionService — anticipated regulator/CRO questions (SPEC §5).

For a banking use case, retrieves the relevant regulatory passages and generates the
exact questions a regulator or Chief Risk Officer would ask, each with ``why_asked``,
a sourced ``model_answer``, the associated ``regulator``, and page-level citations.
This is consequential output (it shapes supervisory engagement) and runs through the
maker-checker pipeline.

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
    Regulator,
    RegulatorQuestion,
    RetrievedPassage,
)
from .prompts import (
    _CITATION_RULES,
    REGULATOR_QUESTIONS_SYSTEM,
    REGULATOR_QUESTIONS_USER,
)

_REGULATOR_BY_VALUE: dict[str, Regulator] = {r.value: r for r in Regulator}

_REGULATOR_QUESTIONS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "why_asked": {"type": "string"},
                    "model_answer": {"type": "string"},
                    "regulator": {
                        "type": "string",
                        "enum": [r.value for r in Regulator],
                    },
                    "used_source_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": ["question", "why_asked", "model_answer", "regulator"],
            },
        }
    },
    "required": ["items"],
}


def _coerce_regulator(value: Any, default: Regulator = Regulator.CROSS) -> Regulator:
    """Map a model-emitted regulator string to the ``Regulator`` enum defensively."""
    if isinstance(value, Regulator):
        return value
    if isinstance(value, str):
        return _REGULATOR_BY_VALUE.get(value.strip().upper(), default)
    return default


class RegulatorQuestionService:
    """Generate anticipated regulator/CRO questions. Signature fixed by SPEC §5."""

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

    def generate(self, use_case: str, actor: str) -> list[RegulatorQuestion]:
        """Generate the questions a regulator/CRO would ask about ``use_case``."""
        span = self._tracer.span(
            "regulator_questions.generate",
            action="regulator_questions",
            actor=actor,
        )
        with span if span is not None else nullcontext():
            return self._generate_inner(use_case, actor)

    def _generate_inner(self, use_case: str, actor: str) -> list[RegulatorQuestion]:
        redacted = self._redaction.redact(use_case).text

        in_verdict: GuardrailVerdict = self._guardrail.screen(redacted, Direction.INPUT)
        if not in_verdict.allowed:
            self._write_audit(actor, redacted, "", Decision.BLOCKED)
            raise GuardrailBlockedError(
                in_verdict.reason or "regulator-question request blocked by guardrail"
            )
        screened = in_verdict.sanitized_text or redacted

        query = f"supervisory expectations and obligations for: {screened}"
        passages: list[RetrievedPassage] = g.retrieve_passages(self._retrieval, query)
        if not passages:
            self._write_audit(actor, redacted, "", Decision.ESCALATED)
            raise RetrievalEmptyError(
                f"no regulatory passages retrieved for use case: {use_case!r}"
            )

        passage_block = g.render_passages(passages)
        system = REGULATOR_QUESTIONS_SYSTEM.format(citation_rules=_CITATION_RULES)
        user = REGULATOR_QUESTIONS_USER.format(use_case=screened, passages=passage_block)
        request = g.build_llm_request(
            system_instruction=system,
            user_content=user,
            model=None,
            response_schema=_REGULATOR_QUESTIONS_SCHEMA,
        )
        response = self._llm.generate(request)
        g.maybe_record_usage(self._tracer, response)

        parsed = g.parse_structured(response)
        questions = self._build_questions(parsed, passages)
        self._audit_questions(actor, redacted, questions)
        return questions

    def _build_questions(
        self, parsed: dict[str, Any], passages: list[RetrievedPassage]
    ) -> list[RegulatorQuestion]:
        raw_items = parsed.get("items")
        if not isinstance(raw_items, list):
            return []
        out: list[RegulatorQuestion] = []
        for raw in raw_items:
            if not isinstance(raw, dict):
                continue
            question = str(raw.get("question") or "").strip()
            if not question:
                continue
            out.append(
                RegulatorQuestion(
                    question=question,
                    why_asked=str(raw.get("why_asked") or "").strip(),
                    model_answer=str(raw.get("model_answer") or "").strip(),
                    regulator=_coerce_regulator(raw.get("regulator")),
                    citations=g.citations_for_source_ids(
                        g.as_str_list(raw.get("used_source_ids")), passages
                    ),
                )
            )
        return out

    # ------------------------------------------------------------------ #
    # Audit
    # ------------------------------------------------------------------ #
    def _audit_questions(
        self,
        actor: str,
        redacted_prompt: str,
        questions: list[RegulatorQuestion],
    ) -> None:
        summary = "; ".join(q.question for q in questions)
        citations = tuple(c for q in questions for c in q.citations)
        self._write_audit(
            actor,
            redacted_prompt,
            summary,
            Decision.ESCALATED,  # consequential -> human checker (P-06)
            citations=citations,
            metadata={"n_questions": str(len(questions))},
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
            action="regulator_questions",
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
