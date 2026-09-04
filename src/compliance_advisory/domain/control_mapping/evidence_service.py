"""EvidencePackService — assemble the regulator-grade evidence pack (SPEC §5).

For a scope (a regulator or a use case), builds the deliverable handed to an
auditor/regulator: the full set of ``ControlMapping`` objects, the derived
``ControlGap`` list, and a coverage summary (counts by ``Coverage``). The pack is
always ``requires_human_review=True`` — a maker (the toolkit) proposes and a checker
(a qualified human) signs it off before it reaches a regulator (P-06).

Pure domain code — no Google Cloud / ADK imports.
"""

from __future__ import annotations

import contextlib
from contextlib import nullcontext
from typing import Any

from .gap_service import GapAnalysisService
from .mapping_service import ControlMappingService
from .models import (
    AuditEvent,
    Coverage,
    Decision,
    EvidencePack,
)
from .review_policy import MappingReviewPolicy


class EvidencePackService:
    """Build a per-scope evidence pack. Signature fixed by SPEC §5."""

    def __init__(
        self,
        requirement_source: Any,
        control_inventory: Any,
        llm: Any,
        tracer: Any,
        audit: Any,
        review_policy: MappingReviewPolicy | None = None,
        review_router: Any = None,
    ) -> None:
        self._requirements = requirement_source
        self._inventory = control_inventory
        self._llm = llm
        self._tracer = tracer
        self._audit = audit
        self._review = review_policy or MappingReviewPolicy()
        # Rule R8: an evidence pack always requires human review, so it is routed to
        # human-review-console (the
        # maker-checker console) via the shared review-kit, not left as a boolean. Optional so
        # existing callers/tests that build the service without a router still assemble the pack (it
        # just is not forwarded to a console).
        self._review_router = review_router
        self._mapper = ControlMappingService(
            requirement_source, control_inventory, llm, tracer, audit, review_policy=self._review
        )
        self._gaps = GapAnalysisService(
            requirement_source, control_inventory, llm, tracer, audit, review_policy=self._review
        )

    def build(
        self, scope: str, actor: str, regulator: str | None = None, tenant: str = ""
    ) -> EvidencePack:
        """Assemble the evidence pack for ``scope`` (always human-reviewed).

        ``actor`` is the server-verified maker (the audit subject / non-repudiation identity);
        ``tenant`` is the verified tenant partition threaded from the request (the pack itself
        carries no tenant field). Both flow to the R8 hand-off asserted to human-review-console. The
        default empty
        ``tenant`` keeps existing callers/tests unaffected.
        """
        span = self._tracer.span(
            "control_mapping.evidence_pack", action="evidence_pack", actor=actor
        )
        with span if span is not None else nullcontext():
            return self._build_inner(scope, actor, regulator, tenant)

    def _build_inner(
        self, scope: str, actor: str, regulator: str | None, tenant: str = ""
    ) -> EvidencePack:
        mappings = self._mapper.map(scope, actor, regulator)
        uncovered = [mp for mp in mappings if mp.coverage in (Coverage.PARTIAL, Coverage.NONE)]
        gaps = self._gaps.gaps_from_mappings(scope, uncovered)

        summary = self._coverage_summary(mappings)
        pack = EvidencePack(
            scope=scope,
            mappings=tuple(mappings),
            gaps=tuple(gaps),
            coverage_summary=summary,
            requires_human_review=self._review.pack_requires_review(),
        )
        self._audit_pack(actor, scope, pack)

        # Rule R8: route the escalated, already-assembled, already-audited pack to
        # human-review-console for human
        # review. Routing is a hand-off, never fatal to a pack that is already assembled and
        # audited (the audit ESCALATED record is the durable trail), so any failure is suppressed.
        if self._review_router is not None and pack.requires_human_review:
            with contextlib.suppress(Exception):
                self._review_router.route(pack, maker=actor, tenant=tenant)
        return pack

    @staticmethod
    def _coverage_summary(mappings: list[Any]) -> dict[str, int]:
        """Count mappings by coverage value (FULL / PARTIAL / NONE), zero-filled."""
        counts: dict[str, int] = {c.value: 0 for c in Coverage}
        for mp in mappings:
            counts[mp.coverage.value] = counts.get(mp.coverage.value, 0) + 1
        return counts

    # ------------------------------------------------------------------ #
    # Audit
    # ------------------------------------------------------------------ #
    def _audit_pack(self, actor: str, scope: str, pack: EvidencePack) -> None:
        citations = tuple(c for mp in pack.mappings for c in mp.citations)
        summary = ", ".join(f"{k}={v}" for k, v in pack.coverage_summary.items())
        event = AuditEvent(
            action="evidence_pack",
            actor=actor,
            decision=Decision.ESCALATED,  # the pack is always human-reviewed (P-06)
            redacted_prompt=f"scope={scope}",
            redacted_response=summary,
            citations=citations,
            metadata={
                "n_mappings": str(len(pack.mappings)),
                "n_gaps": str(len(pack.gaps)),
                "requires_human_review": str(pack.requires_human_review).lower(),
            },
        )
        try:
            self._audit.record(event)
        except Exception:  # noqa: BLE001 - audit must not crash the request
            return
