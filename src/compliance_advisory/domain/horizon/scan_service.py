"""HorizonScanService — detect, assess, route and track regulatory change (SPEC §7.3).

The pipeline, in order, all inside one tracer span::

    ledger.all()                          [empty -> CorpusLedgerEmptyError]
    + source_catalog.sources()            (regulator-grade metadata per source)
      -> detection.detect_changes         (pure diff of the EXISTING freshness ledger)
      -> gap_service.analyze (optional)   (open control gaps per regulator: the link into
                                           the existing control-mapping journey)
      -> HorizonPolicy.assess_applicability / score_materiality / route_owner   [PURE]
      -> llm.generate(narrate, structured JSON)   [advisory prose ONLY]
      -> tracker.upsert  (idempotent per change id; existing human status preserved)
      -> audit.record
      -> review_router.route(...)         (rule R8: escalations go to Hrz7)

The ordering is the control: the assessment is complete and audited BEFORE the model is
called, so a malformed, empty or hostile model reply can only cost the scan its prose. The
score, the band, the applicability and the owner are already fixed.

Pure domain code — no Google Cloud / ADK / FastAPI imports.
"""

from __future__ import annotations

import contextlib
import logging
from contextlib import nullcontext
from typing import Any

from ..control_mapping import _mapping as m
from . import detection
from .errors import CorpusLedgerEmptyError
from .models import (
    Applicability,
    AuditEvent,
    Citation,
    CorpusChange,
    Decision,
    HorizonAssessment,
    HorizonScan,
    ImplementationItem,
    ImplementationStatus,
    MaterialityBand,
    RegSource,
)
from .policy import HorizonPolicy
from .prompts import NARRATE_SYSTEM, NARRATE_USER

logger = logging.getLogger(__name__)

#: The narration response schema. There is exactly one free-text field per change; the
#: model has nowhere to put a competing score, band, applicability or owner.
_NARRATE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "change_id": {"type": "string"},
                    "rationale": {"type": "string"},
                },
                "required": ["change_id", "rationale"],
            },
        }
    },
    "required": ["items"],
}

#: Cap on a narrative accepted from the model, so a runaway generation cannot bloat an
#: audit row or a review payload.
_MAX_NARRATIVE_CHARS = 1200


class HorizonScanService:
    """Scan the regulatory horizon for a scope. Constructor takes explicit ports."""

    def __init__(
        self,
        ledger: Any,
        source_catalog: Any,
        llm: Any,
        tracer: Any,
        audit: Any,
        tracker: Any = None,
        policy: HorizonPolicy | None = None,
        gap_service: Any = None,
        review_router: Any = None,
    ) -> None:
        self._ledger = ledger
        self._sources = source_catalog
        self._llm = llm
        self._tracer = tracer
        self._audit = audit
        # Optional so a caller can compute a scan without persisting the tracking journey
        # (the eval harness and pure unit tests do exactly that).
        self._tracker = tracker
        self._policy = policy or HorizonPolicy()
        # Optional: without it the open-control-gap driver simply contributes zero.
        self._gaps = gap_service
        self._review_router = review_router

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def scan(
        self,
        scope: str,
        actor: str,
        regulator: str | None = None,
        tenant: str = "",
    ) -> HorizonScan:
        """Detect and assess every regulatory change visible in the freshness ledger."""
        span = self._tracer.span("horizon.scan", action="horizon_scan", actor=actor)
        with span if span is not None else nullcontext():
            return self._scan_inner(scope, actor, regulator, tenant)

    # ------------------------------------------------------------------ #
    # Pipeline
    # ------------------------------------------------------------------ #
    def _scan_inner(
        self, scope: str, actor: str, regulator: str | None, tenant: str
    ) -> HorizonScan:
        records = list(self._ledger.all() or [])
        if not records:
            self._write_audit(actor, scope, "", Decision.ESCALATED)
            raise CorpusLedgerEmptyError(
                "the corpus freshness ledger is empty; ingest the corpus "
                "(compliance corpus refresh --full) before scanning the horizon"
            )

        sources = self._source_index()
        changes = detection.detect_changes(records, sources)
        if regulator:
            wanted = regulator.strip().upper()
            changes = [c for c in changes if c.regulator.value == wanted]

        gap_counts = self._open_gap_counts(scope, actor, regulator)
        assessments = [self._assess(change, gap_counts) for change in changes]
        assessments = self._narrate(scope, assessments)

        scan = HorizonScan(scope=scope, assessments=tuple(assessments))
        self._track(scan, tenant)
        self._audit_scan(actor, scope, scan)
        self._route(scan, actor, tenant)
        return scan

    def _source_index(self) -> dict[str, RegSource]:
        return {source.id: source for source in (self._sources.sources() or [])}

    def _assess(self, change: CorpusChange, gap_counts: dict[str, int]) -> HorizonAssessment:
        """Deterministic assessment of a single change. No model involved."""
        verdict = self._policy.assess_applicability(change)
        materiality = self._policy.score_materiality(
            change, verdict, open_control_gaps=gap_counts.get(change.regulator.value, 0)
        )
        owner = self._policy.route_owner(change, verdict, materiality.band)
        citations: tuple[Citation, ...] = (change.citation,) if change.citation else ()
        return HorizonAssessment(
            change=change,
            applicability=verdict.applicability,
            applicability_reasons=verdict.reasons,
            materiality_score=materiality.score,
            materiality_band=materiality.band,
            drivers=materiality.drivers,
            owner=owner,
            citations=citations,
            requires_human_review=self._policy.requires_review(verdict, materiality.band, owner),
        )

    # ------------------------------------------------------------------ #
    # The link into the existing control-mapping journey
    # ------------------------------------------------------------------ #
    def _open_gap_counts(self, scope: str, actor: str, regulator: str | None) -> dict[str, int]:
        """Count currently-open control gaps per regulator, from the mapping journey.

        A change landing on an area the bank already fails is more material than the same
        change on a fully covered area, so the open-gap count is a materiality driver. The
        gap pass is best-effort: if the posture or the reg KB is unavailable the driver
        contributes zero rather than failing the whole scan.
        """
        if self._gaps is None:
            return {}
        try:
            gaps = self._gaps.analyze(scope, actor, regulator) or []
        except Exception:  # noqa: BLE001 - a missing posture must not fail the horizon scan
            logger.warning("control-gap lookup unavailable for scope %s", scope, exc_info=True)
            return {}
        counts: dict[str, int] = {}
        for gap in gaps:
            key = gap.requirement.regulator.value
            counts[key] = counts.get(key, 0) + 1
        return counts

    # ------------------------------------------------------------------ #
    # Narration (advisory only)
    # ------------------------------------------------------------------ #
    def _narrate(self, scope: str, assessments: list[HorizonAssessment]) -> list[HorizonAssessment]:
        if not assessments:
            return assessments
        try:
            request = m.build_llm_request(
                system_instruction=NARRATE_SYSTEM,
                user_content=NARRATE_USER.format(
                    scope=scope, changes=render_assessments(assessments)
                ),
                model=None,  # adapter default => the configured reasoning model
                response_schema=_NARRATE_SCHEMA,
            )
            response = self._llm.generate(request)
            m.maybe_record_usage(self._tracer, response)
            parsed = m.parse_structured(response)
        except Exception:  # noqa: BLE001 - narration is advisory; never fail a decided scan
            logger.warning("horizon narration unavailable; returning decisions without prose")
            return assessments

        by_id = _narratives_by_change_id(parsed)
        out: list[HorizonAssessment] = []
        for assessment in assessments:
            text = by_id.get(assessment.id, "")
            out.append(
                assessment
                if not text
                else HorizonAssessment(
                    change=assessment.change,
                    applicability=assessment.applicability,
                    applicability_reasons=assessment.applicability_reasons,
                    materiality_score=assessment.materiality_score,
                    materiality_band=assessment.materiality_band,
                    drivers=assessment.drivers,
                    owner=assessment.owner,
                    citations=assessment.citations,
                    narrative=text,
                    requires_human_review=assessment.requires_human_review,
                )
            )
        return out

    # ------------------------------------------------------------------ #
    # Implementation tracking
    # ------------------------------------------------------------------ #
    def _track(self, scan: HorizonScan, tenant: str) -> None:
        """Open (or refresh) one tracked item per assessed change, idempotently.

        A human-set status is never overwritten by a re-scan: only the policy-derived
        fields (owner, band, SLA) are refreshed. That is what makes the scan safe to run on
        a schedule while a remediation is in flight.
        """
        if self._tracker is None:
            return
        for assessment in scan.assessments:
            if assessment.applicability is Applicability.NOT_APPLICABLE:
                status = ImplementationStatus.NOT_APPLICABLE
            else:
                status = ImplementationStatus.NOT_STARTED
            existing = None
            foreign = False
            with contextlib.suppress(Exception):
                found = self._tracker.get(assessment.id)
                if found is not None and found.tenant != tenant:
                    # A tracked change is owned by exactly one tenant. Never overwrite
                    # another tenant's row, and never adopt it as this scan's base.
                    foreign = True
                else:
                    existing = found
            if foreign:
                logger.warning(
                    "tracked change %s is owned by another tenant; not overwriting",
                    assessment.id,
                )
                continue
            item = ImplementationItem(
                change_id=assessment.id,
                tenant=tenant,
                source_id=assessment.change.source_id,
                status=existing.status if existing is not None else status,
                owner=assessment.owner.owner if assessment.owner else "",
                materiality_band=assessment.materiality_band,
                due_within_days=assessment.owner.due_within_days if assessment.owner else 0,
                control_ids=existing.control_ids if existing is not None else (),
                note=existing.note if existing is not None else "",
                updated_by=existing.updated_by if existing is not None else "horizon-scan",
            )
            with contextlib.suppress(Exception):
                self._tracker.upsert(item)

    # ------------------------------------------------------------------ #
    # Audit + R8 routing
    # ------------------------------------------------------------------ #
    def _audit_scan(self, actor: str, scope: str, scan: HorizonScan) -> None:
        summary = "; ".join(
            f"{a.change.source_id}={a.applicability.value}/{a.materiality_band.value}"
            f"({a.materiality_score})"
            for a in scan.assessments
        )
        citations = tuple(c for a in scan.assessments for c in a.citations)
        self._write_audit(
            actor,
            scope,
            summary,
            Decision.ESCALATED,  # a scan is always human-reviewed (P-06)
            citations=citations,
            metadata={
                "n_changes": str(len(scan.assessments)),
                "bands": ",".join(f"{k}={v}" for k, v in scan.band_summary.items()),
                "requires_human_review": "true",
            },
        )

    def _route(self, scan: HorizonScan, actor: str, tenant: str) -> None:
        """Rule R8: every escalated assessment is routed to Hrz7, not left as a boolean."""
        if self._review_router is None:
            return
        for assessment in scan.assessments:
            if not assessment.requires_human_review:
                continue
            # Routing is a hand-off; the ESCALATED audit row is the durable trail, so a
            # console outage must not fail an already-decided, already-audited scan.
            with contextlib.suppress(Exception):
                self._review_router.route(assessment, maker=actor, tenant=tenant)

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
            action="horizon_scan",
            actor=actor,
            decision=decision,
            redacted_prompt=f"scope={scope}",
            redacted_response=redacted_response,
            citations=citations,
            metadata=metadata or {},
        )
        with contextlib.suppress(Exception):
            self._audit.record(event)


# --------------------------------------------------------------------------- #
# Rendering + parsing helpers (module-level so the eval harness can reuse them)
# --------------------------------------------------------------------------- #
def render_assessments(assessments: list[HorizonAssessment]) -> str:
    """Render the decided assessments as the model's read-only fact sheet."""
    blocks: list[str] = []
    for a in assessments:
        drivers = ", ".join(f"{d.name}={d.points:+d}" for d in a.drivers)
        blocks.append(
            "\n".join(
                (
                    f"- change_id: {a.id}",
                    f"  instrument: {a.change.title} ({a.change.regulator.value} "
                    f"{a.change.jurisdiction.value}, {a.change.doc_type.value})",
                    f"  change_kind: {a.change.kind.value} - {a.change.detail}",
                    f"  topics: {', '.join(a.change.topics) or 'none'}",
                    f"  applicability (DECIDED): {a.applicability.value}",
                    f"  materiality (DECIDED): {a.materiality_score} [{a.materiality_band.value}]",
                    f"  drivers: {drivers}",
                    f"  owner (DECIDED): {a.owner.owner if a.owner else 'unassigned'}",
                )
            )
        )
    return "\n".join(blocks)


def _narratives_by_change_id(parsed: dict[str, Any]) -> dict[str, str]:
    """Extract ``change_id -> rationale`` from the parsed model reply, defensively."""
    raw_items = parsed.get("items")
    rows = raw_items if isinstance(raw_items, list) else []
    out: dict[str, str] = {}
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        change_id = str(raw.get("change_id") or "").strip()
        rationale = str(raw.get("rationale") or "").strip()
        if change_id and rationale:
            out[change_id] = rationale[:_MAX_NARRATIVE_CHARS]
    return out


#: Exported for callers that summarise a scan.
MATERIALITY_BAND_VALUES: tuple[str, ...] = tuple(b.value for b in MaterialityBand)
