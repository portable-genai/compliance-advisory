"""Configuration and the adapter factory (dependency injection for the hexagon).

The factory reads ``config/settings.yaml`` (with ``${ENV_VAR}`` interpolation) and binds
each port to a concrete adapter by dotted path. Switching the whole system from the GCP
managed stack to an on-prem stack is a one-line change of ``profile`` — proof of the
ports-and-adapters / no-lock-in principle (P-02). Every adapter follows one construction
convention: ``Adapter(settings: Settings)``.
"""

from __future__ import annotations

import importlib
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Any

import yaml
from hex_service_kit.netdefaults import ConfiguredEmptyError, EnvSetting, read_env_setting

from .domain.horizon.policy import HorizonPolicyConfig
from .domain.policy import CompliancePolicyConfig
from .envread import setting_or_default
from .ports.identity import CLIENT_ASSERTED, declared_end_user_auth

_ENV_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)(?::-(.*?))?\}")

_PROFILE_ENV = "COMPLIANCE_PROFILE"

#: Every profile the container binds an adapter family for. Membership is exact and
#: case-sensitive: every posture decision downstream compares the profile string exactly, so
#: ``Local`` would select none of the relaxations but also none of the restrictions.
#: Normalising the case here would turn a typo into a silent choice; refusing it turns the
#: typo into a construction failure.
RUNTIME_PROFILES = frozenset({"local", "live", "gcp", "platform", "onprem"})

#: The profiles that serve seeded dev personas: an UNAUTHENTICATED grant of the
#: compliance-approver entitlement, with no IdP anywhere in the request path. ``live`` swaps
#: only the generator and the corpus for real ones, so it authenticates exactly as little as
#: ``local`` does and must be confined the same way.
NO_AUTH_PROFILES = frozenset({"local", "live"})

#: The profile string handed to every relaxation when ``COMPLIANCE_PROFILE`` was never set.
#: It is deliberately NOT a member of :data:`RUNTIME_PROFILES` and never reaches
#: :class:`Settings`: it exists so that "no choice was made" is a distinct input to the
#: security layers rather than being indistinguishable from a chosen ``local``.
UNCONSENTED_PROFILE = "unconfigured"


def _validate_profile(profile: str) -> str:
    """Fail closed on a profile string nothing binds, INCLUDING a capitalisation typo."""
    if profile not in RUNTIME_PROFILES:
        expected = ", ".join(sorted(RUNTIME_PROFILES))
        raise ValueError(f"unknown {_PROFILE_ENV} {profile!r}; expected one of: {expected}")
    return profile


@dataclass(frozen=True, slots=True)
class ProfileChoice:
    """The ONE resolution of ``COMPLIANCE_PROFILE``, and what each consumer keys off.

    Every module that needs the profile goes through :func:`resolve_profile` (or the
    :class:`Settings` properties derived from it). No module may re-derive it with its own
    ``os.environ.get("COMPLIANCE_PROFILE", "local")``: that fallback reads an UNSET variable
    as consent, which is the fail-open this type exists to remove
    (``tests/unit/test_profile_single_source.py`` fails the build if one reappears).

    The two derived profile strings differ because the two decisions fail closed in OPPOSITE
    directions, so a single "effective profile" string would harden one and weaken the other.
    """

    #: Which adapter family to bind. Absent consent this is still ``local`` (the SDK-free
    #: adapters), because the alternative would import cloud SDKs that are not installed; the
    #: local IDENTITY adapter refuses to construct when :attr:`explicit` is False, so an
    #: unconsented run has data adapters but no end-user identity.
    profile: str = "local"
    #: Was the profile named DELIBERATELY (env var, or a reviewed ``profile:`` in settings)?
    explicit: bool = True

    @property
    def exposure_profile(self) -> str:
        """The profile every *relaxation* keys off: the CORS dev-origin fallback.

        That decision grants something extra to ``local``, so an unconsented run must NOT
        look like ``local``: it gets :data:`UNCONSENTED_PROFILE`, which is no origin's
        allowlist.
        """
        return self.profile if self.explicit else UNCONSENTED_PROFILE

    @property
    def bind_profile(self) -> str:
        """The profile the bind guard keys off, where ``local`` is the RESTRICTIVE case.

        ``resolve_bind_host`` confines ``local`` to loopback and lets fronted profiles take
        ``0.0.0.0``, so this fails closed in the opposite direction to
        :attr:`exposure_profile`: an unconsented run must look like ``local`` and stay on
        loopback. Every no-auth profile collapses onto ``local`` here for the same reason,
        since ``live`` serves the same seeded personas and the kit's guard compares one string.
        """
        if not self.explicit or self.profile in NO_AUTH_PROFILES:
            return "local"
        return self.profile


def _profile_setting(environ: Mapping[str, str] | None) -> EnvSetting:
    """Return the single profile choice with absent and configured-empty kept distinct."""
    if environ is None:
        return read_env_setting(_PROFILE_ENV)
    raw = environ.get(_PROFILE_ENV)
    return EnvSetting(name=_PROFILE_ENV, raw=raw, value="" if raw is None else raw.strip())


def resolve_profile(
    environ: Mapping[str, str] | None = None, *, configured: str = ""
) -> ProfileChoice:
    """Read ``COMPLIANCE_PROFILE`` once, treating absent/blank as NO CHOICE, not ``local``.

    Three states, where unset is not a member of the valid set: the variable is absent (no
    choice), present but blank (refused), or names a value. An unknown value is also refused.
    ``configured`` is
    the reviewed ``profile:`` key from ``config/settings.yaml``, which counts as a deliberate
    choice because a human wrote it into a reviewed file; the environment still wins over it.
    """
    setting = _profile_setting(environ)
    if setting.is_configured_empty:
        raise ConfiguredEmptyError(
            f"{_PROFILE_ENV} is set to an empty value, which is not a profile. Unset it to "
            "leave the choice to settings.yaml, or set one of "
            f"{', '.join(sorted(RUNTIME_PROFILES))}."
        )
    raw = setting.value if setting.has_value else (configured or "").strip()
    if raw:
        _validate_profile(raw)
    return ProfileChoice(profile=raw or "local", explicit=bool(raw))


def _interpolate(value: Any) -> Any:
    """Interpolate settings while keeping absent and configured-empty distinct."""
    if isinstance(value, str):

        def repl(m: re.Match[str]) -> str:
            setting = read_env_setting(m.group(1))
            if setting.is_configured_empty:
                raise ConfiguredEmptyError(
                    f"{m.group(1)} is set to an empty value; unset it to inherit the reviewed "
                    "settings default, or give it a value"
                )
            return (m.group(2) or "") if setting.is_unset else setting.value

        return _ENV_PATTERN.sub(repl, value)
    if isinstance(value, dict):
        return {k: _interpolate(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_interpolate(v) for v in value]
    return value


@dataclass(frozen=True)
class ModelSettings:
    #: The Vertex location the model client calls, NOT the compute region. Gemini 3
    #: serves the `us` and `eu` multi-regions only; `global` carries no residency
    #: guarantee. See models.location in config/settings.yaml.
    location: str = "us"
    reasoning: str = "gemini-3.5-flash"
    triage: str = "gemini-3.5-flash"
    hard_reasoning: str = "gemini-3.5-flash"  # Preview — feature-flagged off by default
    use_hard_reasoning: bool = False


@dataclass(frozen=True)
class AgentSearchSettings:
    data_store_id: str = "compliance-reg-kb"
    location: str = "asia-southeast1"
    serving_config: str = "default_search"
    engine_id: str = "compliance-advisory-engine"


@dataclass(frozen=True)
class AlloyDBSettings:
    instance_uri: str = ""  # projects/.../locations/.../clusters/.../instances/...
    database: str = "compliance"
    user: str = "compliance_app"
    ip_type: str = "PRIVATE"
    table: str = "corpus_freshness"
    horizon_table: str = "horizon_tracking"


@dataclass(frozen=True)
class ModelArmorSettings:
    template_id: str = "compliance-guardrail"
    host: str = "modelarmor.asia-southeast1.rep.googleapis.com"


@dataclass(frozen=True)
class DlpSettings:
    inspect_template: str = ""  # projects/.../inspectTemplates/...
    deidentify_template: str = ""  # projects/.../deidentifyTemplates/...


@dataclass(frozen=True)
class PostureSettings:
    """Live control-posture sources for the control-mapping capability:
    Security Command Center + Cloud Asset Inventory + Assured Workloads."""

    scc_parent: str = ""  # organizations/<org_id> | projects/<project_id>
    asset_scope: str = ""  # projects/<project_id> | organizations/<org_id>
    assured_workload: str = ""  # the Assured Workloads folder/workload resource name


@dataclass(frozen=True)
class LoggingSettings:
    log_name: str = "compliance-advisory-audit"
    bucket: str = "compliance-advisory-worm"
    retention_days: int = 2557  # ~7 years


@dataclass(frozen=True)
class AgentEngineSettings:
    resource_name: str = ""  # reasoningEngine resource id, set after deploy
    display_name: str = "compliance-advisory"


@dataclass(frozen=True)
class CorpusSettings:
    ttl_days: int = 7
    registry_path: str = "src/compliance_advisory/pipelines/sources/registry.yaml"


@dataclass(frozen=True)
class LocalSettings:
    """Paths for the SDK-free ``local`` profile stores (SQLite FTS5 + append-only audit).

    Empty strings select the per-package default under ``~/.compliance_advisory/``;
    tests pass ``:memory:`` for ephemeral, deterministic stores. No Google Cloud here.
    """

    db_path: str = ""  # SQLite FTS5 retrieval index; "" => ~/.compliance_advisory/local.db
    audit_path: str = ""  # append-only audit store;   "" => ~/.compliance_advisory/audit.db
    ledger_path: str = ""  # freshness ledger;          "" => ~/.compliance_advisory/ledger.db
    horizon_path: str = ""  # horizon tracking store;   "" => ~/.compliance_advisory/horizon.db


@dataclass(frozen=True)
class LiveSettings:
    """The ``live`` profile's local model server (real inference on this machine).

    Points at any OpenAI-compatible ``/chat/completions`` endpoint (MLX, Ollama, vLLM,
    llama.cpp). The live profile pairs this generator with the REAL regulatory corpus
    ingested by ``pipelines.refresh_job``; the deterministic local LLM and the fictional
    seed passages never appear under it.
    """

    llm_url: str = "http://127.0.0.1:8001/chat/completions"
    llm_model: str = "mlx-community/gemma-4-26b-a4b-it-8bit"
    timeout_seconds: float = 240.0
    max_output_tokens: int = 2048


def _live_settings(raw: dict[str, Any]) -> LiveSettings:
    """Build LiveSettings with numeric coercion (env interpolation yields strings)."""
    if "timeout_seconds" in raw:
        raw["timeout_seconds"] = float(raw["timeout_seconds"])
    if "max_output_tokens" in raw:
        raw["max_output_tokens"] = int(raw["max_output_tokens"])
    return LiveSettings(**raw)


# Horizon-scanning policy numbers are BANK-OWNED (B4): the dataclass defaults in
# ``domain.horizon.policy`` are the reference policy, and ``config/settings.yaml`` overrides
# them per adopter. Reusing :class:`HorizonPolicyConfig` as the settings type keeps ONE home
# for those numbers instead of a settings mirror that can drift from the engine.
_HORIZON_TUPLE_FIELDS = (
    "in_scope_regulators",
    "in_scope_jurisdictions",
    "in_scope_topics",
)
_HORIZON_INT_MAP_FIELDS = (
    "change_kind_weights",
    "doc_type_weights",
    "band_thresholds",
    "sla_days",
)
_HORIZON_INT_FIELDS = (
    "topic_points",
    "max_topic_points",
    "open_gap_points",
    "max_open_gap_points",
    "conditional_penalty",
)


def _horizon_settings(raw: dict[str, Any]) -> HorizonPolicyConfig:
    """Build the horizon policy config from YAML, coercing types.

    ``${ENV}`` interpolation yields strings and YAML yields lists, so numeric and tuple
    fields are coerced here rather than trusting the file's shape. An unknown key is a
    configuration error and raises, so a typo in a bank's policy override fails loudly
    instead of silently leaving the reference number in force.
    """
    coerced: dict[str, Any] = {}
    for key, value in raw.items():
        if key in _HORIZON_TUPLE_FIELDS:
            coerced[key] = tuple(str(v) for v in (value or ()))
        elif key in _HORIZON_INT_MAP_FIELDS:
            coerced[key] = {str(k): int(v) for k, v in (value or {}).items()}
        elif key in _HORIZON_INT_FIELDS:
            coerced[key] = int(value)
        else:
            coerced[key] = value
    return HorizonPolicyConfig(**coerced)


def _policy_settings(raw: dict[str, Any]) -> CompliancePolicyConfig:
    """Build the assistant policy and coerce YAML lists to immutable tuples."""
    allowed = {
        "answer_confidence_floor",
        "review_all_answers",
        "always_review_artifacts",
        "high_severities",
        "high_risk_topics",
    }
    unknown = set(raw) - allowed
    if unknown:
        raise ValueError(f"unknown policy settings: {sorted(unknown)}")
    defaults = CompliancePolicyConfig()
    review_raw = raw.get("review_all_answers", defaults.review_all_answers)
    if not isinstance(review_raw, bool):
        raise ValueError("policy.review_all_answers must be true or false")
    return CompliancePolicyConfig(
        answer_confidence_floor=float(
            raw.get("answer_confidence_floor", defaults.answer_confidence_floor)
        ),
        review_all_answers=review_raw,
        always_review_artifacts=tuple(
            str(item)
            for item in raw.get("always_review_artifacts", defaults.always_review_artifacts)
        ),
        high_severities=tuple(
            str(item) for item in raw.get("high_severities", defaults.high_severities)
        ),
        high_risk_topics=tuple(
            str(item) for item in raw.get("high_risk_topics", defaults.high_risk_topics)
        ),
    )


@dataclass(frozen=True)
class Settings:
    project_id: str = "your-gcp-project"
    region: str = "asia-southeast1"
    profile: str = "local"  # gcp | local | live | platform | onprem (local = default when unset)
    kms_key: str = ""  # projects/.../cryptoKeys/... (regional)
    grounding_enabled: bool = False
    models: ModelSettings = field(default_factory=ModelSettings)
    agent_search: AgentSearchSettings = field(default_factory=AgentSearchSettings)
    alloydb: AlloyDBSettings = field(default_factory=AlloyDBSettings)
    model_armor: ModelArmorSettings = field(default_factory=ModelArmorSettings)
    dlp: DlpSettings = field(default_factory=DlpSettings)
    posture: PostureSettings = field(default_factory=PostureSettings)
    logging: LoggingSettings = field(default_factory=LoggingSettings)
    agent_engine: AgentEngineSettings = field(default_factory=AgentEngineSettings)
    corpus: CorpusSettings = field(default_factory=CorpusSettings)
    local: LocalSettings = field(default_factory=LocalSettings)
    live: LiveSettings = field(default_factory=LiveSettings)
    horizon: HorizonPolicyConfig = field(default_factory=HorizonPolicyConfig)
    policy: CompliancePolicyConfig = field(default_factory=CompliancePolicyConfig)
    # Was the profile chosen DELIBERATELY, or merely inherited from the fallback? ``load``
    # sets this False when COMPLIANCE_PROFILE is absent AND settings.yaml names no profile.
    # Direct construction is deliberate by definition (a caller named the profile in code), so
    # the default is True. The seeded-persona identity adapter refuses to construct when this
    # is False: a compliance assistant must never hand out an approver persona, with no
    # authentication at all, because an env var went missing.
    profile_explicit: bool = True
    # port_name -> { profile -> "module.path:ClassName" }
    adapters: dict[str, dict[str, str]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_profile(self.profile)

    @property
    def exposure_profile(self) -> str:
        """The profile every relaxation keys off (see :meth:`ProfileChoice.exposure_profile`)."""
        return ProfileChoice(self.profile, self.profile_explicit).exposure_profile

    @property
    def bind_profile(self) -> str:
        """The profile the bind guard keys off (see :meth:`ProfileChoice.bind_profile`)."""
        return ProfileChoice(self.profile, self.profile_explicit).bind_profile

    @staticmethod
    def load(path: str | os.PathLike[str] | None = None) -> Settings:
        path = Path(path or setting_or_default("COMPLIANCE_SETTINGS", "config/settings.yaml"))
        raw = _interpolate(yaml.safe_load(path.read_text())) if path.exists() else {}
        raw = raw or {}
        nested: dict[str, Any] = {
            "models": ModelSettings(**(raw.pop("models", {}) or {})),
            "agent_search": AgentSearchSettings(**(raw.pop("agent_search", {}) or {})),
            "alloydb": AlloyDBSettings(**(raw.pop("alloydb", {}) or {})),
            "model_armor": ModelArmorSettings(**(raw.pop("model_armor", {}) or {})),
            "dlp": DlpSettings(**(raw.pop("dlp", {}) or {})),
            "posture": PostureSettings(**(raw.pop("posture", {}) or {})),
            "logging": LoggingSettings(**(raw.pop("logging", {}) or {})),
            "agent_engine": AgentEngineSettings(**(raw.pop("agent_engine", {}) or {})),
            "corpus": CorpusSettings(**(raw.pop("corpus", {}) or {})),
            "local": LocalSettings(**(raw.pop("local", {}) or {})),
            "live": _live_settings(raw.pop("live", {}) or {}),
            "horizon": _horizon_settings(raw.pop("horizon", {}) or {}),
            "policy": _policy_settings(raw.pop("policy", {}) or {}),
        }
        # Three-state resolution: unset/blank is NO CHOICE, not ``local``. ``profile`` and
        # ``profile_explicit`` come only from here, so a settings file cannot assert consent
        # on the operator's behalf by writing ``profile_explicit: true``.
        choice = resolve_profile(configured=str(raw.pop("profile", "") or ""))
        reserved = {"profile", "profile_explicit"}
        known = {f for f in Settings.__dataclass_fields__ if f not in nested and f not in reserved}
        flat: dict[str, Any] = {k: v for k, v in raw.items() if k in known}
        return Settings(profile=choice.profile, profile_explicit=choice.explicit, **flat, **nested)


def instantiate(dotted: str, settings: Settings) -> Any:
    """Import ``module.path:ClassName`` and construct it with ``settings``."""
    module_path, _, class_name = dotted.partition(":")
    module = importlib.import_module(module_path)
    cls = getattr(module, class_name)
    return cls(settings)


class Container:
    """Lazily-built registry of port -> adapter instances.

    Adapters are imported only on first access so that, e.g., a unit test using the
    on-prem profile never needs the Google Cloud SDKs installed.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def _bind(self, port_name: str) -> Any:
        binding = self.settings.adapters.get(port_name, {})
        dotted = binding.get(self.settings.profile)
        if not dotted:
            raise KeyError(
                f"No adapter configured for port '{port_name}' "
                f"under profile '{self.settings.profile}'."
            )
        return instantiate(dotted, self.settings)

    # One cached_property per port keeps wiring declarative and type-greppable.
    @cached_property
    def retrieval(self) -> Any:
        return self._bind("retrieval")

    @cached_property
    def requirement_source(self) -> Any:
        # Control-mapping reg KB. Binds in-process to the shared retrieval port (one reg KB).
        return self._bind("requirement_source")

    @cached_property
    def control_inventory(self) -> Any:
        # Live GCP control posture (SCC + Asset Inventory + Assured Workloads).
        return self._bind("control_inventory")

    @cached_property
    def llm(self) -> Any:
        return self._bind("llm")

    @cached_property
    def grounding(self) -> Any:
        return self._bind("grounding")

    @cached_property
    def guardrail(self) -> Any:
        return self._bind("guardrail")

    @cached_property
    def redaction(self) -> Any:
        return self._bind("redaction")

    @cached_property
    def agent_runtime(self) -> Any:
        return self._bind("agent_runtime")

    @cached_property
    def session(self) -> Any:
        return self._bind("session")

    @cached_property
    def memory(self) -> Any:
        return self._bind("memory")

    @cached_property
    def audit(self) -> Any:
        return self._bind("audit")

    @cached_property
    def tracer(self) -> Any:
        return self._bind("tracer")

    @cached_property
    def evaluation(self) -> Any:
        return self._bind("evaluation")

    @cached_property
    def registry(self) -> Any:
        return self._bind("registry")

    @cached_property
    def tool_catalog(self) -> Any:
        return self._bind("tool_catalog")

    @cached_property
    def ledger(self) -> Any:
        return self._bind("ledger")

    @cached_property
    def ingestion(self) -> Any:
        return self._bind("ingestion")

    @cached_property
    def identity(self) -> Any:
        return self._bind("identity")

    @cached_property
    def review_router(self) -> Any:
        return self._bind("review_router")

    @cached_property
    def source_catalog(self) -> Any:
        # Regulatory source registry metadata behind each freshness-ledger row (horizon).
        return self._bind("source_catalog")

    @cached_property
    def horizon_tracker(self) -> Any:
        # Implementation journey for each assessed regulatory change (horizon).
        return self._bind("horizon_tracker")


def build_container(settings: Settings | None = None) -> Container:
    return Container(settings or Settings.load())


def identity_adapter_class(settings: Settings) -> type:
    """The identity adapter CLASS the active binding names, resolved WITHOUT constructing it.

    Reads the same ``adapters:`` table the container binds from, so a deployment that rebound
    the identity port in ``config/settings.yaml`` (the documented on-premises path: swap the
    placeholder for the client's own IdP adapter) is answered about the adapter it ACTUALLY
    runs, not about the one the profile name suggests.

    Constructing is deliberately avoided: the seeded-persona adapter refuses to construct under
    an inherited profile, so a posture computed from an instance would be unobtainable in one
    of the exact cases it has to describe.
    """
    target = settings.adapters["identity"][settings.profile]
    module_path, _, class_name = target.partition(":")
    resolved = getattr(importlib.import_module(module_path), class_name)
    if not isinstance(resolved, type):
        raise TypeError(f"identity binding {target!r} does not name a class")
    return resolved


def end_user_auth_kind(settings: Settings | None = None) -> str:
    """What the BOUND identity adapter declares it does for end-user authentication.

    This is the one question "are this service's end-user routes authenticated?" reduces to.
    See ``ports/identity.py``: the profile string cannot answer it, because ``onprem`` names a
    placeholder today and a real IdP once a client rebinds it.

    Any failure to establish the answer resolves to ``CLIENT_ASSERTED``. A guard that switches
    OFF because a lookup raised is a guard that fails open, and nothing is lost by failing
    closed here: the same failure surfaces loudly at the first request, when the container
    resolves the identical binding for real.
    """
    try:
        return declared_end_user_auth(identity_adapter_class(settings or Settings.load()))
    except Exception:
        return CLIENT_ASSERTED
