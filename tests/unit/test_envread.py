"""Focused value-semantics tests for the shared three-state readers."""

from __future__ import annotations

import pytest
from hex_service_kit.netdefaults import ConfiguredEmptyError

from compliance_advisory.envread import boolean_setting


@pytest.mark.parametrize("value", ["false", "FALSE", "0", "no", "off"])
def test_boolean_setting_reads_false_literals_as_false(monkeypatch, value: str) -> None:
    monkeypatch.setenv("COMPLIANCE_API_RELOAD", value)
    assert boolean_setting("COMPLIANCE_API_RELOAD") is False


@pytest.mark.parametrize("value", ["true", "TRUE", "1", "yes", "on"])
def test_boolean_setting_reads_true_literals_as_true(monkeypatch, value: str) -> None:
    monkeypatch.setenv("COMPLIANCE_API_RELOAD", value)
    assert boolean_setting("COMPLIANCE_API_RELOAD") is True


def test_boolean_setting_distinguishes_unset_empty_and_invalid(monkeypatch) -> None:
    monkeypatch.delenv("COMPLIANCE_API_RELOAD", raising=False)
    assert boolean_setting("COMPLIANCE_API_RELOAD") is False
    monkeypatch.setenv("COMPLIANCE_API_RELOAD", "")
    with pytest.raises(ConfiguredEmptyError):
        boolean_setting("COMPLIANCE_API_RELOAD")
    monkeypatch.setenv("COMPLIANCE_API_RELOAD", "sometimes")
    with pytest.raises(ValueError, match="COMPLIANCE_API_RELOAD"):
        boolean_setting("COMPLIANCE_API_RELOAD")
