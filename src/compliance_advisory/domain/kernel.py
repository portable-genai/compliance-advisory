"""The vertical-neutral kernel of the Rsk1 domain (practices-audit A7).

This module owns the machinery a fork of this repo inherits UNCHANGED when it swaps the
compliance vertical for another one: provenance and citation envelopes, the source
registry and its publisher taxonomy, retrieval queries and passages, the LLM envelope,
guardrail and redaction verdicts, session/memory, the WORM audit event, the corpus
freshness ledger, agent cards and tool specs, the shared severity scale, and the single
clock.

The point of the split is a DEPENDENCY DIRECTION, not a filename. This module imports the
Python standard library and the shared commons packages, and **nothing at all from
``compliance_advisory``**. That is what makes the arrow real:

    vertical artifacts (models.py) -> kernel envelopes (this file) -> ports

``domain/models.py`` imports this module and re-exports every name it defines, so every
existing import site (``from compliance_advisory.domain.models import Citation``) keeps
working byte for byte. ``tests/unit/test_kernel_boundary.py`` proves the direction by
EXECUTION: a fresh interpreter imports this module and asserts that
``compliance_advisory.domain.models`` never enters ``sys.modules``.

Why the regulator/jurisdiction taxonomy lives here and not with the vertical artifacts:
``Citation``, ``RegSource`` and ``FetchedDocument`` are typed with it, and ports, audit
evidence and the serialization contract all depend on those field types. Moving the
envelopes here while leaving their field types behind would have recreated the very
import the split exists to remove. A fork changes the MEMBERS of ``Regulator`` and
``Jurisdiction`` (which supervisor, which country) without changing the shape of any
envelope; it replaces the generated artifacts in ``models.py`` outright. See
ARCHITECTURE.md section 1.1.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

# ``agent_eval_kit.report`` is imported as a SUBMODULE, not through the package root: the root
# re-exports ``PromotionGateClient``, which imports httpx, and this module promises to stay
# framework-free. The report types themselves are pure stdlib.
from agent_eval_kit.report import EvalMetricResult as EvalMetricResult
from agent_eval_kit.report import EvalReport as EvalReport
from hex_service_kit import StrEnum as StrEnum
from hex_service_kit.observability import TokenUsage as TokenUsage


def utcnow() -> datetime:
    """Timezone-aware UTC now, the single clock the domain uses."""
    return datetime.now(UTC)


# --------------------------------------------------------------------------- #
# Publisher taxonomy (the field types the provenance envelopes are built from)
# --------------------------------------------------------------------------- #
class Regulator(StrEnum):
    """Financial-services regulators whose guidance forms the knowledge base."""

    MAS = "MAS"  # Monetary Authority of Singapore
    HKMA = "HKMA"  # Hong Kong Monetary Authority
    APRA = "APRA"  # Australian Prudential Regulation Authority
    FSA = "FSA"  # Financial Services Agency of Japan
    CROSS = "CROSS"  # Cross-jurisdiction cloud / AI guidance (e.g. cloud, GenAI)


class Jurisdiction(StrEnum):
    SG = "SG"
    HK = "HK"
    AU = "AU"
    JP = "JP"
    GLOBAL = "GLOBAL"


REGULATOR_JURISDICTION: dict[Regulator, Jurisdiction] = {
    Regulator.MAS: Jurisdiction.SG,
    Regulator.HKMA: Jurisdiction.HK,
    Regulator.APRA: Jurisdiction.AU,
    Regulator.FSA: Jurisdiction.JP,
    Regulator.CROSS: Jurisdiction.GLOBAL,
}


class DocType(StrEnum):
    NOTICE = "notice"
    GUIDELINE = "guideline"
    CIRCULAR = "circular"
    STANDARD = "standard"  # e.g. APRA CPS 230
    CONSULTATION = "consultation"
    ADVISORY = "advisory"
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class RegSource:
    """A primary regulatory document tracked in the source registry."""

    id: str  # stable slug, e.g. "mas-trm-guidelines-2021"
    regulator: Regulator
    jurisdiction: Jurisdiction
    title: str
    url: str
    doc_type: DocType = DocType.OTHER
    version: str = "unknown"  # publisher version / revision label
    published_date: str | None = None  # ISO date as published by the regulator
    topics: tuple[str, ...] = ()  # e.g. ("outsourcing", "cloud", "operational-resilience")


# --------------------------------------------------------------------------- #
# Retrieval & citation
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class Citation:
    """Regulator-grade provenance attached to every generated claim.

    Page-level citation is a hard requirement: a compliance answer must point to the
    exact source document and page so a regulator/CRO can verify it.
    """

    source_id: str
    regulator: Regulator
    jurisdiction: Jurisdiction
    title: str
    url: str
    version: str = "unknown"
    page: int | None = None
    snippet: str = ""
    score: float | None = None


@dataclass(frozen=True, slots=True)
class RetrievedPassage:
    text: str
    citation: Citation
    score: float = 0.0


@dataclass(frozen=True, slots=True)
class RetrievalQuery:
    text: str
    top_k: int = 10
    # Structured filters resolved by the adapter (e.g. {"regulator": "MAS"}).
    filters: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class WebCitation:
    """Provenance for a public-web grounded fact (secondary, cross-border)."""

    title: str
    url: str
    snippet: str = ""


# --------------------------------------------------------------------------- #
# Generation (LLM)
# --------------------------------------------------------------------------- #
class ThinkingLevel(StrEnum):
    MINIMAL = "minimal"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True, slots=True)
class LlmMessage:
    role: str  # "user" | "model" | "system"
    content: str


@dataclass(frozen=True, slots=True)
class LlmRequest:
    messages: tuple[LlmMessage, ...]
    system_instruction: str | None = None
    model: str | None = None  # None => adapter default from config
    thinking: ThinkingLevel = ThinkingLevel.MEDIUM
    temperature: float = 0.0  # omitted at a call site means this value; it must not sample
    max_output_tokens: int = 4096
    response_schema: dict | None = None  # JSON schema for structured output


# ``TokenUsage`` is deliberately NOT declared here. Three int fields defaulting to zero,
# hand-copied byte for byte into every repo that needs them, is a shared value type that is not
# being shared. It comes from :mod:`hex_service_kit.observability` (imported at the top of this
# module and re-exported here), so there is one definition to change rather than one per repo to
# keep in step. The field names, types and defaults are the commons ones.


@dataclass(frozen=True, slots=True)
class LlmResponse:
    text: str
    usage: TokenUsage = field(default_factory=TokenUsage)
    model: str = ""
    web_citations: tuple[WebCitation, ...] = ()
    raw: dict | None = None


# --------------------------------------------------------------------------- #
# Safety (guardrail + PII redaction), A1 Guardrail Gateway concerns
# --------------------------------------------------------------------------- #
class GuardrailCategory(StrEnum):
    PROMPT_INJECTION = "prompt_injection"
    JAILBREAK = "jailbreak"
    SENSITIVE_DATA = "sensitive_data"
    MALICIOUS_URL = "malicious_url"
    HATE = "hate"
    HARASSMENT = "harassment"
    SEXUAL = "sexual"
    DANGEROUS = "dangerous"
    OTHER = "other"


class Direction(StrEnum):
    INPUT = "input"
    OUTPUT = "output"


@dataclass(frozen=True, slots=True)
class GuardrailFinding:
    category: GuardrailCategory
    confidence: str  # e.g. "low" | "medium" | "high"
    detail: str = ""


@dataclass(frozen=True, slots=True)
class GuardrailVerdict:
    allowed: bool
    direction: Direction
    findings: tuple[GuardrailFinding, ...] = ()
    # Text after any inline sanitisation the guardrail applied (may equal input).
    sanitized_text: str | None = None
    reason: str = ""


@dataclass(frozen=True, slots=True)
class RedactionFinding:
    info_type: str  # e.g. "PERSON_NAME", "SG_NRIC_FIN", "CREDIT_CARD_NUMBER"
    count: int = 1


@dataclass(frozen=True, slots=True)
class RedactionResult:
    text: str  # de-identified text safe to send to the model / audit log
    findings: tuple[RedactionFinding, ...] = ()

    @property
    def redacted(self) -> bool:
        return bool(self.findings)


# --------------------------------------------------------------------------- #
# Runtime, session & memory
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class Session:
    id: str
    user_id: str
    case_id: str | None = None
    created_at: datetime = field(default_factory=utcnow)


@dataclass(frozen=True, slots=True)
class MemoryItem:
    id: str
    content: str
    scope: str = "user"  # "user" | "case" | "global"
    created_at: datetime = field(default_factory=utcnow)


# --------------------------------------------------------------------------- #
# Audit & observability, A5 Observability, Audit & FinOps concerns
# --------------------------------------------------------------------------- #
class Decision(StrEnum):
    ALLOWED = "allowed"
    BLOCKED = "blocked"
    ESCALATED = "escalated"  # routed to a human (maker-checker)


@dataclass(frozen=True, slots=True)
class AuditEvent:
    """An immutable, WORM-stored record of one assistant interaction.

    Prompt and response are stored **already redacted** (P-04): PII is removed at the
    boundary before it is ever written to the audit sink or a trace span.
    """

    action: str  # "ask" | "checklist" | "testcases" | "regulator_questions"
    actor: str  # authenticated user / service identity
    decision: Decision
    redacted_prompt: str
    redacted_response: str
    citations: tuple[Citation, ...] = ()
    resource: str = "compliance-advisory"
    trace_id: str | None = None
    timestamp: datetime = field(default_factory=utcnow)
    metadata: dict[str, str] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Evaluation gate, A4 AI Quality & Model-Risk concerns
# --------------------------------------------------------------------------- #
# ``EvalMetricResult`` and ``EvalReport`` are deliberately NOT declared here either. They come
# from :mod:`agent_eval_kit.report` (imported at the top of this module and re-exported here).
#
# The commons ``EvalReport`` is a strict SUPERSET of what a local declaration would carry: the
# same ``dataset`` / ``results`` / ``n_examples`` fields with the same defaults, plus the durable
# evidence a promotion decision has to be reconstructible from (``run_id``, ``dataset_version``,
# ``dataset_digest``, ``evaluator``, ``schema_version``, ``trace_id``, ``correlation_id``,
# ``artifact_refs``, ``attested``), all defaulted, so a plain three-field constructor compiles
# against it unchanged.
#
# Critically, the ``passed`` rule is IDENTICAL, character for character:
# ``n_examples > 0 and bool(results) and all(r.passed for r in results)``. The fail-closed guards
# this repo relies on are the commons rule, not a local strengthening that a re-export would
# silently weaken, so no gate verdict moves.


# --------------------------------------------------------------------------- #
# Governance, A3 Agent Registry & Governance concerns (A2A AgentCard)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class AgentSkill:
    id: str
    name: str
    description: str


@dataclass(frozen=True, slots=True)
class AgentCard:
    """Minimal A2A-style agent card published at /.well-known/agent-card.json."""

    name: str
    description: str
    url: str
    version: str
    skills: tuple[AgentSkill, ...] = ()
    provider: str = "compliance-advisory"


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """A governed, least-privilege tool exposed to the agent (typically via MCP)."""

    name: str
    description: str
    input_schema: dict = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Corpus freshness, the 7-day fetch-at-runtime ledger
# --------------------------------------------------------------------------- #
class FreshnessStatus(StrEnum):
    FRESH = "fresh"
    EXPIRED = "expired"
    MISSING = "missing"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class FreshnessRecord:
    """One regulatory source's freshness state, plus the state it superseded.

    The ``previous_*`` fields are what makes the ledger diffable: an upsert carries the
    prior generation forward (see ``compliance_advisory.domain.horizon.carry_forward``,
    a VERTICAL consumer of this kernel envelope, never an import of it) so horizon
    scanning can tell a re-fetch that changed nothing from a genuine republication
    without a second, shadow ledger. They are empty/``None`` on the first ingest of a
    source, which is exactly the signal for a NEW_SOURCE change.
    """

    source_id: str
    url: str
    version: str
    fetched_at: datetime
    expires_at: datetime
    checksum: str = ""
    status: FreshnessStatus = FreshnessStatus.FRESH
    # Superseded generation (the diff base for horizon change detection).
    previous_version: str = ""
    previous_checksum: str = ""
    previous_fetched_at: datetime | None = None
    previous_status: FreshnessStatus | None = None

    def is_fresh(self, now: datetime | None = None) -> bool:
        now = now or utcnow()
        return self.status == FreshnessStatus.FRESH and now < self.expires_at

    @property
    def has_history(self) -> bool:
        """Whether this record supersedes an earlier ingest of the same source."""
        return bool(self.previous_checksum or self.previous_version)


@dataclass(frozen=True, slots=True)
class FetchedDocument:
    source: RegSource
    content: bytes
    mime_type: str
    fetched_at: datetime = field(default_factory=utcnow)
    checksum: str = ""


@dataclass(frozen=True, slots=True)
class IngestResult:
    source_id: str
    document_id: str
    chunks: int = 0
    ok: bool = True
    detail: str = ""


# --------------------------------------------------------------------------- #
# The shared severity scale (used by checklists, gaps, horizon changes alike)
# --------------------------------------------------------------------------- #
class Severity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"
