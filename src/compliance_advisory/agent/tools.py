"""ADK FunctionTools that expose the C1 domain services to the agent.

Each tool is a thin, side-effect-honest wrapper: it builds the relevant domain
service from a :class:`~compliance_advisory.config.Container` (so every port is
bound to the adapter selected by the active profile), invokes the service, and
returns a JSON-safe dict via :func:`~compliance_advisory.domain.serialization.to_jsonable`.

Design notes
------------
* The domain services own orchestration (redact -> guardrail -> retrieve ->
  ground -> generate -> self-critique -> HITL -> guardrail -> audit; SPEC §5).
  These tools deliberately add **no** business logic of their own — the model
  decides *which* artifact to produce, the service decides *how*.
* ``google.adk`` is imported lazily inside :func:`build_function_tools` so this
  module imports cleanly under the on-prem/test profile with no ADK installed
  (SPEC §4). The plain Python tool callables are importable and unit-testable
  without ADK at all.
* Every callable carries a precise type-hinted signature and a docstring: ADK
  derives the tool's name, description and JSON parameter schema from them, so
  the wording here is part of the agent's contract surface (the "Gemini
  Enterprise Agent Platform" Agent Runtime sees exactly this).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..config import Container, Settings, build_container

if TYPE_CHECKING:  # pragma: no cover - typing only, never imported at runtime
    from google.adk.tools import FunctionTool


# --------------------------------------------------------------------------- #
# Default actor for tool-initiated calls.
#
# The model invokes a tool on behalf of an authenticated end user. The deployed
# Agent Runtime injects the real identity (via session state / request context);
# until then we record a stable service identity so every audit row has an actor.
# --------------------------------------------------------------------------- #
_DEFAULT_ACTOR = "compliance-advisory-agent"


def _container(settings: Settings | None) -> Container:
    """Build the DI container, honouring an optionally-injected ``Settings``."""
    return build_container(settings)


# --------------------------------------------------------------------------- #
# The four tool callables (plain functions: importable & testable without ADK).
# Lazy-import the domain services inside each call so this module stays light and
# so service construction always reflects the live container bindings.
# --------------------------------------------------------------------------- #
def answer_question(
    question: str,
    actor: str = _DEFAULT_ACTOR,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Answer a regulatory-compliance question with regulator-grade citations.

    Produces a grounded answer over the MAS / HKMA / APRA / FSA knowledge base,
    with regulator + jurisdiction + document + version + page citations and a
    maker-checker (``requires_human_review``) flag for low-confidence or
    high-severity replies.

    Args:
      question: The compliance question to answer.
      actor: Authenticated user / service identity the request is made for.

    Returns:
      A JSON-safe ``Answer`` dict (answer text, citations, confidence,
      requires_human_review, caveats).
    """
    from ..domain.serialization import to_jsonable
    from ..domain.services import ComplianceQAService

    c = _container(settings)
    service = ComplianceQAService(
        retrieval=c.retrieval,
        llm=c.llm,
        guardrail=c.guardrail,
        redaction=c.redaction,
        grounding=c.grounding,
        tracer=c.tracer,
        audit=c.audit,
        review_router=c.review_router,
    )
    return to_jsonable(service.answer(question, actor))


def build_checklist(
    use_case: str,
    actor: str = _DEFAULT_ACTOR,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Build a use-case-specific control checklist with cited controls.

    Returns a ``ControlChecklist`` of controls (each with rationale, severity and
    citations). Checklists are consequential, so the result is flagged for human
    review (maker-checker, P-06).

    Args:
      use_case: The business / technical use case to assess (e.g. "deploying a
        GenAI customer-service assistant on public cloud in Singapore").
      actor: Authenticated user / service identity the request is made for.

    Returns:
      A JSON-safe ``ControlChecklist`` dict.
    """
    from ..domain.serialization import to_jsonable
    from ..domain.services import ChecklistService

    c = _container(settings)
    service = ChecklistService(
        retrieval=c.retrieval,
        llm=c.llm,
        guardrail=c.guardrail,
        redaction=c.redaction,
        tracer=c.tracer,
        audit=c.audit,
    )
    return to_jsonable(service.build(use_case, actor))


def generate_testcases(
    use_case: str,
    actor: str = _DEFAULT_ACTOR,
    settings: Settings | None = None,
) -> list[dict[str, Any]]:
    """Generate automated test cases that verify each control for a use case.

    Returns a list of ``TestCase`` objects (steps, expected result, an optional
    automated check expressed as pseudocode / rego / SQL, and citations).

    Args:
      use_case: The business / technical use case to generate tests for.
      actor: Authenticated user / service identity the request is made for.

    Returns:
      A JSON-safe list of ``TestCase`` dicts.
    """
    from ..domain.serialization import to_jsonable
    from ..domain.services import TestCaseService

    c = _container(settings)
    service = TestCaseService(
        retrieval=c.retrieval,
        llm=c.llm,
        guardrail=c.guardrail,
        redaction=c.redaction,
        tracer=c.tracer,
        audit=c.audit,
    )
    return to_jsonable(service.generate(use_case, actor))


def regulator_questions(
    use_case: str,
    actor: str = _DEFAULT_ACTOR,
    settings: Settings | None = None,
) -> list[dict[str, Any]]:
    """Anticipate the questions a regulator / CRO will ask, with model answers.

    Returns a list of ``RegulatorQuestion`` objects (the question, why it is
    asked, a cited model answer, and the regulator it maps to) so a compliance
    team can rehearse a regulatory review.

    Args:
      use_case: The business / technical use case under regulatory scrutiny.
      actor: Authenticated user / service identity the request is made for.

    Returns:
      A JSON-safe list of ``RegulatorQuestion`` dicts.
    """
    from ..domain.serialization import to_jsonable
    from ..domain.services import RegulatorQuestionService

    c = _container(settings)
    service = RegulatorQuestionService(
        retrieval=c.retrieval,
        llm=c.llm,
        guardrail=c.guardrail,
        redaction=c.redaction,
        tracer=c.tracer,
        audit=c.audit,
    )
    return to_jsonable(service.generate(use_case, actor))


def map_controls(
    scope: str,
    actor: str = _DEFAULT_ACTOR,
    regulator: str | None = None,
    settings: Settings | None = None,
) -> list[dict[str, Any]]:
    """Map each regulatory requirement in scope to the GCP control(s) that satisfy it.

    Returns one ``ControlMapping`` per requirement: the mapped GCP control ids, a
    coverage verdict (full / partial / none) computed from the observed control
    posture, a rationale and citations. Runs over the bank's own control posture
    (no customer data), so this path carries no guardrail / DLP redaction by design.

    Args:
      scope: The control scope to map (e.g. a project, workload or use case).
      actor: Authenticated user / service identity the request is made for.
      regulator: Optional regulator filter (e.g. "MAS") to narrow the requirements.

    Returns:
      A JSON-safe list of ``ControlMapping`` dicts.
    """
    from ..domain.control_mapping.mapping_service import ControlMappingService
    from ..domain.serialization import to_jsonable

    c = _container(settings)
    service = ControlMappingService(
        requirement_source=c.requirement_source,
        control_inventory=c.control_inventory,
        llm=c.llm,
        tracer=c.tracer,
        audit=c.audit,
    )
    return to_jsonable(service.map(scope, actor, regulator))


def analyze_gaps(
    scope: str,
    actor: str = _DEFAULT_ACTOR,
    regulator: str | None = None,
    settings: Settings | None = None,
) -> list[dict[str, Any]]:
    """Derive remediation for each requirement whose GCP controls are missing or weak.

    Returns a ``ControlGap`` per uncovered requirement: a severity, the missing
    control families, concrete remediation guidance and citations. Consequential,
    so gaps are flagged for human review (maker-checker, P-06).

    Args:
      scope: The control scope to analyse for gaps.
      actor: Authenticated user / service identity the request is made for.
      regulator: Optional regulator filter to narrow the requirements.

    Returns:
      A JSON-safe list of ``ControlGap`` dicts.
    """
    from ..domain.control_mapping.gap_service import GapAnalysisService
    from ..domain.serialization import to_jsonable

    c = _container(settings)
    service = GapAnalysisService(
        requirement_source=c.requirement_source,
        control_inventory=c.control_inventory,
        llm=c.llm,
        tracer=c.tracer,
        audit=c.audit,
    )
    return to_jsonable(service.analyze(scope, actor, regulator))


def build_evidence_pack(
    scope: str,
    actor: str = _DEFAULT_ACTOR,
    regulator: str | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Assemble an audit-ready evidence pack (mappings + gaps + coverage summary).

    Returns an ``EvidencePack`` bundling the control mappings, the derived gaps and
    a coverage summary for the scope, ready to hand to an auditor. Evidence packs
    are always sent for human review (maker-checker) before use.

    Args:
      scope: The control scope the pack attests to.
      actor: Authenticated user / service identity the request is made for.
      regulator: Optional regulator filter to narrow the requirements.

    Returns:
      A JSON-safe ``EvidencePack`` dict (always ``requires_human_review``).
    """
    from ..domain.control_mapping.evidence_service import EvidencePackService
    from ..domain.serialization import to_jsonable

    c = _container(settings)
    service = EvidencePackService(
        requirement_source=c.requirement_source,
        control_inventory=c.control_inventory,
        llm=c.llm,
        tracer=c.tracer,
        audit=c.audit,
        review_router=c.review_router,
    )
    return to_jsonable(service.build(scope, actor, regulator))


def scan_horizon(
    scope: str,
    actor: str = _DEFAULT_ACTOR,
    regulator: str | None = None,
    tenant: str = "",
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Scan the regulatory horizon: what changed, does it apply, how material, who owns it.

    Diffs the corpus freshness ledger to detect new, republished, re-versioned or
    withdrawn regulatory instruments, then assesses each one deterministically:
    applicability against the bank's configured footprint, a materiality score and
    band computed in pure code from config-owned weights, and an accountable owner.
    Every assessment carries the citation of the instrument that drove it and is sent
    for human review (maker-checker, P-06).

    Args:
      scope: The control scope or use case the scan is run for.
      actor: Authenticated user / service identity the request is made for.
      regulator: Optional regulator filter (MAS, HKMA, APRA, FSA, CROSS).
      tenant: Verified tenant partition the tracked journey belongs to.

    Returns:
      A JSON-safe ``HorizonScan`` dict (always ``requires_human_review``).
    """
    from ..api.deps import build_horizon_scan_service
    from ..domain.serialization import to_jsonable

    service = build_horizon_scan_service(_container(settings))
    return to_jsonable(service.scan(scope, actor, regulator=regulator, tenant=tenant))


def track_horizon_implementation(
    change_id: str,
    status: str,
    actor: str = _DEFAULT_ACTOR,
    tenant: str = "",
    note: str = "",
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Advance an assessed regulatory change through the implementation journey.

    Moves one tracked change to ``not_started``, ``in_progress``, ``implemented``,
    ``accepted_risk`` or ``not_applicable``. Access is gated on the verified tenant:
    a change owned by another tenant is refused. Closing a change as implemented or
    accepted risk is consequential and is routed for human sign-off.

    Args:
      change_id: The stable id of the assessed change to advance.
      status: The new implementation status.
      actor: Authenticated user / service identity the request is made for.
      tenant: Verified tenant partition that owns the tracked change.
      note: Optional free-text note recorded with the transition.

    Returns:
      A JSON-safe ``ImplementationItem`` dict for the updated change.
    """
    from ..api.deps import build_horizon_tracking_service
    from ..domain.horizon import ImplementationStatus
    from ..domain.serialization import to_jsonable

    service = build_horizon_tracking_service(_container(settings))
    item = service.update_status(change_id, ImplementationStatus(status), actor, tenant, note=note)
    return to_jsonable(item)


# The plain callables, in stable order, so the agent layer and tests share one list.
TOOL_FUNCTIONS = (
    answer_question,
    build_checklist,
    generate_testcases,
    regulator_questions,
    map_controls,
    analyze_gaps,
    build_evidence_pack,
    scan_horizon,
    track_horizon_implementation,
)


def build_function_tools() -> list[FunctionTool]:
    """Wrap each domain-service callable as an ADK ``FunctionTool``.

    ADK introspects each function's signature and docstring to derive the tool
    name, description and parameter JSON schema. ``google.adk`` is imported here
    (lazily) so the module is import-safe without ADK installed (SPEC §4).
    """
    from google.adk.tools import FunctionTool

    return [FunctionTool(func=fn) for fn in TOOL_FUNCTIONS]
