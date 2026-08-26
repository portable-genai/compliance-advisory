"""Unit tests for serialization, Settings.load, and Container wiring.

* domain/serialization.to_jsonable round-trips enums (-> .value) and datetimes.
* Settings.load parses config/settings.yaml.
* Container under profile=onprem binds the on-prem placeholder adapters, and each
  bound adapter satisfies its runtime_checkable Protocol (structural parity).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from tests.fixtures import sample_regs

from compliance_advisory import ports
from compliance_advisory.config import RUNTIME_PROFILES, Container, Settings
from compliance_advisory.domain.models import (
    Answer,
    AuditEvent,
    Citation,
    Decision,
    Jurisdiction,
    Regulator,
    Severity,
)

CONFIG_PATH = "config/settings.yaml"


def test_settings_refuses_a_configured_empty_interpolated_value(monkeypatch) -> None:
    from hex_service_kit.netdefaults import ConfiguredEmptyError

    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "")
    with pytest.raises(ConfiguredEmptyError, match="GOOGLE_CLOUD_PROJECT"):
        Settings.load(CONFIG_PATH)


# port name -> its Protocol (from ports.__all__ / the Container cached_properties)
PORT_PROTOCOLS = {
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
}


# --------------------------------------------------------------------------- #
# to_jsonable
# --------------------------------------------------------------------------- #
def _to_jsonable():
    from compliance_advisory.domain.serialization import to_jsonable

    return to_jsonable


def test_to_jsonable_enum_becomes_value():
    to_jsonable = _to_jsonable()
    assert to_jsonable(Regulator.MAS) == "MAS"
    assert to_jsonable(Severity.HIGH) == "high"
    assert to_jsonable(Decision.BLOCKED) == "blocked"


def test_to_jsonable_datetime_is_json_safe_string():
    to_jsonable = _to_jsonable()
    dt = datetime(2026, 6, 20, 8, 30, tzinfo=UTC)
    out = to_jsonable(dt)
    assert isinstance(out, str)
    # round-trips through json without error
    assert json.loads(json.dumps(out)) == out
    assert "2026-06-20" in out


def test_to_jsonable_citation_roundtrips_through_json():
    to_jsonable = _to_jsonable()
    citation = sample_regs.PRIMARY_PASSAGE.citation
    out = to_jsonable(citation)
    assert isinstance(out, dict)
    # enums serialised to their .value
    assert out["regulator"] == citation.regulator.value
    assert out["jurisdiction"] == citation.jurisdiction.value
    assert out["page"] == citation.page
    # fully JSON serialisable
    text = json.dumps(out)
    assert json.loads(text)["source_id"] == citation.source_id


def test_to_jsonable_answer_nested_dataclass_and_enums():
    to_jsonable = _to_jsonable()
    answer = Answer(
        question="q",
        answer="a",
        citations=(
            Citation(
                source_id="mas-trm-guidelines",
                regulator=Regulator.MAS,
                jurisdiction=Jurisdiction.SG,
                title="t",
                url="u",
                page=42,
            ),
        ),
        confidence=0.9,
        requires_human_review=True,
    )
    out = to_jsonable(answer)
    text = json.dumps(out)  # must not raise
    reloaded = json.loads(text)
    assert reloaded["requires_human_review"] is True
    assert reloaded["citations"][0]["regulator"] == "MAS"
    assert reloaded["citations"][0]["page"] == 42


def test_to_jsonable_audit_event_is_worm_serialisable():
    to_jsonable = _to_jsonable()
    event = AuditEvent(
        action="ask",
        actor="analyst",
        decision=Decision.ALLOWED,
        redacted_prompt="[NRIC]",
        redacted_response="ok",
        citations=(sample_regs.PRIMARY_PASSAGE.citation,),
    )
    out = to_jsonable(event)
    text = json.dumps(out)
    reloaded = json.loads(text)
    assert reloaded["decision"] == "allowed"
    assert reloaded["action"] == "ask"


# --------------------------------------------------------------------------- #
# Settings.load parses config/settings.yaml
# --------------------------------------------------------------------------- #
def test_settings_load_parses_yaml():
    settings = Settings.load(CONFIG_PATH)
    assert settings.region == "asia-southeast1"
    assert settings.corpus.ttl_days == 7
    assert settings.models.reasoning == "gemini-3.7-flash"
    assert settings.models.triage == "gemini-3.1-flash-lite"
    # adapter map is populated for every port
    assert set(PORT_PROTOCOLS) <= set(settings.adapters)


def test_settings_pins_models_to_allowed_ids():
    settings = Settings.load(CONFIG_PATH)
    # Never the floating default or a discontinued model.
    assert settings.models.reasoning != "gemini-2.0-flash"
    assert settings.models.triage != "gemini-2.0-flash"
    assert settings.models.reasoning.startswith("gemini-3")


# --------------------------------------------------------------------------- #
# Container binds on-prem adapters under profile=onprem, with structural parity.
# --------------------------------------------------------------------------- #
def _onprem_settings() -> Settings:
    settings = Settings.load(CONFIG_PATH)
    return Settings(
        project_id=settings.project_id,
        region=settings.region,
        profile="onprem",
        kms_key=settings.kms_key,
        grounding_enabled=settings.grounding_enabled,
        models=settings.models,
        agent_search=settings.agent_search,
        alloydb=settings.alloydb,
        model_armor=settings.model_armor,
        dlp=settings.dlp,
        logging=settings.logging,
        agent_engine=settings.agent_engine,
        corpus=settings.corpus,
        adapters=settings.adapters,
    )


def test_container_binds_onprem_adapters_with_protocol_parity():
    container = Container(_onprem_settings())
    for port_name, protocol in PORT_PROTOCOLS.items():
        adapter = getattr(container, port_name)
        # Construct cleanly with no Google Cloud SDK installed, and satisfy the Protocol.
        assert isinstance(adapter, protocol), (
            f"on-prem adapter for '{port_name}' is not structurally a {protocol.__name__}"
        )


def test_container_configuration_names_each_profile_binding_explicitly():
    # Managed reuse is allowed only when it is visible in the reviewed adapter map. A
    # different profile must never acquire the gcp implementation by an implicit fallback.
    settings = _onprem_settings()
    binding = settings.adapters["guardrail"]
    assert binding["onprem"].endswith("OnPremGuardrailAdapter")
    assert set(binding) == set(RUNTIME_PROFILES)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
