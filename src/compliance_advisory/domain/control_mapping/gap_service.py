"""GapAnalysisService — derive control gaps for a scope (SPEC §5).

Builds on the mapping pipeline: it maps requirements to controls, keeps the
PARTIAL / NONE coverage mappings (those whose controls are missing or
misconfigured), and turns each into a ``ControlGap`` with a severity, remediation
guidance, and the requirement's citations. Gaps are consequential output and run
through the maker-checker review policy (P-06).

Pure domain code — no Google Cloud / ADK imports.
"""

from __future__ import annotations

from contextlib import nullcontext
from typing import Any

from . import _mapping as m
from .mapping_service import ControlMappingService
from .models import (
    AuditEvent,
    ControlFamily,
    ControlGap,
    ControlMapping,
    Coverage,
    Decision,
    Severity,
)
from .prompts import _MAPPING_RULES, GAP_SYSTEM, GAP_USER
from .review_policy import MappingReviewPolicy

_GAP_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "requirement_id": {"type": "string"},
                    "severity": {
                        "type": "string",
                        "enum": ["low", "medium", "high", "critical"],
                    },
                    "remediation": {"type": "string"},
                },
                "required": ["requirement_id", "severity", "remediation"],
            },
        }
    },
    "required": ["items"],
}

#: Severity floor by coverage when the model gives no usable severity: an entirely
#: uncovered (NONE) requirement is at least HIGH; a PARTIAL one at least MEDIUM.
_DEFAULT_SEVERITY: dict[Coverage, Severity] = {
    Coverage.NONE: Severity.HIGH,
    Coverage.PARTIAL: Severity.MEDIUM,
}


class GapAnalysisService:
    """Analyse control gaps for a scope. Signature fixed by SPEC §5."""

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
        # Reuse the mapping service for the fetch -> observe -> map skeleton.
        self._mapper = ControlMappingService(
            requirement_source,
            control_inventory,
            llm,
            tracer,
            audit,
            review_policy=self._review,
        )

    def analyze(self, scope: str, actor: str, regulator: str | None = None) -> list[ControlGap]:
        """Return the control gaps for ``scope`` (consequential, human-reviewed)."""
        span = self._tracer.span("control_mapping.gaps", action="gaps", actor=actor)
        with span if span is not None else nullcontext():
            return self._analyze_inner(scope, actor, regulator)

    def _analyze_inner(self, scope: str, actor: str, regulator: str | None) -> list[ControlGap]:
        mappings = self._mapper.map(scope, actor, regulator)
        uncovered = [mp for mp in mappings if mp.coverage in (Coverage.PARTIAL, Coverage.NONE)]
        gaps = self.gaps_from_mappings(scope, uncovered)
        self._audit_gaps(actor, scope, gaps)
        return gaps

    def gaps_from_mappings(self, scope: str, uncovered: list[ControlMapping]) -> list[ControlGap]:
        """Turn PARTIAL/NONE-coverage mappings into ``ControlGap`` objects.

        Shared with :class:`EvidencePackService` so a pack and a standalone gap
        analysis derive gaps identically. The LLM supplies severity + remediation;
        the missing control families are computed from the observations.
        """
        if not uncovered:
            return []
        parsed = self._generate(scope, uncovered)
        by_req = {item.get("requirement_id"): item for item in self._items(parsed)}

        gaps: list[ControlGap] = []
        for mp in uncovered:
            raw = by_req.get(mp.requirement.id, {})
            severity = self._severity_for(raw, mp.coverage)
            remediation = str(raw.get("remediation") or "").strip() or self._fallback_remediation(
                mp
            )
            gaps.append(
                ControlGap(
                    requirement=mp.requirement,
                    missing_controls=self._missing_families(mp),
                    severity=severity,
                    remediation=remediation,
                    citations=mp.citations,
                )
            )
        return gaps

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    def _generate(self, scope: str, uncovered: list[ControlMapping]) -> dict[str, Any]:
        gaps_block = "\n".join(
            f"[{mp.requirement.id}] coverage={mp.coverage.value} "
            f"missing_families={[f.value for f in self._missing_families(mp)]}"
            for mp in uncovered
        )
        observations = [o for mp in uncovered for o in mp.observations]
        system = GAP_SYSTEM.format(mapping_rules=_MAPPING_RULES)
        user = GAP_USER.format(
            scope=scope,
            gaps=gaps_block or "(none)",
            observations=m.render_observations(observations),
        )
        request = m.build_llm_request(
            system_instruction=system,
            user_content=user,
            model=None,
            response_schema=_GAP_SCHEMA,
        )
        response = self._llm.generate(request)
        m.maybe_record_usage(self._tracer, response)
        return m.parse_structured(response)

    @staticmethod
    def _items(parsed: dict[str, Any]) -> list[dict[str, Any]]:
        raw = parsed.get("items")
        return [r for r in raw if isinstance(r, dict)] if isinstance(raw, list) else []

    @staticmethod
    def _missing_families(mp: ControlMapping) -> tuple[ControlFamily, ...]:
        return m.missing_control_families(mp.controls, mp.observations)

    @staticmethod
    def _severity_for(raw: dict[str, Any], coverage: Coverage) -> Severity:
        if raw.get("severity"):
            return m.coerce_severity(raw.get("severity"))
        return _DEFAULT_SEVERITY.get(coverage, Severity.MEDIUM)

    @staticmethod
    def _fallback_remediation(mp: ControlMapping) -> str:
        families = ", ".join(f.value for f in GapAnalysisService._missing_families(mp)) or "the"
        return (
            f"Enable {families} control(s) for requirement {mp.requirement.id} and "
            "re-observe the posture before relying on this evidence."
        )

    # ------------------------------------------------------------------ #
    # Audit
    # ------------------------------------------------------------------ #
    def _audit_gaps(self, actor: str, scope: str, gaps: list[ControlGap]) -> None:
        summary = "; ".join(f"{gp.requirement.id}={gp.severity.value}" for gp in gaps)
        citations = tuple(c for gp in gaps for c in gp.citations)
        event = AuditEvent(
            action="gaps",
            actor=actor,
            decision=Decision.ESCALATED,  # consequential -> human checker (P-06)
            redacted_prompt=f"scope={scope}",
            redacted_response=summary,
            citations=citations,
            metadata={"n_gaps": str(len(gaps))},
        )
        try:
            self._audit.record(event)
        except Exception:  # noqa: BLE001 - audit must not crash the request
            return
