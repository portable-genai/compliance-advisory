"""Unit tests for ControlMappingService — the SPEC §5 mapping pipeline (merged from C2).

Pipeline (SPEC §5):
    requirement_source.fetch(scope) -> control_inventory.observe(scope)
      + control_inventory.list_controls()
      -> llm.generate(structured) -> assemble ControlMapping[] (compute Coverage from
         which mapped controls are ENABLED) -> review policy -> audit.record

Driven by the real ``local`` control-inventory + deterministic LLM adapters and a seeded
``RequirementSourcePort`` double (see ``tests/conftest.py``); no Google Cloud SDK.
"""

from __future__ import annotations

import pytest
from tests.fixtures import sample_controls

from compliance_advisory.domain.control_mapping.errors import (
    PostureUnavailableError,
    RequirementsEmptyError,
)
from compliance_advisory.domain.control_mapping.mapping_service import ControlMappingService
from compliance_advisory.domain.control_mapping.models import (
    ControlMapping,
    Coverage,
    Decision,
)

ACTOR = "ciso@bank.test"
SCOPE = sample_controls.SAMPLE_SCOPE


# --------------------------------------------------------------------------- #
# Happy path: mappings produced, coverage computed from ENABLED observations.
# --------------------------------------------------------------------------- #
def test_map_produces_one_mapping_per_requirement(mapping_service):
    mappings = mapping_service.map(SCOPE, actor=ACTOR)

    assert isinstance(mappings, list)
    ids = {mp.requirement.id for mp in mappings}
    assert ids == {r.id for r in sample_controls.SAMPLE_REQUIREMENTS}
    for mp in mappings:
        assert isinstance(mp, ControlMapping)


def test_coverage_computed_from_enabled_observations(mapping_service):
    by_req = {mp.requirement.id: mp for mp in mapping_service.map(SCOPE, actor=ACTOR)}

    # MAS residency: VPC-SC + org-policy + assured-workloads all ENABLED -> FULL.
    assert by_req["mas-data-residency"].coverage is Coverage.FULL
    # APRA encryption: CMEK is MISCONFIGURED (not ENABLED) -> NONE.
    assert by_req["apra-encryption-at-rest"].coverage is Coverage.NONE
    # HKMA audit trail: WORM logging DISABLED -> NONE.
    assert by_req["hkma-audit-trail"].coverage is Coverage.NONE


def test_mappings_carry_requirement_citation_with_page(mapping_service):
    mappings = mapping_service.map(SCOPE, actor=ACTOR)
    for mp in mappings:
        assert mp.citations, "every mapping must cite its requirement"
        for c in mp.citations:
            assert c.page is not None, "page-level citation is required"


def test_partial_or_none_coverage_requires_review(mapping_service):
    by_req = {mp.requirement.id: mp for mp in mapping_service.map(SCOPE, actor=ACTOR)}

    assert by_req["mas-data-residency"].requires_human_review is False  # FULL
    assert by_req["apra-encryption-at-rest"].requires_human_review is True  # NONE
    assert by_req["hkma-audit-trail"].requires_human_review is True  # NONE


def test_only_known_controls_are_mapped(mapping_service):
    mappings = mapping_service.map(SCOPE, actor=ACTOR)
    known = {c.id for c in sample_controls.SAMPLE_CONTROLS}
    for mp in mappings:
        assert {c.id for c in mp.controls} <= known, "must never map an unknown control id"


# --------------------------------------------------------------------------- #
# Observability + audit.
# --------------------------------------------------------------------------- #
def test_map_wrapped_in_tracer_span(mapping_service, tracer):
    mapping_service.map(SCOPE, actor=ACTOR)
    assert tracer.spans, "the mapping pipeline must open at least one trace span"
    assert "control_mapping.map" in tracer.spans


def test_map_writes_audit_event(mapping_service, audit):
    mapping_service.map(SCOPE, actor=ACTOR)
    assert audit.events, "mapping was not audited"
    event = audit.events[-1]
    assert event.action == "map"
    assert event.actor == ACTOR
    # Some requirements are uncovered, so the run is escalated for human review.
    assert any(e.decision is Decision.ESCALATED for e in audit.events)


def test_regulator_filter_narrows_requirements(mapping_service, requirement_source):
    mappings = mapping_service.map(SCOPE, actor=ACTOR, regulator="MAS")
    assert {mp.requirement.regulator.value for mp in mappings} == {"MAS"}
    assert requirement_source.calls[-1] == (SCOPE, "MAS")


# --------------------------------------------------------------------------- #
# Hard errors: empty requirements / unobservable posture.
# --------------------------------------------------------------------------- #
def test_empty_requirements_raises(empty_requirement_source, control_inventory, llm, tracer, audit):
    service = ControlMappingService(empty_requirement_source, control_inventory, llm, tracer, audit)
    with pytest.raises(RequirementsEmptyError):
        service.map(SCOPE, actor=ACTOR)
    assert llm.requests == [], "no generation when there are no requirements"


def test_unobservable_posture_raises(requirement_source, empty_inventory, llm, tracer, audit):
    service = ControlMappingService(requirement_source, empty_inventory, llm, tracer, audit)
    with pytest.raises(PostureUnavailableError):
        service.map(SCOPE, actor=ACTOR)
    assert llm.requests == [], "no generation when posture is unavailable"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
