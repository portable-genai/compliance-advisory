"""Shared control-mapping routine (private to the domain layer).

The three services (mapping, evidence pack, gap analysis) share the same skeleton:
fetch requirements from the reg KB, observe the live control posture and list the
known controls, render them into the prompt context, call the LLM with a
structured-output schema, defensively parse the JSON, and map the model's returned
ids back to the real ``GcpControl`` / ``RegRequirement`` / ``Citation`` objects, then
compute coverage from which required controls are actually ENABLED.

This module factors out that machinery (the analogue of C1's ``_grounded.py``) so each
service keeps the exact constructor and method signature mandated by SPEC §5 while
sharing one well-tested core. It is ``_``-prefixed and not part of the public domain
API.

Pure domain code — talks only to ports and models, no Google Cloud / ADK imports.
"""

from __future__ import annotations

import json
from typing import Any

from .models import (
    SATISFYING_STATES,
    ControlFamily,
    ControlObservation,
    Coverage,
    GcpControl,
    LlmMessage,
    LlmRequest,
    LlmResponse,
    RegRequirement,
    Severity,
    ThinkingLevel,
)
from .prompts import CONTROL_BLOCK, OBSERVATION_BLOCK, REQUIREMENT_BLOCK

#: Severity rank for picking the "highest" severity across gaps.
_SEVERITY_RANK: dict[Severity, int] = {
    Severity.LOW: 0,
    Severity.MEDIUM: 1,
    Severity.HIGH: 2,
    Severity.CRITICAL: 3,
}

_SEVERITY_BY_VALUE: dict[str, Severity] = {s.value: s for s in Severity}
_COVERAGE_BY_VALUE: dict[str, Coverage] = {c.value: c for c in Coverage}


def coerce_severity(value: Any, default: Severity = Severity.MEDIUM) -> Severity:
    """Map a model-emitted severity string to the ``Severity`` enum defensively."""
    if isinstance(value, Severity):
        return value
    if isinstance(value, str):
        return _SEVERITY_BY_VALUE.get(value.strip().lower(), default)
    return default


def coerce_coverage(value: Any, default: Coverage = Coverage.NONE) -> Coverage:
    """Map a model-emitted coverage string to the ``Coverage`` enum defensively."""
    if isinstance(value, Coverage):
        return value
    if isinstance(value, str):
        return _COVERAGE_BY_VALUE.get(value.strip().lower(), default)
    return default


def highest_severity(severities: list[Severity]) -> Severity | None:
    """Return the most severe entry, or None for an empty list."""
    if not severities:
        return None
    return max(severities, key=lambda s: _SEVERITY_RANK[s])


# --------------------------------------------------------------------------- #
# Prompt-context rendering
# --------------------------------------------------------------------------- #
def render_requirements(requirements: list[RegRequirement]) -> str:
    """Render requirements into the numbered context block for the prompt."""
    if not requirements:
        return "(no requirements were fetched)"
    blocks: list[str] = []
    for r in requirements:
        c = r.citation
        page = str(c.page) if c.page is not None else "?"
        blocks.append(
            REQUIREMENT_BLOCK.format(
                id=r.id,
                regulator=r.regulator.value,
                jurisdiction=r.jurisdiction.value,
                title=r.title,
                text=r.text.strip(),
                source_id=c.source_id,
                page=page,
                citation_title=c.title,
            )
        )
    return "\n".join(blocks)


def render_controls(controls: list[GcpControl]) -> str:
    """Render the available GCP controls into the prompt context block."""
    if not controls:
        return "(no controls are known)"
    return "\n".join(
        CONTROL_BLOCK.format(
            id=ctrl.id,
            family=ctrl.family.value,
            name=ctrl.name,
            description=ctrl.description.strip(),
            config_ref=ctrl.config_ref or "(none)",
        )
        for ctrl in controls
    )


def render_observations(observations: list[ControlObservation]) -> str:
    """Render the live control observations into the prompt context block."""
    if not observations:
        return "(no posture was observed)"
    return "\n".join(
        OBSERVATION_BLOCK.format(
            control_id=o.control_id,
            resource=o.resource,
            state=o.state.value,
            source=o.source,
            detail=o.detail.strip(),
        )
        for o in observations
    )


# --------------------------------------------------------------------------- #
# Structured-output parsing
# --------------------------------------------------------------------------- #
def parse_structured(response: LlmResponse) -> dict[str, Any]:
    """Parse an LLM structured-output response into a dict, defensively.

    The GCP adapter returns the structured JSON as ``LlmResponse.text`` when a
    ``response_schema`` is set. We ``json.loads`` it; on any failure (plain text,
    truncation, a fenced block) we fall back to extracting the first balanced JSON
    object, and finally to an empty dict so callers degrade gracefully rather than
    raising on a malformed model reply.
    """
    text = (response.text or "").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {"items": parsed}
    except (json.JSONDecodeError, ValueError):
        pass

    snippet = _extract_json_object(text)
    if snippet is not None:
        try:
            parsed = json.loads(snippet)
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, ValueError):
            pass
    return {}


def _extract_json_object(text: str) -> str | None:
    """Return the first balanced ``{...}`` block in ``text``, or None."""
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def as_str_list(value: Any) -> list[str]:
    """Coerce an arbitrary model value into a list of stripped non-empty strings."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    return []


# --------------------------------------------------------------------------- #
# Id -> object resolution + coverage computation
# --------------------------------------------------------------------------- #
def controls_for_ids(
    control_ids: list[str],
    controls: list[GcpControl],
) -> tuple[GcpControl, ...]:
    """Map model-returned ``control_ids`` back to real ``GcpControl`` objects.

    Unknown ids the model may have hallucinated are dropped — we only ever map to
    controls the toolkit actually knows about. Order follows the model's list,
    de-duplicated.
    """
    by_id = {c.id: c for c in controls}
    out: list[GcpControl] = []
    seen: set[str] = set()
    for cid in control_ids:
        ctrl = by_id.get(cid)
        if ctrl is not None and ctrl.id not in seen:
            seen.add(ctrl.id)
            out.append(ctrl)
    return tuple(out)


def observations_for_controls(
    controls: tuple[GcpControl, ...],
    observations: list[ControlObservation],
) -> tuple[ControlObservation, ...]:
    """Return the observations that pertain to ``controls`` (by control id)."""
    wanted = {c.id for c in controls}
    return tuple(o for o in observations if o.control_id in wanted)


def compute_coverage(
    controls: tuple[GcpControl, ...],
    observations: tuple[ControlObservation, ...],
) -> Coverage:
    """Compute a coverage verdict from which mapped controls are ENABLED.

    A control counts as satisfying only when at least one of its observations is in
    a ``SATISFYING_STATES`` state (ENABLED). FULL when every mapped control is
    satisfied, PARTIAL when some are, NONE when none are (or no controls mapped).
    This is the authoritative server-side coverage; the model's coverage hint is
    only used as a tie-breaker when no observations exist for a control.
    """
    if not controls:
        return Coverage.NONE
    by_control: dict[str, list[ControlObservation]] = {}
    for o in observations:
        by_control.setdefault(o.control_id, []).append(o)

    satisfied = 0
    for ctrl in controls:
        obs = by_control.get(ctrl.id, [])
        if any(o.state in SATISFYING_STATES for o in obs):
            satisfied += 1

    if satisfied == len(controls):
        return Coverage.FULL
    if satisfied == 0:
        return Coverage.NONE
    return Coverage.PARTIAL


def missing_control_families(
    controls: tuple[GcpControl, ...],
    observations: tuple[ControlObservation, ...],
) -> tuple[ControlFamily, ...]:
    """Return the control families that are not satisfied (for a ControlGap)."""
    by_control: dict[str, list[ControlObservation]] = {}
    for o in observations:
        by_control.setdefault(o.control_id, []).append(o)

    families: list[ControlFamily] = []
    for ctrl in controls:
        obs = by_control.get(ctrl.id, [])
        if not any(o.state in SATISFYING_STATES for o in obs) and ctrl.family not in families:
            families.append(ctrl.family)
    return tuple(families)


# --------------------------------------------------------------------------- #
# LlmRequest assembly + tracer/usage plumbing
# --------------------------------------------------------------------------- #
def build_llm_request(
    system_instruction: str,
    user_content: str,
    model: str | None,
    response_schema: dict | None,
    thinking: ThinkingLevel = ThinkingLevel.HIGH,
    temperature: float = 0.2,
    max_output_tokens: int = 4096,
) -> LlmRequest:
    """Assemble an ``LlmRequest`` with a single user message and a system prompt.

    ``model=None`` lets the adapter pick its configured default (the reasoning model,
    ``gemini-3.5-flash``); thinking defaults to HIGH for control reasoning per SPEC.
    """
    return LlmRequest(
        messages=(LlmMessage(role="user", content=user_content),),
        system_instruction=system_instruction,
        model=model,
        thinking=thinking,
        temperature=temperature,
        max_output_tokens=max_output_tokens,
        response_schema=response_schema,
    )


def maybe_record_usage(tracer: Any, response: Any) -> None:
    """Emit token usage to the tracer for FinOps, defensively (never fatal)."""
    try:
        usage = getattr(response, "usage", None)
        model = getattr(response, "model", "") or ""
        if usage is not None and hasattr(tracer, "record_token_usage"):
            tracer.record_token_usage(usage, model)
    except Exception:  # noqa: BLE001 - metrics must never break a generation path
        return
