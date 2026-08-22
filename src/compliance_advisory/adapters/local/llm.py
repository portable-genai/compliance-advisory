"""Local LLM adapter (LLMPort) — a deterministic, schema-driven generator.

The ``local`` profile's stand-in for **Gemini**: no model, no network, fully
reproducible. It reads ``request.response_schema`` (the JSON schema the calling service
asks for) and emits a deterministic JSON object whose keys match it, including
``used_source_ids`` mapped from the source-id headers present in the rendered passage
block, plus a plausible ``classify``. There is no Google emulator for Gemini, so this
path is unconditional.

The schema-driven ``FakeLLM`` is a real, registered adapter rather than a test fixture, so
the in-memory implementation lives once under ``adapters/local`` and drives both the offline
tests and the CLI.
"""

from __future__ import annotations

import json
import re
from typing import Any

from ...config import Settings
from ...domain.models import (
    LlmRequest,
    LlmResponse,
    TokenUsage,
)
from .control_mapping_seed import REQUIREMENT_CONTROL_MAP

# The rendered passage block keys each source with ``[source_id p.N]`` headers; recover
# the ids the service actually grounded on so the answer cites only retrieved sources.
_SOURCE_HEADER_RE = re.compile(r"\[([a-z0-9][a-z0-9\-]*?)(?:\s+p\.[^\]]+)?\]")
_QUESTION_RE = re.compile(r"QUESTION:\s*\n(.*?)\n\s*PASSAGES:", re.DOTALL)
# The control-mapping REQUIREMENTS/GAP blocks render each requirement id as a
# line-anchored ``[id]`` header; recover them so the deterministic mapper only maps ids
# the service supplied. Control/observation ids also match, but they are not in
# REQUIREMENT_CONTROL_MAP (yield []) and the service drops any id that is not a supplied
# requirement, so scanning the whole message is harmless (matches the C2 behaviour).
_REQ_HEADER_RE = re.compile(r"^\[([a-z0-9][a-z0-9\-]*)\]", re.MULTILINE)


def _schema_properties(schema: dict | None) -> dict[str, Any]:
    if not schema:
        return {}
    props = schema.get("properties")
    return props if isinstance(props, dict) else {}


class LocalDeterministicLLMAdapter:
    """Deterministic LLM whose ``generate`` returns JSON matching the request schema.

    The body is shaped from ``request.response_schema``: the Q&A answer schema and the
    self-critique schema are flat objects, while the three generators wrap an ``items``
    array whose element schema differs (checklist / test case / regulator question). The
    adapter inspects the nested item properties to emit the right element shape, so it
    stays correct for whichever service calls it. Every item and the answer reference the
    source ids actually present in the prompt via ``used_source_ids`` so the services map
    page-level citations.
    """

    REASONING_MODEL = "gemini-3.5-flash"
    TRIAGE_MODEL = "gemini-3.1-flash-lite"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._reasoning_model = settings.models.reasoning or self.REASONING_MODEL
        self._triage_model = settings.models.triage or self.TRIAGE_MODEL

    # ------------------------------------------------------------------ #
    # LLMPort
    # ------------------------------------------------------------------ #
    def generate(self, request: LlmRequest) -> LlmResponse:
        source_ids = self._source_ids_from_request(request)
        body = self._body_for_schema(request, source_ids)
        return LlmResponse(
            text=json.dumps(body),
            usage=TokenUsage(input_tokens=128, output_tokens=64, thinking_tokens=32),
            model=request.model or self._reasoning_model,
            web_citations=(),
            raw=body,
        )

    def classify(self, text: str, labels: list[str]) -> str:
        # Deterministic triage: first label (the services only use this for routing).
        return labels[0] if labels else ""

    # ------------------------------------------------------------------ #
    # Schema-driven body
    # ------------------------------------------------------------------ #
    def _source_ids_from_request(self, request: LlmRequest) -> list[str]:
        user = ""
        for message in reversed(request.messages):
            if message.role == "user":
                user = message.content
                break
        seen: list[str] = []
        for sid in _SOURCE_HEADER_RE.findall(user):
            if sid not in seen:
                seen.append(sid)
        return seen

    def _flat_field(self, name: str, source_ids: list[str]) -> Any:
        return {
            "answer": (
                "Before onboarding a cloud provider, conduct provider due diligence "
                "covering data residency, exit strategy and concentration risk, and "
                "retain audit rights."
            ),
            "confidence": 0.86,
            "used_source_ids": list(source_ids),
            "citations": list(source_ids),
            "caveats": ["Verify the current version of each instrument."],
            "grounded": True,
            "groundedness": 0.9,
            "supported": True,
        }.get(name, "")

    def _item_for(self, item_props: set[str], source_ids: list[str]) -> dict[str, Any]:
        if "question" in item_props:  # regulator question
            item: dict[str, Any] = {
                "question": "How did you assess concentration risk for this provider?",
                "why_asked": "Concentration risk is a core supervisory concern.",
                "model_answer": "We mapped provider dependencies and set limits.",
                "regulator": "MAS",
            }
        elif "steps" in item_props:  # test case
            item = {
                "id": "tc-001",
                "title": "Verify a due-diligence record exists.",
                "control_id": "C-001",
                "steps": ["Open the vendor file", "Check the due-diligence sign-off"],
                "expected_result": "A signed due-diligence record is present.",
                "automated_check": "SELECT count(*) FROM dd WHERE signed = true;",
            }
        else:  # checklist control
            item = {
                "control_id": "C-001",
                "control": "Perform cloud provider due diligence.",
                "rationale": "Required before any material cloud outsourcing.",
                "severity": "high",
            }
        item["used_source_ids"] = list(source_ids)
        return {k: v for k, v in item.items() if k in item_props or k == "used_source_ids"}

    def _requirement_ids(self, request: LlmRequest) -> list[str]:
        user = request.messages[-1].content if request.messages else ""
        return _REQ_HEADER_RE.findall(user)

    def _mapping_items(self, request: LlmRequest) -> list[dict[str, Any]]:
        # One item per requirement id, controls from the built-in map; the mapping
        # service recomputes coverage from the live observations (this hint is ignored).
        items: list[dict[str, Any]] = []
        for rid in self._requirement_ids(request):
            controls = REQUIREMENT_CONTROL_MAP.get(rid, [])
            items.append(
                {
                    "requirement_id": rid,
                    "control_ids": controls,
                    "coverage": "full",
                    "rationale": f"Controls {controls} address requirement {rid}.",
                }
            )
        return items

    def _gap_items(self, request: LlmRequest) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for rid in self._requirement_ids(request):
            items.append(
                {
                    "requirement_id": rid,
                    "severity": "high",
                    "remediation": f"Enable the missing control(s) for {rid} and re-observe.",
                }
            )
        return items

    def _body_for_schema(self, request: LlmRequest, source_ids: list[str]) -> dict[str, Any]:
        schema = request.response_schema
        props = _schema_properties(schema)
        if not props:
            return {
                "answer": self._flat_field("answer", source_ids),
                "confidence": 0.86,
                "used_source_ids": list(source_ids),
                "grounded": True,
                "caveats": [],
            }
        if "items" in props:
            items_decl = props["items"] if isinstance(props["items"], dict) else {}
            item_schema = items_decl.get("items", {})
            item_props = set(_schema_properties(item_schema))
            if "coverage" in item_props:  # control-mapping schema
                return {"items": self._mapping_items(request)}
            if "remediation" in item_props:  # gap-remediation schema
                return {"items": self._gap_items(request)}
            return {"items": [self._item_for(item_props, source_ids)]}
        return {name: self._flat_field(name, source_ids) for name in props}
