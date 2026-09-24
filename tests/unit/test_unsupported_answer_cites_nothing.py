"""An answer that says its passages do not address the question cites none of them.

Found on the deployment on 2026-09-22: `cdd-sow-research` asked what CDD/AML expectations apply
to a customer, the corpus held no AML/CFT instrument, and the model answered, correctly, that
the passages did not address the question, with confidence 0.0, while still listing in
``used_source_ids`` the seven unrelated passages retrieval had ranked highest. The service
mapped all seven onto the answer, and the flagship reads "has citations" as "is grounded".

The model now states ``supported`` in the structured answer, and only an explicit ``true``
keeps the citations. These tests script the model rather than use the local adapter, which
always answers as if supported.
"""

from __future__ import annotations

import json
from typing import Any

from tests.fixtures import sample_regs

from compliance_advisory.domain.models import Decision, LlmRequest, LlmResponse, TokenUsage
from compliance_advisory.domain.qa_service import _ANSWER_SCHEMA

ACTOR = "analyst@bank.test"

NOT_ADDRESSED = (
    "The provided passages do not address customer due diligence or anti-money laundering "
    "expectations. Human review is recommended."
)


class _ScriptedAnswerLLM:
    """Answers the Q&A request from a script and the self-critique as fully grounded.

    The critique is scripted to agree with the draft on purpose: the deployed critique found
    nothing to object to either, because saying the passages are insufficient is itself a
    grounded statement. The cap has to hold without the critique's help.
    """

    def __init__(self, answer: dict[str, Any]) -> None:
        self._answer = answer
        self.requests: list[LlmRequest] = []

    def generate(self, request: LlmRequest) -> LlmResponse:
        self.requests.append(request)
        if request.response_schema is _ANSWER_SCHEMA:
            body = self._answer
        else:
            body = {"grounded": True, "confidence": 1.0, "caveats": []}
        return LlmResponse(
            text=json.dumps(body),
            usage=TokenUsage(input_tokens=1, output_tokens=1, thinking_tokens=0),
            model="scripted",
            web_citations=(),
            raw=body,
        )

    def classify(self, text: str, labels: list[str]) -> str:
        return labels[0] if labels else ""


def _every_retrieved_id() -> list[str]:
    return [p.citation.source_id for p in sample_regs.SAMPLE_PASSAGES]


def _service(llm, retrieval, guardrail, redaction, grounding, tracer, audit):
    from tests.conftest import load_service

    return load_service("ComplianceQAService")(
        retrieval, llm, guardrail, redaction, grounding, tracer, audit
    )


def test_the_answer_schema_requires_the_model_to_state_support() -> None:
    assert _ANSWER_SCHEMA["properties"]["supported"] == {"type": "boolean"}
    assert "supported" in _ANSWER_SCHEMA["required"]


def test_an_answer_marked_unsupported_carries_no_citations(
    retrieval, guardrail, redaction, grounding, tracer, audit
) -> None:
    llm = _ScriptedAnswerLLM(
        {
            "answer": NOT_ADDRESSED,
            "used_source_ids": _every_retrieved_id(),
            "confidence": 0.0,
            "supported": False,
        }
    )

    answer = _service(llm, retrieval, guardrail, redaction, grounding, tracer, audit).answer(
        sample_regs.SAMPLE_QUESTION, actor=ACTOR
    )

    assert answer.answer == NOT_ADDRESSED, "the model's own statement is kept, not replaced"
    assert answer.citations == ()
    assert answer.confidence == 0.0
    assert answer.requires_human_review is True
    assert any("do not address" in c for c in answer.caveats)
    audited = [e for e in audit.events if e.decision is Decision.ALLOWED]
    assert audited and audited[-1].citations == ()
    assert audited[-1].metadata["n_citations"] == "0"


def test_an_unsupported_answer_is_capped_however_confident_the_model_says_it_is(
    retrieval, guardrail, redaction, grounding, tracer, audit
) -> None:
    llm = _ScriptedAnswerLLM(
        {
            "answer": NOT_ADDRESSED,
            "used_source_ids": _every_retrieved_id(),
            "confidence": 0.9,
            "supported": False,
        }
    )

    answer = _service(llm, retrieval, guardrail, redaction, grounding, tracer, audit).answer(
        sample_regs.SAMPLE_QUESTION, actor=ACTOR
    )

    assert answer.citations == ()
    assert answer.confidence <= 0.2
    assert answer.requires_human_review is True


def test_a_missing_support_flag_is_not_a_claim_of_support(
    retrieval, guardrail, redaction, grounding, tracer, audit
) -> None:
    """The schema requires the flag, so its absence is a malformed answer, not a yes."""
    llm = _ScriptedAnswerLLM(
        {
            "answer": "Outsourcing arrangements require due diligence.",
            "used_source_ids": _every_retrieved_id(),
            "confidence": 0.9,
        }
    )

    answer = _service(llm, retrieval, guardrail, redaction, grounding, tracer, audit).answer(
        sample_regs.SAMPLE_QUESTION, actor=ACTOR
    )

    assert answer.citations == ()
    assert answer.confidence <= 0.2


def test_a_truthy_non_boolean_flag_is_not_a_claim_of_support(
    retrieval, guardrail, redaction, grounding, tracer, audit
) -> None:
    llm = _ScriptedAnswerLLM(
        {
            "answer": "Outsourcing arrangements require due diligence.",
            "used_source_ids": _every_retrieved_id(),
            "confidence": 0.9,
            "supported": "false",
        }
    )

    answer = _service(llm, retrieval, guardrail, redaction, grounding, tracer, audit).answer(
        sample_regs.SAMPLE_QUESTION, actor=ACTOR
    )

    assert answer.citations == ()


def test_an_answer_marked_supported_keeps_the_passages_it_used(
    retrieval, guardrail, redaction, grounding, tracer, audit
) -> None:
    llm = _ScriptedAnswerLLM(
        {
            "answer": "Outsourcing arrangements require due diligence [mas-trm-guidelines p.42].",
            "used_source_ids": [sample_regs.PRIMARY_SOURCE_ID],
            "confidence": 0.8,
            "supported": True,
        }
    )

    answer = _service(llm, retrieval, guardrail, redaction, grounding, tracer, audit).answer(
        sample_regs.SAMPLE_QUESTION, actor=ACTOR
    )

    assert [c.source_id for c in answer.citations] == [sample_regs.PRIMARY_SOURCE_ID]
    assert answer.confidence == 0.8
    assert not any("do not address" in c for c in answer.caveats)
