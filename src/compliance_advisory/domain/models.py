"""Rsk1 vertical domain artifacts, on top of the vertical-neutral kernel.

This module is the heart of the hexagon. It has **no dependency on Google Cloud,
ADK, FastAPI, or any framework**, only the Python standard library. Every adapter
(GCP, remote-platform, or on-prem placeholder) speaks in terms of these types, which
is what lets the managed-service stack be swapped for an on-premise one without
touching domain logic (General Principle P-02, "no vendor lock-in / ports & adapters").

Since practices-audit A7 closed, the kernel/vertical boundary is PHYSICAL rather than a
comment banner. The vertical-neutral machinery (provenance and citations, the source
registry and its publisher taxonomy, retrieval, the LLM envelope, guardrail and redaction
verdicts, session/memory, the WORM audit event, the freshness ledger, agent cards, the
shared severity scale, the clock) lives in :mod:`compliance_advisory.domain.kernel`,
which imports nothing from this package. What remains DECLARED here is what a fork of
this repo rewrites: the four generated Rsk1 artifacts.

Every kernel name is re-exported below with ``from .kernel import X as X``, so existing
import sites such as ``from compliance_advisory.domain.models import Citation`` keep
working unchanged. ``tests/unit/test_kernel_boundary.py`` proves, by running a fresh
interpreter, that importing the kernel does NOT drag this module in.
"""

from __future__ import annotations

from dataclasses import dataclass

from .kernel import (
    REGULATOR_JURISDICTION as REGULATOR_JURISDICTION,
)
from .kernel import (
    AgentCard as AgentCard,
)
from .kernel import (
    AgentSkill as AgentSkill,
)
from .kernel import (
    AuditEvent as AuditEvent,
)
from .kernel import (
    Citation as Citation,
)
from .kernel import (
    Decision as Decision,
)
from .kernel import (
    Direction as Direction,
)
from .kernel import (
    DocType as DocType,
)
from .kernel import (
    EvalMetricResult as EvalMetricResult,
)
from .kernel import (
    EvalReport as EvalReport,
)
from .kernel import (
    FetchedDocument as FetchedDocument,
)
from .kernel import (
    FreshnessRecord as FreshnessRecord,
)
from .kernel import (
    FreshnessStatus as FreshnessStatus,
)
from .kernel import (
    GuardrailCategory as GuardrailCategory,
)
from .kernel import (
    GuardrailFinding as GuardrailFinding,
)
from .kernel import (
    GuardrailVerdict as GuardrailVerdict,
)
from .kernel import (
    IngestResult as IngestResult,
)
from .kernel import (
    Jurisdiction as Jurisdiction,
)
from .kernel import (
    LlmMessage as LlmMessage,
)
from .kernel import (
    LlmRequest as LlmRequest,
)
from .kernel import (
    LlmResponse as LlmResponse,
)
from .kernel import (
    MemoryItem as MemoryItem,
)
from .kernel import (
    RedactionFinding as RedactionFinding,
)
from .kernel import (
    RedactionResult as RedactionResult,
)
from .kernel import (
    RegSource as RegSource,
)
from .kernel import (
    Regulator as Regulator,
)
from .kernel import (
    RetrievalQuery as RetrievalQuery,
)
from .kernel import (
    RetrievedPassage as RetrievedPassage,
)
from .kernel import (
    Session as Session,
)
from .kernel import (
    Severity as Severity,
)
from .kernel import (
    StrEnum as StrEnum,
)
from .kernel import (
    ThinkingLevel as ThinkingLevel,
)
from .kernel import (
    TokenUsage as TokenUsage,
)
from .kernel import (
    ToolSpec as ToolSpec,
)
from .kernel import (
    WebCitation as WebCitation,
)
from .kernel import (
    utcnow as utcnow,
)

# --------------------------------------------------------------------------- #
# Top-level assistant outputs (the four artifacts C1 produces).
#
# THIS is the Rsk1 vertical. A fork replaces the dataclasses below (and the services,
# `control_mapping/` and `horizon/` modules that produce them) while keeping every kernel
# envelope above untouched.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Answer:
    question: str
    answer: str
    citations: tuple[Citation, ...] = ()
    web_citations: tuple[WebCitation, ...] = ()
    confidence: float = 0.0
    requires_human_review: bool = True  # maker-checker floor (P-06)
    caveats: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ChecklistItem:
    control_id: str
    control: str
    rationale: str
    severity: Severity = Severity.MEDIUM
    citations: tuple[Citation, ...] = ()


@dataclass(frozen=True, slots=True)
class ControlChecklist:
    use_case: str
    items: tuple[ChecklistItem, ...] = ()
    requires_human_review: bool = True


@dataclass(frozen=True, slots=True)
class TestCase:
    id: str
    title: str
    control_id: str
    steps: tuple[str, ...]
    expected_result: str
    automated_check: str | None = None  # pseudocode / rego / SQL the test maps to
    citations: tuple[Citation, ...] = ()


@dataclass(frozen=True, slots=True)
class RegulatorQuestion:
    question: str
    why_asked: str
    model_answer: str
    regulator: Regulator
    citations: tuple[Citation, ...] = ()
