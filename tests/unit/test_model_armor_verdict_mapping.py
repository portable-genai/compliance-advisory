"""The Model Armor adapter blocks on a match, and fails closed on no verdict or an API error.

It also fails closed on an INCOMPLETE screen: ``invocationResult`` ``PARTIAL`` or ``FAILURE``
means some or all filters were skipped or failed, and a skipped filter reports
``NO_MATCH_FOUND``. Padding a prompt past the prompt-injection filter's token limit would
otherwise get it through unscreened, so only ``NO_MATCH_FOUND`` with ``SUCCESS`` allows.

The adapter calls the REST API, so the verdict arrives as proto3 JSON: enums as their NAMES,
and a zero-valued enum (``*_UNSPECIFIED``) either named or omitted entirely. The mapping this
replaced read ``filterMatchState != "MATCH_FOUND"``, never read ``invocationResult``, and fell
back to "no findings" when the state was absent, so UNSPECIFIED, a missing or empty result, and
a PARTIAL/FAILURE screen were all allowed.

This module tests at two levels:

* **SDK-free** (always runs, including the offline gate's SDK-free ``make check``): REST
  bodies are built from ``_MirrorState`` / ``_MirrorInvocation``, stdlib ``IntEnum`` mirrors
  with the real member names and numbers, and screened through ``screen()`` with a recording
  HTTP client, so nothing touches the network.
* **Real SDK** (runs where ``google-cloud-modelarmor`` is installed, skips where it is not):
  responses are built from the real ``modelarmor_v1`` messages, serialized to the exact proto3
  JSON the REST API returns, and screened the same way. The first of these tests also pins the
  mirrors to the real enums, so the SDK-free half cannot drift.
"""

from __future__ import annotations

import enum
import json
from typing import Any

import httpx
import pytest

from compliance_advisory.adapters.gcp.model_armor_guardrail import ModelArmorGuardrailAdapter
from compliance_advisory.config import Settings
from compliance_advisory.domain.models import Direction, GuardrailCategory

TEXT = "Does this outsourcing arrangement need MAS notification?"
DIRECTIONS = [Direction.INPUT, Direction.OUTPUT]


class _MirrorState(enum.IntEnum):
    """``modelarmor_v1.FilterMatchState``'s members, by name and number."""

    FILTER_MATCH_STATE_UNSPECIFIED = 0
    NO_MATCH_FOUND = 1
    MATCH_FOUND = 2


class _MirrorInvocation(enum.IntEnum):
    """``modelarmor_v1.InvocationResult``'s members, by name and number."""

    INVOCATION_RESULT_UNSPECIFIED = 0
    SUCCESS = 1
    PARTIAL = 2
    FAILURE = 3


# --------------------------------------------------------------------------- #
# A recording HTTP client standing in for httpx.Client
# --------------------------------------------------------------------------- #
class _FakeHttp:
    """Answers every POST with a canned JSON body or status, or raises the canned error."""

    def __init__(
        self, body: Any = None, *, status: int = 200, error: Exception | None = None
    ) -> None:
        self._body = body
        self._status = status
        self._error = error
        self.urls: list[str] = []
        self.payloads: list[Any] = []
        self.timeouts: list[Any] = []

    def post(self, url: str, *, json: Any, headers: Any, timeout: Any) -> httpx.Response:
        self.urls.append(url)
        self.payloads.append(json)
        self.timeouts.append(timeout)
        if self._error is not None:
            raise self._error
        return httpx.Response(self._status, json=self._body, request=httpx.Request("POST", url))


def _adapter(http: _FakeHttp) -> ModelArmorGuardrailAdapter:
    adapter = ModelArmorGuardrailAdapter(Settings(project_id="p", profile="gcp"))
    adapter._client = http  # skip the real client; the mapping is what is under test
    adapter._bearer_token = lambda: "test-token"  # type: ignore[method-assign]
    return adapter


def _screen(body: Any, direction: Direction = Direction.INPUT) -> Any:
    return _adapter(_FakeHttp(body)).screen(TEXT, direction)


def _mirror_body(
    state: _MirrorState | None, invocation: _MirrorInvocation | None = _MirrorInvocation.SUCCESS
) -> dict[str, Any]:
    """A REST body as proto3 JSON renders it: enum NAMES; ``None`` omits the field."""
    result: dict[str, Any] = {}
    if state is not None:
        result["filterMatchState"] = state.name
    if invocation is not None:
        result["invocationResult"] = invocation.name
    return {"sanitizationResult": result}


# --------------------------------------------------------------------------- #
# SDK-free: the mapping, through screen()
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("direction", DIRECTIONS)
def test_match_found_blocks_sdk_free(direction: Direction) -> None:
    verdict = _screen(_mirror_body(_MirrorState.MATCH_FOUND), direction)
    assert verdict.allowed is False
    assert verdict.sanitized_text is None
    assert verdict.findings, "a block must carry a finding for the audit trail"


@pytest.mark.parametrize("direction", DIRECTIONS)
def test_no_match_found_with_success_allows_sdk_free(direction: Direction) -> None:
    verdict = _screen(_mirror_body(_MirrorState.NO_MATCH_FOUND), direction)
    assert verdict.allowed is True
    assert verdict.sanitized_text == TEXT
    assert verdict.findings == ()


@pytest.mark.parametrize("direction", DIRECTIONS)
@pytest.mark.parametrize(
    "invocation", [*_MirrorInvocation, None], ids=lambda m: m.name if m else "absent"
)
def test_match_found_blocks_however_many_filters_ran_sdk_free(
    direction: Direction, invocation: _MirrorInvocation | None
) -> None:
    verdict = _screen(_mirror_body(_MirrorState.MATCH_FOUND, invocation), direction)
    assert verdict.allowed is False
    assert verdict.sanitized_text is None


@pytest.mark.parametrize("direction", DIRECTIONS)
@pytest.mark.parametrize(
    "invocation",
    [
        _MirrorInvocation.PARTIAL,
        _MirrorInvocation.FAILURE,
        _MirrorInvocation.INVOCATION_RESULT_UNSPECIFIED,
        None,
    ],
    ids=["PARTIAL", "FAILURE", "UNSPECIFIED", "absent"],
)
def test_no_match_from_an_incomplete_screen_blocks_sdk_free(
    direction: Direction, invocation: _MirrorInvocation | None
) -> None:
    """A skipped filter reports no match. That is not a pass: the text was not screened."""
    verdict = _screen(_mirror_body(_MirrorState.NO_MATCH_FOUND, invocation), direction)
    assert verdict.allowed is False
    assert verdict.sanitized_text is None
    assert "no complete filter decision" in verdict.reason


def test_exactly_one_combination_allows_sdk_free() -> None:
    allowed = [
        (state.name, invocation.name)
        for state in _MirrorState
        for invocation in _MirrorInvocation
        if _screen(_mirror_body(state, invocation)).allowed
    ]
    assert allowed == [("NO_MATCH_FOUND", "SUCCESS")]


@pytest.mark.parametrize(
    "body",
    [
        _mirror_body(_MirrorState.FILTER_MATCH_STATE_UNSPECIFIED),
        # proto3 JSON may omit a zero-valued enum, so UNSPECIFIED can arrive as no field.
        {"sanitizationResult": {"invocationResult": "SUCCESS"}},
        {"sanitizationResult": {}},
        {"sanitizationResult": None},
        {},
        [],
        # An integer encoding is not the name the REST mapping carries; it must not allow.
        {"sanitizationResult": {"filterMatchState": 1, "invocationResult": 1}},
        # A lower-case or otherwise unknown name is not a decision either.
        {
            "sanitizationResult": {
                "filterMatchState": "no_match_found",
                "invocationResult": "SUCCESS",
            }
        },
    ],
    ids=[
        "unspecified",
        "state-missing",
        "empty-result",
        "null-result",
        "no-result",
        "list-body",
        "integer-enums",
        "unknown-name",
    ],
)
def test_no_verdict_fails_closed_sdk_free(body: Any) -> None:
    verdict = _screen(body)
    assert verdict.allowed is False
    assert verdict.sanitized_text is None
    assert verdict.findings


def test_match_found_reports_the_matched_filter_sdk_free() -> None:
    body = _mirror_body(_MirrorState.MATCH_FOUND)
    body["sanitizationResult"]["filterResults"] = {
        "pi_and_jailbreak": {
            "piAndJailbreakFilterResult": {"matchState": "MATCH_FOUND", "confidenceLevel": "HIGH"}
        }
    }
    verdict = _screen(body)
    assert verdict.allowed is False
    assert [f.category for f in verdict.findings] == [GuardrailCategory.PROMPT_INJECTION]


def test_a_body_that_is_not_json_raises_sdk_free() -> None:
    """``httpx`` sends no body for ``json=None``; decoding it fails and the caller refuses."""
    with pytest.raises(ValueError):
        _screen(None)


@pytest.mark.parametrize("direction", DIRECTIONS)
def test_every_call_carries_the_deadline_sdk_free(direction: Direction) -> None:
    http = _FakeHttp(_mirror_body(_MirrorState.NO_MATCH_FOUND))
    _adapter(http).screen(TEXT, direction)
    assert len(http.timeouts) == 1
    assert isinstance(http.timeouts[0], int | float) and 0 < http.timeouts[0] <= 60


@pytest.mark.parametrize(
    "http",
    [
        _FakeHttp(error=httpx.ConnectTimeout("Model Armor deadline exceeded")),
        _FakeHttp({"error": {"code": 503}}, status=503),
        _FakeHttp({"error": {"code": 403}}, status=403),
    ],
    ids=["deadline", "503", "403"],
)
def test_api_errors_propagate_sdk_free(http: _FakeHttp) -> None:
    """An API failure must not turn into an allow; it reaches the caller."""
    with pytest.raises(httpx.HTTPError):
        _adapter(http).screen(TEXT, Direction.INPUT)


# --------------------------------------------------------------------------- #
# Real SDK: real modelarmor_v1 messages, serialized to the REST wire shape
# --------------------------------------------------------------------------- #
def _ma() -> Any:
    return pytest.importorskip("google.cloud.modelarmor_v1")


def _real_body(
    direction: Direction,
    state_name: str | None,
    invocation_name: str = "SUCCESS",
    *,
    skipped: bool = False,
    pi_match: bool = False,
) -> Any:
    """A real sanitize response as REST JSON; ``state_name=None`` leaves the result unset.

    ``skipped`` adds the prompt-injection filter as not having run, the shape a prompt padded
    past that filter's token limit produces; ``pi_match`` adds it as having matched.
    """
    ma = _ma()
    cls = (
        ma.SanitizeUserPromptResponse
        if direction is Direction.INPUT
        else ma.SanitizeModelResponseResponse
    )
    if state_name is None:
        message = cls()
    else:
        filter_results = {}
        if skipped:
            filter_results["pi_and_jailbreak"] = ma.FilterResult(
                pi_and_jailbreak_filter_result=ma.PiAndJailbreakFilterResult(
                    execution_state=ma.FilterExecutionState.EXECUTION_SKIPPED,
                    match_state=ma.FilterMatchState.NO_MATCH_FOUND,
                )
            )
        if pi_match:
            filter_results["pi_and_jailbreak"] = ma.FilterResult(
                pi_and_jailbreak_filter_result=ma.PiAndJailbreakFilterResult(
                    execution_state=ma.FilterExecutionState.EXECUTION_SUCCESS,
                    match_state=ma.FilterMatchState.MATCH_FOUND,
                    confidence_level=ma.DetectionConfidenceLevel.HIGH,
                )
            )
        message = cls(
            sanitization_result=ma.SanitizationResult(
                filter_match_state=ma.FilterMatchState[state_name],
                invocation_result=ma.InvocationResult[invocation_name],
                filter_results=filter_results,
            )
        )
    # proto3 JSON, as the REST endpoint returns it: camelCase fields, enum names.
    return json.loads(cls.to_json(message, use_integers_for_enums=False))


@pytest.mark.parametrize(
    ("mirror", "real_name"),
    [(_MirrorState, "FilterMatchState"), (_MirrorInvocation, "InvocationResult")],
    ids=["FilterMatchState", "InvocationResult"],
)
def test_the_mirror_matches_the_real_enum(mirror: Any, real_name: str) -> None:
    real = getattr(_ma(), real_name)
    assert {m.name: int(m) for m in real} == {m.name: int(m) for m in mirror}


def test_the_real_wire_shape_is_what_the_mirror_builds() -> None:
    """The enum fields the mirror half writes are exactly what the real JSON carries."""

    def verdict_fields(body: dict[str, Any]) -> dict[str, Any]:
        result = body["sanitizationResult"]
        return {k: result[k] for k in ("filterMatchState", "invocationResult") if k in result}

    for state in _MirrorState:
        for invocation in _MirrorInvocation:
            real = _real_body(Direction.INPUT, state.name, invocation.name)
            assert verdict_fields(real) == verdict_fields(_mirror_body(state, invocation))


@pytest.mark.parametrize("direction", DIRECTIONS)
def test_match_found_blocks(direction: Direction) -> None:
    http = _FakeHttp(_real_body(direction, "MATCH_FOUND", pi_match=True))
    verdict = _adapter(http).screen(TEXT, direction)
    assert verdict.allowed is False
    assert verdict.sanitized_text is None
    assert [f.category for f in verdict.findings] == [GuardrailCategory.PROMPT_INJECTION]
    assert len(http.urls) == 1


@pytest.mark.parametrize("direction", DIRECTIONS)
def test_no_match_found_allows(direction: Direction) -> None:
    verdict = _screen(_real_body(direction, "NO_MATCH_FOUND"), direction)
    assert verdict.allowed is True
    assert verdict.sanitized_text == TEXT


@pytest.mark.parametrize("direction", DIRECTIONS)
@pytest.mark.parametrize(
    "state_name",
    [None, "FILTER_MATCH_STATE_UNSPECIFIED"],
    ids=["missing-result", "unspecified-state"],
)
def test_no_verdict_fails_closed(direction: Direction, state_name: str | None) -> None:
    verdict = _screen(_real_body(direction, state_name), direction)
    assert verdict.allowed is False
    assert verdict.sanitized_text is None


@pytest.mark.parametrize("direction", DIRECTIONS)
@pytest.mark.parametrize("invocation_name", ["PARTIAL", "FAILURE", "INVOCATION_RESULT_UNSPECIFIED"])
def test_no_match_from_a_screen_where_filters_did_not_run_blocks(
    direction: Direction, invocation_name: str
) -> None:
    body = _real_body(direction, "NO_MATCH_FOUND", invocation_name, skipped=True)
    verdict = _screen(body, direction)
    assert verdict.allowed is False
    assert verdict.sanitized_text is None
    assert "no complete filter decision" in verdict.reason


def test_api_errors_propagate() -> None:
    _ma()
    with pytest.raises(httpx.HTTPStatusError):
        _adapter(_FakeHttp({"error": {"code": 503}}, status=503)).screen(TEXT, Direction.INPUT)
