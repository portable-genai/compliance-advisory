"""Pydantic v2 request/response models for the C1 Compliance Assistant API.

These schemas mirror the frozen domain dataclasses in
:mod:`compliance_advisory.domain.models` one-for-one, so the HTTP boundary is a thin,
typed projection of the domain — the React/Next.js UI and the CLI consume exactly these
shapes. Each response model exposes a ``from_domain`` classmethod that builds itself from
the corresponding domain object, using :func:`domain.serialization.to_jsonable` where a
nested structure is easiest to project verbatim (enums become their ``.value`` strings).

Nothing here imports Google Cloud, ADK, or any adapter: the API layer depends only on the
domain models, the ports, and the orchestration services — never on a concrete adapter.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from ..domain import models as m
from ..domain.serialization import to_jsonable

# --------------------------------------------------------------------------- #
# Shared citation projections
# --------------------------------------------------------------------------- #


class CitationModel(BaseModel):
    """Regulator-grade provenance attached to a generated claim (mirror of Citation)."""

    source_id: str
    regulator: str
    jurisdiction: str
    title: str
    url: str
    version: str = "unknown"
    page: int | None = None
    snippet: str = ""
    score: float | None = None

    @classmethod
    def from_domain(cls, citation: m.Citation) -> CitationModel:
        return cls(**to_jsonable(citation))


class WebCitationModel(BaseModel):
    """Provenance for a public-web grounded fact (mirror of WebCitation)."""

    title: str
    url: str
    snippet: str = ""

    @classmethod
    def from_domain(cls, citation: m.WebCitation) -> WebCitationModel:
        return cls(**to_jsonable(citation))


# --------------------------------------------------------------------------- #
# Requests
# --------------------------------------------------------------------------- #


class AskRequest(BaseModel):
    """Inbound grounded-Q&A request.

    The audit ``actor`` is NOT part of this schema: it is resolved server-side from the
    verified :class:`Principal` (see ``api/security.py``), never client-asserted. Any
    ``actor`` field a legacy client still sends is ignored.
    """

    question: str = Field(..., min_length=1, description="The compliance question to answer.")
    # Structured retrieval filters, e.g. {"regulator": "MAS", "jurisdiction": "SG"}.
    filters: dict[str, str] | None = Field(
        default=None, description="Optional structured retrieval filters."
    )


class UseCaseRequest(BaseModel):
    """Inbound request for the consequential artifact generators.

    Shared by /checklist, /testcases and /regulator-questions: each is scoped to a
    use case (e.g. "Deploying a customer-facing GenAI assistant on public cloud").
    Like :class:`AskRequest`, the audit ``actor`` comes from the verified Principal,
    never from the request body.
    """

    use_case: str = Field(..., min_length=1, description="The use case to assess.")


# --------------------------------------------------------------------------- #
# Artifact responses
# --------------------------------------------------------------------------- #


class AnswerResponse(BaseModel):
    """Grounded Q&A answer with regulator-grade citations (mirror of Answer)."""

    question: str
    answer: str
    citations: list[CitationModel] = Field(default_factory=list)
    web_citations: list[WebCitationModel] = Field(default_factory=list)
    confidence: float = 0.0
    requires_human_review: bool = True
    caveats: list[str] = Field(default_factory=list)

    @classmethod
    def from_domain(cls, answer: m.Answer) -> AnswerResponse:
        return cls(
            question=answer.question,
            answer=answer.answer,
            citations=[CitationModel.from_domain(c) for c in answer.citations],
            web_citations=[WebCitationModel.from_domain(c) for c in answer.web_citations],
            confidence=answer.confidence,
            requires_human_review=answer.requires_human_review,
            caveats=list(answer.caveats),
        )


class ChecklistItemModel(BaseModel):
    """A single control in a use-case checklist (mirror of ChecklistItem)."""

    control_id: str
    control: str
    rationale: str
    severity: str = "medium"
    citations: list[CitationModel] = Field(default_factory=list)

    @classmethod
    def from_domain(cls, item: m.ChecklistItem) -> ChecklistItemModel:
        return cls(
            control_id=item.control_id,
            control=item.control,
            rationale=item.rationale,
            severity=item.severity.value,
            citations=[CitationModel.from_domain(c) for c in item.citations],
        )


class ChecklistResponse(BaseModel):
    """Use-case control checklist (mirror of ControlChecklist)."""

    use_case: str
    items: list[ChecklistItemModel] = Field(default_factory=list)
    requires_human_review: bool = True

    @classmethod
    def from_domain(cls, checklist: m.ControlChecklist) -> ChecklistResponse:
        return cls(
            use_case=checklist.use_case,
            items=[ChecklistItemModel.from_domain(i) for i in checklist.items],
            requires_human_review=checklist.requires_human_review,
        )


class TestCaseModel(BaseModel):
    """An automated test case verifying a control (mirror of TestCase)."""

    id: str
    title: str
    control_id: str
    steps: list[str] = Field(default_factory=list)
    expected_result: str
    automated_check: str | None = None
    citations: list[CitationModel] = Field(default_factory=list)

    @classmethod
    def from_domain(cls, test_case: m.TestCase) -> TestCaseModel:
        return cls(
            id=test_case.id,
            title=test_case.title,
            control_id=test_case.control_id,
            steps=list(test_case.steps),
            expected_result=test_case.expected_result,
            automated_check=test_case.automated_check,
            citations=[CitationModel.from_domain(c) for c in test_case.citations],
        )


class TestCasesResponse(BaseModel):
    """A use case's generated automated test cases."""

    use_case: str
    test_cases: list[TestCaseModel] = Field(default_factory=list)

    @classmethod
    def from_domain(cls, use_case: str, cases: list[m.TestCase]) -> TestCasesResponse:
        return cls(
            use_case=use_case,
            test_cases=[TestCaseModel.from_domain(c) for c in cases],
        )


class RegulatorQuestionModel(BaseModel):
    """A question a regulator/CRO will ask, with a model answer (mirror)."""

    question: str
    why_asked: str
    model_answer: str
    regulator: str
    citations: list[CitationModel] = Field(default_factory=list)

    @classmethod
    def from_domain(cls, question: m.RegulatorQuestion) -> RegulatorQuestionModel:
        return cls(
            question=question.question,
            why_asked=question.why_asked,
            model_answer=question.model_answer,
            regulator=question.regulator.value,
            citations=[CitationModel.from_domain(c) for c in question.citations],
        )


class RegulatorQuestionsResponse(BaseModel):
    """A use case's likely regulator/CRO questions with model answers."""

    use_case: str
    questions: list[RegulatorQuestionModel] = Field(default_factory=list)

    @classmethod
    def from_domain(
        cls, use_case: str, questions: list[m.RegulatorQuestion]
    ) -> RegulatorQuestionsResponse:
        return cls(
            use_case=use_case,
            questions=[RegulatorQuestionModel.from_domain(q) for q in questions],
        )


# --------------------------------------------------------------------------- #
# Corpus & health
# --------------------------------------------------------------------------- #


class FreshnessRecordModel(BaseModel):
    """One source's freshness state in the 7-day fetch-at-runtime ledger."""

    source_id: str
    url: str
    version: str
    fetched_at: str
    expires_at: str
    checksum: str = ""
    status: str = "fresh"

    @classmethod
    def from_domain(cls, record: m.FreshnessRecord) -> FreshnessRecordModel:
        return cls(**to_jsonable(record))


class CorpusStatusResponse(BaseModel):
    """Summary of the freshness ledger (ledger.all() rolled up)."""

    total: int = 0
    fresh: int = 0
    expired: int = 0
    missing: int = 0
    failed: int = 0
    ttl_days: int = 7
    records: list[FreshnessRecordModel] = Field(default_factory=list)

    @classmethod
    def from_records(cls, records: list[m.FreshnessRecord], ttl_days: int) -> CorpusStatusResponse:
        counts: dict[str, int] = {s.value: 0 for s in m.FreshnessStatus}
        now = m.utcnow()
        for record in records:
            # An expired TTL window dominates the stored status for the summary view.
            effective = record.status.value
            if record.status == m.FreshnessStatus.FRESH and not record.is_fresh(now):
                effective = m.FreshnessStatus.EXPIRED.value
            counts[effective] = counts.get(effective, 0) + 1
        return cls(
            total=len(records),
            fresh=counts.get(m.FreshnessStatus.FRESH.value, 0),
            expired=counts.get(m.FreshnessStatus.EXPIRED.value, 0),
            missing=counts.get(m.FreshnessStatus.MISSING.value, 0),
            failed=counts.get(m.FreshnessStatus.FAILED.value, 0),
            ttl_days=ttl_days,
            records=[FreshnessRecordModel.from_domain(r) for r in records],
        )


class CorpusRefreshResponse(BaseModel):
    """Acknowledgement that a corpus refresh was accepted (202)."""

    accepted: bool = True
    detail: str = "Corpus refresh scheduled."
    expired: list[str] = Field(default_factory=list, description="Source ids being refreshed.")


class CorpusUploadResponse(BaseModel):
    """Outcome of ingesting one uploaded document into the regulatory corpus."""

    source_id: str
    ok: bool = True
    chunks: int = 0
    detail: str = ""


class HealthResponse(BaseModel):
    """Liveness/readiness of the assistant and its active profile."""

    status: str = "ok"
    profile: str = "local"
    region: str = "asia-southeast1"


# --------------------------------------------------------------------------- #
# Governance — A2A AgentCard projection
# --------------------------------------------------------------------------- #


class AgentSkillModel(BaseModel):
    id: str
    name: str
    description: str


class AgentCardModel(BaseModel):
    """A2A AgentCard served at /.well-known/agent-card.json (mirror of AgentCard)."""

    name: str
    description: str
    url: str
    version: str
    provider: str = "compliance-advisory"
    skills: list[AgentSkillModel] = Field(default_factory=list)

    @classmethod
    def from_domain(cls, card: m.AgentCard) -> AgentCardModel:
        data: dict[str, Any] = to_jsonable(card)
        return cls(**data)
