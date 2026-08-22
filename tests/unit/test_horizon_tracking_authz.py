"""Object-level authorization for the horizon implementation journey (fail-closed).

The tracked implementation state of a regulatory change is per-tenant data. These tests
drive the REAL app over the real ``local`` adapters and prove the properties the catalog
audit demands of every per-tenant repo:

* the tenant comes from the VERIFIED principal, never from the request body or a query,
* a cross-tenant read or write is refused with **403**, not a 404 that hides existence,
* a genuinely unknown change is a 404, and
* a listing never leaks another tenant's rows.

``X-Dev-Persona: other-tenant`` is the seeded cross-tenant persona in the local identity
adapter, so the denial is exercised end to end rather than simulated in the domain.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from tests.conftest import LOOPBACK_PEER

from compliance_advisory.adapters.local.ledger import LocalLedgerAdapter
from compliance_advisory.api import deps
from compliance_advisory.api.app import app
from compliance_advisory.domain.horizon import carry_forward
from compliance_advisory.domain.models import FreshnessRecord, FreshnessStatus, utcnow
from compliance_advisory.pipelines.fetch import load_registry

_OWNER_TENANT_PERSONA = "analyst"  # tenant "demo-bank"
_OTHER_TENANT_PERSONA = "other-tenant"  # tenant "other-bank"


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """A TestClient over the real app with ephemeral, seeded local stores.

    ``:memory:`` SQLite is per-connection, so the ledger and tracker files are pointed at
    shared in-memory URIs: the ledger seeded here is the same one the request path reads.
    """
    monkeypatch.setenv("COMPLIANCE_PROFILE", "local")
    monkeypatch.setenv("COMPLIANCE_LOCAL_DB", ":memory:")
    monkeypatch.setenv("COMPLIANCE_LOCAL_AUDIT", ":memory:")
    # Unique shared-cache names per test: a fixed name would let one test's writes leak
    # into the next (an in-memory shared-cache DB outlives the container that opened it).
    unique = uuid.uuid4().hex
    monkeypatch.setenv(
        "COMPLIANCE_LOCAL_LEDGER", f"file:horizon-ledger-{unique}?mode=memory&cache=shared"
    )
    monkeypatch.setenv(
        "COMPLIANCE_LOCAL_HORIZON", f"file:horizon-tracker-{unique}?mode=memory&cache=shared"
    )
    deps.get_container.cache_clear()
    container = deps.get_container()
    _seed_ledger(container.ledger)
    try:
        with TestClient(app, client=LOOPBACK_PEER) as test_client:
            yield test_client
    finally:
        deps.get_container.cache_clear()


def _seed_ledger(ledger: LocalLedgerAdapter) -> None:
    """Ingest-equivalent seeding: two registry sources land, one is then republished."""
    now = utcnow()
    sources = load_registry("src/compliance_advisory/pipelines/sources/registry.yaml")[:2]
    for index, source in enumerate(sources):
        first = FreshnessRecord(
            source_id=source.id,
            url=source.url,
            version=source.version,
            fetched_at=now,
            expires_at=now,
            checksum=f"seed-{index}",
            status=FreshnessStatus.FRESH,
        )
        ledger.upsert(carry_forward(ledger.get(source.id), first))
    republished = sources[0]
    ledger.upsert(
        carry_forward(
            ledger.get(republished.id),
            FreshnessRecord(
                source_id=republished.id,
                url=republished.url,
                version=republished.version,
                fetched_at=now,
                expires_at=now,
                checksum="seed-republished",
                status=FreshnessStatus.FRESH,
            ),
        )
    )


def _scan(client: TestClient, persona: str) -> dict:
    response = client.post(
        "/horizon/scan",
        json={"scope": "projects/acme-sg-prod"},
        headers={"X-Dev-Persona": persona},
    )
    assert response.status_code == 200, response.text
    return response.json()


# --------------------------------------------------------------------------- #
# The scan surface
# --------------------------------------------------------------------------- #
def test_scan_returns_cited_assessments_that_require_review(client: TestClient) -> None:
    body = _scan(client, _OWNER_TENANT_PERSONA)

    assert body["requires_human_review"] is True
    assert body["assessments"]
    for assessment in body["assessments"]:
        assert assessment["citations"], "every assessment must cite its corpus item"
        assert assessment["drivers"], "the materiality arithmetic must be on the wire"
        assert sum(d["points"] for d in assessment["drivers"]) == (
            assessment["materiality_score"]
        ) or assessment["materiality_score"] in (0, 100)


def test_scan_on_an_empty_ledger_is_422_not_500(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COMPLIANCE_PROFILE", "local")
    monkeypatch.setenv("COMPLIANCE_LOCAL_DB", ":memory:")
    monkeypatch.setenv("COMPLIANCE_LOCAL_AUDIT", ":memory:")
    monkeypatch.setenv("COMPLIANCE_LOCAL_LEDGER", ":memory:")
    monkeypatch.setenv("COMPLIANCE_LOCAL_HORIZON", ":memory:")
    deps.get_container.cache_clear()
    try:
        with TestClient(app, client=LOOPBACK_PEER) as test_client:
            response = test_client.post("/horizon/scan", json={"scope": "empty"})
        assert response.status_code == 422
        assert "ledger is empty" in response.json()["detail"]
    finally:
        deps.get_container.cache_clear()


# --------------------------------------------------------------------------- #
# Fail-closed, server-verified object-level authorization
# --------------------------------------------------------------------------- #
def test_cross_tenant_read_is_403_not_404(client: TestClient) -> None:
    """The denial test: another tenant must be refused, and told so explicitly."""
    body = _scan(client, _OWNER_TENANT_PERSONA)
    change_id = body["assessments"][0]["id"]

    owner_view = client.get(
        f"/horizon/items/{change_id}", headers={"X-Dev-Persona": _OWNER_TENANT_PERSONA}
    )
    assert owner_view.status_code == 200
    assert owner_view.json()["tenant"] == "demo-bank"

    intruder = client.get(
        f"/horizon/items/{change_id}", headers={"X-Dev-Persona": _OTHER_TENANT_PERSONA}
    )
    assert intruder.status_code == 403
    assert "another tenant" in intruder.json()["detail"]


def test_cross_tenant_status_update_is_403(client: TestClient) -> None:
    body = _scan(client, _OWNER_TENANT_PERSONA)
    change_id = body["assessments"][0]["id"]

    denied = client.post(
        f"/horizon/items/{change_id}/status",
        json={"status": "implemented"},
        headers={"X-Dev-Persona": _OTHER_TENANT_PERSONA},
    )
    assert denied.status_code == 403

    # And the write really did not happen.
    still_open = client.get(
        f"/horizon/items/{change_id}", headers={"X-Dev-Persona": _OWNER_TENANT_PERSONA}
    )
    assert still_open.json()["status"] == "not_started"


def test_unknown_change_is_404(client: TestClient) -> None:
    response = client.get(
        "/horizon/items/does-not-exist", headers={"X-Dev-Persona": _OWNER_TENANT_PERSONA}
    )
    assert response.status_code == 404


def test_listing_never_leaks_another_tenants_rows(client: TestClient) -> None:
    _scan(client, _OWNER_TENANT_PERSONA)

    mine = client.get("/horizon/items", headers={"X-Dev-Persona": _OWNER_TENANT_PERSONA})
    theirs = client.get("/horizon/items", headers={"X-Dev-Persona": _OTHER_TENANT_PERSONA})

    assert mine.json()["items"]
    assert all(i["tenant"] == "demo-bank" for i in mine.json()["items"])
    assert theirs.json()["items"] == []


# --------------------------------------------------------------------------- #
# The implementation journey
# --------------------------------------------------------------------------- #
def test_status_update_records_the_verified_actor_and_linked_controls(
    client: TestClient,
) -> None:
    body = _scan(client, _OWNER_TENANT_PERSONA)
    change_id = body["assessments"][0]["id"]

    response = client.post(
        f"/horizon/items/{change_id}/status",
        json={
            "status": "implemented",
            "note": "closed by the CMEK rollout",
            "control_ids": ["cmek-keys", "vpc-sc-perimeter"],
        },
        headers={"X-Dev-Persona": _OWNER_TENANT_PERSONA},
    )
    assert response.status_code == 200
    item = response.json()
    assert item["status"] == "implemented"
    assert item["control_ids"] == ["cmek-keys", "vpc-sc-perimeter"]
    # The actor is the verified persona, never a client-supplied value.
    assert item["updated_by"] == "demo.analyst@bank.example"


def test_unknown_status_is_422_with_the_allowed_values(client: TestClient) -> None:
    body = _scan(client, _OWNER_TENANT_PERSONA)
    change_id = body["assessments"][0]["id"]

    response = client.post(
        f"/horizon/items/{change_id}/status",
        json={"status": "finished-ish"},
        headers={"X-Dev-Persona": _OWNER_TENANT_PERSONA},
    )
    assert response.status_code == 422
    assert "accepted_risk" in response.json()["detail"]


def test_open_only_filter_hides_closed_changes(client: TestClient) -> None:
    body = _scan(client, _OWNER_TENANT_PERSONA)
    change_id = body["assessments"][0]["id"]
    client.post(
        f"/horizon/items/{change_id}/status",
        json={"status": "implemented"},
        headers={"X-Dev-Persona": _OWNER_TENANT_PERSONA},
    )

    open_items = client.get(
        "/horizon/items?open_only=true", headers={"X-Dev-Persona": _OWNER_TENANT_PERSONA}
    ).json()["items"]
    assert change_id not in {i["change_id"] for i in open_items}
