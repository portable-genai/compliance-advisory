"""Span ATTRIBUTES carry structure, never content, and this is the test that can tell.

The conftest ``RecordingTracer`` records span NAMES (``self.spans.append(name)``), which is
right for the tests that assert a pipeline opened its span and structurally blind to the one
defect that matters here: it throws the attributes away, so a span that started carrying the
analyst's question or the drafted answer would keep every existing test green. A trace
backend is not the WORM audit trail. It has no redaction stage, a wider read audience and no
retention rule written against a regulator's requirement, so an attribute is OUTSIDE the
boundary redact-before-retrieval (P-04) holds. The sibling tests
``test_redaction_runs_before_retrieval`` and the audit-redaction test prove the NRIC never
reaches retrieval or the audit log; neither of those stages runs on a span.

C4 opens spans from nine domain sites. This module deliberately guards the PRIMARY request
path, ``ComplianceQAService.answer`` (span ``qa.answer``), rather than enumerating all nine:
it is the path a user actually drives, the only one carrying free-text PII from the caller,
and the one whose fixture (``SAMPLE_QUESTION``) already embeds a planted NRIC and email so a
leak fails on a literal rather than on a subtlety. The other eight open their spans through
the same ``action``/``actor`` shape, so extending ``_ALLOWED`` to cover them is a mechanical
follow-on rather than a different kind of assertion.
"""

from __future__ import annotations

import pytest
from tests.conftest import _settings
from tests.fixtures import sample_regs

from compliance_advisory.adapters.local.tracer import LocalNoopTracerAdapter
from compliance_advisory.config import Settings

ACTOR = "analyst@bank.test"

#: The complete attribute key set the guarded span may carry. Widening it is a decision
#: about what leaves the trust boundary, so it is made here rather than at a call site.
_ALLOWED = {
    "qa.answer": {"action", "actor"},
}

#: Planted identifiers inside SAMPLE_QUESTION. Neither may reach a span attribute.
_PLANTED = ("S1234567A", "jane.doe@example.com")


class _AttributeRecordingTracer(LocalNoopTracerAdapter):
    """Keeps (name, attributes) per span, unlike the name-only conftest recorder."""

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.recorded: list[tuple[str, dict[str, str]]] = []

    def span(self, name: str, **attributes: str):  # type: ignore[no-untyped-def]
        self.recorded.append((name, dict(attributes)))
        return super().span(name, **attributes)


@pytest.fixture
def tracer() -> _AttributeRecordingTracer:  # type: ignore[override]
    """Override the conftest tracer so ``qa_service`` assembles with THIS recorder."""
    return _AttributeRecordingTracer(_settings())


def test_the_primary_request_path_opens_exactly_the_known_span(qa_service, tracer) -> None:
    qa_service.answer(sample_regs.SAMPLE_QUESTION, actor=ACTOR)
    names = {name for name, _ in tracer.recorded}
    assert names == set(_ALLOWED), (
        "the set of spans the answer path opens changed; a new span site is a "
        "trust-boundary decision, so record it in _ALLOWED here deliberately"
    )


def test_every_span_carries_allowlisted_keys_only(qa_service, tracer) -> None:
    qa_service.answer(sample_regs.SAMPLE_QUESTION, actor=ACTOR)
    assert tracer.recorded, "the answer path opened no span at all"
    for name, attributes in tracer.recorded:
        assert name in _ALLOWED, f"unexpected span {name!r}; add it here deliberately"
        assert set(attributes) == _ALLOWED[name], (
            f"span {name!r} attribute keys changed; widening the set is a trust-boundary "
            "decision, so update _ALLOWED here deliberately"
        )


def test_no_span_attribute_carries_the_planted_identifiers(qa_service, tracer) -> None:
    """SAMPLE_QUESTION embeds an NRIC and an email; neither may reach a span."""
    qa_service.answer(sample_regs.SAMPLE_QUESTION, actor=ACTOR)
    emitted = " ".join(value for _, attributes in tracer.recorded for value in attributes.values())
    for planted in _PLANTED:
        assert planted not in emitted, f"span attribute leaked the planted {planted!r}"
    assert sample_regs.SAMPLE_QUESTION not in emitted, "the question reached a span attribute"


def test_no_span_attribute_carries_the_drafted_answer(qa_service, tracer) -> None:
    """The drafted answer is model output over regulatory text, not span structure."""
    answer = qa_service.answer(sample_regs.SAMPLE_QUESTION, actor=ACTOR)
    emitted = " ".join(value for _, attributes in tracer.recorded for value in attributes.values())
    assert answer.answer, "the pipeline drafted no answer, so this proves nothing"
    assert answer.answer not in emitted, "the drafted answer reached a span attribute"


def test_a_blocked_question_leaks_nothing_either(qa_service, tracer) -> None:
    """The guardrail-blocked branch still opens the span; it must stay just as empty."""
    qa_service.answer(sample_regs.MALICIOUS_QUESTION, actor=ACTOR)
    assert tracer.recorded, "the blocked path opened no span at all"
    emitted = " ".join(value for _, attributes in tracer.recorded for value in attributes.values())
    assert sample_regs.MALICIOUS_QUESTION not in emitted, (
        "the injection attempt reached a span attribute; a blocked input is still an input"
    )


def test_every_attribute_value_is_a_string(qa_service, tracer) -> None:
    """The port declares str values; a structured object smuggles content past a grep."""
    qa_service.answer(sample_regs.SAMPLE_QUESTION, actor=ACTOR)
    for name, attributes in tracer.recorded:
        for key, value in attributes.items():
            assert isinstance(value, str), f"span {name!r} attribute {key!r} is not a str"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
