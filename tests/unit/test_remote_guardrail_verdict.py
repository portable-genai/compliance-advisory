"""The remote guardrail-gateway client allows ONLY on a JSON ``true``.

The parse this replaced was ``bool(body.get("allowed", False))``: the string ``"false"`` is
truthy, so a gateway (or anything in between) that rendered its verdict as a string had every
blocked prompt allowed, and so did ``1``, ``"no"`` and any non-empty object. HTTP is intercepted
with ``respx``; no gateway is contacted.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx

from compliance_advisory.adapters.platform.remote_guardrail import (
    RemoteGuardrailAdapter,
    RemoteGuardrailError,
)
from compliance_advisory.config import Settings
from compliance_advisory.domain.models import Direction

_BASE = "http://localhost:18081"


def _verdict(allowed: Any, *, present: bool = True) -> dict[str, Any]:
    body: dict[str, Any] = {"direction": "input", "findings": [], "reason": "r"}
    if present:
        body["allowed"] = allowed
    return body


def test_literal_true_allows() -> None:
    verdict = RemoteGuardrailAdapter._parse_verdict(_verdict(True), Direction.INPUT)
    assert verdict.allowed is True


@pytest.mark.parametrize(
    "allowed",
    [False, "false", "False", "true", "True", "yes", "no", 1, 0, 1.0, [True], {"v": True}, None],
    ids=repr,
)
def test_anything_but_literal_true_blocks(allowed: Any) -> None:
    verdict = RemoteGuardrailAdapter._parse_verdict(_verdict(allowed), Direction.INPUT)
    assert verdict.allowed is False


def test_absent_allowed_blocks() -> None:
    verdict = RemoteGuardrailAdapter._parse_verdict(_verdict(None, present=False), Direction.INPUT)
    assert verdict.allowed is False


@pytest.mark.parametrize("body", [[], None, "true", True, 1], ids=repr)
def test_a_body_that_is_not_an_object_raises(body: Any) -> None:
    with pytest.raises(RemoteGuardrailError):
        RemoteGuardrailAdapter._parse_verdict(body, Direction.INPUT)


@respx.mock
def test_string_false_from_the_gateway_blocks_end_to_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GUARDRAIL_GATEWAY_URL", _BASE)
    respx.post(f"{_BASE}/v1/guardrail/screen").mock(
        return_value=httpx.Response(200, json=_verdict("false"))
    )
    verdict = RemoteGuardrailAdapter(Settings()).screen("text", Direction.INPUT)
    assert verdict.allowed is False


@respx.mock
def test_gateway_errors_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GUARDRAIL_GATEWAY_URL", _BASE)
    respx.post(f"{_BASE}/v1/guardrail/screen").mock(return_value=httpx.Response(503))
    with pytest.raises(RemoteGuardrailError):
        RemoteGuardrailAdapter(Settings()).screen("text", Direction.INPUT)
