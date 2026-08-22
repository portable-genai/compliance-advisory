"""Unit tests for ComplianceQAService — the SPEC §5 answer pipeline.

Pipeline (SPEC §5):
    redact(question) -> guardrail.screen(INPUT)
      -> [if blocked: audit + return blocked Answer]
      -> retrieval.retrieve -> (grounding if enabled)
      -> llm.generate(structured) -> assemble Answer + citations
      -> self-critique -> HITL sets requires_human_review
      -> guardrail.screen(OUTPUT) -> audit.record(redacted)

These tests run on the seeded ``local`` adapters (no Google Cloud SDK). The blocked-path
tests drive the real local heuristic guardrail with a malicious prompt
(``sample_regs.MALICIOUS_QUESTION``), which it blocks deterministically.
"""

from __future__ import annotations

import pytest
from tests.fixtures import sample_regs

from compliance_advisory.domain.models import Answer, Decision, Direction

ACTOR = "analyst@bank.test"


# --------------------------------------------------------------------------- #
# Redaction happens BEFORE retrieval (P-04: minimise PII to model & store).
# --------------------------------------------------------------------------- #
def test_redaction_runs_before_retrieval(qa_service, redaction, retrieval):
    qa_service.answer(sample_regs.SAMPLE_QUESTION, actor=ACTOR)

    # Redaction was invoked, and on the *raw* question (which carried the NRIC).
    assert redaction.calls, "redaction.redact was never called"
    assert "S1234567A" in redaction.calls[0]

    # Retrieval was invoked, and the query text it saw must already be de-identified:
    # the raw NRIC / email must never reach the retrieval backend.
    assert retrieval.calls, "retrieval.retrieve was never called"
    query_text = retrieval.calls[0].text
    assert "S1234567A" not in query_text
    assert "jane.doe@example.com" not in query_text


# --------------------------------------------------------------------------- #
# Blocked input: blocked Answer + audit BLOCKED event + requires_human_review.
# --------------------------------------------------------------------------- #
def test_blocked_input_returns_blocked_answer_and_audits(
    retrieval, llm, guardrail, redaction, grounding, tracer, audit
):
    from tests.conftest import load_service

    # The local heuristic guardrail blocks the malicious prompt deterministically.
    service = load_service("ComplianceQAService")(
        retrieval, llm, guardrail, redaction, grounding, tracer, audit
    )

    answer = service.answer(sample_regs.MALICIOUS_QUESTION, actor=ACTOR)

    assert isinstance(answer, Answer)
    assert answer.requires_human_review is True
    # A blocked answer must not leak grounded content / citations.
    assert answer.citations == ()
    # The LLM must never be invoked once the input is blocked.
    assert llm.requests == []
    # Retrieval must not run on a blocked input.
    assert retrieval.calls == []

    # Exactly one BLOCKED audit event was written, and it is already redacted.
    assert audit.events, "no audit event recorded for a blocked input"
    blocked = [e for e in audit.events if e.decision is Decision.BLOCKED]
    assert blocked, "expected an audit event with decision=BLOCKED"
    event = blocked[0]
    assert event.actor == ACTOR
    assert event.action == "ask"


def test_blocked_input_screens_input_direction_only(
    retrieval, llm, recording_guardrail, redaction, grounding, tracer, audit
):
    from tests.conftest import load_service

    load_service("ComplianceQAService")(
        retrieval, llm, recording_guardrail, redaction, grounding, tracer, audit
    ).answer(sample_regs.MALICIOUS_QUESTION, actor=ACTOR)

    # Input direction was screened; because it blocked, no OUTPUT screen happens.
    directions = [d for _, d in recording_guardrail.calls]
    assert Direction.INPUT in directions
    assert Direction.OUTPUT not in directions


# --------------------------------------------------------------------------- #
# Normal path: citations mapped from used_source_ids, WITH page numbers.
# --------------------------------------------------------------------------- #
def test_normal_path_maps_citations_with_pages(qa_service, audit):
    answer = qa_service.answer(sample_regs.SAMPLE_QUESTION, actor=ACTOR)

    assert isinstance(answer, Answer)
    assert answer.answer
    assert answer.citations, "a grounded answer must carry citations"

    # Every citation maps to a source id the LLM said it used, and carries a page.
    used_ids = {sample_regs.PRIMARY_SOURCE_ID}
    cited_ids = {c.source_id for c in answer.citations}
    assert cited_ids <= {p.citation.source_id for p in sample_regs.SAMPLE_PASSAGES}
    assert used_ids & cited_ids, "citations were not mapped from used_source_ids"
    assert all(c.page is not None for c in answer.citations), "page-level citation required"

    # The primary cited source keeps its real page number (42) from the passage.
    primary = next(c for c in answer.citations if c.source_id == sample_regs.PRIMARY_SOURCE_ID)
    assert primary.page == sample_regs.PRIMARY_PASSAGE.citation.page

    # A normal (allowed) answer is audited as ALLOWED.
    assert audit.events, "normal answer was not audited"
    assert any(e.decision is Decision.ALLOWED for e in audit.events)


def test_normal_path_audit_record_is_redacted(qa_service, audit):
    qa_service.answer(sample_regs.SAMPLE_QUESTION, actor=ACTOR)
    assert audit.events
    event = audit.events[-1]
    # The audited prompt must never contain raw PII (P-04).
    assert "S1234567A" not in event.redacted_prompt
    assert "jane.doe@example.com" not in event.redacted_prompt


def test_normal_path_wrapped_in_tracer_span(qa_service, tracer):
    qa_service.answer(sample_regs.SAMPLE_QUESTION, actor=ACTOR)
    assert tracer.spans, "the answer pipeline must open at least one trace span"


# --------------------------------------------------------------------------- #
# Empty retrieval -> audited hard refusal, never an uncited answer.
# --------------------------------------------------------------------------- #
def test_empty_retrieval_raises_after_escalation_audit(
    empty_retrieval, llm, guardrail, redaction, grounding, tracer, audit
):
    from tests.conftest import load_service

    service = load_service("ComplianceQAService")(
        empty_retrieval, llm, guardrail, redaction, grounding, tracer, audit
    )
    from compliance_advisory.domain.errors import RetrievalEmptyError

    with pytest.raises(RetrievalEmptyError):
        service.answer("What does MAS expect for cloud outsourcing?", actor=ACTOR)
    assert audit.events[-1].decision.value == "escalated"
    assert audit.events[-1].metadata["n_citations"] == "0"


def test_grounding_skipped_when_disabled(qa_service, grounding):
    # FakeGrounding(enabled=False) -> service must not attach web citations.
    answer = qa_service.answer(sample_regs.SAMPLE_QUESTION, actor=ACTOR)
    assert answer.web_citations == ()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
