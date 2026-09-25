"""The service half of the model pills: which model ANSWERED, and whether it searched.

The console shows two pills at the top right: the model that answered the last request, and
``Search`` when that answer used an online search tool. Both come from response headers the kit
emits (``install_answer_provenance`` in ``api/app.py``) for whatever the model adapters NOTED as
they called. Before a request is answered the pill shows ``generator_model`` from ``/healthz``,
so that value must be the model the bound adapter calls, never one a configuration flag names
while the adapter calls another.

These drive the real app and the real adapters. The Gemini ones run against the recording
stand-in in ``tests/fixtures/fake_genai.py``, because the offline gate installs no cloud SDK.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from hex_service_kit import provenance
from tests.conftest import LOOPBACK_PEER
from tests.fixtures import fake_genai, sample_regs

from compliance_advisory.adapters.gcp.gemini_grounding import GeminiGoogleSearchGroundingAdapter
from compliance_advisory.adapters.gcp.gemini_llm import GeminiLLMAdapter
from compliance_advisory.api import deps
from compliance_advisory.api.app import app
from compliance_advisory.config import OFFLINE_STUB_MODEL, ModelSettings, Settings
from compliance_advisory.domain.kernel import LlmMessage, LlmRequest

ANSWERED_BY = "x-answered-by"
SEARCH_USED = "x-search-used"
CONFIG_PATH = "config/settings.yaml"
REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """The real app under the ``local`` profile, with ephemeral in-memory stores."""
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


def _ask(client: TestClient) -> dict[str, str]:
    response = client.post("/ask", json={"question": sample_regs.SAMPLE_QUESTION})
    assert response.status_code == 200, response.text
    return dict(response.headers)


def _models(headers: dict[str, str]) -> list[str]:
    return [name.strip() for name in headers[ANSWERED_BY].split(",")]


# --------------------------------------------------------------------------- #
# Through the API
# --------------------------------------------------------------------------- #
def test_an_answer_names_the_model_that_answered_it(client: TestClient) -> None:
    """Under ``local`` that is the stub, by the same name ``/healthz`` gives it."""
    headers = _ask(client)
    configured = client.get("/healthz").json()["generator_model"]
    assert configured == OFFLINE_STUB_MODEL
    assert _models(headers) == [configured]
    assert SEARCH_USED not in headers, "no search tool ran, so no Search pill"


def test_every_model_backed_artifact_names_what_answered(client: TestClient) -> None:
    for route in ("/checklist", "/testcases", "/regulator-questions"):
        response = client.post(route, json={"use_case": sample_regs.SAMPLE_USE_CASE})
        assert response.status_code == 200, (route, response.text)
        assert response.headers[ANSWERED_BY] == OFFLINE_STUB_MODEL, route


def test_a_request_no_model_answered_names_no_model(client: TestClient) -> None:
    """Nothing noted, nothing sent: the pill never invents a model the route did not call."""
    headers = client.get("/healthz").headers
    assert ANSWERED_BY not in headers
    assert SEARCH_USED not in headers


def test_a_cross_origin_console_can_read_both_headers(client: TestClient) -> None:
    """The console calls this service directly, and a browser hides unlisted headers.

    Without ``Access-Control-Expose-Headers`` naming both, the pills could never read what
    answered and would sit on the configured model forever with every other test here green.
    """
    response = client.post(
        "/ask",
        json={"question": sample_regs.SAMPLE_QUESTION},
        headers={"Origin": "http://localhost:3000"},
    )
    assert response.headers.get("access-control-allow-origin") == "http://localhost:3000"
    exposed = {
        name.strip().lower()
        for value in response.headers.get_list("access-control-expose-headers")
        for name in value.split(",")
    }
    assert {ANSWERED_BY, SEARCH_USED} <= exposed


def test_the_web_grounding_leg_shows_as_search(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The real Gemini grounding adapter, with its google_search tool attached, notes both.

    Its model joins the stub in ``X-Answered-By`` (the grounding call runs first), and
    ``X-Search-Used`` is set because a search tool was attached to that very call.
    """
    fake_genai.install(monkeypatch)
    container = deps.get_container()
    settings = dataclasses.replace(container.settings, grounding_enabled=True)
    grounding = GeminiGoogleSearchGroundingAdapter(settings)
    fake = fake_genai.FakeClient(
        chunks=(fake_genai.web_chunk("https://example.test/mas", "MAS notice"),)
    )
    grounding._client = fake
    container.grounding = grounding

    headers = _ask(client)

    assert fake.calls, "the grounding leg never ran, so nothing was checked"
    tools = fake.calls[0]["config"].kwargs["tools"]
    assert any(hasattr(tool, "google_search") for tool in tools)
    assert headers[SEARCH_USED] == "true"
    assert _models(headers) == [settings.models.reasoning, OFFLINE_STUB_MODEL]


def test_a_disabled_grounding_leg_never_claims_a_search(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_genai.install(monkeypatch)
    container = deps.get_container()
    grounding = GeminiGoogleSearchGroundingAdapter(
        dataclasses.replace(container.settings, grounding_enabled=False)
    )
    grounding._client = fake_genai.FakeClient()
    container.grounding = grounding
    assert SEARCH_USED not in _ask(client)


# --------------------------------------------------------------------------- #
# The managed adapter notes the model it called, and the pill starts from that model
# --------------------------------------------------------------------------- #
def _request(model: str | None = None) -> LlmRequest:
    return LlmRequest(messages=(LlmMessage(role="user", content="q"),), model=model)


@pytest.fixture
def gcp_settings() -> Settings:
    return dataclasses.replace(Settings.load(CONFIG_PATH), profile="gcp")


def _gemini(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> GeminiLLMAdapter:
    fake_genai.install(monkeypatch)
    adapter = GeminiLLMAdapter(settings)
    adapter._client = fake_genai.FakeClient(text="MAS")
    return adapter


def test_the_gemini_adapter_notes_the_model_it_called(
    monkeypatch: pytest.MonkeyPatch, gcp_settings: Settings
) -> None:
    adapter = _gemini(monkeypatch, gcp_settings)
    with provenance.scope() as record:
        adapter.generate(_request("an-explicit-model"))
        adapter.classify("text", ["MAS"])
    assert record.models == ["an-explicit-model", gcp_settings.models.triage]
    assert record.search_used is False, "a plain generation attached no search tool"


def test_the_configured_pill_is_the_model_the_gemini_adapter_calls(
    monkeypatch: pytest.MonkeyPatch, gcp_settings: Settings
) -> None:
    """``/healthz``'s model under ``gcp`` is what a request with no explicit model reaches."""
    adapter = _gemini(monkeypatch, gcp_settings)
    with provenance.scope() as record:
        adapter.generate(_request(None))
    assert record.models == [gcp_settings.generator_model]


def test_the_hard_reasoning_flag_does_not_exist() -> None:
    """The latent false banner: a flag that moved the pill but not the model that answered.

    ``generator_model`` once named ``models.hard_reasoning`` when ``models.use_hard_reasoning``
    was set, while the Gemini adapter called ``request.model or models.reasoning`` and never
    read the flag. The pill would then have named a model that never answered. Both settings
    are gone, from the settings type, the settings file and every source file.
    """
    fields = {field.name for field in dataclasses.fields(ModelSettings)}
    assert not fields & {"use_hard_reasoning", "hard_reasoning"}
    assert "hard_reasoning" not in (REPO_ROOT / CONFIG_PATH).read_text(encoding="utf-8")
    for source in sorted((REPO_ROOT / "src").rglob("*.py")):
        assert "hard_reasoning" not in source.read_text(encoding="utf-8"), source
