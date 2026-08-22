"""Optional Google emulator detection for the ``local`` profile (opt-in, never required).

For the stores that have an official Google emulator, the local adapters can route to
it for higher-fidelity local development WHEN the standard emulator env var is set AND
the matching client library (from the ``[gcp]`` extra) imports. Otherwise the adapters
use their SDK-free SQLite / in-process path, which is the default.

This module only *detects* the opt-in; it deliberately performs **no google-cloud
import at module top level**. Each adapter that supports an emulator imports the google
client lazily, inside the method, and only on the emulator branch, so the default local
path and the offline test suite never import a google-cloud package.

There is no emulator for Agent Search, Gemini, Model Armor, DLP or Document AI, so those
adapters stay on the SDK-free workaround unconditionally.
"""

from __future__ import annotations

from ...envread import ConfiguredEmptyError, read_env_setting

#: Standard emulator host env vars, by logical backend.
FIRESTORE_EMULATOR_ENV = "FIRESTORE_EMULATOR_HOST"
PUBSUB_EMULATOR_ENV = "PUBSUB_EMULATOR_HOST"
STORAGE_EMULATOR_ENV = "STORAGE_EMULATOR_HOST"


def _emulator_host(name: str) -> str | None:
    """Resolve an opt-in without confusing absence with an explicit empty value."""
    setting = read_env_setting(name)
    if setting.is_unset:
        return None
    if setting.is_configured_empty:
        raise ConfiguredEmptyError(
            f"{name} is set to an empty value; unset it for the SDK-free local fallback "
            "or configure an emulator host"
        )
    return setting.value


def firestore_emulator_host() -> str | None:
    """Resolve the optional Firestore emulator host using the three-state contract."""
    return _emulator_host(FIRESTORE_EMULATOR_ENV)


def pubsub_emulator_host() -> str | None:
    """Resolve the optional Pub/Sub emulator host using the three-state contract."""
    return _emulator_host(PUBSUB_EMULATOR_ENV)


def storage_emulator_host() -> str | None:
    """Resolve the optional Cloud Storage emulator host using the three-state contract."""
    return _emulator_host(STORAGE_EMULATOR_ENV)


def firestore_client_available() -> bool:
    """Whether ``google-cloud-firestore`` is importable (the ``[gcp]`` extra is installed).

    The import is attempted lazily here (not at module top level) so that the default
    SDK-free local path never imports a google-cloud package.
    """
    try:
        import google.cloud.firestore  # noqa: F401  (lazy availability probe only)
    except Exception:  # noqa: BLE001 - any import failure means the emulator path is off
        return False
    return True


def firestore_emulator_active() -> bool:
    """True only when both the emulator env var is set AND the client lib imports."""
    return firestore_emulator_host() is not None and firestore_client_available()
