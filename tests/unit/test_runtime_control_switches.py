"""The cheap runtime controls each have a switch, default on, and behave as a user expects.

The fleet's runtime-control contract (2026-09-24): the guardrail, PII redaction and review
routing are each switched by one environment variable read in three states; off binds a
disabled adapter and says so at startup; on under a networked profile refuses to boot without
the configuration it needs; and the response tells the user when redaction changed their
input and what happened to the human-review hand-off.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from tests.conftest import LOOPBACK_PEER

from compliance_advisory.adapters.controls import (
    DisabledGuardrail,
    DisabledRedaction,
    DisabledReviewRouter,
    DisclosingRedaction,
    RecordingReviewRouter,
    ReviewRouting,
)
from compliance_advisory.adapters.gcp.dlp_redaction import DlpRedactionAdapter
from compliance_advisory.adapters.local.redaction import LocalRegexRedactionAdapter
from compliance_advisory.api import deps
from compliance_advisory.api.app import app
from compliance_advisory.config import (
    GUARDRAIL_ENV,
    HUMAN_REVIEW_URL_ENV,
    PII_REDACTION_ENV,
    REVIEW_ROUTING_ENV,
    Container,
    ControlSwitches,
    Settings,
    build_container,
)
from compliance_advisory.envread import ConfiguredEmptyError

_SWITCHES = (GUARDRAIL_ENV, PII_REDACTION_ENV, REVIEW_ROUTING_ENV)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (*_SWITCHES, HUMAN_REVIEW_URL_ENV):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("COMPLIANCE_PROFILE", "local")


# --------------------------------------------------------------------------- #
# Three states
# --------------------------------------------------------------------------- #
def test_every_control_is_on_when_nothing_is_said() -> None:
    assert Settings.load().controls == ControlSwitches(True, True, True)


@pytest.mark.parametrize("name", _SWITCHES)
def test_a_control_switched_off_is_off(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    monkeypatch.setenv(name, "false")
    assert Settings.load().controls.switched_off() == (name,)


@pytest.mark.parametrize("name", _SWITCHES)
def test_an_emptied_switch_refuses_at_load(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    monkeypatch.setenv(name, "")
    with pytest.raises(ConfiguredEmptyError, match=name):
        Settings.load()


@pytest.mark.parametrize("name", _SWITCHES)
def test_an_unrecognised_switch_refuses_at_load(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    monkeypatch.setenv(name, "sometimes")
    with pytest.raises(ValueError, match=name):
        Settings.load()


# --------------------------------------------------------------------------- #
# Off binds the disabled adapter, and says so
# --------------------------------------------------------------------------- #
def test_off_binds_the_disabled_adapters() -> None:
    container = Container(Settings(controls=ControlSwitches(False, False, False)))
    assert isinstance(container.guardrail, DisabledGuardrail)
    assert isinstance(container.redaction, DisabledRedaction)
    assert isinstance(container.review_router, DisabledReviewRouter)


def test_on_binds_the_profile_adapters() -> None:
    container = Container(Settings.load())
    assert not isinstance(container.guardrail, DisabledGuardrail)
    assert not isinstance(container.redaction, DisabledRedaction)
    assert not isinstance(container.review_router, DisabledReviewRouter)


def test_a_process_with_a_control_off_says_so_at_startup(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="compliance_advisory.config"):
        build_container(Settings(controls=ControlSwitches(guardrail=False)))
    assert GUARDRAIL_ENV in caplog.text


# --------------------------------------------------------------------------- #
# On has to work: checked at boot under a networked profile
# --------------------------------------------------------------------------- #
def test_routing_on_under_gcp_without_a_console_refuses_at_boot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COMPLIANCE_PROFILE", "gcp")
    with pytest.raises(ConfiguredEmptyError, match=HUMAN_REVIEW_URL_ENV):
        Settings.load()


def test_routing_on_under_gcp_with_a_console_loads(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COMPLIANCE_PROFILE", "gcp")
    monkeypatch.setenv(HUMAN_REVIEW_URL_ENV, "https://review.example.test")
    assert Settings.load().controls.review_routing is True


def test_routing_stated_off_under_gcp_needs_no_console(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COMPLIANCE_PROFILE", "gcp")
    monkeypatch.setenv(REVIEW_ROUTING_ENV, "off")
    assert Settings.load().controls.review_routing is False


def test_the_local_profile_needs_no_console() -> None:
    assert Settings.load().controls.review_routing is True


# --------------------------------------------------------------------------- #
# The four routing outcomes
# --------------------------------------------------------------------------- #
class _Accepting:
    def route(self, answer: object, *, maker: str, tenant: str = "") -> None:
        return None


class _Refusing:
    def route(self, answer: object, *, maker: str, tenant: str = "") -> None:
        raise ConnectionError("console unreachable")


def test_routing_outcomes_take_each_of_their_four_values() -> None:
    nothing_required = RecordingReviewRouter(_Accepting())
    assert nothing_required.outcome is ReviewRouting.NOT_REQUIRED

    routed = RecordingReviewRouter(_Accepting())
    routed.route(object(), maker="m")
    assert routed.outcome is ReviewRouting.ROUTED

    off = RecordingReviewRouter(DisabledReviewRouter(Settings()))
    off.route(object(), maker="m")
    assert off.outcome is ReviewRouting.OFF


def test_a_failed_hand_off_is_reported_and_logged_never_raised(
    caplog: pytest.LogCaptureFixture,
) -> None:
    failed = RecordingReviewRouter(_Refusing())
    with caplog.at_level(logging.WARNING, logger="compliance_advisory.adapters.controls"):
        failed.route(object(), maker="m")
    assert failed.outcome is ReviewRouting.FAILED
    assert "ConnectionError" in caplog.text


def test_one_failure_among_several_hand_offs_is_what_the_response_reports() -> None:
    class _FailsSecond:
        calls = 0

        def route(self, answer: object, *, maker: str, tenant: str = "") -> None:
            self.calls += 1
            if self.calls == 2:
                raise TimeoutError

    router = RecordingReviewRouter(_FailsSecond())
    for _ in range(3):
        router.route(object(), maker="m")
    assert router.outcome is ReviewRouting.FAILED


# --------------------------------------------------------------------------- #
# Through the API: the user sees what the controls did
# --------------------------------------------------------------------------- #
@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("COMPLIANCE_LOCAL_DB", ":memory:")
    monkeypatch.setenv("COMPLIANCE_LOCAL_AUDIT", ":memory:")
    monkeypatch.setenv("COMPLIANCE_LOCAL_LEDGER", ":memory:")
    deps.get_container.cache_clear()
    try:
        with TestClient(app, client=LOOPBACK_PEER) as test_client:
            yield test_client
    finally:
        deps.get_container.cache_clear()


_CLEAN_QUESTION = "What cloud outsourcing controls does MAS expect before onboarding a provider?"
_PII_QUESTION = "Customer S1234567A (jane.doe@example.com) wants cloud outsourcing advice."


def test_an_answer_reports_its_hand_off_and_an_unchanged_question(client: TestClient) -> None:
    body = client.post("/ask", json={"question": _CLEAN_QUESTION}).json()
    assert body["requires_human_review"] is True
    assert body["review_routing"] == "routed"
    assert body["input_redacted"] is False


def test_an_answer_discloses_that_the_question_was_masked(client: TestClient) -> None:
    body = client.post("/ask", json={"question": _PII_QUESTION}).json()
    assert body["input_redacted"] is True


def test_an_answer_says_routing_is_off_when_it_is(
    monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    monkeypatch.setenv(REVIEW_ROUTING_ENV, "false")
    deps.get_container.cache_clear()
    body = client.post("/ask", json={"question": _CLEAN_QUESTION}).json()
    assert body["review_routing"] == "off"


def test_a_generator_discloses_that_the_use_case_was_masked(client: TestClient) -> None:
    body = client.post("/checklist", json={"use_case": _PII_QUESTION}).json()
    assert body["input_redacted"] is True


def test_the_disclosure_wrapper_notices_only_a_change() -> None:
    wrapper = DisclosingRedaction(LocalRegexRedactionAdapter(Settings()))
    wrapper.redact("no personal data here")
    assert wrapper.changed is False
    wrapper.redact("reach me at jane.doe@example.com")
    assert wrapper.changed is True


# --------------------------------------------------------------------------- #
# Redaction tuned against false positives
# --------------------------------------------------------------------------- #
_BENIGN = (
    "What does MAS Notice 626 require for customer due diligence?",
    "Does CPS 230 paragraph 36 apply to our cloud provider?",
    "Is a single transfer of SGD 90000000 subject to enhanced due diligence?",
    "Is a transfer of S$ 80000000 reportable under the Guidelines to Notice 626?",
    "Guidelines to MAS Notice 626 last revised on 2025-07-01, section 8.3",
    "Report suspicious transactions above S$20,000 within 15 business days",
    "HKMA SPM OR-2 and APRA CPG 230 on operational resilience",
    "Basel III ratio of 8.5% and the 2024 BCBS 239 principles",
)


@pytest.mark.parametrize("text", _BENIGN)
def test_benign_regulatory_input_reaches_the_model_unchanged(text: str) -> None:
    assert LocalRegexRedactionAdapter(Settings()).redact(text).text == text


@pytest.mark.parametrize(
    ("text", "masked"),
    [
        ("NRIC S1234567A on file", "[NRIC]"),
        ("write to jane.doe@example.com", "[EMAIL]"),
        ("call +65 9123 4567 today", "[PHONE]"),
        ("call 61234567 today", "[PHONE]"),
    ],
)
def test_true_personal_data_is_still_masked(text: str, masked: str) -> None:
    assert masked in LocalRegexRedactionAdapter(Settings()).redact(text).text


def test_the_inline_dlp_config_is_tuned_against_false_positives() -> None:
    adapter = DlpRedactionAdapter(Settings())
    request = adapter._build_request("What does MAS Notice 626 require?")
    inspect = request["inspect_config"]
    assert inspect["min_likelihood"] == "LIKELY"
    assert all(c["likelihood"] == "VERY_LIKELY" for c in inspect["custom_info_types"])
    exclusion = inspect["rule_set"][0]
    assert exclusion["info_types"] == [{"name": "PERSON_NAME"}]
    assert "Monetary Authority" in exclusion["rules"][0]["exclusion_rule"]["regex"]["pattern"]
    transformation = request["deidentify_config"]["info_type_transformations"]["transformations"][0]
    assert transformation["primitive_transformation"] == {"replace_with_info_type_config": {}}
