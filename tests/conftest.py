"""Pytest fixtures: the ``local`` adapters (seeded) + assembled domain services.

The unit suite is driven by the **real** ``local`` adapter family
(``src/compliance_advisory/adapters/local``) rather than bespoke in-memory fakes, so
the offline implementation lives in exactly one place and the tests exercise the same
code the offline CLI runs. Every adapter constructs with a single ``Settings`` (the
adapter convention) pointed at ``:memory:`` SQLite, and the retrieval index is seeded
with the synthetic ``tests/fixtures/sample_regs`` corpus for determinism.

A few fixtures wrap the local adapter in a thin **recording** subclass that captures
call arguments for assertions (``.calls`` / ``.requests`` / ``.spans`` / ``.events``).
These add no behaviour: every method delegates to the real local adapter, so the
in-memory implementation is still the one under ``adapters/local``. The recorders are
the test instrumentation the previous bespoke fakes used to bundle.

The local LLM is schema-driven (it reads ``request.response_schema`` and returns a JSON
object whose keys match it, including ``used_source_ids`` recovered from the rendered
passage headers), so it stays correct whatever field names the four services declare.
"""

from __future__ import annotations

import importlib
from typing import Any

import pytest

from compliance_advisory.adapters.local.audit import LocalAppendOnlyAuditAdapter
from compliance_advisory.adapters.local.evaluation import LocalOfflineEvalAdapter
from compliance_advisory.adapters.local.grounding import LocalDisabledGroundingAdapter
from compliance_advisory.adapters.local.guardrail import LocalHeuristicGuardrailAdapter
from compliance_advisory.adapters.local.inventory import LocalControlInventoryAdapter
from compliance_advisory.adapters.local.ledger import LocalLedgerAdapter
from compliance_advisory.adapters.local.llm import LocalDeterministicLLMAdapter
from compliance_advisory.adapters.local.memory import LocalMemoryAdapter
from compliance_advisory.adapters.local.redaction import LocalRegexRedactionAdapter
from compliance_advisory.adapters.local.registry import LocalRegistryAdapter
from compliance_advisory.adapters.local.retrieval import (
    LocalFtsRetrievalAdapter,
    LocalIngestionAdapter,
)
from compliance_advisory.adapters.local.runtime import LocalAgentRuntimeAdapter
from compliance_advisory.adapters.local.session import LocalSessionAdapter
from compliance_advisory.adapters.local.tool_catalog import LocalToolCatalogAdapter
from compliance_advisory.adapters.local.tracer import LocalNoopTracerAdapter
from compliance_advisory.config import LocalSettings, Settings
from compliance_advisory.domain.control_mapping.models import (
    ControlObservation,
    GcpControl,
    RegRequirement,
)
from compliance_advisory.domain.models import (
    AuditEvent,
    Direction,
    LlmRequest,
    LlmResponse,
    RetrievalQuery,
    RetrievedPassage,
)
from tests.fixtures import sample_controls, sample_regs

#: A loopback peer for every API test. The app-object exposure guard refuses the
#: unauthenticated local posture to any other peer, and ``TestClient``'s default peer is the
#: literal host ``"testclient"``, which is not loopback. See
#: ``tests/unit/test_serving_path_exposure.py``.
LOOPBACK_PEER = ("127.0.0.1", 50000)


def _settings() -> Settings:
    """Settings whose local stores are ephemeral in-memory SQLite (deterministic)."""
    return Settings(
        profile="local",
        local=LocalSettings(db_path=":memory:", audit_path=":memory:", ledger_path=":memory:"),
    )


# --------------------------------------------------------------------------- #
# Recording wrappers — thin subclasses of the local adapters that capture call
# arguments for assertions. Every method delegates to the real local adapter.
# --------------------------------------------------------------------------- #
class RecordingRetrieval(LocalFtsRetrievalAdapter):
    """Local FTS5 retrieval that records the queries it received."""

    def __init__(self, settings: Settings, passages: list[RetrievedPassage] | None = None) -> None:
        super().__init__(settings)
        # Re-seed the in-memory index with the requested corpus (empty for the empty case).
        self.seed(list(sample_regs.SAMPLE_PASSAGES) if passages is None else list(passages))
        self.calls: list[RetrievalQuery] = []

    def retrieve(self, query: RetrievalQuery) -> list[RetrievedPassage]:
        self.calls.append(query)
        return super().retrieve(query)


class RecordingLLM(LocalDeterministicLLMAdapter):
    """Local deterministic LLM that records the requests it received."""

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.requests: list[LlmRequest] = []
        self.classify_calls: list[tuple[str, list[str]]] = []

    def generate(self, request: LlmRequest) -> LlmResponse:
        self.requests.append(request)
        return super().generate(request)

    def classify(self, text: str, labels: list[str]) -> str:
        self.classify_calls.append((text, labels))
        return super().classify(text, labels)


class RecordingRedaction(LocalRegexRedactionAdapter):
    """Local regex redaction that records the raw text it was asked to redact."""

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.calls: list[str] = []

    def redact(self, text: str):  # type: ignore[no-untyped-def]
        self.calls.append(text)
        return super().redact(text)


class RecordingTracer(LocalNoopTracerAdapter):
    """Local no-op tracer that records the span names it opened."""

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.spans: list[str] = []

    def span(self, name: str, **attributes: str):  # type: ignore[no-untyped-def]
        self.spans.append(name)
        return super().span(name, **attributes)


class RecordingGuardrail(LocalHeuristicGuardrailAdapter):
    """Local heuristic guardrail that records the (text, direction) screen calls.

    Behaviour is the real heuristic: benign text passes, malicious text (e.g.
    ``sample_regs.MALICIOUS_QUESTION``) is blocked. Only the recording is added.
    """

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.calls: list[tuple[str, Direction]] = []

    def screen(self, text: str, direction: Direction):  # type: ignore[no-untyped-def]
        self.calls.append((text, direction))
        return super().screen(text, direction)


class RecordingAudit(LocalAppendOnlyAuditAdapter):
    """Local append-only audit that also keeps the AuditEvent objects for assertions."""

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.events: list[AuditEvent] = []

    def record(self, event: AuditEvent) -> None:
        self.events.append(event)
        super().record(event)


# --------------------------------------------------------------------------- #
# Service / policy resolvers — locate the domain classes wherever they live.
# --------------------------------------------------------------------------- #
_SERVICE_MODULE_CANDIDATES = (
    "compliance_advisory.domain.qa_service",
    "compliance_advisory.domain.checklist_service",
    "compliance_advisory.domain.testcase_service",
    "compliance_advisory.domain.regulator_questions_service",
    "compliance_advisory.domain.services",
    "compliance_advisory.domain.orchestration",
    "compliance_advisory.domain.pipeline",
)
_POLICY_MODULE_CANDIDATES = (
    "compliance_advisory.domain.hitl",
    "compliance_advisory.domain.freshness_policy",
    "compliance_advisory.domain.policies",
    "compliance_advisory.domain.services",
)


def _resolve(symbol: str, candidates: tuple[str, ...]) -> Any:
    last: Exception | None = None
    for mod_name in candidates:
        try:
            module = importlib.import_module(mod_name)
        except ModuleNotFoundError as exc:  # pragma: no cover - layout fallback
            last = exc
            continue
        obj = getattr(module, symbol, None)
        if obj is not None:
            return obj
    raise ImportError(f"Could not locate domain symbol {symbol!r} in any of {candidates}") from last


def load_service(name: str) -> Any:
    return _resolve(name, _SERVICE_MODULE_CANDIDATES)


def load_policy(name: str) -> Any:
    return _resolve(name, _POLICY_MODULE_CANDIDATES)


# --------------------------------------------------------------------------- #
# Pytest fixtures — construct the (seeded) local adapters.
# --------------------------------------------------------------------------- #
@pytest.fixture
def retrieval() -> RecordingRetrieval:
    return RecordingRetrieval(_settings())


@pytest.fixture
def empty_retrieval() -> RecordingRetrieval:
    return RecordingRetrieval(_settings(), passages=[])


@pytest.fixture
def llm() -> RecordingLLM:
    return RecordingLLM(_settings())


@pytest.fixture
def grounding() -> LocalDisabledGroundingAdapter:
    return LocalDisabledGroundingAdapter(_settings())


@pytest.fixture
def guardrail() -> LocalHeuristicGuardrailAdapter:
    return LocalHeuristicGuardrailAdapter(_settings())


@pytest.fixture
def redaction() -> RecordingRedaction:
    return RecordingRedaction(_settings())


@pytest.fixture
def tracer() -> RecordingTracer:
    return RecordingTracer(_settings())


@pytest.fixture
def audit() -> RecordingAudit:
    return RecordingAudit(_settings())


@pytest.fixture
def session() -> LocalSessionAdapter:
    return LocalSessionAdapter(_settings())


@pytest.fixture
def memory() -> LocalMemoryAdapter:
    return LocalMemoryAdapter(_settings())


@pytest.fixture
def agent_runtime() -> LocalAgentRuntimeAdapter:
    return LocalAgentRuntimeAdapter(_settings())


@pytest.fixture
def evaluation() -> LocalOfflineEvalAdapter:
    return LocalOfflineEvalAdapter(_settings())


@pytest.fixture
def registry() -> LocalRegistryAdapter:
    return LocalRegistryAdapter(_settings())


@pytest.fixture
def tool_catalog() -> LocalToolCatalogAdapter:
    return LocalToolCatalogAdapter(_settings())


@pytest.fixture
def ledger() -> LocalLedgerAdapter:
    return LocalLedgerAdapter(_settings())


@pytest.fixture
def ingestion() -> LocalIngestionAdapter:
    return LocalIngestionAdapter(_settings())


@pytest.fixture
def recording_guardrail() -> RecordingGuardrail:
    """Heuristic guardrail that records screen calls; blocks malicious text deterministically."""
    return RecordingGuardrail(_settings())


# Direction is re-exported for unit tests that import it from the conftest namespace.
__all__ = ["Direction"]


@pytest.fixture
def qa_service(retrieval, llm, guardrail, redaction, grounding, tracer, audit):
    """ComplianceQAService(retrieval, llm, guardrail, redaction, grounding, tracer, audit)."""
    cls = load_service("ComplianceQAService")
    return cls(retrieval, llm, guardrail, redaction, grounding, tracer, audit)


@pytest.fixture
def checklist_service(retrieval, llm, guardrail, redaction, tracer, audit):
    """ChecklistService(retrieval, llm, guardrail, redaction, tracer, audit)."""
    cls = load_service("ChecklistService")
    return cls(retrieval, llm, guardrail, redaction, tracer, audit)


@pytest.fixture
def testcase_service(retrieval, llm, guardrail, redaction, tracer, audit):
    """TestCaseService(retrieval, llm, guardrail, redaction, tracer, audit)."""
    cls = load_service("TestCaseService")
    return cls(retrieval, llm, guardrail, redaction, tracer, audit)


@pytest.fixture
def regq_service(retrieval, llm, guardrail, redaction, tracer, audit):
    """RegulatorQuestionService(retrieval, llm, guardrail, redaction, tracer, audit)."""
    cls = load_service("RegulatorQuestionService")
    return cls(retrieval, llm, guardrail, redaction, tracer, audit)


# --------------------------------------------------------------------------- #
# Control-mapping capability (merged from C2) — ports + assembled services.
#
# The control-inventory fixtures wrap the REAL ``local`` inventory adapter (seeded with
# the deterministic ``sample_controls`` posture) exactly as the C1 fixtures wrap the real
# local retrieval/LLM/audit adapters. The requirement source is the one exception: in the
# merged app it is retrieval-backed (``RetrievalRequirementSourceAdapter``) and derives
# requirement ids from citations, so it cannot be seeded to the fixed ids the deterministic
# mapper keys on. ``SeededRequirementSource`` is therefore a thin ``RequirementSourcePort``
# double returning the ``sample_controls`` obligations (whose ids equal the local mapper's
# ``REQUIREMENT_CONTROL_MAP`` keys), so the mapping/gap/evidence assertions stay deterministic.
# The ``llm`` / ``tracer`` / ``audit`` fixtures above are reused unchanged (the mapping
# services take exactly those ports and no guardrail/redaction — posture split, R1/P-04 = N/A).
# --------------------------------------------------------------------------- #
class SeededRequirementSource:
    """RequirementSourcePort double: returns seeded obligations, records fetch calls."""

    def __init__(self, requirements: list[RegRequirement] | None = None) -> None:
        if requirements is None:
            requirements = list(sample_controls.SAMPLE_REQUIREMENTS)
        self._requirements = list(requirements)
        self.calls: list[tuple[str, str | None]] = []

    def fetch(self, scope: str, regulator: str | None = None) -> list[RegRequirement]:
        self.calls.append((scope, regulator))
        if regulator is None:
            return list(self._requirements)
        wanted = regulator.strip().upper()
        return [r for r in self._requirements if r.regulator.value.upper() == wanted]


class RecordingControlInventory(LocalControlInventoryAdapter):
    """Real local control inventory (seeded posture) that records observe calls."""

    def __init__(
        self,
        settings: Settings,
        observations: list[ControlObservation] | None = None,
        controls: list[GcpControl] | None = None,
    ) -> None:
        super().__init__(settings)
        obs = list(sample_controls.SAMPLE_OBSERVATIONS) if observations is None else observations
        ctrls = list(sample_controls.SAMPLE_CONTROLS) if controls is None else controls
        self.seed_observations(obs)
        self.seed_controls(ctrls)
        self.observe_calls: list[str] = []

    def observe(self, scope: str) -> list[ControlObservation]:
        self.observe_calls.append(scope)
        return super().observe(scope)


@pytest.fixture
def requirement_source() -> SeededRequirementSource:
    return SeededRequirementSource()


@pytest.fixture
def empty_requirement_source() -> SeededRequirementSource:
    return SeededRequirementSource(requirements=[])


@pytest.fixture
def control_inventory() -> RecordingControlInventory:
    return RecordingControlInventory(_settings())


@pytest.fixture
def empty_inventory() -> RecordingControlInventory:
    return RecordingControlInventory(_settings(), observations=[])


@pytest.fixture
def mapping_service(requirement_source, control_inventory, llm, tracer, audit):
    """ControlMappingService(requirement_source, control_inventory, llm, tracer, audit)."""
    from compliance_advisory.domain.control_mapping.mapping_service import ControlMappingService

    return ControlMappingService(requirement_source, control_inventory, llm, tracer, audit)


@pytest.fixture
def evidence_service(requirement_source, control_inventory, llm, tracer, audit):
    """EvidencePackService(requirement_source, control_inventory, llm, tracer, audit)."""
    from compliance_advisory.domain.control_mapping.evidence_service import EvidencePackService

    return EvidencePackService(requirement_source, control_inventory, llm, tracer, audit)


@pytest.fixture
def gap_service(requirement_source, control_inventory, llm, tracer, audit):
    """GapAnalysisService(requirement_source, control_inventory, llm, tracer, audit)."""
    from compliance_advisory.domain.control_mapping.gap_service import GapAnalysisService

    return GapAnalysisService(requirement_source, control_inventory, llm, tracer, audit)
