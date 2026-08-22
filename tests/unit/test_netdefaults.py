"""Fail-closed network defaults (C5).

The API bound 0.0.0.0 unconditionally and the CORS fallback trusted the dev origins in
every profile (C5 PARTIAL). Both are now wired through the shared ``hex-service-kit``
rules; these tests prove THIS repo's wiring (each was red against the pre-adoption
behaviour).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from tests.conftest import LOOPBACK_PEER

from compliance_advisory.api import app as app_module


def _origins_for_profile(
    monkeypatch: pytest.MonkeyPatch, profile: str, *, explicit: bool = True
) -> list[str]:
    import dataclasses

    monkeypatch.delenv("COMPLIANCE_CORS_ORIGINS", raising=False)
    settings = dataclasses.replace(
        app_module.deps.get_settings(), profile=profile, profile_explicit=explicit
    )
    monkeypatch.setattr(app_module.deps, "get_settings", lambda: settings)
    return app_module._cors_origins()


def test_cors_fallback_only_under_local_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _origins_for_profile(monkeypatch, "local") == [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]
    # A secure deploy that forgets the allowlist gets NO cross-origin trust (was: dev
    # origins with credentials in every profile).
    assert _origins_for_profile(monkeypatch, "gcp") == []
    assert _origins_for_profile(monkeypatch, "platform") == []


def test_cors_fallback_needs_a_deliberate_local_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    """The dev-origin fallback is a RELAXATION, so an inherited local profile does not get it.

    Before the three-state fix an absent COMPLIANCE_PROFILE was read as a chosen ``local``,
    which handed the localhost dev origins cross-origin trust in any deployment that simply
    forgot to set the variable.
    """
    assert _origins_for_profile(monkeypatch, "local", explicit=False) == []


def test_explicit_allowlist_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COMPLIANCE_CORS_ORIGINS", "https://tenant.example")
    assert app_module._cors_origins() == ["https://tenant.example"]


def test_emptied_allowlist_grants_nothing_not_the_dev_origins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Set-and-empty is a configuration, not an omission.

    An operator who empties COMPLIANCE_CORS_ORIGINS named no origin, so nothing is trusted.
    The two-state read this replaced handed that case the unset default, which under a
    local profile is the localhost dev-origin relaxation.
    """
    import dataclasses

    monkeypatch.setenv("COMPLIANCE_CORS_ORIGINS", "")
    settings = dataclasses.replace(
        app_module.deps.get_settings(), profile="local", profile_explicit=True
    )
    monkeypatch.setattr(app_module.deps, "get_settings", lambda: settings)
    assert app_module._cors_origins() == []


def test_frame_ancestors_resolves_three_states() -> None:
    """Unset keeps 'self'; an emptied allowlist refuses framing; a named parent is used.

    Red before the fix: the emptied state produced "" and the response carried the header
    ``Content-Security-Policy: frame-ancestors`` with an empty directive, which browsers
    discard as a parse error.
    """
    assert app_module._frame_ancestors(None) == "'self'"
    assert app_module._frame_ancestors("") == "'none'"
    assert app_module._frame_ancestors("   ") == "'none'"
    assert app_module._frame_ancestors("https://portal.example") == "https://portal.example"
    assert app_module._frame_ancestors(" https://a.example\n https://b.example ") == (
        "https://a.example https://b.example"
    )


@pytest.mark.parametrize(
    ("raw", "expected_csp", "expected_legacy"),
    [
        (None, "frame-ancestors 'self'", "SAMEORIGIN"),
        ("", "frame-ancestors 'none'", "DENY"),
    ],
)
def test_clickjacking_control_survives_an_emptied_allowlist(
    monkeypatch: pytest.MonkeyPatch, raw: str | None, expected_csp: str, expected_legacy: str
) -> None:
    """Every response carries a parseable CSP directive AND its X-Frame-Options backstop.

    Red before the fix for ``raw=""``: the CSP directive was empty and the ``== "'self'"``
    branch was skipped, so X-Frame-Options was absent as well and the clickjacking control
    silently disappeared from both headers at once.
    """
    monkeypatch.setattr(app_module, "_FRAME_ANCESTORS", app_module._frame_ancestors(raw))
    response = TestClient(app_module.app, client=LOOPBACK_PEER).get("/healthz")
    assert response.headers["Content-Security-Policy"] == expected_csp
    assert response.headers["X-Frame-Options"] == expected_legacy


def test_local_profile_refuses_non_loopback_bind(monkeypatch: pytest.MonkeyPatch) -> None:
    from hex_service_kit import InsecureBindError, resolve_bind_host

    monkeypatch.setenv("COMPLIANCE_API_HOST", "0.0.0.0")
    monkeypatch.delenv("COMPLIANCE_ALLOW_INSECURE_DEMO", raising=False)
    with pytest.raises(InsecureBindError):
        resolve_bind_host(
            "local",
            host_env="COMPLIANCE_API_HOST",
            insecure_demo_env="COMPLIANCE_ALLOW_INSECURE_DEMO",
        )


def test_api_still_serves(monkeypatch: pytest.MonkeyPatch) -> None:
    client = TestClient(app_module.app, client=LOOPBACK_PEER)
    response = client.get("/healthz")
    assert response.status_code == 200


@pytest.mark.parametrize("raw", ["*", " * ", "'self' *", "https://portal.example *"])
def test_a_wildcard_frame_ancestor_is_refused(raw: str) -> None:
    """``frame-ancestors *`` lets ANY page iframe the console, so it is a boot refusal.

    Red before the refusal landed: the three-state read carefully distinguished unset from
    emptied and then passed ``*`` straight through to the header, so the "never ``*``" rule
    the comment above ``_FRAME_ANCESTORS_ENV`` states existed only as prose. One operator
    typo re-opened the clickjacking surface that the emptied-state fix had just closed, and
    nothing in the suite could tell.
    """
    with pytest.raises(ValueError, match="wildcard"):
        app_module._frame_ancestors(raw)


@pytest.mark.parametrize("raw", ["*", " * ", "https://tenant.example,*"])
def test_a_wildcard_cors_origin_is_refused(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    """An allowlist naming ``*`` is not an allowlist, and credentialed CORS makes it worse.

    ``allow_credentials=True`` is set on the middleware, so a wildcard origin would hand
    every site on the internet the browser's cookies for this service. Refused where the
    value is resolved, which runs at import, so a misconfigured deployment cannot boot.
    """
    monkeypatch.setenv("COMPLIANCE_CORS_ORIGINS", raw)
    with pytest.raises(ValueError, match="wildcard"):
        app_module._cors_origins()


#: Every way an operator, a template or a YAML quirk can spell "everybody", checked against
#: both allowlists. ``ui/lib/csp.mjs`` refuses the same set on the document half.
_WILDCARD_SPELLINGS = ["*", "'*'", "null", "*.*", "https://*.example", "*.example", "https://*"]


@pytest.mark.parametrize("entry", _WILDCARD_SPELLINGS)
def test_every_wildcard_spelling_is_refused_in_either_list(
    monkeypatch: pytest.MonkeyPatch, entry: str
) -> None:
    """Measured before the fix: only the bare ``*`` was refused, and the other six were ACCEPTED.

    The check was ``origin.strip() == "*"``, an equality test, so it saw an entry that IS an
    asterisk and nothing else. Nothing downstream looked at these values either: the resolved
    string goes straight into ``Content-Security-Policy: frame-ancestors`` and into
    ``CORSMiddleware(allow_origins=...)``, so there is no origin validator here to catch a
    spelling incidentally. All six reached a response header verbatim.

    Each one is a working wildcard. ``https://*.example`` is a CSP host-source wildcard, so
    every subdomain may frame the console including one an attacker takes over. ``'*'`` is what
    a quoted Terraform variable or a YAML string renders. ``*.*`` matches every name with a dot
    in it. ``null`` is the one that reads as harmless and is not: a SANDBOXED iframe presents
    the origin ``null``, so naming it hands framing and credentialed cross-origin rights to any
    page that can open one, and it carries no asterisk for a character test to find.
    """
    with pytest.raises(ValueError, match="wildcard"):
        app_module._frame_ancestors(entry)

    monkeypatch.setenv("COMPLIANCE_CORS_ORIGINS", entry)
    with pytest.raises(ValueError, match="wildcard"):
        app_module._cors_origins()


def test_the_refusal_still_admits_a_real_tenant_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    """A refusal that also turns away valid configuration is an outage, not a control.

    A port and a hyphen are the two shapes a hand-written token set gets wrong, and both are
    ordinary in a tenant origin: a hyphenated host is the norm and a non-443 port is how a
    staging portal is reached.
    """
    tenant = "https://portal.demo-bank.example:8443 https://admin-console.demo-bank.example"
    assert app_module._frame_ancestors(tenant) == tenant

    monkeypatch.setenv(
        "COMPLIANCE_CORS_ORIGINS",
        "https://portal.demo-bank.example:8443,https://admin-console.demo-bank.example",
    )
    assert app_module._cors_origins() == [
        "https://portal.demo-bank.example:8443",
        "https://admin-console.demo-bank.example",
    ]


def test_the_refusal_leaves_the_legitimate_states_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    """The wildcard case is an ADDITION: unset, emptied and named origins are unchanged."""
    assert app_module._frame_ancestors(None) == "'self'"
    assert app_module._frame_ancestors("") == "'none'"
    assert app_module._frame_ancestors("https://portal.example") == "https://portal.example"
    monkeypatch.setenv("COMPLIANCE_CORS_ORIGINS", "https://tenant.example")
    assert app_module._cors_origins() == ["https://tenant.example"]
    monkeypatch.setenv("COMPLIANCE_CORS_ORIGINS", "")
    assert app_module._cors_origins() == []
    assert _origins_for_profile(monkeypatch, "local") == [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]
