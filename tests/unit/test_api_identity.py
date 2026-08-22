"""API-boundary tests for server-resolved identity (the embeddable-secure-ui seam).

The FastAPI routes resolve the audit actor from the verified :class:`Principal` (local
profile: seeded dev personas selected by ``X-Dev-Persona``), never from the request
body. These tests drive the real app over the real ``local`` adapters and prove:

* an unknown ``X-Dev-Persona`` is rejected with 401,
* a legacy client-supplied body ``actor`` is ignored: the audit trail records the
  verified default persona, not the spoofed value,
* the persona header selects the audit actor,
* ``/personas`` lists the seeded personas in the local profile, and
* the embedding-surface security headers (CSP frame-ancestors) are emitted.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from tests.conftest import LOOPBACK_PEER

from compliance_advisory.api import deps
from compliance_advisory.api.app import app

_DEFAULT_SUBJECT = "demo.analyst@bank.example"
_QUESTION = "What cloud outsourcing controls does MAS expect before onboarding a provider?"


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


def test_unknown_dev_persona_is_401(client: TestClient) -> None:
    response = client.post(
        "/ask",
        json={"question": _QUESTION},
        headers={"X-Dev-Persona": "does-not-exist"},
    )
    assert response.status_code == 401


def test_body_actor_is_ignored_and_default_persona_is_audited(client: TestClient) -> None:
    # A legacy client still sending ``actor`` gets a normal answer, but the audit
    # trail records the server-verified principal, not the client-asserted value.
    response = client.post(
        "/ask",
        json={"question": _QUESTION, "actor": "spoofed.identity@attacker.example"},
    )
    assert response.status_code == 200
    actors = {event["actor"] for event in deps.get_container().audit.read_all()}
    assert "spoofed.identity@attacker.example" not in actors
    assert _DEFAULT_SUBJECT in actors


def test_persona_header_selects_the_audit_actor(client: TestClient) -> None:
    response = client.post(
        "/ask",
        json={"question": _QUESTION},
        headers={"X-Dev-Persona": "auditor"},
    )
    assert response.status_code == 200
    actors = {event["actor"] for event in deps.get_container().audit.read_all()}
    assert "demo.auditor@bank.example" in actors


def test_personas_endpoint_lists_seeded_personas(client: TestClient) -> None:
    response = client.get("/personas")
    assert response.status_code == 200
    ids = {p["id"] for p in response.json()}
    assert {"analyst", "approver", "auditor", "other-tenant"} <= ids


def test_healthz_reports_profile_for_the_picker_gate(client: TestClient) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["profile"] == "local"


def test_embedding_security_headers_present(client: TestClient) -> None:
    response = client.get("/healthz")
    assert response.headers["Content-Security-Policy"] == "frame-ancestors 'self'"
    assert response.headers["X-Frame-Options"] == "SAMEORIGIN"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert "Strict-Transport-Security" not in response.headers


@pytest.mark.parametrize("profile", ["gcp", "platform"])
def test_secure_profile_adds_hsts(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, profile: str
) -> None:
    from types import SimpleNamespace

    monkeypatch.setattr(
        deps,
        "get_settings",
        lambda: SimpleNamespace(
            exposure_profile=profile,
            profile=profile,
            region="asia-southeast1",
        ),
    )
    response = client.get("/healthz")
    assert response.headers["Strict-Transport-Security"] == ("max-age=31536000; includeSubDomains")


def test_unconsented_profile_does_not_claim_hsts(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    monkeypatch.setattr(
        deps,
        "get_settings",
        lambda: SimpleNamespace(
            exposure_profile="unconfigured",
            profile="local",
            profile_explicit=False,
            region="asia-southeast1",
        ),
    )
    response = client.get("/healthz")
    assert "Strict-Transport-Security" not in response.headers


def test_live_plain_http_profile_does_not_claim_hsts(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    monkeypatch.setattr(
        deps,
        "get_settings",
        lambda: SimpleNamespace(exposure_profile="live", profile="live", region="asia-southeast1"),
    )
    response = client.get("/healthz")
    assert "Strict-Transport-Security" not in response.headers


def test_empty_retrieval_is_a_422_refusal(client: TestClient) -> None:
    from compliance_advisory.domain.errors import RetrievalEmptyError

    class EmptyService:
        def answer(self, *args: object, **kwargs: object) -> object:
            raise RetrievalEmptyError("no grounding")

    from compliance_advisory.api.app import app

    app.dependency_overrides[deps.get_qa_service] = EmptyService
    try:
        response = client.post("/ask", json={"question": _QUESTION})
    finally:
        app.dependency_overrides.pop(deps.get_qa_service, None)
    assert response.status_code == 422
    assert response.json() == {
        "error": "ungrounded",
        "detail": "no grounding",
        "requires_human_review": True,
        "citations": [],
    }
