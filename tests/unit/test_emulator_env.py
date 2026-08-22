"""Three-state contract for optional local emulator selection."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from compliance_advisory.adapters.local._emulator import (
    firestore_emulator_host,
    pubsub_emulator_host,
    storage_emulator_host,
)
from compliance_advisory.envread import ConfiguredEmptyError

_HELPERS: tuple[tuple[Callable[[], str | None], str], ...] = (
    (firestore_emulator_host, "FIRESTORE_EMULATOR_HOST"),
    (pubsub_emulator_host, "PUBSUB_EMULATOR_HOST"),
    (storage_emulator_host, "STORAGE_EMULATOR_HOST"),
)


@pytest.mark.parametrize(("helper", "name"), _HELPERS)
def test_emulator_host_falls_back_only_when_unset(
    monkeypatch: pytest.MonkeyPatch, helper: Callable[[], str | None], name: str
) -> None:
    monkeypatch.delenv(name, raising=False)

    assert helper() is None


@pytest.mark.parametrize(("helper", "name"), _HELPERS)
@pytest.mark.parametrize("value", ["", " \t "])
def test_emulator_host_refuses_configured_empty(
    monkeypatch: pytest.MonkeyPatch,
    helper: Callable[[], str | None],
    name: str,
    value: str,
) -> None:
    monkeypatch.setenv(name, value)

    with pytest.raises(ConfiguredEmptyError, match=name):
        helper()


@pytest.mark.parametrize(("helper", "name"), _HELPERS)
def test_emulator_host_returns_trimmed_value(
    monkeypatch: pytest.MonkeyPatch, helper: Callable[[], str | None], name: str
) -> None:
    monkeypatch.setenv(name, " 127.0.0.1:8080 ")

    assert helper() == "127.0.0.1:8080"
