"""ControlMappingService — map regulatory requirements to GCP controls (SPEC §5).

Owns the standard mapping pipeline and calls only ports. The pipeline, in order:

    tracer.span("control_mapping.map"):
      requirement_source.fetch(scope, regulator)   [empty -> RequirementsEmptyError]
      -> control_inventory.observe(scope)          [unavailable -> PostureUnavailableError]
      -> control_inventory.list_controls()
      -> llm.generate(map requirements->controls, structured JSON)
      -> assemble ControlMapping[] (compute Coverage from ENABLED observations)
      -> review policy sets requires_human_review (PARTIAL/NONE coverage)
      -> audit.record

Defensive throughout: a malformed model JSON degrades to NONE-coverage mappings (flagged
for review) rather than raising; an empty requirement set or unobservable posture is a
hard error so the toolkit never asserts coverage it cannot evidence.

Pure domain code — no Google Cloud / ADK / FastAPI imports.
"""

from __future__ import annotations

from contextlib import nullcontext
from typing import Any

from ..serialization import to_jsonable
from . import _mapping as m
from .errors import PostureUnavailableError, RequirementsEmptyError
from .models import (
    AuditEvent,
    Citation,
    ControlMapping,
    ControlObservation,
    Coverage,
    Decision,
    GcpControl,
    RegRequirement,
)
from .prompts import _MAPPING_RULES, MAP_SYSTEM, MAP_USER
from .review_policy import MappingReviewPolicy

# JSON schema for the structured mapping response.
_MAP_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "requirement_id": {"type": "string"},
                    "control_ids": {"type": "array", "items": {"type": "string"}},
                    "coverage": {
                        "type": "string",
                        "enum": ["full", "partial", "none"],
                    },
                    "rationale": {"type": "string"},
                },
                "required": ["requirement_id", "control_ids", "coverage", "rationale"],
            },
        }
    },
    "required": ["items"],
}


class ControlMappingService:
    """Map requirements to GCP controls. Constructor takes explicit ports (SPEC §5)."""

    def __init__(
        self,
        requirement_source: Any,
        control_inventory: Any,
        llm: Any,
        tracer: Any,
        audit: Any,
        review_policy: MappingReviewPolicy | None = None,
    ) -> None:
        self._requirements = requirement_source
        self._inventory = control_inventory
        self._llm = llm
        self._tracer = tracer
        self._audit = audit
        self._review = review_policy or MappingReviewPolicy()

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def map(
        self,
        scope: str,
        actor: str,
        regulator: str | None = None,
    ) -> list[ControlMapping]:
        """Map the requirements for ``scope`` to GCP controls with a coverage verdict."""
        span = self._tracer.span("control_mapping.map", action="map", actor=actor)
        with span if span is not None else nullcontext():
            return self._map_inner(scope, actor, regulator)

    # ------------------------------------------------------------------ #
    # Pipeline
    # ------------------------------------------------------------------ #
    def _map_inner(self, scope: str, actor: str, regulator: str | None) -> list[ControlMapping]:
        requirements = list(self._requirements.fetch(scope, regulator) or [])
        if not requirements:
            self._write_audit(actor, scope, "", Decision.ESCALATED)
            raise RequirementsEmptyError(f"no regulatory requirements for scope: {scope!r}")

        observations = list(self._inventory.observe(scope) or [])
        if not observations:
            self._write_audit(actor, scope, "", Decision.ESCALATED)
            raise PostureUnavailableError(f"no control posture observed for scope: {scope!r}")
        controls = list(self._inventory.list_controls() or [])

        parsed = self._generate(scope, requirements, controls, observations)
        mappings = self._build_mappings(parsed, requirements, controls, observations)
        self._audit_mappings(actor, scope, mappings)
        return mappings

    def _generate(
        self,
        scope: str,
        requirements: list[RegRequirement],
        controls: list[GcpControl],
        observations: list[ControlObservation],
    ) -> dict[str, Any]:
        system = MAP_SYSTEM.format(mapping_rules=_MAPPING_RULES)
        user = MAP_USER.format(
            scope=scope,
            requirements=m.render_requirements(requirements),
            controls=m.render_controls(controls),
            observations=m.render_observations(observations),
        )
        request = m.build_llm_request(
            system_instruction=system,
            user_content=user,
            model=None,  # adapter default => reasoning model gemini-3.5-flash
            response_schema=_MAP_SCHEMA,
        )
        response = self._llm.generate(request)
        m.maybe_record_usage(self._tracer, response)
        return m.parse_structured(response)

    def _build_mappings(
        self,
        parsed: dict[str, Any],
        requirements: list[RegRequirement],
        controls: list[GcpControl],
        observations: list[ControlObservation],
    ) -> list[ControlMapping]:
        by_req = {r.id: r for r in requirements}
        raw_items = parsed.get("items")
        rows = raw_items if isinstance(raw_items, list) else []

        out: list[ControlMapping] = []
        for raw in rows:
            if not isinstance(raw, dict):
                continue
            requirement = by_req.get(str(raw.get("requirement_id") or "").strip())
            if requirement is None:
                continue  # never map an id the toolkit did not supply
            mapped_controls = m.controls_for_ids(m.as_str_list(raw.get("control_ids")), controls)
            obs = m.observations_for_controls(mapped_controls, observations)
            # Authoritative coverage is computed from ENABLED observations, not the
            # model's hint; the model's value is only a fallback when no obs exist.
            coverage = m.compute_coverage(mapped_controls, obs)
            if not obs:
                coverage = m.coerce_coverage(raw.get("coverage"), coverage)
            out.append(
                ControlMapping(
                    requirement=requirement,
                    controls=mapped_controls,
                    observations=obs,
                    coverage=coverage,
                    rationale=str(raw.get("rationale") or "").strip(),
                    citations=(requirement.citation,),
                    requires_human_review=self._review.mapping_requires_review(coverage),
                )
            )
        return out

    # ------------------------------------------------------------------ #
    # Audit
    # ------------------------------------------------------------------ #
    def _audit_mappings(self, actor: str, scope: str, mappings: list[ControlMapping]) -> None:
        summary = "; ".join(f"{mp.requirement.id}={mp.coverage.value}" for mp in mappings)
        citations = tuple(c for mp in mappings for c in mp.citations)
        any_review = any(mp.requires_human_review for mp in mappings)
        self._write_audit(
            actor,
            scope,
            summary,
            Decision.ESCALATED if any_review else Decision.ALLOWED,
            citations=citations,
            metadata={
                "n_mappings": str(len(mappings)),
                "requires_human_review": str(any_review).lower(),
            },
        )

    def _write_audit(
        self,
        actor: str,
        scope: str,
        redacted_response: str,
        decision: Decision,
        citations: tuple[Citation, ...] = (),
        metadata: dict[str, str] | None = None,
    ) -> None:
        event = AuditEvent(
            action="map",
            actor=actor,
            decision=decision,
            redacted_prompt=f"scope={scope}",
            redacted_response=redacted_response,
            citations=citations,
            metadata=metadata or {},
        )
        try:
            self._audit.record(event)
        except Exception:  # noqa: BLE001 - audit failure must not crash the request
            to_jsonable(event)


# Map of valid Coverage values, exported for callers that summarise packs.
COVERAGE_VALUES: tuple[str, ...] = tuple(c.value for c in Coverage)
