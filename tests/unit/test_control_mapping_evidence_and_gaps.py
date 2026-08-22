"""Unit tests for EvidencePackService and GapAnalysisService (merged from C2).

SPEC §5: an EvidencePack is the regulator deliverable, so it always requires human
review and carries a coverage summary + the derived gaps. A ControlGap is derived for
every PARTIAL/NONE-coverage mapping, with a severity, remediation and the requirement's
citations. Driven by the real ``local`` adapters + a seeded requirement-source double
(see ``tests/conftest.py``); no Google Cloud SDK.
"""

from __future__ import annotations

import pytest
from tests.fixtures import sample_controls

from compliance_advisory.domain.control_mapping.errors import RequirementsEmptyError
from compliance_advisory.domain.control_mapping.evidence_service import EvidencePackService
from compliance_advisory.domain.control_mapping.models import (
    ControlGap,
    Coverage,
    Decision,
    EvidencePack,
    Severity,
)

ACTOR = "grc@bank.test"
SCOPE = sample_controls.SAMPLE_SCOPE


# --------------------------------------------------------------------------- #
# EvidencePackService
# --------------------------------------------------------------------------- #
def test_evidence_pack_is_well_formed_and_requires_review(evidence_service):
    pack = evidence_service.build(SCOPE, actor=ACTOR)

    assert isinstance(pack, EvidencePack)
    assert pack.scope == SCOPE
    assert pack.requires_human_review is True, "an evidence pack is always human-reviewed"
    assert pack.mappings, "the pack must carry the control mappings"
    assert pack.gaps, "this scope has uncovered requirements -> gaps"


def test_coverage_summary_counts_match_mappings(evidence_service):
    pack = evidence_service.build(SCOPE, actor=ACTOR)

    # Zero-filled over every Coverage value.
    assert set(pack.coverage_summary) == {c.value for c in Coverage}
    assert sum(pack.coverage_summary.values()) == len(pack.mappings)
    # One FULL (MAS residency), two NONE (APRA + HKMA) per the fixture posture.
    assert pack.coverage_summary[Coverage.FULL.value] == 1
    assert pack.coverage_summary[Coverage.NONE.value] == 2


def test_pack_gaps_cover_the_uncovered_requirements(evidence_service):
    pack = evidence_service.build(SCOPE, actor=ACTOR)
    gap_reqs = {g.requirement.id for g in pack.gaps}
    assert gap_reqs == {"apra-encryption-at-rest", "hkma-audit-trail"}


def test_evidence_pack_audited_as_escalated(evidence_service, audit):
    evidence_service.build(SCOPE, actor=ACTOR)
    assert any(
        e.action == "evidence_pack" and e.decision is Decision.ESCALATED for e in audit.events
    )


def test_evidence_pack_wrapped_in_span(evidence_service, tracer):
    evidence_service.build(SCOPE, actor=ACTOR)
    assert "control_mapping.evidence_pack" in tracer.spans


def test_evidence_pack_empty_requirements_raises(
    empty_requirement_source, control_inventory, llm, tracer, audit
):
    service = EvidencePackService(empty_requirement_source, control_inventory, llm, tracer, audit)
    with pytest.raises(RequirementsEmptyError):
        service.build(SCOPE, actor=ACTOR)


# --------------------------------------------------------------------------- #
# GapAnalysisService
# --------------------------------------------------------------------------- #
def test_gaps_are_well_formed_and_cited(gap_service):
    gaps = gap_service.analyze(SCOPE, actor=ACTOR)

    assert isinstance(gaps, list)
    assert gaps, "expected gaps for the uncovered requirements"
    for gap in gaps:
        assert isinstance(gap, ControlGap)
        assert gap.remediation, "a gap needs remediation guidance"
        assert isinstance(gap.severity, Severity)
        assert gap.missing_controls, "a gap names the missing control families"
        assert gap.citations, "a gap cites its requirement"
        for c in gap.citations:
            assert c.page is not None, "page-level citation is required"


def test_gaps_only_for_uncovered_requirements(gap_service):
    gaps = gap_service.analyze(SCOPE, actor=ACTOR)
    assert {g.requirement.id for g in gaps} == {"apra-encryption-at-rest", "hkma-audit-trail"}


def test_gap_missing_families_reflect_posture(gap_service):
    by_req = {g.requirement.id: g for g in gap_service.analyze(SCOPE, actor=ACTOR)}

    # APRA encryption gap: the CMEK family is missing (observed MISCONFIGURED).
    apra_families = {f.value for f in by_req["apra-encryption-at-rest"].missing_controls}
    assert "cmek" in apra_families
    # HKMA audit gap: the LOGGING family is missing (observed DISABLED).
    hkma_families = {f.value for f in by_req["hkma-audit-trail"].missing_controls}
    assert "logging" in hkma_families


def test_gaps_audited_as_escalated(gap_service, audit):
    gap_service.analyze(SCOPE, actor=ACTOR)
    assert any(e.action == "gaps" and e.decision is Decision.ESCALATED for e in audit.events)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
