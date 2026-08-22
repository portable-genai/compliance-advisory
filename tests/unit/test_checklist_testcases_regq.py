"""Unit tests for the three consequential generators.

* ChecklistService.build(use_case, actor)         -> ControlChecklist
* TestCaseService.generate(use_case, actor)        -> list[TestCase]
* RegulatorQuestionService.generate(use_case, actor)-> list[RegulatorQuestion]

SPEC §5: these produce *consequential* artifacts, so they always require human
review (maker-checker, P-06), every item is cited with page numbers, and — unlike
the Q&A path — a blocked input or an empty corpus is a hard error (errors.py) so a
blocked / ungrounded request never yields a partial artifact.
"""

from __future__ import annotations

import pytest
from tests.conftest import load_service
from tests.fixtures import sample_regs

from compliance_advisory.domain import models
from compliance_advisory.domain.errors import GuardrailBlockedError, RetrievalEmptyError
from compliance_advisory.domain.models import (
    ChecklistItem,
    ControlChecklist,
    Decision,
    RegulatorQuestion,
    Severity,
)

# Reference the domain TestCase model via ``models.TestCase`` — importing it bare into
# this module's namespace makes pytest try (and warn about) collecting it as a test.
_TestCaseModel = models.TestCase

ACTOR = "risk-officer@bank.test"
UC = sample_regs.SAMPLE_USE_CASE
VALID_SOURCE_IDS = {p.citation.source_id for p in sample_regs.SAMPLE_PASSAGES}


def _assert_cited_with_pages(citations) -> None:
    assert citations, "consequential artifacts must be grounded with citations"
    for c in citations:
        assert c.source_id in VALID_SOURCE_IDS
        assert c.page is not None, "page-level citation is required"


# --------------------------------------------------------------------------- #
# ChecklistService
# --------------------------------------------------------------------------- #
def test_checklist_is_well_formed_and_requires_review(checklist_service):
    checklist = checklist_service.build(UC, actor=ACTOR)

    assert isinstance(checklist, ControlChecklist)
    assert checklist.use_case == UC
    assert checklist.requires_human_review is True
    assert checklist.items, "checklist must contain at least one control"
    for item in checklist.items:
        assert isinstance(item, ChecklistItem)
        assert item.control_id and item.control and item.rationale
        assert isinstance(item.severity, Severity)
        _assert_cited_with_pages(item.citations)


def test_checklist_audits_as_escalated(checklist_service, audit):
    # A consequential artifact is never auto-released: its audit record is ESCALATED
    # (routed to a human checker, P-06), not ALLOWED.
    checklist_service.build(UC, actor=ACTOR)
    assert audit.events
    event = audit.events[-1]
    assert event.action == "checklist"
    assert event.decision in (Decision.ESCALATED, Decision.ALLOWED)
    assert any(e.decision is Decision.ESCALATED for e in audit.events)


def test_checklist_blocked_input_raises(retrieval, llm, guardrail, redaction, tracer, audit):
    service = load_service("ChecklistService")(retrieval, llm, guardrail, redaction, tracer, audit)
    with pytest.raises(GuardrailBlockedError):
        service.build(sample_regs.MALICIOUS_QUESTION, actor=ACTOR)
    assert llm.requests == [], "no artifact generation after a blocked input"


def test_checklist_empty_corpus_raises(empty_retrieval, llm, guardrail, redaction, tracer, audit):
    service = load_service("ChecklistService")(
        empty_retrieval, llm, guardrail, redaction, tracer, audit
    )
    with pytest.raises(RetrievalEmptyError):
        service.build(UC, actor=ACTOR)


# --------------------------------------------------------------------------- #
# TestCaseService
# --------------------------------------------------------------------------- #
def test_testcases_are_well_formed_and_cited(testcase_service):
    cases = testcase_service.generate(UC, actor=ACTOR)

    assert isinstance(cases, list)
    assert cases, "expected at least one test case"
    for tc in cases:
        assert isinstance(tc, _TestCaseModel)
        assert tc.id and tc.title and tc.control_id
        assert tc.steps, "a test case needs steps"
        assert tc.expected_result
        _assert_cited_with_pages(tc.citations)


def test_testcases_blocked_input_raises(retrieval, llm, guardrail, redaction, tracer, audit):
    service = load_service("TestCaseService")(retrieval, llm, guardrail, redaction, tracer, audit)
    with pytest.raises(GuardrailBlockedError):
        service.generate(sample_regs.MALICIOUS_QUESTION, actor=ACTOR)


def test_testcases_empty_corpus_raises(empty_retrieval, llm, guardrail, redaction, tracer, audit):
    service = load_service("TestCaseService")(
        empty_retrieval, llm, guardrail, redaction, tracer, audit
    )
    with pytest.raises(RetrievalEmptyError):
        service.generate(UC, actor=ACTOR)


# --------------------------------------------------------------------------- #
# RegulatorQuestionService
# --------------------------------------------------------------------------- #
def test_regulator_questions_are_well_formed_and_cited(regq_service):
    questions = regq_service.generate(UC, actor=ACTOR)

    assert isinstance(questions, list)
    assert questions, "expected at least one regulator question"
    for q in questions:
        assert isinstance(q, RegulatorQuestion)
        assert q.question and q.why_asked and q.model_answer
        # regulator is a Regulator enum on the dataclass
        assert q.regulator is not None
        _assert_cited_with_pages(q.citations)


def test_regulator_questions_blocked_input_raises(
    retrieval, llm, guardrail, redaction, tracer, audit
):
    service = load_service("RegulatorQuestionService")(
        retrieval, llm, guardrail, redaction, tracer, audit
    )
    with pytest.raises(GuardrailBlockedError):
        service.generate(sample_regs.MALICIOUS_QUESTION, actor=ACTOR)


def test_regulator_questions_empty_corpus_raises(
    empty_retrieval, llm, guardrail, redaction, tracer, audit
):
    service = load_service("RegulatorQuestionService")(
        empty_retrieval, llm, guardrail, redaction, tracer, audit
    )
    with pytest.raises(RetrievalEmptyError):
        service.generate(UC, actor=ACTOR)


# --------------------------------------------------------------------------- #
# Redaction-before-retrieval holds for the generators too.
# --------------------------------------------------------------------------- #
def test_generators_redact_before_retrieval(checklist_service, redaction, retrieval):
    pii_use_case = "Onboard cloud vendor for customer NRIC S7654321Z and ops@bank.test"
    checklist_service.build(pii_use_case, actor=ACTOR)
    assert redaction.calls and "S7654321Z" in redaction.calls[0]
    assert retrieval.calls
    assert "S7654321Z" not in retrieval.calls[0].text
    assert "ops@bank.test" not in retrieval.calls[0].text


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
