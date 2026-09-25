"""The ``live`` laptop lane: the core on the local model, Gemini only for optional grounding.

Owner decision 2026-09-23: this assistant runs its CORE on the fleet's local open-weight
model and uses Gemini only while its optional web grounding is on. These tests pin the four
things that decision rests on, offline, against a fake transport:

* the live LLM adapter maps the domain request onto the shared kit client and back, including
  a fenced, then invalid, first answer that the kit feeds back and retries;
* ``live`` binds and builds every port with no cloud SDK and no credentials;
* the grounding switch is OFF under ``live`` unless set, and ``false`` really means off;
* grounding switched on without credentials reports itself unavailable instead of crashing.
"""

from __future__ import annotations

import dataclasses
import json
from typing import Any

import pytest
from hex_service_kit.localmodel import (
    LocalModelClient,
    LocalModelSettings,
    LocalModelUnavailable,
)

from compliance_advisory.adapters.live import grounding as live_grounding
from compliance_advisory.adapters.live.grounding import LiveOptionalGroundingAdapter
from compliance_advisory.adapters.live.llm import LocalModelLLMAdapter
from compliance_advisory.config import Container, LocalSettings, Settings
from compliance_advisory.domain.models import (
    LlmMessage,
    LlmRequest,
    TokenUsage,
    WebCitation,
)

CONFIG_PATH = "config/settings.yaml"

_SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {"type": "string"},
        "confidence": {"type": "number"},
    },
    "required": ["answer", "confidence"],
}


class FakeTransport:
    """Answers each POST with the next scripted body; records what was sent."""

    def __init__(self, *answers: str, model: str = "served-model", usage: Any = None) -> None:
        self._answers = list(answers)
        self._model = model
        self._usage = usage
        self.sent: list[dict[str, Any]] = []

    def __call__(self, url: str, body: bytes | None, timeout: float) -> bytes:
        assert body is not None
        self.sent.append(json.loads(body))
        reply: dict[str, Any] = {
            "model": self._model,
            "choices": [{"message": {"role": "assistant", "content": self._answers.pop(0)}}],
        }
        if self._usage is not None:
            reply["usage"] = self._usage
        return json.dumps(reply).encode()


def _live_settings(**overrides: Any) -> Settings:
    base = Settings.load(CONFIG_PATH)
    return dataclasses.replace(
        base,
        profile="live",
        local=LocalSettings(
            db_path=":memory:",
            audit_path=":memory:",
            ledger_path=":memory:",
            horizon_path=":memory:",
        ),
        **overrides,
    )


def _adapter(transport: FakeTransport) -> LocalModelLLMAdapter:
    client = LocalModelClient(LocalModelSettings(), transport=transport)
    return LocalModelLLMAdapter(_live_settings(), client=client)


def _request(**kwargs: Any) -> LlmRequest:
    return LlmRequest(
        messages=(LlmMessage(role="user", content="What does CPS 230 require?"),),
        system_instruction="Answer only from the passages.",
        **kwargs,
    )


# --------------------------------------------------------------------------- #
# The live LLM adapter
# --------------------------------------------------------------------------- #
def test_structured_call_retries_a_fenced_invalid_answer_and_returns_clean_json() -> None:
    transport = FakeTransport(
        '```json\n{"answer": "It requires a register."}\n```',  # fenced AND missing confidence
        '{"answer": "It requires a register.", "confidence": 0.8}',
        usage={"prompt_tokens": 40, "completion_tokens": 9},
    )
    response = _adapter(transport).generate(
        _request(response_schema=_SCHEMA, temperature=0.0, max_output_tokens=512)
    )

    assert len(transport.sent) == 2, "the invalid first answer must be fed back and retried"
    retry = transport.sent[1]["messages"]
    assert "confidence" in retry[-1]["content"], "the retry names the missing field"
    assert json.loads(response.text) == {"answer": "It requires a register.", "confidence": 0.8}
    assert response.raw == {"answer": "It requires a register.", "confidence": 0.8}
    assert response.model == "served-model", "model is the id that answered"
    assert response.usage == TokenUsage(input_tokens=80, output_tokens=18)


def test_the_request_maps_onto_messages_temperature_and_budget() -> None:
    transport = FakeTransport("plain text")
    request = LlmRequest(
        messages=(
            LlmMessage(role="user", content="q1"),
            LlmMessage(role="model", content="a1"),
            LlmMessage(role="user", content="q2"),
        ),
        system_instruction="sys",
        temperature=0.3,
        max_output_tokens=100_000,
    )
    response = _adapter(transport).generate(request)

    sent = transport.sent[0]
    assert [m["role"] for m in sent["messages"]] == ["system", "user", "assistant", "user"]
    assert sent["messages"][0]["content"] == "sys"
    assert sent["temperature"] == 0.3, "the request's temperature passes through unchanged"
    assert sent["max_tokens"] == Settings().live.max_output_tokens, "the live budget caps it"
    assert response.text == "plain text"
    assert response.usage == TokenUsage(), "no usage reported is zeros, the type's default"


def test_an_answer_that_never_validates_degrades_to_the_last_text() -> None:
    transport = FakeTransport("not json", "still not", "nope")
    response = _adapter(transport).generate(_request(response_schema=_SCHEMA))

    assert len(transport.sent) == 3
    assert response.text == "nope", "the domain parses this defensively, as a bad Gemini answer"


def test_an_unreachable_server_raises_with_the_start_recipe() -> None:
    def down(url: str, body: bytes | None, timeout: float) -> bytes:
        raise OSError("connection refused")

    adapter = LocalModelLLMAdapter(
        _live_settings(), client=LocalModelClient(LocalModelSettings(), transport=down)
    )
    with pytest.raises(LocalModelUnavailable, match="mlx_vlm.server"):
        adapter.generate(_request())


def test_classify_coerces_the_reply_to_a_label() -> None:
    transport = FakeTransport("Label: Outsourcing.")
    label = _adapter(transport).classify("text", ["cloud", "outsourcing"])

    assert label == "outsourcing"
    assert transport.sent[0]["temperature"] == 0.0


# --------------------------------------------------------------------------- #
# The profile
# --------------------------------------------------------------------------- #
def test_live_binds_the_local_model_for_the_core_and_the_optional_leg_for_search() -> None:
    adapters = Settings.load(CONFIG_PATH).adapters
    assert adapters["llm"]["live"].endswith("adapters.live.llm:LocalModelLLMAdapter")
    assert adapters["grounding"]["live"].endswith(
        "adapters.live.grounding:LiveOptionalGroundingAdapter"
    )


def test_the_container_builds_every_port_under_live_with_no_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    container = Container(_live_settings())
    for port in Settings.load(CONFIG_PATH).adapters:
        assert getattr(container, port) is not None, port
    assert isinstance(container.llm, LocalModelLLMAdapter)
    assert container.grounding.enabled is False


def test_the_banner_names_the_local_model_under_live(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCAL_MODEL", "some-org/some-local-model")
    assert _live_settings().generator_model == "some-org/some-local-model"


# --------------------------------------------------------------------------- #
# The grounding switch
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("profile", "expected"), [("live", False), ("local", False), ("gcp", True), ("platform", True)]
)
def test_grounding_is_off_under_live_unless_set_and_keeps_the_managed_default(
    monkeypatch: pytest.MonkeyPatch, profile: str, expected: bool
) -> None:
    monkeypatch.delenv("COMPLIANCE_GROUNDING_ENABLED", raising=False)
    monkeypatch.setenv("COMPLIANCE_PROFILE", profile)
    monkeypatch.setenv("COMPLIANCE_REVIEW_ROUTING", "off")
    monkeypatch.setenv("COMPLIANCE_GUARDRAIL", "off")
    assert Settings.load(CONFIG_PATH).grounding_enabled is expected


@pytest.mark.parametrize(("raw", "expected"), [("false", False), ("off", False), ("true", True)])
def test_the_grounding_switch_is_a_real_boolean(
    monkeypatch: pytest.MonkeyPatch, raw: str, expected: bool
) -> None:
    """``"false"`` used to reach the adapters as a non-empty, truthy STRING."""
    monkeypatch.setenv("COMPLIANCE_PROFILE", "local")
    monkeypatch.setenv("COMPLIANCE_GROUNDING_ENABLED", raw)
    assert Settings.load(CONFIG_PATH).grounding_enabled is expected


def test_a_grounding_switch_that_is_not_a_boolean_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COMPLIANCE_PROFILE", "local")
    monkeypatch.setenv("COMPLIANCE_GROUNDING_ENABLED", "maybe")
    with pytest.raises(ValueError, match="COMPLIANCE_GROUNDING_ENABLED"):
        Settings.load(CONFIG_PATH)


# --------------------------------------------------------------------------- #
# The optional grounding leg
# --------------------------------------------------------------------------- #
def test_grounding_off_imports_nothing_and_answers_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    def must_not_run(settings: Settings) -> tuple[Any, str]:
        raise AssertionError("no credential lookup while the switch is off")

    monkeypatch.setattr(live_grounding, "_gemini_or_reason", must_not_run)
    leg = LiveOptionalGroundingAdapter(_live_settings(grounding_enabled=False))
    assert (leg.enabled, leg.status) == (False, "off")
    assert leg.ground("anything") == []


def test_grounding_on_without_credentials_is_unavailable_not_fatal(
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings = _live_settings(grounding_enabled=True, project_id="your-gcp-project")
    leg = LiveOptionalGroundingAdapter(settings)

    assert (leg.enabled, leg.status) == (False, "unavailable")
    assert "GOOGLE_CLOUD_PROJECT" in leg.reason
    assert "unavailable" in caplog.text
    assert leg.ground("anything") == []


def test_grounding_on_with_credentials_delegates_and_survives_a_failing_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Gemini:
        fail = False

        def ground(self, query: str, max_results: int = 5) -> list[WebCitation]:
            if self.fail:
                raise RuntimeError("quota")
            return [WebCitation(title="MAS", url="https://www.mas.gov.sg/")]

    gemini = Gemini()
    monkeypatch.setattr(live_grounding, "_gemini_or_reason", lambda settings: (gemini, ""))
    leg = LiveOptionalGroundingAdapter(_live_settings(grounding_enabled=True))

    assert (leg.enabled, leg.status, leg.reason) == (True, "on", "")
    assert [c.url for c in leg.ground("q")] == ["https://www.mas.gov.sg/"]
    gemini.fail = True
    assert leg.ground("q") == []
