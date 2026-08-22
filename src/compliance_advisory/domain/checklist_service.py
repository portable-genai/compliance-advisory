"""ChecklistService — use-case control-checklist generation (SPEC §5).

Retrieves the controls relevant to a banking use case, generates a grounded
``ControlChecklist`` of ``ChecklistItem`` entries (control_id / control / rationale /
severity / citations), and always flags the result for human review (maker-checker,
P-06) because a control checklist is a consequential artifact.

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
    ChecklistItem,
    Citation,
    ControlChecklist,
    Decision,
    Direction,
    GuardrailVerdict,
    RetrievedPassage,
)
from .prompts import _CITATION_RULES, CHECKLIST_SYSTEM, CHECKLIST_USER

_CHECKLIST_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "control_id": {"type": "string"},
                    "control": {"type": "string"},
                    "rationale": {"type": "string"},
                    "severity": {
                        "type": "string",
                        "enum": ["low", "medium", "high", "critical"],
                    },
                    "used_source_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": ["control_id", "control", "rationale", "severity"],
            },
        }
    },
    "required": ["items"],
}


class ChecklistService:
    """Generate a use-case control checklist. Signature fixed by SPEC §5."""

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

    def build(self, use_case: str, actor: str) -> ControlChecklist:
        """Build a control checklist for ``use_case`` (always human-reviewed)."""
        span = self._tracer.span("checklist.build", action="checklist", actor=actor)
        with span if span is not None else nullcontext():
            return self._build_inner(use_case, actor)

    def _build_inner(self, use_case: str, actor: str) -> ControlChecklist:
        redacted = self._redaction.redact(use_case).text

        in_verdict: GuardrailVerdict = self._guardrail.screen(redacted, Direction.INPUT)
        if not in_verdict.allowed:
            self._write_audit(actor, redacted, "", Decision.BLOCKED)
            raise GuardrailBlockedError(
                in_verdict.reason or "checklist request blocked by guardrail"
            )
        screened = in_verdict.sanitized_text or redacted

        query = f"controls and obligations for: {screened}"
        passages: list[RetrievedPassage] = g.retrieve_passages(self._retrieval, query)
        if not passages:
            self._write_audit(actor, redacted, "", Decision.ESCALATED)
            raise RetrievalEmptyError(
                f"no regulatory passages retrieved for use case: {use_case!r}"
            )

        passage_block = g.render_passages(passages)
        system = CHECKLIST_SYSTEM.format(citation_rules=_CITATION_RULES)
        user = CHECKLIST_USER.format(use_case=screened, passages=passage_block)
        request = g.build_llm_request(
            system_instruction=system,
            user_content=user,
            model=None,
            response_schema=_CHECKLIST_SCHEMA,
        )
        response = self._llm.generate(request)
        g.maybe_record_usage(self._tracer, response)

        parsed = g.parse_structured(response)
        items = self._build_items(parsed, passages)

        # P-06: a control checklist is consequential and always requires review.
        checklist = ControlChecklist(use_case=use_case, items=items, requires_human_review=True)
        self._audit_checklist(actor, redacted, checklist)
        return checklist

    def _build_items(
        self, parsed: dict[str, Any], passages: list[RetrievedPassage]
    ) -> tuple[ChecklistItem, ...]:
        raw_items = parsed.get("items")
        if not isinstance(raw_items, list):
            return ()
        out: list[ChecklistItem] = []
        for i, raw in enumerate(raw_items):
            if not isinstance(raw, dict):
                continue
            control = str(raw.get("control") or "").strip()
            if not control:
                continue
            control_id = str(raw.get("control_id") or f"control-{i + 1}").strip()
            out.append(
                ChecklistItem(
                    control_id=control_id,
                    control=control,
                    rationale=str(raw.get("rationale") or "").strip(),
                    severity=g.coerce_severity(raw.get("severity")),
                    citations=g.citations_for_source_ids(
                        g.as_str_list(raw.get("used_source_ids")), passages
                    ),
                )
            )
        return tuple(out)

    # ------------------------------------------------------------------ #
    # Audit
    # ------------------------------------------------------------------ #
    def _audit_checklist(
        self, actor: str, redacted_prompt: str, checklist: ControlChecklist
    ) -> None:
        summary = "; ".join(item.control for item in checklist.items)
        citations = tuple(c for item in checklist.items for c in item.citations)
        self._write_audit(
            actor,
            redacted_prompt,
            summary,
            Decision.ESCALATED,  # always routed to a human checker (P-06)
            citations=citations,
            metadata={"n_items": str(len(checklist.items))},
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
            action="checklist",
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
