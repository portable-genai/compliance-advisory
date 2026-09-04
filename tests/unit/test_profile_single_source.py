"""The profile has ONE source of truth, and it fails closed on an unset variable.

Mirrors human-review-console (``human-review-console/tests/test_profile_single_source.py``) as the
standing gate for the absence-read-as-consent class. Guarding this fail-open in one module
while another keeps re-deriving the same decision with its own raw fallback is how a write
path stays open. A drift guard is therefore part of the defence: any module
that reads ``COMPLIANCE_PROFILE`` directly can reintroduce the whole class, so only
``config.resolve_profile`` may read it.

The three states this pins down are: absent (NO CHOICE), set but blank (refused), set to
something nothing binds (refused), and set to a known profile (carried through).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from hex_service_kit.netdefaults import ConfiguredEmptyError

from compliance_advisory.adapters.local.identity import (
    LocalPersonaIdentityAdapter,
    LocalPersonaProfileError,
)
from compliance_advisory.config import (
    NO_AUTH_PROFILES,
    RUNTIME_PROFILES,
    UNCONSENTED_PROFILE,
    LocalSettings,
    Settings,
    resolve_profile,
)

_SRC = Path(__file__).resolve().parents[2] / "src" / "compliance_advisory"
_CONFIG = _SRC / "config.py"


def _local_settings(**kwargs: object) -> Settings:
    return Settings(local=LocalSettings(db_path=":memory:", audit_path=":memory:"), **kwargs)  # type: ignore[arg-type]


def _python_sources() -> list[Path]:
    return sorted(p for p in _SRC.rglob("*.py") if p != _CONFIG)


def test_only_the_resolver_reads_the_profile_variable_from_the_environment() -> None:
    offenders = []
    for path in _python_sources():
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if re.search(r"(os\.environ|os\.getenv)[^\n]*PROFILE", line):
                offenders.append(f"{path.relative_to(_SRC)}:{number}: {line.strip()}")
    assert not offenders, (
        "these modules re-derive the profile instead of calling config.resolve_profile, "
        "so an unset COMPLIANCE_PROFILE can again be read as consent:\n" + "\n".join(offenders)
    )


def test_the_resolver_treats_an_absent_variable_as_no_choice() -> None:
    assert resolve_profile({}).explicit is False


@pytest.mark.parametrize("value", ["", "   "])
def test_the_resolver_refuses_a_configured_empty_profile(value: str) -> None:
    with pytest.raises(ConfiguredEmptyError, match="COMPLIANCE_PROFILE"):
        resolve_profile({"COMPLIANCE_PROFILE": value})


def test_an_unconsented_run_is_not_the_local_profile_for_any_relaxation() -> None:
    choice = resolve_profile({})
    assert choice.exposure_profile == UNCONSENTED_PROFILE
    assert choice.exposure_profile != "local"
    assert UNCONSENTED_PROFILE not in RUNTIME_PROFILES


def test_an_unconsented_run_still_binds_loopback() -> None:
    """The bind guard fails closed in the opposite direction: local is the restrictive case."""
    assert resolve_profile({}).bind_profile == "local"


def test_every_no_auth_profile_binds_loopback() -> None:
    """``live`` serves the same seeded personas as ``local``, so it is confined the same way."""
    for profile in NO_AUTH_PROFILES:
        assert resolve_profile({"COMPLIANCE_PROFILE": profile}).bind_profile == "local"
    assert resolve_profile({"COMPLIANCE_PROFILE": "gcp"}).bind_profile == "gcp"


def test_a_deliberate_profile_is_carried_through_unchanged() -> None:
    choice = resolve_profile({"COMPLIANCE_PROFILE": "gcp"})
    assert (choice.profile, choice.explicit) == ("gcp", True)
    assert choice.exposure_profile == "gcp"
    assert choice.bind_profile == "gcp"


def test_a_reviewed_settings_file_profile_counts_as_a_deliberate_choice() -> None:
    choice = resolve_profile({}, configured="gcp")
    assert (choice.profile, choice.explicit) == ("gcp", True)


def test_the_environment_wins_over_the_settings_file() -> None:
    choice = resolve_profile({"COMPLIANCE_PROFILE": "onprem"}, configured="gcp")
    assert choice.profile == "onprem"


@pytest.mark.parametrize("value", ["bogus", "Local", "GCP", "LOCAL", "on-prem"])
def test_an_unknown_or_mis_capitalised_profile_is_refused(value: str) -> None:
    """Exact, case-sensitive membership: a typo must not select a posture by accident."""
    with pytest.raises(ValueError, match="COMPLIANCE_PROFILE"):
        resolve_profile({"COMPLIANCE_PROFILE": value})


def test_settings_refuses_a_profile_nothing_binds() -> None:
    with pytest.raises(ValueError, match="COMPLIANCE_PROFILE"):
        Settings(profile="Local")


def test_seeded_personas_are_refused_when_nobody_chose_the_local_profile() -> None:
    """The concrete fail-open: an absent env var must not yield an unauthenticated approver."""
    with pytest.raises(LocalPersonaProfileError, match="COMPLIANCE_PROFILE"):
        LocalPersonaIdentityAdapter(_local_settings(profile="local", profile_explicit=False))


def test_seeded_personas_are_refused_outside_the_no_auth_profiles() -> None:
    with pytest.raises(LocalPersonaProfileError):
        LocalPersonaIdentityAdapter(_local_settings(profile="gcp", profile_explicit=True))


def test_seeded_personas_still_serve_a_deliberate_no_auth_run() -> None:
    for profile in sorted(NO_AUTH_PROFILES):
        settings = _local_settings(profile=profile, profile_explicit=True)
        assert LocalPersonaIdentityAdapter(settings).personas()
