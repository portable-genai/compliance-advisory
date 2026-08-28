"""Serve the governed tool catalog C1 already declares, over MCP 2026-07-28.

The catalog has existed since the adapter was written: four governed, least-privilege tools
with explicit JSON input schemas. It was never SERVED. There was no MCP server process anywhere
in the fleet, so a surface described in two places, an A2A agent card and this catalog, could be
read by a human and reached by nobody.

Nothing is declared here. The catalog is the single source of what exists, and this module only
supplies the callables that answer it. `hex_service_kit.mcpserve.bind` refuses a mismatch in
either direction at start-up, which is what keeps the two honest: a declared tool with no
handler is a capability the service advertises and cannot perform, and a handler for an
undeclared tool is a reachable entry point nobody governed. Neither starts.

The actor is the reason this module is small and deliberate rather than a router. Every domain
service here takes an `actor` and writes it to the audit trail, so serving MCP must not
manufacture one. The transport carries no verified end-user identity, so the caller identity is
supplied by the composition root that starts the server and is recorded as what it is: a
service-to-service caller, not a person. Nothing here escalates that into a human subject.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from hex_service_kit import mcpserve

from ..api import deps

#: The tools this module answers. Kept as data so a test can compare it against the catalog
#: without starting a server or importing the MCP SDK.
HANDLER_NAMES: tuple[str, ...] = (
    "retrieve_regulations",
    "generate_checklist",
    "generate_testcases",
    "regulator_questions",
)


def _filters(arguments: Mapping[str, Any]) -> dict[str, str]:
    raw = arguments.get("filters") or {}
    return {str(k): str(v) for k, v in raw.items()} if isinstance(raw, Mapping) else {}


def build_handlers(actor: str) -> dict[str, mcpserve.Handler]:
    """Bind each declared tool to the domain service that already performs it.

    ``actor`` is the audited caller. It is passed in rather than derived here because this
    transport verifies no end user: the composition root decides what to record, and recording
    a service caller as a person would be the more expensive kind of wrong.
    """

    # Each handler returns the DOMAIN object. The kit renders it through its own shared
    # `to_jsonable`, so the wire shape is decided in one place for every repo that serves rather
    # than re-invented per tool here.
    def retrieve_regulations(**arguments: Any) -> Any:
        return deps.get_qa_service().answer(
            str(arguments.get("query", "")), actor=actor, filters=_filters(arguments)
        )

    def generate_checklist(**arguments: Any) -> Any:
        return deps.get_checklist_service().build(str(arguments.get("use_case", "")), actor=actor)

    def generate_testcases(**arguments: Any) -> Any:
        return deps.get_testcase_service().generate(str(arguments.get("use_case", "")), actor=actor)

    def regulator_questions(**arguments: Any) -> Any:
        return deps.get_regulator_question_service().generate(
            str(arguments.get("use_case", "")), actor=actor
        )

    return {
        "retrieve_regulations": retrieve_regulations,
        "generate_checklist": generate_checklist,
        "generate_testcases": generate_testcases,
        "regulator_questions": regulator_questions,
    }


def build_server(actor: str, *, with_audit_tools: bool = True) -> Any:
    """Build the MCP server for C1's catalog, refusing on any catalog/handler mismatch.

    ``with_audit_tools`` adds the kit's two READ-ONLY evidence tools, so a client that can reach
    this service can also verify and carry out its trail. They are read-only on purpose and the
    kit is where that is enforced: appending to the trail is something a service does as it
    works, never something a caller asks for.
    """
    container = deps.get_container()
    catalog = container.tool_catalog
    return mcpserve.build_server(
        name="compliance-advisory",
        version=str(getattr(container.settings, "version", "") or "0.0.1"),
        catalog=catalog,
        handlers=build_handlers(actor),
        audit_store=container.audit if with_audit_tools else None,
    )
