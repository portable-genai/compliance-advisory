"""ComplianceQAService — grounded compliance Q&A orchestration (SPEC §5).

Owns the standard answer pipeline and calls only ports. The pipeline, in order:

    tracer.span("qa.answer"):
      redact(question)
      -> guardrail.screen(INPUT)          [blocked -> audit BLOCKED + blocked Answer]
      -> retrieval.retrieve               [empty -> audit ESCALATED + hard refusal]
      -> grounding.ground (if enabled)
      -> llm.generate(system + passages, structured {answer, used_source_ids, confidence})
      -> assemble Answer + map used_source_ids back to retrieved Citations (keep page)
      -> self-critique groundedness pass (second llm call) adjusts confidence/caveats
      -> HumanReviewPolicy sets requires_human_review
      -> guardrail.screen(OUTPUT)
      -> audit.record(redacted prompt + response)

Defensive throughout: empty retrieval raises after an escalation audit, malformed model JSON
degrades to a safe reviewed answer, and guardrail blocks return an explicit blocked answer.

Pure domain code — no Google Cloud / ADK / FastAPI imports.
"""

from __future__ import annotations

import contextlib
from contextlib import nullcontext
from typing import Any

from . import _grounded as g
from .errors import RetrievalEmptyError
from .hitl import HumanReviewPolicy
from .models import (
    Answer,
    AuditEvent,
    Citation,
    Decision,
    Direction,
    GuardrailVerdict,
    RedactionResult,
    RetrievedPassage,
    Severity,
    WebCitation,
)
from .policy import CompliancePolicyConfig
from .prompts import (
    _CITATION_RULES,
    GROUNDED_QA_SYSTEM,
    GROUNDED_QA_USER,
    SELF_CRITIQUE_SYSTEM,
    SELF_CRITIQUE_USER,
)
from .serialization import to_jsonable

# JSON schema for the structured Q&A response.
_ANSWER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "answer": {"type": "string"},
        "used_source_ids": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "number"},
    },
    "required": ["answer", "used_source_ids", "confidence"],
}

# JSON schema for the self-critique groundedness pass.
_CRITIQUE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "grounded": {"type": "boolean"},
        "confidence": {"type": "number"},
        "caveats": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["grounded", "confidence", "caveats"],
}

_BLOCKED_CAVEAT = "The request was blocked by the safety guardrail and was not answered."
_UNSUPPORTED_CAVEAT = "The model returned no usable grounded answer; human review is required."


def _clamp(value: float) -> float:
    """Clamp a confidence into [0.0, 1.0], defaulting non-numerics to 0.0."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, f))


class ComplianceQAService:
    """Grounded Q&A service. Constructor takes explicit port instances (SPEC §5)."""

    def __init__(
        self,
        retrieval: Any,
        llm: Any,
        guardrail: Any,
        redaction: Any,
        grounding: Any,
        tracer: Any,
        audit: Any,
        review_policy: HumanReviewPolicy | None = None,
        policy: CompliancePolicyConfig | None = None,
        review_router: Any = None,
    ) -> None:
        self._retrieval = retrieval
        self._llm = llm
        self._guardrail = guardrail
        self._redaction = redaction
        self._grounding = grounding
        self._tracer = tracer
        self._audit = audit
        self._policy = policy or CompliancePolicyConfig()
        self._review = review_policy or HumanReviewPolicy.from_policy(self._policy)
        # Rule R8 hand-off (optional). When bound, an escalated answer is routed to the
        # human-review-console
        # maker-checker console. A None router leaves requires_human_review as a plain flag,
        # so the routing is additive and never changes the assembled answer.
        self._review_router = review_router

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def answer(
        self,
        question: str,
        actor: str,
        filters: dict[str, str] | None = None,
        *,
        tenant: str = "",
    ) -> Answer:
        """Answer ``question`` for ``actor`` via the SPEC §5 grounded pipeline."""
        span = self._tracer.span("qa.answer", action="ask", actor=actor)
        with span if span is not None else nullcontext():
            answer = self._answer_inner(question, actor, filters)
            # R8: an escalated answer is routed to the human-review-console maker-checker console
            # after it is
            # assembled and audited. Routing is a hand-off, never fatal to an already-audited
            # answer, so failures are suppressed (the outbox/relay is the durability seam).
            if self._review_router is not None and answer.requires_human_review:
                with contextlib.suppress(Exception):
                    self._review_router.route(answer, maker=actor, tenant=tenant)
            return answer

    # ------------------------------------------------------------------ #
    # Pipeline
    # ------------------------------------------------------------------ #
    def _answer_inner(
        self,
        question: str,
        actor: str,
        filters: dict[str, str] | None,
    ) -> Answer:
        # 1) Redact the question (P-04) before it touches the model or the audit log.
        redaction: RedactionResult = self._redaction.redact(question)
        redacted_q = redaction.text

        # 2) Guardrail screen (INPUT). Blocked -> audit BLOCKED + blocked Answer.
        in_verdict: GuardrailVerdict = self._guardrail.screen(redacted_q, Direction.INPUT)
        if not in_verdict.allowed:
            return self._blocked_answer(question, redacted_q, actor, in_verdict)

        screened_q = in_verdict.sanitized_text or redacted_q

        # 3) Retrieve grounding passages. Empty -> audited hard refusal: no uncited answer.
        passages: list[RetrievedPassage] = g.retrieve_passages(
            self._retrieval, screened_q, filters=filters
        )
        if not passages:
            self._audit_empty_retrieval(actor, redacted_q)
            raise RetrievalEmptyError(
                "No regulatory passages ground this question; refine it or escalate to a "
                "compliance specialist."
            )

        # 4) Public-web grounding (secondary evidence), only when enabled.
        web_citations = self._maybe_ground(screened_q)

        # 5) Build the grounded LlmRequest and generate the structured answer.
        passage_block = g.render_passages(passages)
        system = GROUNDED_QA_SYSTEM.format(citation_rules=_CITATION_RULES)
        user = GROUNDED_QA_USER.format(question=screened_q, passages=passage_block)
        request = g.build_llm_request(
            system_instruction=system,
            user_content=user,
            model=None,  # adapter default => reasoning model gemini-3.5-flash
            response_schema=_ANSWER_SCHEMA,
        )
        response = self._llm.generate(request)
        g.maybe_record_usage(self._tracer, response)

        parsed = g.parse_structured(response)
        answer_text = str(parsed.get("answer") or "").strip()
        used_ids = g.as_str_list(parsed.get("used_source_ids"))
        confidence = _clamp(parsed.get("confidence", 0.0))

        # 6) Map used_source_ids back to retrieved Citations (preserve page).
        citations: tuple[Citation, ...] = g.citations_for_source_ids(used_ids, passages)

        # Defensive: a model that returned nothing usable is treated as unsupported.
        caveats: list[str] = []
        if not answer_text:
            answer_text = (
                "The available passages do not support a confident answer to this "
                "question. Human review is recommended."
            )
            confidence = min(confidence, 0.2)
            caveats.append(_UNSUPPORTED_CAVEAT)

        # 7) Self-critique groundedness pass (second LLM call) adjusts confidence.
        confidence, critique_caveats = self._self_critique(
            screened_q, answer_text, passage_block, confidence
        )
        caveats.extend(critique_caveats)

        # 8) HITL policy: low confidence or high/critical cited severity -> review.
        severity = self._severity_for(citations)
        requires_review = self._review.requires_review(
            confidence=confidence, severity=severity, artifact_kind="answer"
        )

        # 9) Guardrail screen (OUTPUT) on the assembled answer text.
        out_verdict: GuardrailVerdict = self._guardrail.screen(answer_text, Direction.OUTPUT)
        if not out_verdict.allowed:
            return self._blocked_answer(
                question, redacted_q, actor, out_verdict, direction=Direction.OUTPUT
            )
        final_answer_text = out_verdict.sanitized_text or answer_text

        answer = Answer(
            question=question,
            answer=final_answer_text,
            citations=citations,
            web_citations=tuple(web_citations),
            confidence=confidence,
            requires_human_review=requires_review,
            caveats=tuple(dict.fromkeys(caveats)),  # de-dup, preserve order
        )

        # 10) Audit (already-redacted prompt + response).
        self._audit_answer(actor, redacted_q, answer, Decision.ALLOWED)
        return answer

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    def _maybe_ground(self, query: str) -> list[WebCitation]:
        """Run public-web grounding when the grounding port is enabled."""
        try:
            if getattr(self._grounding, "enabled", False):
                return list(self._grounding.ground(query) or [])
        except Exception:  # noqa: BLE001 - grounding is best-effort, never fatal
            return []
        return []

    def _self_critique(
        self,
        question: str,
        answer_text: str,
        passage_block: str,
        prior_confidence: float,
    ) -> tuple[float, list[str]]:
        """Second LLM pass auditing groundedness; returns (confidence, caveats)."""
        request = g.build_llm_request(
            system_instruction=SELF_CRITIQUE_SYSTEM,
            user_content=SELF_CRITIQUE_USER.format(
                question=question, answer=answer_text, passages=passage_block
            ),
            model=None,
            response_schema=_CRITIQUE_SCHEMA,
            temperature=0.0,
        )
        try:
            response = self._llm.generate(request)
        except Exception:  # noqa: BLE001 - critique failure must not drop the answer
            return prior_confidence, []
        g.maybe_record_usage(self._tracer, response)

        parsed = g.parse_structured(response)
        if not parsed:
            return prior_confidence, []

        critic_conf = _clamp(parsed.get("confidence", prior_confidence))
        grounded = bool(parsed.get("grounded", True))
        caveats = g.as_str_list(parsed.get("caveats"))

        # Take the more conservative of the two confidence signals.
        confidence = min(prior_confidence, critic_conf)
        if not grounded:
            confidence = min(confidence, 0.4)
            if not caveats:
                caveats = ["Self-critique flagged unsupported claims; review required."]
        return confidence, caveats

    def _severity_for(self, citations: tuple[Citation, ...]) -> Severity | None:
        """Highest severity across cited topics (best-effort from citation metadata).

        Citations do not carry an explicit severity field, so a topic-keyword
        heuristic maps high-risk regulatory themes to HIGH. This only ever *raises*
        the review bar, never lowers it.
        """
        if not citations:
            return None
        severities: list[Severity] = []
        for c in citations:
            text = f"{c.title} {c.snippet}".lower()
            if any(topic.lower() in text for topic in self._policy.high_risk_topics):
                severities.append(Severity.HIGH)
        return g.highest_severity(severities)

    # ------------------------------------------------------------------ #
    # Degraded / blocked answers
    # ------------------------------------------------------------------ #
    def _audit_empty_retrieval(self, actor: str, redacted_q: str) -> None:
        """Persist the refusal before raising so an ungrounded request cannot disappear."""
        refused = Answer(
            question="",
            answer="No grounded answer was produced.",
            citations=(),
            confidence=0.0,
            requires_human_review=True,
            caveats=("empty retrieval; escalated",),
        )
        self._audit_answer(actor, redacted_q, refused, Decision.ESCALATED)

    def _blocked_answer(
        self,
        question: str,
        redacted_q: str,
        actor: str,
        verdict: GuardrailVerdict,
        direction: Direction = Direction.INPUT,
    ) -> Answer:
        """Blocked answer when the guardrail denies input or output."""
        reason = verdict.reason or "blocked by guardrail"
        answer = Answer(
            question=question,
            answer=(
                "This request was blocked by the safety guardrail and was not "
                f"answered ({direction.value}: {reason})."
            ),
            citations=(),
            confidence=0.0,
            requires_human_review=True,
            caveats=(_BLOCKED_CAVEAT,),
        )
        self._audit_answer(actor, redacted_q, answer, Decision.BLOCKED)
        return answer

    # ------------------------------------------------------------------ #
    # Audit
    # ------------------------------------------------------------------ #
    def _audit_answer(
        self,
        actor: str,
        redacted_prompt: str,
        answer: Answer,
        decision: Decision,
    ) -> None:
        """Write an already-redacted WORM audit record for this interaction."""
        event = AuditEvent(
            action="ask",
            actor=actor,
            decision=decision,
            redacted_prompt=redacted_prompt,
            redacted_response=answer.answer,
            citations=answer.citations,
            metadata={
                "confidence": f"{answer.confidence:.3f}",
                "requires_human_review": str(answer.requires_human_review).lower(),
                "n_citations": str(len(answer.citations)),
            },
        )
        try:
            self._audit.record(event)
        except Exception:  # noqa: BLE001 - audit failure must not crash the request
            # The event is best-effort; serialization is validated to be jsonable.
            to_jsonable(event)
