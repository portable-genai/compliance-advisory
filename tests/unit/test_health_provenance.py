"""The banner's server half: this service names its runtime and its model.

Every served UI in the fleet states, at the top of every page, where it is running and
which model answers (org decision, 2026-08-30). The console must never infer either. A
page that read its runtime from ``window.location`` would be right until the deployment
served through a proxy, and wrong silently after that; a page that hard-coded a model name
would keep printing it after the binding changed.

So the service answers, and the answer is DERIVED FROM THE BINDING the container will
actually build rather than from a second field someone has to remember to update. That is
the property these tests pin: rebinding ``llm`` for a profile has to change what the banner
says, in the same edit.
"""

from __future__ import annotations

import dataclasses

import pytest
from fastapi.testclient import TestClient

from compliance_advisory.api.app import app
from compliance_advisory.config import Settings

CONFIG_PATH = "config/settings.yaml"


@pytest.fixture
def settings() -> Settings:
    return Settings.load(CONFIG_PATH)


def test_healthz_states_the_runtime_and_the_model() -> None:
    # Loopback peer: the unconfigured local posture refuses a non-loopback caller outright,
    # which is the exposure guard doing its job and not this test's subject.
    body = TestClient(app, client=("127.0.0.1", 50000)).get("/healthz").json()
    assert body["runtime"] == "local"
    assert body["generator_model"] == "deterministic-offline-stub"


@pytest.mark.parametrize(
    ("profile", "expected"),
    [
        ("local", "local"),
        ("live", "local"),
        ("gcp", "gcp"),
        ("platform", "gcp"),
        ("onprem", "local"),
    ],
)
def test_the_runtime_says_where_the_process_runs_not_whose_model_it_calls(
    settings: Settings, profile: str, expected: str
) -> None:
    """``live`` is the row that carries the distinction, and it reads ``local``.

    Since 2026-08-30 every model call in the live profile is the Gemini API, so it would be
    easy to call that runtime "GCP". It is not: the process, the corpus and the audit
    trail are all on the operator's laptop.
    The banner states WHERE, and the model half states WHOSE, precisely so the two facts
    cannot be collapsed into one misleading sentence. ``onprem`` reads local for the same
    reason, and there it is the whole selling point.
    """
    assert dataclasses.replace(settings, profile=profile).runtime == expected


@pytest.mark.parametrize(
    ("profile", "expected"),
    [
        ("local", "deterministic-offline-stub"),
        ("live", "gemini-3.5-flash"),
        ("gcp", "gemini-3.5-flash"),
        ("platform", "gemini-3.5-flash"),
        ("onprem", "onprem-not-implemented"),
    ],
)
def test_the_model_is_read_off_the_binding_the_container_builds(
    settings: Settings, profile: str, expected: str
) -> None:
    """``live`` answers a Gemini model because it BINDS one, not because a string says so.

    Before 2026-08-30 the live profile bound a Gemma build on a local model server. The
    conversion changed one line in ``config/settings.yaml``; this row follows it because the
    value is read from that line rather than kept beside it.
    """
    assert dataclasses.replace(settings, profile=profile).generator_model == expected


def test_the_onprem_placeholder_does_not_advertise_a_model_it_never_serves(
    settings: Settings,
) -> None:
    """The on-prem adapter raises rather than generating.

    Naming a model for it would put a working generator at the top of a page that cannot
    generate, which is the exact class of claim this banner exists to prevent.
    """
    assert "not-implemented" in dataclasses.replace(settings, profile="onprem").generator_model
