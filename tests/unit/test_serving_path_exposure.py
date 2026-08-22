"""The loopback bound is a property of the APP OBJECT, not of ``main()``.

The defect this guards is invisible to ``tests/unit/test_netdefaults.py``, which proves
``resolve_bind_host`` refuses a non-loopback bind: that call lives in ``main()``, and the
shipped ``Dockerfile`` CMD is
``exec uvicorn compliance_advisory.api.app:app --host 0.0.0.0 --port ${PORT}``, which never
reaches it. Executed before the guard existed, against the exact object that CMD names: a peer
at 203.0.113.7 got ``200`` on ``GET /personas`` with the complete seeded-persona list,
subjects, tenants and the compliance-approver entitlement included, off an unauthenticated
``local`` deployment.

Both directions are proved here, because a guard that refuses everybody would satisfy the
first claim while breaking the offline demo it exists to protect, and would be reverted:

* an off-loopback peer is refused with ``503`` before any route or dependency runs, and
* a loopback peer still gets the seeded personas and the working local demo.

``live`` gets its own case. It binds the SAME seeded-persona adapter as ``local`` (swapping
only the generator and the corpus for real ones), so a guard keyed on the profile string would
have read it as a configured deployment and served the personas to the LAN. Reading the
identity BINDING is what catches it.

The control is the last pair: a VERIFYING identity binding stands the guard down, so
"everything refuses a LAN peer" is not true for the boring reason that the guard is always on.
"""

from __future__ import annotations

import importlib
from collections.abc import Iterator
from types import ModuleType

import pytest
from fastapi.testclient import TestClient
from tests.conftest import LOOPBACK_PEER

from compliance_advisory.api import app as app_module

_PROFILE_ENV = "COMPLIANCE_PROFILE"
_INSECURE_DEMO_ENV = "COMPLIANCE_ALLOW_INSECURE_DEMO"

#: A peer on the LAN. RFC 5737 documentation address: no real host, and obviously fictional.
LAN_PEER = ("203.0.113.7", 51234)

#: The GETs that need no request body. The unauthenticated posture must refuse ALL of them,
#: including the routes that need no identity at all: a deployment nobody can authenticate
#: against has no business answering a stranger about its corpus either.
UNAUTHENTICATED_ROUTES: tuple[str, ...] = (
    "/healthz",
    "/personas",
    "/corpus/status",
    "/.well-known/agent-card.json",
)


@pytest.fixture
def rebuilt_app(monkeypatch: pytest.MonkeyPatch) -> Iterator[ModuleType]:
    """Re-import the API module under a chosen profile, and restore it on teardown.

    The posture is decided at import (that is what makes it a boot-time property of the app
    object), so changing it means rebuilding the module rather than patching afterwards.
    """
    yield app_module
    monkeypatch.undo()
    importlib.reload(app_module.deps)
    importlib.reload(app_module)


def _under_profile(
    module: ModuleType, monkeypatch: pytest.MonkeyPatch, profile: str | None
) -> ModuleType:
    if profile is None:
        monkeypatch.delenv(_PROFILE_ENV, raising=False)
    else:
        monkeypatch.setenv(_PROFILE_ENV, profile)
    monkeypatch.delenv(_INSECURE_DEMO_ENV, raising=False)
    importlib.reload(module.deps)
    return importlib.reload(module)


@pytest.mark.parametrize("path", UNAUTHENTICATED_ROUTES)
def test_a_lan_peer_is_refused_before_any_route_runs(path: str) -> None:
    """The exact defect: ``GET /personas`` used to answer 200 with the seeded personas."""
    response = TestClient(app_module.app, client=LAN_PEER).get(path)
    assert response.status_code == 503, f"{path} answered {response.status_code}"
    detail = response.json()["detail"]
    assert "non-loopback peer" in detail
    assert "203.0.113.7" in detail


def test_the_mounted_routers_are_behind_the_guard_too() -> None:
    """The guard is outermost, so the two included routers are covered without knowing it."""
    response = TestClient(app_module.app, client=LAN_PEER).get("/horizon/items")
    assert response.status_code == 503


def test_the_refusal_names_the_documented_opt_in_so_it_is_actionable() -> None:
    response = TestClient(app_module.app, client=LAN_PEER).get("/personas")
    assert _INSECURE_DEMO_ENV in response.json()["detail"]


@pytest.mark.parametrize("path", UNAUTHENTICATED_ROUTES)
def test_a_loopback_peer_still_gets_the_offline_demo(path: str) -> None:
    """The other direction. A guard that breaks local development would simply be reverted."""
    assert TestClient(app_module.app, client=LOOPBACK_PEER).get(path).status_code == 200


def test_the_loopback_peer_still_gets_the_whole_seeded_persona_list() -> None:
    """The picker the local UI needs is intact; only the LAN is cut off from it."""
    personas = TestClient(app_module.app, client=LOOPBACK_PEER).get("/personas").json()
    assert [p["id"] for p in personas] == ["analyst", "approver", "auditor", "other-tenant"]


def test_a_forwarding_header_disqualifies_even_a_loopback_peer() -> None:
    """A proxy has already rewritten the scope peer, so the header's presence is disqualifying."""
    response = TestClient(app_module.app, client=LOOPBACK_PEER).get(
        "/personas", headers={"X-Forwarded-For": "127.0.0.1"}
    )
    assert response.status_code == 503
    assert "forwarding header" in response.json()["detail"]


def test_the_documented_opt_in_restores_service_to_a_lan_peer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An operator may accept the exposure explicitly; nothing else may accept it for them."""
    monkeypatch.setenv(_INSECURE_DEMO_ENV, "1")
    assert TestClient(app_module.app, client=LAN_PEER).get("/personas").status_code == 200


def test_the_shipped_entry_point_serves_the_app_object_not_main() -> None:
    """If this stops being true, a bound living in ``main()`` would be enough. It is not."""
    with open("Dockerfile", encoding="utf-8") as handle:
        dockerfile = handle.read()
    assert "compliance_advisory.api.app:app" in dockerfile
    assert "--host 0.0.0.0" in dockerfile


def test_an_unconsented_deployment_refuses_a_lan_peer_and_names_itself(
    rebuilt_app: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unset is not consent: a deploy whose profile variable went missing is bounded too."""
    module = _under_profile(rebuilt_app, monkeypatch, None)
    response = TestClient(module.app, client=LAN_PEER).get("/healthz")
    assert response.status_code == 503
    # The refusal names the posture, so an operator can tell an unconfigured deployment from a
    # deliberate offline demo without reading the source.
    assert "unconfigured" in response.json()["detail"]


def test_the_live_profile_is_bounded_exactly_as_local_is(
    rebuilt_app: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``live`` binds the same seeded personas, so it authenticates exactly as little."""
    module = _under_profile(rebuilt_app, monkeypatch, "live")
    response = TestClient(module.app, client=LAN_PEER).get("/personas")
    assert response.status_code == 503
    assert "'live'" in response.json()["detail"]


def test_a_verifying_identity_binding_stands_the_guard_down(
    rebuilt_app: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The control: the managed profile verifies an IAP assertion, so it may be reached.

    Without this the refusals above would be satisfied by a guard that is simply always on,
    which is not a working service and would prove nothing about the derivation.
    """
    module = _under_profile(rebuilt_app, monkeypatch, "gcp")
    assert TestClient(module.app, client=LAN_PEER).get("/healthz").status_code == 200


def test_an_unimplemented_identity_binding_is_bounded_like_any_other_no_auth_posture(
    rebuilt_app: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``onprem`` is a placeholder that resolves nobody, so it authenticates nobody."""
    module = _under_profile(rebuilt_app, monkeypatch, "onprem")
    response = TestClient(module.app, client=LAN_PEER).get("/healthz")
    assert response.status_code == 503
    assert "'onprem'" in response.json()["detail"]
