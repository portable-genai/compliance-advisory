"""Sampling is decided per call: pinned where the output is compared, free where it is drafted.

History. On 2026-08-26 two runs of one identical case against the `cdd-sow-research` deployment,
minutes apart, returned different scores, confidences and scorecard factors: the shared request
builder defaulted to `temperature=0.2`, so every grounded call sampled. This file first applied
that finding here by pinning EVERY request at 0.0, on the type and on both builders.

What changed (owner decision, 2026-09-23). A blanket pin is wrong in the other direction: it
flattens drafting, narration and judging, which gain nothing from it, and some models (Opus 5,
Fable 5) reject the parameter outright, so "free" has to mean the parameter is OMITTED, never
1.0. So the type and the builders now default to ``None`` (send nothing), and each call site
states its own choice:

* **pinned 0.0** where the output is extracted, classified, scored or labelled: the checklist
  (model-emitted severity labels), the control mapping (coverage classification), the gap
  analysis (severity labels), and the eval harness's inline answer (scored against a golden set);
* **free** for drafting and judging: the grounded answer, its self-critique judge, the test
  cases, the regulator questions and the horizon narration.

Each assertion below drives the REAL service with a recording model and reads the request it
actually sent, so a call site that loses its pin (or gains one) fails here by name.

**Temperature 0 is not a promise of determinism, and nothing here asserts one.** A hosted model
can still vary across batching and model revisions. It is the strongest thing a caller controls.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from tests.conftest import RecordingLLM
from tests.fixtures import fake_genai, sample_controls, sample_regs

from compliance_advisory.adapters.gcp.gemini_llm import GeminiLLMAdapter
from compliance_advisory.adapters.local.ledger import LocalLedgerAdapter
from compliance_advisory.config import LocalSettings, Settings
from compliance_advisory.domain import _grounded
from compliance_advisory.domain.control_mapping import _mapping
from compliance_advisory.domain.horizon import HorizonPolicy, HorizonScanService, carry_forward
from compliance_advisory.domain.kernel import LlmMessage, LlmRequest
from compliance_advisory.domain.models import (
    DocType,
    FreshnessRecord,
    FreshnessStatus,
    Jurisdiction,
    RegSource,
    Regulator,
)

ACTOR = "risk-officer@bank.test"


def _temperatures(llm: RecordingLLM) -> list[float | None]:
    assert llm.requests, "the service made no model call, so nothing was checked"
    return [request.temperature for request in llm.requests]


def test_the_request_type_and_both_builders_send_no_temperature_by_default() -> None:
    """Omitted means free: a caller that must be reproducible says 0.0 itself."""
    assert LlmRequest.__dataclass_fields__["temperature"].default is None
    for builder in (_grounded.build_llm_request, _mapping.build_llm_request):
        assert inspect.signature(builder).parameters["temperature"].default is None


# --------------------------------------------------------------------------- #
# Free: drafting and judging
# --------------------------------------------------------------------------- #
def test_the_answer_and_its_self_critique_sample_freely(qa_service: Any, llm: RecordingLLM) -> None:
    qa_service.answer(sample_regs.SAMPLE_QUESTION, actor=ACTOR)
    assert len(llm.requests) == 2, "expected the drafted answer and its critique"
    assert _temperatures(llm) == [None, None]


def test_the_test_cases_are_drafted_freely(testcase_service: Any, llm: RecordingLLM) -> None:
    testcase_service.generate(sample_regs.SAMPLE_USE_CASE, actor=ACTOR)
    assert _temperatures(llm) == [None]


def test_the_regulator_questions_are_drafted_freely(regq_service: Any, llm: RecordingLLM) -> None:
    regq_service.generate(sample_regs.SAMPLE_USE_CASE, actor=ACTOR)
    assert _temperatures(llm) == [None]


def _horizon_ledger() -> tuple[LocalLedgerAdapter, list[RegSource]]:
    source = RegSource(
        id="mas-trm",
        regulator=Regulator.MAS,
        jurisdiction=Jurisdiction.SG,
        title="MAS Technology Risk Management Guidelines",
        url="https://example.test/mas/trm",
        doc_type=DocType.GUIDELINE,
        version="2021",
        topics=("technology-risk",),
    )
    ledger = LocalLedgerAdapter(
        Settings(
            profile="local",
            local=LocalSettings(
                db_path=":memory:",
                audit_path=":memory:",
                ledger_path=":memory:",
                horizon_path=":memory:",
            ),
        )
    )
    t0 = datetime(2026, 1, 1, tzinfo=UTC)

    def record(checksum: str) -> FreshnessRecord:
        return FreshnessRecord(
            source_id=source.id,
            url=source.url,
            version=source.version,
            fetched_at=t0,
            expires_at=t0 + timedelta(days=7),
            checksum=checksum,
            status=FreshnessStatus.FRESH,
        )

    ledger.upsert(record("aaaa1111"))
    ledger.upsert(carry_forward(ledger.get(source.id), record("bbbb2222")))
    return ledger, [source]


class _Catalog:
    def __init__(self, sources: list[RegSource]) -> None:
        self._sources = sources

    def sources(self) -> list[RegSource]:
        return list(self._sources)

    def get(self, source_id: str) -> RegSource | None:
        return next((s for s in self._sources if s.id == source_id), None)


def test_the_horizon_narration_samples_freely(llm: RecordingLLM, tracer: Any, audit: Any) -> None:
    ledger, sources = _horizon_ledger()
    HorizonScanService(
        ledger=ledger,
        source_catalog=_Catalog(sources),
        llm=llm,
        tracer=tracer,
        audit=audit,
        tracker=None,
        policy=HorizonPolicy(),
        gap_service=None,
        review_router=None,
    ).scan("scope", ACTOR)
    assert _temperatures(llm) == [None]


# --------------------------------------------------------------------------- #
# Pinned: extraction, classification, labelling
# --------------------------------------------------------------------------- #
def test_the_checklist_is_pinned_because_it_labels_severity(
    checklist_service: Any, llm: RecordingLLM
) -> None:
    checklist_service.build(sample_regs.SAMPLE_USE_CASE, actor=ACTOR)
    assert _temperatures(llm) == [0.0]


def test_the_control_mapping_is_pinned_because_it_classifies_coverage(
    mapping_service: Any, llm: RecordingLLM
) -> None:
    mapping_service.map(sample_controls.SAMPLE_SCOPE, actor=ACTOR)
    assert _temperatures(llm) == [0.0]


def test_the_gap_analysis_is_pinned_because_it_labels_severity(
    gap_service: Any, llm: RecordingLLM
) -> None:
    gap_service.analyze(sample_controls.SAMPLE_SCOPE, actor=ACTOR)
    assert set(_temperatures(llm)) == {0.0}


# --------------------------------------------------------------------------- #
# The managed adapter: free means the parameter is not sent at all
# --------------------------------------------------------------------------- #
def _request(temperature: float | None) -> LlmRequest:
    return LlmRequest(messages=(LlmMessage(role="user", content="q"),), temperature=temperature)


@pytest.fixture
def gemini(monkeypatch: pytest.MonkeyPatch) -> tuple[GeminiLLMAdapter, fake_genai.FakeClient]:
    fake_genai.install(monkeypatch)
    adapter = GeminiLLMAdapter(Settings.load("config/settings.yaml"))
    client = fake_genai.FakeClient(text="MAS")
    adapter._client = client
    return adapter, client


def test_the_gemini_adapter_omits_temperature_when_the_caller_left_it_free(
    gemini: tuple[GeminiLLMAdapter, fake_genai.FakeClient],
) -> None:
    adapter, client = gemini
    adapter.generate(_request(None))
    assert "temperature" not in client.calls[-1]["config"].kwargs


def test_the_gemini_adapter_sends_a_pinned_temperature(
    gemini: tuple[GeminiLLMAdapter, fake_genai.FakeClient],
) -> None:
    adapter, client = gemini
    adapter.generate(_request(0.0))
    assert client.calls[-1]["config"].kwargs["temperature"] == 0.0


def test_the_gemini_classifier_stays_pinned(
    gemini: tuple[GeminiLLMAdapter, fake_genai.FakeClient],
) -> None:
    adapter, client = gemini
    assert adapter.classify("text", ["MAS", "HKMA"]) == "MAS"
    assert client.calls[-1]["config"].kwargs["temperature"] == 0.0
