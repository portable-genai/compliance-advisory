"""The outbound S2S credentials resolve in three states, not two.

`adapters/platform/_s2s.headers` delegates to `hex_service_kit.s2s.client_headers`. A
two-state read of both credentials, `os.environ.get(name, "").strip()` followed by a
truthiness test, collapses UNSET and SET-AND-EMPTY into one state. A bearer an operator
deliberately emptied inherited the unset behaviour, and the platform-profile call left with
no `Authorization` header at all, with nothing in this process refusing. These tests pin the
three states so a regression in the commons is caught here rather than in production.
"""

from __future__ import annotations

import pytest
from hex_service_kit.netdefaults import ConfiguredEmptyError

from compliance_advisory.adapters.platform import _s2s


def test_unset_credentials_are_the_offline_zero_secret_posture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(_s2s.TOKEN_ENV, raising=False)
    monkeypatch.delenv(_s2s.SIGNING_KEY_ENV, raising=False)
    assert _s2s.headers("analyst@example.com") == {}


def test_configured_credentials_sign_the_actor(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_s2s.TOKEN_ENV, "tok")
    monkeypatch.setenv(_s2s.SIGNING_KEY_ENV, "key")
    out = _s2s.headers("analyst@example.com")
    assert out["Authorization"] == "Bearer tok"
    assert out["X-Ca-Actor"] == "analyst@example.com"
    assert out["X-Ca-Actor-Sig"]


def test_emptied_bearer_refuses_instead_of_calling_anonymously(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_s2s.TOKEN_ENV, "  ")
    monkeypatch.delenv(_s2s.SIGNING_KEY_ENV, raising=False)
    with pytest.raises(ConfiguredEmptyError):
        _s2s.headers("analyst@example.com")


def test_emptied_signing_key_refuses_instead_of_dropping_the_actor_proof(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_s2s.TOKEN_ENV, "tok")
    monkeypatch.setenv(_s2s.SIGNING_KEY_ENV, "")
    with pytest.raises(ConfiguredEmptyError):
        _s2s.headers("analyst@example.com")
