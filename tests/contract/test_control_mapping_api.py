"""Contract test: the merged app still exposes the C2 control-mapping surface unchanged.

The C2->C1 merge mounts ``/map``, ``/evidence-pack`` and ``/gaps`` on the assistant's
FastAPI app without colliding with its own surface (``/ask``, ``/checklist``, ...). An
external consumer — Rsk3, the architecture validator — POSTs ``/evidence-pack`` and
depends on that shape, so this pins the routes' existence and their response contracts,
driving the real app over the real ``local`` adapters (no Google Cloud SDK).

Also asserts the domain invariant the pack shape rests on: an evidence pack ALWAYS
requires human review (SPEC §5 / P-06 maker-checker).
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from compliance_advisory.api import deps
from compliance_advisory.api.app import app
from tests.conftest import LOOPBACK_PEER

SCOPE = "projects/acme-sg-prod"


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """A TestClient over the real app: local profile, ephemeral in-memory stores."""
    monkeypatch.setenv("COMPLIANCE_PROFILE", "local")
    monkeypatch.setenv("COMPLIANCE_LOCAL_DB", ":memory:")
    monkeypatch.setenv("COMPLIANCE_LOCAL_AUDIT", ":memory:")
    monkeypatch.setenv("COMPLIANCE_LOCAL_LEDGER", ":memory:")
    deps.get_container.cache_clear()
    try:
        with TestClient(app, client=LOOPBACK_PEER) as test_client:
            yield test_client
    finally:
        deps.get_container.cache_clear()


def test_control_mapping_routes_are_mounted() -> None:
    """The three C2 routes are present on the merged app's OpenAPI surface."""
    paths = app.openapi()["paths"]
    for route in ("/map", "/evidence-pack", "/gaps"):
        assert route in paths, f"merged app dropped the control-mapping route {route}"
        assert "post" in paths[route], f"{route} must be a POST"


def test_map_returns_c2_mapping_shape(client: TestClient) -> None:
    response = client.post("/map", json={"scope": SCOPE})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["scope"] == SCOPE
    assert isinstance(body["mappings"], list) and body["mappings"], "expected mappings offline"
    mapping = body["mappings"][0]
    # The C2 mapping projection: requirement + controls + coverage + citations + review flag.
    assert set(mapping) >= {
        "requirement",
        "controls",
        "observations",
        "coverage",
        "rationale",
        "citations",
        "requires_human_review",
    }
    assert mapping["coverage"] in {"full", "partial", "none"}
    assert set(mapping["requirement"]) >= {"id", "regulator", "jurisdiction", "title", "citation"}
    assert mapping["requirement"]["citation"]["page"] is not None, "page-level citation required"


def test_evidence_pack_returns_c2_shape_and_is_always_review(client: TestClient) -> None:
    response = client.post("/evidence-pack", json={"scope": SCOPE})
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) >= {
        "scope",
        "mappings",
        "gaps",
        "coverage_summary",
        "generated_at",
        "requires_human_review",
    }
    assert body["scope"] == SCOPE
    assert body["requires_human_review"] is True, "an evidence pack is always human-reviewed (R8)"
    assert isinstance(body["coverage_summary"], dict)
    assert sum(body["coverage_summary"].values()) == len(body["mappings"])


#: The five fields Rsk3, the architecture validator, reads off every gap. Named once so the
#: route test and the serialization test below cannot pin different sets.
_GAP_FIELDS = frozenset({"requirement", "missing_controls", "severity", "remediation", "citations"})


def test_gaps_returns_c2_shape(client: TestClient) -> None:
    """The route answers, and any gap it returns carries the consumer's shape.

    The loop here CANNOT be the evidence for that shape, and used to be. The offline
    corpus has full control coverage, so ``/gaps`` returns an empty list for every scope
    and the loop body ran zero times -- the second re-audit pass proved it by deleting
    ``missing_controls``, ``severity``, ``remediation`` and ``citations`` from
    ``ControlGapModel.from_domain`` and watching the entire suite stay green.

    So this test asserts what a live route can honestly assert, and the shape itself is
    pinned by ``test_a_gap_serializes_with_every_field_its_consumer_reads`` below, where it
    cannot depend on whether the fixture data happens to produce a gap.
    """
    response = client.post("/gaps", json={"scope": SCOPE})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["scope"] == SCOPE
    assert isinstance(body["gaps"], list)
    for gap in body["gaps"]:
        assert set(gap) >= _GAP_FIELDS


def test_a_gap_serializes_with_every_field_its_consumer_reads() -> None:
    """The C2 gap contract, asserted on a gap that is guaranteed to exist.

    Constructed rather than retrieved, precisely because no scope in the offline corpus
    produces one. A contract that can only be checked when the data happens to cooperate is
    a contract nothing checks.
    """
    from compliance_advisory.api.control_mapping_schemas import ControlGapModel
    from compliance_advisory.domain.control_mapping import models as m

    citation = m.Citation(
        source_id="apra-cps-234",
        regulator=m.Regulator.APRA,
        jurisdiction=m.Jurisdiction.AU,
        title="APRA CPS 234 Information Security",
        url="https://example.test/apra-cps-234",
        version="2019-07-01",
    )
    gap = m.ControlGap(
        requirement=m.RegRequirement(
            id="apra-cps-234-15",
            regulator=m.Regulator.APRA,
            jurisdiction=m.Jurisdiction.AU,
            title="APRA CPS 234 Information Security",
            text="An APRA-regulated entity must protect its information assets.",
            citation=citation,
        ),
        missing_controls=(m.ControlFamily.CMEK,),
        severity=m.Severity.HIGH,
        remediation="Bind the missing control family and re-run the mapping.",
        citations=(citation,),
    )

    serialized = ControlGapModel.from_domain(gap).model_dump()

    assert set(serialized) >= _GAP_FIELDS
    # Each field carries the domain value through, not merely a key with a default. A
    # from_domain that dropped a field would still emit the key with its default and satisfy
    # a set comparison alone.
    assert serialized["severity"] == gap.severity.value
    assert serialized["remediation"] == gap.remediation
    assert serialized["missing_controls"] == [f.value for f in gap.missing_controls]
    assert serialized["requirement"]["id"] == gap.requirement.id


def test_evidence_pack_domain_invariant_always_requires_review(evidence_service) -> None:
    """The pack shape rests on a domain invariant: an evidence pack always requires review."""
    pack = evidence_service.build(SCOPE, actor="grc@bank.test")
    assert pack.requires_human_review is True


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
