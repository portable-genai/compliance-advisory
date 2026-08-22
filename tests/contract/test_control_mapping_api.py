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


def test_gaps_returns_c2_shape(client: TestClient) -> None:
    response = client.post("/gaps", json={"scope": SCOPE})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["scope"] == SCOPE
    assert isinstance(body["gaps"], list)
    for gap in body["gaps"]:
        assert set(gap) >= {
            "requirement",
            "missing_controls",
            "severity",
            "remediation",
            "citations",
        }


def test_evidence_pack_domain_invariant_always_requires_review(evidence_service) -> None:
    """The pack shape rests on a domain invariant: an evidence pack always requires review."""
    pack = evidence_service.build(SCOPE, actor="grc@bank.test")
    assert pack.requires_human_review is True


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
