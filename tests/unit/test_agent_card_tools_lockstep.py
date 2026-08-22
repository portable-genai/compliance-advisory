"""The A2A agent card and the ADK tool surface must stay in lockstep.

The card advertises exactly the skills the agent can invoke: each ``AgentSkill.id``
is the ``__name__`` of a callable in ``tools.TOOL_FUNCTIONS`` (and vice versa), so a
peer agent or the registry never sees a skill the runtime cannot fulfil. This guards
against the surfaces drifting apart (e.g. a route/service added without its skill).
"""

from __future__ import annotations

from compliance_advisory.agent.agent_card import SKILLS, build_agent_card
from compliance_advisory.agent.tools import TOOL_FUNCTIONS
from compliance_advisory.config import LocalSettings, Settings

# The three control-mapping capabilities this agent advertises.
_MAPPING_SKILLS = {"map_controls", "analyze_gaps", "build_evidence_pack"}


def _settings() -> Settings:
    return Settings(profile="local", local=LocalSettings(db_path=":memory:"))


def test_card_skill_ids_are_exactly_the_tool_function_names() -> None:
    skill_ids = [s.id for s in SKILLS]
    tool_names = [fn.__name__ for fn in TOOL_FUNCTIONS]
    # Ordered lockstep: same set, same order, one skill per tool.
    assert skill_ids == tool_names


def test_mapping_skills_are_advertised() -> None:
    skill_ids = {s.id for s in SKILLS}
    assert skill_ids >= _MAPPING_SKILLS
    tool_names = {fn.__name__ for fn in TOOL_FUNCTIONS}
    assert tool_names >= _MAPPING_SKILLS


def test_built_card_carries_the_mapping_skills() -> None:
    card = build_agent_card(_settings())
    ids = {s.id for s in card.skills}
    assert ids >= _MAPPING_SKILLS
    # Every advertised skill has a name and a non-empty description.
    for skill in card.skills:
        assert skill.name
        assert skill.description
