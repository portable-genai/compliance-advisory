"""A2A AgentCard for the C1 Compliance Assistant (A3 Registry & Governance).

This builds the assistant's discovery card — the same minimal A2A shape the
``agent-registry`` service stores and serves (SPEC §6). It is published at
``/.well-known/agent-card.json``; :func:`agent_card_document` returns the JSON-safe
body the API layer serves there.

The card advertises exactly the skills C1 produces (Answer, ControlChecklist,
TestCase[], RegulatorQuestion[], and the control-mapping module's ControlMapping[],
ControlGap[] and EvidencePack), mirroring the ADK FunctionTools so a peer agent or
the registry sees one consistent capability surface.

This module is pure (domain models only) and imports without ADK or any Google
Cloud SDK installed (SPEC §4).
"""

from __future__ import annotations

from typing import Any

from ..config import Settings
from ..domain.models import AgentCard, AgentSkill

# Skill ids are the stable, machine-facing capability names; they intentionally
# match the FunctionTool callables in ``agent.tools`` so the card and the tool
# surface stay in lockstep.
SKILLS: tuple[AgentSkill, ...] = (
    AgentSkill(
        id="answer_question",
        name="Grounded compliance answer",
        description=(
            "Answer a regulatory-compliance question over the MAS / HKMA / APRA / "
            "FSA knowledge base with regulator, jurisdiction, document, version and "
            "page citations, a confidence score and a maker-checker review flag."
        ),
    ),
    AgentSkill(
        id="build_checklist",
        name="Control checklist",
        description=(
            "Build a use-case-specific control checklist; each control carries a "
            "rationale, severity and citations. Flagged for human review (P-06)."
        ),
    ),
    AgentSkill(
        id="generate_testcases",
        name="Control test cases",
        description=(
            "Generate automated test cases that verify each control: steps, expected "
            "result, an optional automated check (pseudocode / rego / SQL), and citations."
        ),
    ),
    AgentSkill(
        id="regulator_questions",
        name="Regulator question rehearsal",
        description=(
            "Anticipate the questions a regulator or CRO will ask for a use case, each "
            "with why it is asked and a cited model answer."
        ),
    ),
    AgentSkill(
        id="map_controls",
        name="Control mapping",
        description=(
            "Map each regulatory requirement in scope to the GCP control(s) that "
            "satisfy it, with a coverage verdict computed from the observed control "
            "posture, a rationale and citations."
        ),
    ),
    AgentSkill(
        id="analyze_gaps",
        name="Control gap analysis",
        description=(
            "Derive remediation for each requirement whose GCP controls are missing "
            "or misconfigured: severity, the missing control families and concrete "
            "remediation guidance. Flagged for human review (P-06)."
        ),
    ),
    AgentSkill(
        id="build_evidence_pack",
        name="Evidence pack",
        description=(
            "Assemble an audit-ready evidence pack (control mappings, gaps and a "
            "coverage summary) for a scope. Always sent for human review."
        ),
    ),
    AgentSkill(
        id="scan_horizon",
        name="Regulatory horizon scan",
        description=(
            "Detect what moved in the regulatory corpus (new, republished, "
            "re-versioned or withdrawn instruments) and assess each change "
            "deterministically: applicability against the bank's footprint, a "
            "materiality score and band computed in pure code from config-owned "
            "weights, and an accountable owner. Cited and always human-reviewed."
        ),
    ),
    AgentSkill(
        id="track_horizon_implementation",
        name="Regulatory change tracking",
        description=(
            "Advance an assessed regulatory change through the implementation "
            "journey and link the GCP controls that evidence its closure. Gated on "
            "the verified tenant; closures are sent for maker-checker sign-off."
        ),
    ),
)

_DESCRIPTION = (
    "Grounded RAG + agentic assistant and control mapper for Compliance/Risk teams "
    "at APAC banks. Produces cited artifacts (grounded answers, control checklists, "
    "control test cases and regulator questions) and maps regulatory requirements to "
    "GCP controls with coverage verdicts, gap remediation and audit-ready evidence "
    "packs. It also scans the regulatory horizon: it detects corpus changes, assesses "
    "applicability and materiality deterministically, routes ownership and tracks "
    "implementation to closure. Over MAS / HKMA / APRA / FSA regulations and cloud / AI "
    "guidance. Built ports-and-adapters on the Gemini Enterprise Agent Platform "
    "(Agent Search retrieval, Agent Runtime hosting)."
)


def build_agent_card(settings: Settings) -> AgentCard:
    """Construct the A2A :class:`AgentCard` for this assistant.

    The card ``url`` is derived from the deployed Agent Runtime resource name when
    available, otherwise a stable logical endpoint; operators override it via the
    public ingress when serving ``/.well-known/agent-card.json``.
    """
    base_url = _resolve_url(settings)
    return AgentCard(
        name="compliance-advisory",
        description=_DESCRIPTION,
        url=base_url,
        version="0.1.0",
        skills=SKILLS,
        provider="compliance-advisory",
    )


def agent_card_document(settings: Settings) -> dict[str, Any]:
    """Return the JSON-safe body to serve at ``/.well-known/agent-card.json``.

    Uses :func:`~compliance_advisory.domain.serialization.to_jsonable` so enums
    and dataclasses become plain JSON (matching the SPEC §6 AgentCard shape).
    """
    from ..domain.serialization import to_jsonable

    return to_jsonable(build_agent_card(settings))


def _resolve_url(settings: Settings) -> str:
    """Best-effort public URL for the card, region-pinned to asia-southeast1."""
    resource = settings.agent_engine.resource_name
    if resource:
        # Logical reasoningEngine endpoint on the Agent Platform API host.
        return f"https://aiplatform.googleapis.com/v1/{resource}"
    return "https://compliance-advisory.rsk.internal/a2a"
