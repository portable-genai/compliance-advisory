"""MCP tool catalog adapter — the governed tool surface for system C1.

Backs the domain ``ToolCatalogPort`` by exposing C1's four governed, least-privilege
capabilities as ``ToolSpec`` objects: ``retrieve_regulations``, ``generate_checklist``,
``generate_testcases`` and ``regulator_questions``. These are the tools the assistant (or
a peer agent) may invoke — each with an explicit JSON input schema so access is scoped
and auditable (P-07, least privilege).

Interop: the catalog speaks **MCP 2026-07-28**. In an ADK deployment these specs are
surfaced to the agent through an ``McpToolset`` connected to an MCP server that fronts the
domain services; here the adapter only *declares* the governed catalog (declarative,
no live MCP connection required for listing). The ``mcp`` package is imported lazily and
only when an actual MCP wire object is requested.
"""

from __future__ import annotations

from typing import Any

from ...config import Settings
from ...domain.models import ToolSpec

# MCP protocol revision this catalog conforms to.
MCP_PROTOCOL_VERSION = "2026-07-28"

# Shared schema fragment: regulator / jurisdiction filters reused across tools.
_FILTERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "regulator": {
            "type": "string",
            "enum": ["MAS", "HKMA", "APRA", "FSA", "CROSS"],
            "description": "Restrict to a single regulator's guidance.",
        },
        "jurisdiction": {
            "type": "string",
            "enum": ["SG", "HK", "AU", "JP", "GLOBAL"],
            "description": "Restrict to a single jurisdiction.",
        },
    },
    "additionalProperties": False,
}


def _build_catalog() -> dict[str, ToolSpec]:
    """Declare the four governed tools with explicit, least-privilege input schemas."""
    return {
        "retrieve_regulations": ToolSpec(
            name="retrieve_regulations",
            description=(
                "Retrieve ranked passages with page-level citations from the "
                "MAS/HKMA/APRA/FSA regulatory knowledge base (Agent Search)."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Natural-language regulatory question.",
                    },
                    "top_k": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 50,
                        "default": 10,
                    },
                    "filters": _FILTERS_SCHEMA,
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        ),
        "generate_checklist": ToolSpec(
            name="generate_checklist",
            description=(
                "Generate a use-case-specific control checklist with cited rationale "
                "for each control. Output requires human review (maker-checker)."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "use_case": {
                        "type": "string",
                        "description": (
                            "The control use case, e.g. 'cloud outsourcing of a "
                            "core banking workload'."
                        ),
                    },
                    "filters": _FILTERS_SCHEMA,
                },
                "required": ["use_case"],
                "additionalProperties": False,
            },
        ),
        "generate_testcases": ToolSpec(
            name="generate_testcases",
            description=(
                "Generate automated test cases that verify each control for a use "
                "case, with citations and an optional automated check."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "use_case": {
                        "type": "string",
                        "description": "The control use case to derive test cases for.",
                    },
                    "filters": _FILTERS_SCHEMA,
                },
                "required": ["use_case"],
                "additionalProperties": False,
            },
        ),
        "regulator_questions": ToolSpec(
            name="regulator_questions",
            description=(
                "Produce the exact questions a regulator/CRO will ask for a use case, "
                "each with why it is asked and a cited model answer."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "use_case": {
                        "type": "string",
                        "description": "The use case under regulatory scrutiny.",
                    },
                    "filters": _FILTERS_SCHEMA,
                },
                "required": ["use_case"],
                "additionalProperties": False,
            },
        ),
    }


class McpToolCatalogAdapter:
    """Declarative MCP 2026-07-28 catalog of C1's four governed tools."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._catalog: dict[str, ToolSpec] = _build_catalog()

    # ------------------------------------------------------------------ #
    # ToolCatalogPort
    # ------------------------------------------------------------------ #
    def list_tools(self) -> list[ToolSpec]:
        return list(self._catalog.values())

    def get_tool(self, name: str) -> ToolSpec | None:
        return self._catalog.get(name)

    # ------------------------------------------------------------------ #
    # MCP wire helpers (lazy ``mcp`` import — only when actually used)
    # ------------------------------------------------------------------ #
    def as_mcp_tools(self) -> list[Any]:
        """Render the catalog as MCP ``Tool`` objects (MCP 2026-07-28 schema).

        Imported lazily so the catalog can be listed without the ``mcp`` package; only
        callers that need the on-the-wire MCP objects (e.g. an MCP server fronting the
        domain services, or an ADK ``McpToolset``) pull the dependency in.
        """
        from mcp import types as mcp_types  # lazy

        # verify: https://modelcontextprotocol.io/specification/2026-07-28
        return [
            mcp_types.Tool(
                name=spec.name,
                description=spec.description,
                input_schema=spec.input_schema,
            )
            for spec in self._catalog.values()
        ]
