"""Shared grounded retrieve-generate-cite routine (private to the domain layer).

All four artifact services (Q&A, checklist, test cases, regulator questions) share
the same skeleton: redact the request, screen it, retrieve passages from Agent
Search, render them into the prompt context, call the LLM with a structured-output
schema, defensively parse the JSON, and map the model's ``used_source_ids`` back to
the retrieved passages' ``Citation`` objects (preserving page provenance).

This module factors out that machinery so each service keeps the exact constructor
and method signature mandated by SPEC §5 while sharing one well-tested core. It is
``_``-prefixed and not part of the public domain API.

Pure domain code — talks only to ports and models, no Google Cloud / ADK imports.
"""

from __future__ import annotations

import json
from typing import Any

from .models import (
    Citation,
    Direction,
    LlmMessage,
    LlmRequest,
    LlmResponse,
    RetrievalQuery,
    RetrievedPassage,
    Severity,
    ThinkingLevel,
)
from .prompts import PASSAGE_BLOCK

#: Severity rank for picking the "highest" severity across cited topics.
_SEVERITY_RANK: dict[Severity, int] = {
    Severity.LOW: 0,
    Severity.MEDIUM: 1,
    Severity.HIGH: 2,
    Severity.CRITICAL: 3,
}

_SEVERITY_BY_VALUE: dict[str, Severity] = {s.value: s for s in Severity}


def coerce_severity(value: Any, default: Severity = Severity.MEDIUM) -> Severity:
    """Map a model-emitted severity string to the ``Severity`` enum defensively.

    **This severity is DECLARED QUALITY: the model produces it, and it is reviewer-facing
    rather than consequential.** Recorded here because the organization front page claims the
    model "never produces the number", and a reader who found this line deserves to know
    exactly how far that claim reaches in this repository.

    Where it goes: onto a checklist gap, which a reviewer reads. `checklist` is listed in
    `always_review_artifacts`, so `GateReviewPolicy.requires_review` returns True for it before
    severity is consulted at all. Raising or lowering this label cannot remove a checker, and
    cannot add one either, because the checker is already unconditional.

    What actually gates review is a different severity: `qa_service._severity_for` derives it
    from citation metadata by topic keyword, deterministically, and that is the one
    `requires_review` reads for an answer. The two were easy to confuse from the outside, which
    is why the distinction is written down here rather than left to be re-derived.

    Two models reading one page of regulation will still label it differently, so this is not
    something a paired comparison could hold to agreement. `temperature` is pinned at 0.0 so the
    label is at least reproducible for one model; see
    `tests/unit/test_grounded_requests_do_not_sample.py`. If this value is ever wired into a
    decision, it stops being declared quality and belongs in the deterministic domain instead.
    """
    if isinstance(value, Severity):
        return value
    if isinstance(value, str):
        return _SEVERITY_BY_VALUE.get(value.strip().lower(), default)
    return default


def highest_severity(severities: list[Severity]) -> Severity | None:
    """Return the most severe entry, or None for an empty list."""
    if not severities:
        return None
    return max(severities, key=lambda s: _SEVERITY_RANK[s])


def render_passages(passages: list[RetrievedPassage]) -> str:
    """Render retrieved passages into the numbered context block for the prompt.

    Each block is keyed by ``source_id`` and page so the model can echo
    ``[source_id p.N]`` citations exactly. Page is rendered as ``?`` when unknown so
    the model emits ``[source_id]`` rather than inventing a page.
    """
    if not passages:
        return "(no passages were retrieved)"
    blocks: list[str] = []
    for p in passages:
        c = p.citation
        page = str(c.page) if c.page is not None else "?"
        blocks.append(
            PASSAGE_BLOCK.format(
                source_id=c.source_id,
                page=page,
                regulator=c.regulator.value,
                jurisdiction=c.jurisdiction.value,
                title=c.title,
                version=c.version,
                text=p.text.strip(),
            )
        )
    return "\n".join(blocks)


def retrieve_passages(
    retrieval: Any,
    query_text: str,
    filters: dict[str, str] | None = None,
    top_k: int = 10,
) -> list[RetrievedPassage]:
    """Run a retrieval query through the RetrievalPort defensively."""
    query = RetrievalQuery(text=query_text, top_k=top_k, filters=dict(filters or {}))
    passages = retrieval.retrieve(query)
    return list(passages or [])


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


def citations_for_source_ids(
    used_source_ids: list[str],
    passages: list[RetrievedPassage],
) -> tuple[Citation, ...]:
    """Map model-returned ``used_source_ids`` back to retrieved passage Citations.

    Preserves the page-level provenance from retrieval (the model only returns ids,
    never pages). When a source_id was cited by multiple passages, each distinct
    (source_id, page) citation is kept once, in retrieval order. Unknown ids the
    model may have hallucinated are dropped — we only ever cite what we retrieved.
    """
    by_id: dict[str, list[Citation]] = {}
    for p in passages:
        by_id.setdefault(p.citation.source_id, []).append(p.citation)

    wanted = list(used_source_ids or [])
    # If the model returned nothing usable, fall back to all retrieved citations
    # so an answer is never left provenance-less.
    selected_ids = [sid for sid in wanted if sid in by_id]
    if not selected_ids:
        selected_ids = list(by_id.keys())

    out: list[Citation] = []
    seen: set[tuple[str, int | None]] = set()
    for sid in selected_ids:
        for citation in by_id.get(sid, ()):
            key = (citation.source_id, citation.page)
            if key not in seen:
                seen.add(key)
                out.append(citation)
    return tuple(out)


def build_llm_request(
    system_instruction: str,
    user_content: str,
    model: str | None,
    response_schema: dict | None,
    thinking: ThinkingLevel = ThinkingLevel.HIGH,
    temperature: float = 0.0,
    max_output_tokens: int = 4096,
) -> LlmRequest:
    """Assemble an ``LlmRequest`` with a single user message and a system prompt.

    ``model=None`` lets the adapter pick its configured default (the reasoning model,
    ``gemini-3.5-flash``); thinking defaults to HIGH for grounded reasoning per SPEC.
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


def screen_or_block(guardrail: Any, text: str, direction: Direction) -> Any:
    """Screen ``text`` through the guardrail port and return the verdict."""
    return guardrail.screen(text, direction)


def maybe_record_usage(tracer: Any, response: Any) -> None:
    """Emit token usage to the tracer for FinOps, defensively (never fatal)."""
    try:
        usage = getattr(response, "usage", None)
        model = getattr(response, "model", "") or ""
        if usage is not None and hasattr(tracer, "record_token_usage"):
            tracer.record_token_usage(usage, model)
    except Exception:  # noqa: BLE001 - metrics must never break a generation path
        return


def as_str_list(value: Any) -> list[str]:
    """Coerce an arbitrary model value into a list of stripped non-empty strings."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    return []
