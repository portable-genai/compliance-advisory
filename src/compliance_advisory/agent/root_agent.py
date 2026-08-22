"""Root ADK agent for the C1 Compliance Assistant, hosted on Agent Runtime.

This is the agent the Gemini Enterprise Agent Platform **Agent Runtime**
(ex-Agent Engine) hosts. It wires together:

* the four domain-service :class:`FunctionTool` wrappers (``agent.tools``),
* the isolated ``google_search`` grounding **sub-agent** as an ``AgentTool``
  (``agent.grounding_agent``; one built-in tool per agent — SPEC §3),
* the defense-in-depth model-boundary **callbacks** (redact + guardrail + audit;
  ``agent.callbacks``), and
* the reasoning model ``settings.models.reasoning`` (``gemini-3.5-flash``) at
  ``thinking=high`` (SPEC §3).

ADK convention is honoured two ways: the module exposes a ``root_agent``
attribute (what ADK / ``adk web`` / Agent Runtime discover by default) **and** a
``build_root_agent(settings)`` factory for explicit, test-friendly construction.

Import safety (SPEC §4)
-----------------------
``google.adk`` is heavy and GCP-only. All ADK imports are quarantined inside
:func:`build_root_agent`, and the module-level ``root_agent`` is built lazily via
:class:`_LazyRootAgent` so merely importing this module never requires ADK — the
on-prem/test profile imports it cleanly. Touch any attribute of ``root_agent``
(as ADK does at deploy time) and it builds on first access.

Deploying to Agent Runtime
--------------------------
Wrap and deploy with the Agent Platform SDK (region pinned to
``asia-southeast1``)::

    from vertexai import agent_engines
    from compliance_advisory.agent.root_agent import build_root_agent
    from compliance_advisory.config import Settings

    remote = agent_engines.create(
        build_root_agent(Settings.load()),
        requirements=["google-adk==2.3.0", "compliance-advisory"],
    )  # -> reasoningEngine resource; record it in settings.agent_engine.resource_name

Exposing over A2A: ``to_a2a(build_root_agent(settings))`` produces an A2A app
that serves ``/.well-known/agent-card.json`` (see :func:`to_a2a_app` and
``agent.agent_card``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..config import Settings

if TYPE_CHECKING:  # pragma: no cover - typing only
    from google.adk.agents import LlmAgent

ROOT_AGENT_NAME = "compliance_advisory"

_ROOT_INSTRUCTION = (
    "You are the C1 Compliance Assistant and Control Mapper for Compliance and "
    "Risk teams at APAC banks. You answer over a knowledge base of MAS, HKMA, "
    "APRA and FSA regulations plus cross-jurisdiction cloud / AI guidance, and you "
    "produce cited artifacts and map requirements to GCP controls.\n\n"
    "Routing:\n"
    "- A regulatory question -> call answer_question.\n"
    "- 'What controls do we need for <use case>?' -> call build_checklist.\n"
    "- 'How do we test these controls?' -> call generate_testcases.\n"
    "- 'What will the regulator/CRO ask about <use case>?' -> call "
    "regulator_questions.\n"
    "- 'Which GCP controls satisfy <scope>'s requirements?' -> call map_controls.\n"
    "- 'Where are our control gaps for <scope>?' -> call analyze_gaps.\n"
    "- 'Produce the evidence pack for <scope>' -> call build_evidence_pack.\n"
    "- Need recent public-web corroboration -> delegate to the web_grounding "
    "sub-agent; treat its results as secondary evidence only.\n\n"
    "Rules:\n"
    "- Every claim must carry a citation to the primary source (regulator, "
    "document, version, page). Never invent a citation or a regulation.\n"
    "- Prefer the primary knowledge base; the web is corroboration, not a source "
    "of truth.\n"
    "- If a consequential output (checklist, test cases) or a low-confidence / "
    "high-severity answer is produced, state that it requires human review "
    "(maker-checker).\n"
    "- Do not request, repeat or store personal data; it is redacted at the "
    "boundary and must not appear in your output."
)


def build_root_agent(settings: Settings | None = None) -> LlmAgent:
    """Construct the root ADK ``LlmAgent`` for the assistant.

    Wires the four FunctionTools, the optional ``google_search`` grounding
    sub-agent (as an ``AgentTool``), and the redact/guardrail/audit callbacks
    built from the DI container. The reasoning model runs at ``thinking=high``
    (SPEC §3). All ADK imports are local to this function (SPEC §4).
    """
    settings = settings or Settings.load()

    from google.adk.agents import LlmAgent
    from google.adk.tools.agent_tool import AgentTool
    from google.genai import types

    from ..config import build_container
    from .callbacks import build_callbacks, configure_span_privacy
    from .grounding_agent import build_grounding_agent
    from .tools import build_function_tools

    # PII must never land in trace spans (SPEC §3); set before anything runs.
    configure_span_privacy()

    container = build_container(settings)
    callbacks = build_callbacks(container)

    tools: list[Any] = list(build_function_tools())

    # One built-in tool per agent => google_search lives in a sub-agent, surfaced
    # to the root as an agent-as-tool. Omitted entirely when grounding is off.
    grounding_agent = build_grounding_agent(settings)
    if grounding_agent is not None:
        tools.append(AgentTool(agent=grounding_agent))

    # thinking=high for the reasoning model (gemini-3.5-flash) per SPEC §3.
    generate_content_config = types.GenerateContentConfig(
        temperature=0.2,
        thinking_config=types.ThinkingConfig(thinking_budget=-1),
    )

    return LlmAgent(
        name=ROOT_AGENT_NAME,
        model=settings.models.reasoning,
        description=(
            "Grounded compliance assistant and control mapper: answers, control "
            "checklists, control test cases and regulator questions, plus control "
            "mapping, gap analysis and evidence packs over MAS/HKMA/APRA/FSA guidance."
        ),
        instruction=_ROOT_INSTRUCTION,
        tools=tools,
        generate_content_config=generate_content_config,
        before_model_callback=callbacks["before_model_callback"],
        after_model_callback=callbacks["after_model_callback"],
        after_agent_callback=callbacks["after_agent_callback"],
    )


def to_a2a_app(settings: Settings | None = None) -> Any:
    """Expose the root agent as an A2A app (serves ``/.well-known/agent-card.json``).

    Thin wrapper over ADK's ``to_a2a`` so peers can discover and call the
    assistant over A2A v1.0 (SPEC §3/§6). ADK is imported lazily (SPEC §4).
    Pair the served card with :func:`~compliance_advisory.agent.agent_card.agent_card_document`.
    """
    from google.adk.a2a.utils.agent_to_a2a import to_a2a

    return to_a2a(build_root_agent(settings))


class _LazyRootAgent:
    """Lazy proxy so ``import root_agent`` never pulls in ADK.

    ADK discovers a module-level ``root_agent``. We must expose that name without
    forcing ADK to be importable at module import time (on-prem/test profile,
    SPEC §4). The real ``LlmAgent`` is built on first attribute access and cached.
    """

    __slots__ = ("_agent",)

    def __init__(self) -> None:
        self._agent: LlmAgent | None = None

    def _resolve(self) -> LlmAgent:
        if self._agent is None:
            self._agent = build_root_agent()
        return self._agent

    def __getattr__(self, name: str) -> Any:
        return getattr(self._resolve(), name)

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        state = "unbuilt" if self._agent is None else "built"
        return f"<LazyRootAgent {ROOT_AGENT_NAME} ({state})>"


# ADK convention: a module-level ``root_agent`` the runtime discovers. Lazy so
# importing this module is safe without ADK installed (SPEC §4).
root_agent = _LazyRootAgent()
