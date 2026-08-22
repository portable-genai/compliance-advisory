"""Contract tests: the ``onprem`` and ``local`` adapters are structural parity of the ports.

For every port the catalog declares, this iterates the adapter map and, for both the
``onprem`` and ``local`` profiles, imports + constructs the bound class (which must build
cleanly with **no Google Cloud SDK** installed), then asserts:

  1. the constructed instance satisfies its runtime_checkable Protocol (isinstance), and
  2. every method/property the Protocol declares actually exists on the instance.

It additionally proves the two profiles' distinct contracts:

* ``onprem`` is the fail-fast Google Distributed Cloud migration target: every method
  raises ``NotImplementedError`` (proven on a representative port), and
* ``local`` is a WORKING offline stack: the same ports construct and answer in-process.

This is the proof of the ports-and-adapters / no-lock-in promise (P-02): the on-prem
migration target and the offline local stack implement the exact same interface as the
managed GCP stack.
"""

from __future__ import annotations

from typing import Protocol, get_type_hints

import pytest

from compliance_advisory import config, ports
from compliance_advisory.config import Settings, instantiate

CONFIG_PATH = "config/settings.yaml"

# Every port name in settings.adapters mapped to its Protocol.
PORT_PROTOCOLS: dict[str, type] = {
    "requirement_source": ports.RequirementSourcePort,
    "control_inventory": ports.ControlInventoryPort,
    "retrieval": ports.RetrievalPort,
    "llm": ports.LLMPort,
    "grounding": ports.GroundingPort,
    "guardrail": ports.GuardrailPort,
    "redaction": ports.PIIRedactionPort,
    "agent_runtime": ports.AgentRuntimePort,
    "session": ports.SessionPort,
    "memory": ports.MemoryPort,
    "audit": ports.AuditSinkPort,
    "tracer": ports.ObservabilityTracerPort,
    "evaluation": ports.EvaluationGatePort,
    "registry": ports.AgentRegistryPort,
    "tool_catalog": ports.ToolCatalogPort,
    "ledger": ports.CorpusLedgerPort,
    "ingestion": ports.CorpusIngestionPort,
    "identity": ports.IdentityPort,
    "review_router": ports.ReviewRouterPort,
    "source_catalog": ports.RegSourceCatalogPort,
    "horizon_tracker": ports.HorizonTrackerPort,
}

# Profiles whose adapters must construct + satisfy the Protocols with no GCP SDK.
# ``live`` is SDK-free too: a local model server over httpx plus the real ingested
# corpus, so an unbound live port would silently fall back to a managed GCP adapter.
SDK_FREE_PROFILES = ("onprem", "local", "live")


def _settings(profile: str) -> Settings:
    base = Settings.load(CONFIG_PATH)
    # Point the local stores at in-memory SQLite so the contract test stays ephemeral.
    from compliance_advisory.config import LocalSettings

    return Settings(
        project_id=base.project_id,
        region=base.region,
        profile=profile,
        kms_key=base.kms_key,
        grounding_enabled=base.grounding_enabled,
        models=base.models,
        agent_search=base.agent_search,
        alloydb=base.alloydb,
        model_armor=base.model_armor,
        dlp=base.dlp,
        logging=base.logging,
        agent_engine=base.agent_engine,
        corpus=base.corpus,
        horizon=base.horizon,
        local=LocalSettings(
            db_path=":memory:",
            audit_path=":memory:",
            ledger_path=":memory:",
            horizon_path=":memory:",
        ),
        adapters=base.adapters,
    )


def _protocol_members(protocol: type) -> set[str]:
    """The attribute names a Protocol declares (methods + properties), no dunders."""
    members = set(getattr(protocol, "__protocol_attrs__", set()))
    if not members:
        # Fallback for older typing internals: union of annotations + callables.
        members |= set(get_type_hints(protocol).keys())
        for name in dir(protocol):
            if name.startswith("_"):
                continue
            members.add(name)
    return {m for m in members if not m.startswith("_")}


def test_every_port_has_an_explicit_binding_for_every_profile():
    settings = Settings.load(CONFIG_PATH)
    for port_name in PORT_PROTOCOLS:
        binding = settings.adapters.get(port_name, {})
        missing = set(config.RUNTIME_PROFILES) - set(binding)
        assert not missing, f"port '{port_name}' has no explicit bindings for {sorted(missing)}"


def test_port_protocols_matches_settings_adapters():
    """Drift guard: the adapter map and this suite's port table must be the SAME set.

    The forward direction (every declared port has a binding) is covered above. This is the
    REVERSE direction: a port added to ``settings.adapters`` but never registered here would
    otherwise ship with no contract coverage at all, silently. Adding a binding must fail
    loudly until its Protocol is declared.
    """
    settings = Settings.load(CONFIG_PATH)
    assert set(settings.adapters) == set(PORT_PROTOCOLS), (
        "settings.adapters and PORT_PROTOCOLS have drifted: "
        f"unregistered bindings={sorted(set(settings.adapters) - set(PORT_PROTOCOLS))}, "
        f"missing bindings={sorted(set(PORT_PROTOCOLS) - set(settings.adapters))}"
    )


@pytest.mark.parametrize("profile", SDK_FREE_PROFILES)
@pytest.mark.parametrize("port_name", sorted(PORT_PROTOCOLS))
def test_adapter_satisfies_protocol(profile: str, port_name: str):
    settings = _settings(profile)
    protocol = PORT_PROTOCOLS[port_name]
    dotted = settings.adapters[port_name][profile]

    # Import + construct with only Settings (the adapter convention), no GCP SDK.
    adapter = instantiate(dotted, settings)

    # 1. Structural conformance via runtime_checkable Protocol.
    assert isinstance(adapter, protocol), (
        f"{dotted} does not structurally satisfy {protocol.__name__}"
    )

    # 2. Every declared Protocol member exists. Check on the *class* (via the MRO), not
    #    the instance: a placeholder property getter may raise, so ``hasattr`` would
    #    wrongly report it missing. Looking the name up on the type tests for declaration
    #    without invoking the getter.
    members = _protocol_members(protocol)
    declared = set().union(*(vars(klass) for klass in type(adapter).__mro__))
    for member in members:
        assert member in declared, (
            f"{dotted} is missing port method/attr '{member}' of {protocol.__name__}"
        )


@pytest.mark.parametrize("profile", SDK_FREE_PROFILES)
@pytest.mark.parametrize("port_name", sorted(PORT_PROTOCOLS))
def test_adapter_constructs_with_single_settings_arg(profile: str, port_name: str):
    """The build contract: every adapter is ``Adapter(settings: Settings)``."""
    settings = _settings(profile)
    dotted = settings.adapters[port_name][profile]
    module_path, _, class_name = dotted.partition(":")
    import importlib

    cls = getattr(importlib.import_module(module_path), class_name)
    # Must accept exactly one positional Settings argument and build cleanly.
    instance = cls(settings)
    assert instance is not None


def test_onprem_retrieval_fails_fast():
    """The on-prem stubs are fail-fast: a representative port raises NotImplementedError."""
    settings = _settings("onprem")
    adapter = instantiate(settings.adapters["retrieval"]["onprem"], settings)
    from compliance_advisory.domain.models import RetrievalQuery

    with pytest.raises(NotImplementedError):
        adapter.retrieve(RetrievalQuery(text="anything"))


def test_local_retrieval_returns_real_passages():
    """The local stack is WORKING: retrieval returns real, page-cited passages offline."""
    settings = _settings("local")
    adapter = instantiate(settings.adapters["retrieval"]["local"], settings)
    from compliance_advisory.domain.models import RetrievalQuery

    passages = adapter.retrieve(RetrievalQuery(text="cloud outsourcing due diligence", top_k=5))
    assert passages, "local FTS5 retrieval returned nothing for the seeded corpus"
    assert all(p.citation.page is not None for p in passages), "page-level citation required"


def test_onprem_inventory_fails_fast():
    """The on-prem control-inventory stub is fail-fast: ``observe`` raises NotImplementedError."""
    settings = _settings("onprem")
    adapter = instantiate(settings.adapters["control_inventory"]["onprem"], settings)

    with pytest.raises(NotImplementedError):
        adapter.observe("projects/anything")


def test_onprem_requirement_source_fails_fast():
    """The on-prem requirement source inherits the retrieval placeholder's fail-fast contract."""
    settings = _settings("onprem")
    adapter = instantiate(settings.adapters["requirement_source"]["onprem"], settings)

    with pytest.raises(NotImplementedError):
        adapter.fetch("projects/anything")


def test_local_requirement_source_returns_real_requirements():
    """The local stack is WORKING: the shared reg-KB source returns real, page-cited obligations."""
    settings = _settings("local")
    adapter = instantiate(settings.adapters["requirement_source"]["local"], settings)

    requirements = adapter.fetch("projects/acme-sg-prod")
    assert requirements, "local reg KB (retrieval-backed) returned nothing for the seeded corpus"
    assert all(r.citation.page is not None for r in requirements), "page-level citation required"


def test_local_inventory_returns_real_posture():
    """The local stack is WORKING: the control inventory returns a real observed posture."""
    settings = _settings("local")
    adapter = instantiate(settings.adapters["control_inventory"]["local"], settings)

    observations = adapter.observe("projects/acme-sg-prod")
    assert observations, "local control inventory returned no posture for the seeded scope"
    assert adapter.list_controls(), "local control inventory returned no control catalog"


def test_onprem_horizon_tracker_fails_fast():
    """The on-prem tracker stub is fail-fast: a tracked change never silently disappears."""
    settings = _settings("onprem")
    adapter = instantiate(settings.adapters["horizon_tracker"]["onprem"], settings)

    with pytest.raises(NotImplementedError):
        adapter.list("demo-bank")
    with pytest.raises(NotImplementedError):
        adapter.get("any-change")


def test_source_catalog_reads_the_registry_under_every_sdk_free_profile():
    """One catalog class serves every profile, so the horizon diff is profile-independent."""
    seen = {}
    for profile in SDK_FREE_PROFILES:
        settings = _settings(profile)
        adapter = instantiate(settings.adapters["source_catalog"][profile], settings)
        sources = adapter.sources()
        assert sources, f"source catalog returned nothing under profile {profile}"
        assert all(s.regulator and s.jurisdiction for s in sources)
        seen[profile] = [s.id for s in sources]
    assert len(set(map(tuple, seen.values()))) == 1, "the registry must not vary by profile"


def test_local_horizon_tracker_round_trips_a_tracked_change():
    """The local stack is WORKING: a tracked change persists and lists by tenant."""
    from compliance_advisory.domain.horizon.models import (
        ImplementationItem,
        ImplementationStatus,
    )

    settings = _settings("local")
    adapter = instantiate(settings.adapters["horizon_tracker"]["local"], settings)
    item = ImplementationItem(
        change_id="mas-trm:content_revised:abc",
        tenant="demo-bank",
        source_id="mas-trm",
        status=ImplementationStatus.IN_PROGRESS,
        owner="ciso-office",
    )
    adapter.upsert(item)

    assert adapter.get(item.change_id) is not None
    assert [i.change_id for i in adapter.list("demo-bank")] == [item.change_id]
    # Tenant-scoped listing: another tenant sees nothing.
    assert adapter.list("other-bank") == []


def test_all_protocols_are_runtime_checkable():
    for protocol in PORT_PROTOCOLS.values():
        assert issubclass(protocol, Protocol)  # type: ignore[arg-type]
        assert getattr(protocol, "_is_runtime_protocol", False), (
            f"{protocol.__name__} must be @runtime_checkable"
        )


def test_the_shared_types_are_the_COMMONS_OBJECTS_not_look_alike_copies():
    """Object identity, because structural conformance cannot see a hand-copy.

    Every other check in this file is structural, and structural checks are exactly what let
    sixteen repositories drift: ``isinstance`` against a ``runtime_checkable`` Protocol passes
    for a byte-identical copy, and it keeps passing while the copy slowly stops being identical.
    ``is`` does not. Redeclare any of these locally and this test fails immediately, which is the
    only mechanism that makes "do not hand-copy the commons" enforceable rather than advisory.
    """
    import agent_eval_kit
    import agent_eval_kit.report
    import hex_service_kit.observability

    from compliance_advisory.domain import models

    assert ports.ObservabilityTracerPort is hex_service_kit.observability.ObservabilityTracerPort
    assert ports.TokenUsage is hex_service_kit.observability.TokenUsage
    assert ports.EvaluationGatePort is agent_eval_kit.EvaluationGatePort
    assert models.TokenUsage is hex_service_kit.observability.TokenUsage
    assert models.EvalReport is agent_eval_kit.report.EvalReport
    assert models.EvalMetricResult is agent_eval_kit.report.EvalMetricResult


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
