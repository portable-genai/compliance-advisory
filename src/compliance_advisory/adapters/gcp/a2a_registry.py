"""A2A registry adapter — agent discovery and governance for system C1 (A3).

Backs the domain ``AgentRegistryPort`` with an in-process, **A2A v1.0**-style registry of
``AgentCard`` objects. In a standalone deployment C1 registers its own card here and can
serve it at the well-known A2A discovery path; inside the full platform the
``platform`` profile swaps this for a thin client to ``agent-registry``.

A2A discovery contract: an agent publishes its capabilities as an **AgentCard** served at
``/.well-known/agent-card.json``; peers fetch that card to learn the agent's skills,
endpoint URL and version before initiating an A2A task. ``agent_card_dict`` produces that
JSON body. No external call is required — this adapter is pure, in-memory governance.
"""

from __future__ import annotations

from ...config import Settings
from ...domain.models import AgentCard, AgentSkill

# The A2A well-known discovery path for an agent's card.
AGENT_CARD_PATH = "/.well-known/agent-card.json"

# C1's own skills, surfaced on its AgentCard so peers/the registry can discover the four
# governed capabilities the Compliance Assistant offers.
_C1_SKILLS: tuple[AgentSkill, ...] = (
    AgentSkill(
        id="answer",
        name="Grounded compliance Q&A",
        description=(
            "Answer regulatory questions over MAS/HKMA/APRA/FSA guidance with page-level citations."
        ),
    ),
    AgentSkill(
        id="checklist",
        name="Control checklist",
        description="Generate a use-case-specific control checklist with citations.",
    ),
    AgentSkill(
        id="testcases",
        name="Control test cases",
        description="Generate automated test cases that verify each control.",
    ),
    AgentSkill(
        id="regulator_questions",
        name="Regulator question set",
        description=("Produce the questions a regulator/CRO will ask, with cited model answers."),
    ),
)


class A2ARegistryAdapter:
    """In-process A2A AgentCard registry: register / get / list, plus card export."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._cards: dict[str, AgentCard] = {}
        # Seed the registry with C1's own card so a standalone deployment is
        # immediately discoverable.
        self.register(self._self_card())

    # ------------------------------------------------------------------ #
    # AgentRegistryPort
    # ------------------------------------------------------------------ #
    def register(self, card: AgentCard) -> None:
        self._cards[card.name] = card

    def get(self, name: str) -> AgentCard | None:
        return self._cards.get(name)

    def list(self) -> list[AgentCard]:
        return list(self._cards.values())

    # ------------------------------------------------------------------ #
    # A2A discovery helper
    # ------------------------------------------------------------------ #
    def agent_card_dict(self, name: str | None = None) -> dict:
        """Return the ``/.well-known/agent-card.json`` body for ``name``.

        Defaults to C1's own card. The shape mirrors the A2A AgentCard contract
        (SPEC §6): ``name``, ``description``, ``url``, ``version``, ``provider`` and a
        list of ``skills`` (``id`` / ``name`` / ``description``).
        """
        card = self.get(name) if name else self._cards.get(self._self_name())
        if card is None:
            raise KeyError(f"No AgentCard registered for '{name}'.")
        return {
            "name": card.name,
            "description": card.description,
            "url": card.url,
            "version": card.version,
            "provider": card.provider,
            "skills": [
                {"id": s.id, "name": s.name, "description": s.description} for s in card.skills
            ],
        }

    # ------------------------------------------------------------------ #
    # C1's own card
    # ------------------------------------------------------------------ #
    def _self_name(self) -> str:
        return self._settings.agent_engine.display_name or "compliance-advisory"

    def _self_card(self) -> AgentCard:
        return AgentCard(
            name=self._self_name(),
            description=(
                "C1 Compliance Assistant — grounded RAG + agentic assistant over "
                "MAS/HKMA/APRA/FSA regulations with regulator-grade citations."
            ),
            url=f"https://compliance-advisory.{self._settings.region}.example/a2a",
            version="1.0.0",
            skills=_C1_SKILLS,
            provider="compliance-advisory",
        )
