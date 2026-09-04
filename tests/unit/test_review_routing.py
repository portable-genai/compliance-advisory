"""R8 routing: an escalated compliance answer is routed to human-review-console via the shared
review-kit.

A low-confidence or high-severity answer sets ``requires_human_review`` (P-06), so rule R8 says it
MUST be handed to the human-review-console maker-checker console rather than left as a boolean.
These tests prove the producer half of that loop end-to-end against the offline local router (an
in-memory outbox), and prove the redact-before-wire boundary so no raw customer identifier reaches
the console. All data is fictional.
"""

from __future__ import annotations

import pytest
from tests.conftest import load_service

from compliance_advisory.adapters._review_payload import answer_to_review
from compliance_advisory.adapters.local.review_router import LocalReviewRouter
from compliance_advisory.config import Settings
from compliance_advisory.domain.models import (
    Answer,
    Citation,
    Jurisdiction,
    Regulator,
)

ACTOR = "officer@bank.test"
TENANT = "demo-bank"


def _qa_with_router(retrieval, llm, guardrail, redaction, grounding, tracer, audit, router):
    cls = load_service("ComplianceQAService")
    return cls(
        retrieval,
        llm,
        guardrail,
        redaction,
        grounding,
        tracer,
        audit,
        review_router=router,
    )


def test_answer_routes_escalated_answer_to_outbox(
    retrieval, llm, guardrail, redaction, grounding, tracer, audit
):
    """An escalated answer enqueues exactly one review to the router's outbox (R8).

    Every grounded answer requires review, so the routing assertion does not depend on a
    confidence threshold. Empty retrieval is a hard refusal and produces no answer to route.
    """
    router = LocalReviewRouter(Settings())
    service = _qa_with_router(
        retrieval, llm, guardrail, redaction, grounding, tracer, audit, router
    )
    assert not router.outbox.pending()

    answer = service.answer(
        "What are the outsourcing notification duties?", actor=ACTOR, tenant=TENANT
    )
    assert answer.requires_human_review

    pending = router.outbox.pending()
    assert len(pending) == 1, (
        "the escalated answer must be routed to human-review-console exactly once"
    )
    review = pending[0].review
    assert review.action == "compliance_answer:ask"
    assert review.maker == ACTOR
    assert review.tenant == TENANT
    assert review.case_ref.startswith("qa-")


def _high_severity_answer_with_pii() -> Answer:
    # A citation snippet carrying a synthetic SG NRIC: it must be masked before the wire. The
    # cited "sanctions" topic is high-risk, so the review escalates to HIGH / dual control.
    cite = Citation(
        source_id="mas-fake-notice",
        regulator=Regulator.MAS,
        jurisdiction=Jurisdiction.SG,
        title="Sanctions screening obligations (FICTIONAL)",
        url="https://example.test/mas-fake-notice",
        page=4,
        snippet="Director NRIC S1234567D flagged during sanctions screening.",
    )
    return Answer(
        question="Is director S1234567D subject to enhanced due diligence?",
        answer="Enhanced due diligence applies; escalate for review.",
        citations=(cite,),
        confidence=0.95,  # high confidence, yet the sanctions topic still forces HIGH severity
        requires_human_review=True,
    )


def test_payload_is_redacted_and_carries_tenant_severity_and_dual_control():
    """The wire payload masks identifiers, carries the tenant, and dual-controls HIGH (R1/R8)."""
    review = answer_to_review(_high_severity_answer_with_pii(), maker=ACTOR, tenant=TENANT)

    assert review.tenant == TENANT
    assert review.severity == "high"
    assert review.required_approvals == 2, "a high-severity answer warrants dual control"
    # No raw NRIC survives into the subject, summary, or any citation the console receives.
    assert "S1234567D" not in review.subject
    assert "S1234567D" not in review.summary
    for citation in review.citations:
        assert "S1234567D" not in citation.snippet
    assert any(c.title.startswith("Sanctions screening") for c in review.citations)


def test_no_router_still_returns_escalated_answer(
    retrieval, llm, guardrail, redaction, grounding, tracer, audit
):
    """Routing is optional: with no router bound, the answer still escalates as before."""
    service = _qa_with_router(retrieval, llm, guardrail, redaction, grounding, tracer, audit, None)
    answer = service.answer("What are the outsourcing notification duties?", actor=ACTOR)
    assert answer.requires_human_review


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
