"""FastAPI dependency wiring for the C1 Compliance Assistant.

This module builds a single, process-wide :class:`~compliance_advisory.config.Container`
(the ports-and-adapters registry) and assembles the four orchestration services from the
Container's port instances. The Container is created lazily on first access so importing
this module — and therefore the FastAPI app — never touches Google Cloud: a unit test or
the on-prem profile can import the API with no GCP SDK installed.

Each ``get_*`` factory is a FastAPI ``Depends`` provider. Services take *explicit port
instances* in their constructors (SPEC §5), so the wiring here is the single place that
knows which ports each service needs. Building a service is cheap (it just captures port
references); the underlying adapters are themselves constructed lazily by the Container.
"""

from __future__ import annotations

from functools import lru_cache

from ..config import Container, Settings, build_container
from ..domain.control_mapping import (
    ControlMappingService,
    EvidencePackService,
    GapAnalysisService,
)
from ..domain.hitl import HumanReviewPolicy
from ..domain.horizon import (
    HorizonPolicy,
    HorizonScanService,
    ImplementationTrackingService,
)
from ..domain.services import (
    ChecklistService,
    ComplianceQAService,
    RegulatorQuestionService,
    TestCaseService,
)


@lru_cache(maxsize=1)
def get_container() -> Container:
    """Return the process-wide Container, building it on first use.

    Cached for the lifetime of the process so every request shares one set of adapter
    instances (and their cached clients). ``Settings.load()`` reads ``config/settings.yaml``
    with ``${ENV_VAR}`` interpolation and selects the active profile.
    """
    return build_container(Settings.load())


def get_settings() -> Settings:
    """Convenience accessor for the active settings (region, profile, corpus TTL…)."""
    return get_container().settings


# --------------------------------------------------------------------------- #
# Service factories — assemble each service from the Container's ports.
# Constructor argument order mirrors SPEC §5 exactly.
# --------------------------------------------------------------------------- #


def get_qa_service() -> ComplianceQAService:
    """ComplianceQAService(retrieval, llm, guardrail, redaction, grounding, tracer, audit)."""
    c = get_container()
    return ComplianceQAService(
        retrieval=c.retrieval,
        llm=c.llm,
        guardrail=c.guardrail,
        redaction=c.redaction,
        grounding=c.grounding,
        tracer=c.tracer,
        audit=c.audit,
        review_policy=HumanReviewPolicy.from_policy(c.settings.policy),
        policy=c.settings.policy,
        review_router=c.review_router,
    )


def get_checklist_service() -> ChecklistService:
    """ChecklistService(retrieval, llm, guardrail, redaction, tracer, audit)."""
    c = get_container()
    return ChecklistService(
        retrieval=c.retrieval,
        llm=c.llm,
        guardrail=c.guardrail,
        redaction=c.redaction,
        tracer=c.tracer,
        audit=c.audit,
    )


def get_testcase_service() -> TestCaseService:
    """TestCaseService(retrieval, llm, guardrail, redaction, tracer, audit)."""
    c = get_container()
    return TestCaseService(
        retrieval=c.retrieval,
        llm=c.llm,
        guardrail=c.guardrail,
        redaction=c.redaction,
        tracer=c.tracer,
        audit=c.audit,
    )


def get_regulator_question_service() -> RegulatorQuestionService:
    """RegulatorQuestionService(retrieval, llm, guardrail, redaction, tracer, audit)."""
    return build_regulator_question_service(get_container())


# --------------------------------------------------------------------------- #
# Container-explicit factories.
#
# The ``get_*`` factories above use the cached, process-wide Container (right for the
# long-lived FastAPI app). The CLI and the ADK tools instead build their own Container
# per invocation — honouring ``COMPLIANCE_PROFILE`` at call time — so they call these
# ``build_*`` variants with an explicit Container. Argument order mirrors SPEC §5.
# --------------------------------------------------------------------------- #


def build_qa_service(container: Container) -> ComplianceQAService:
    """Assemble a :class:`ComplianceQAService` from an explicit Container."""
    return ComplianceQAService(
        retrieval=container.retrieval,
        llm=container.llm,
        guardrail=container.guardrail,
        redaction=container.redaction,
        grounding=container.grounding,
        tracer=container.tracer,
        audit=container.audit,
        review_policy=HumanReviewPolicy.from_policy(container.settings.policy),
        policy=container.settings.policy,
        review_router=container.review_router,
    )


def build_checklist_service(container: Container) -> ChecklistService:
    """Assemble a :class:`ChecklistService` from an explicit Container."""
    return ChecklistService(
        retrieval=container.retrieval,
        llm=container.llm,
        guardrail=container.guardrail,
        redaction=container.redaction,
        tracer=container.tracer,
        audit=container.audit,
    )


def build_testcase_service(container: Container) -> TestCaseService:
    """Assemble a :class:`TestCaseService` from an explicit Container."""
    return TestCaseService(
        retrieval=container.retrieval,
        llm=container.llm,
        guardrail=container.guardrail,
        redaction=container.redaction,
        tracer=container.tracer,
        audit=container.audit,
    )


def build_regulator_question_service(container: Container) -> RegulatorQuestionService:
    """Assemble a :class:`RegulatorQuestionService` from an explicit Container."""
    return RegulatorQuestionService(
        retrieval=container.retrieval,
        llm=container.llm,
        guardrail=container.guardrail,
        redaction=container.redaction,
        tracer=container.tracer,
        audit=container.audit,
    )


# --------------------------------------------------------------------------- #
# Control-mapping service factories (merged from C2).
#
# The three control-mapping services take requirement_source, control_inventory, llm,
# tracer and audit — and NO guardrail / redaction port. That absence is deliberate: the
# control-mapping pipeline reasons over the bank's own cloud control posture, not customer
# PII, so it does NOT route through the assistant's Model Armor guardrail or DLP redaction
# (R1 / P-04 = N/A; see COMPLIANCE.md). The evidence-pack service additionally takes the
# shared review_router so its always-review deliverable is routed to human-review-console (rule R8).
# --------------------------------------------------------------------------- #


def get_mapping_service() -> ControlMappingService:
    """ControlMappingService(requirement_source, control_inventory, llm, tracer, audit)."""
    return build_mapping_service(get_container())


def get_evidence_service() -> EvidencePackService:
    """EvidencePackService(requirement_source, control_inventory, llm, tracer, audit, router)."""
    return build_evidence_service(get_container())


def get_gap_service() -> GapAnalysisService:
    """GapAnalysisService(requirement_source, control_inventory, llm, tracer, audit)."""
    return build_gap_service(get_container())


def build_mapping_service(container: Container) -> ControlMappingService:
    """Assemble a :class:`ControlMappingService` from an explicit Container."""
    return ControlMappingService(
        requirement_source=container.requirement_source,
        control_inventory=container.control_inventory,
        llm=container.llm,
        tracer=container.tracer,
        audit=container.audit,
    )


def build_evidence_service(container: Container) -> EvidencePackService:
    """Assemble an :class:`EvidencePackService` from an explicit Container."""
    return EvidencePackService(
        requirement_source=container.requirement_source,
        control_inventory=container.control_inventory,
        llm=container.llm,
        tracer=container.tracer,
        audit=container.audit,
        review_router=container.review_router,
    )


def build_gap_service(container: Container) -> GapAnalysisService:
    """Assemble a :class:`GapAnalysisService` from an explicit Container."""
    return GapAnalysisService(
        requirement_source=container.requirement_source,
        control_inventory=container.control_inventory,
        llm=container.llm,
        tracer=container.tracer,
        audit=container.audit,
    )


# --------------------------------------------------------------------------- #
# Horizon-scanning service factories.
#
# The horizon services take the EXISTING corpus freshness ledger (the diff base), the
# source catalog, the llm (narration only), tracer, audit, the tracking store and the
# shared review_router — and NO guardrail / redaction port, for the same reason the
# control-mapping services do not: the pipeline reasons over published regulatory
# instruments and the bank's own implementation state, never customer PII
# (R1 / P-04 = N-A; see COMPLIANCE.md).
#
# The scan service is additionally handed the control-mapping GapAnalysisService: open
# control gaps for a regulator are a materiality driver, which is the link between the
# horizon journey and the existing control-mapping journey.
# --------------------------------------------------------------------------- #


def get_horizon_scan_service() -> HorizonScanService:
    """HorizonScanService(ledger, source_catalog, llm, tracer, audit, tracker, ...)."""
    return build_horizon_scan_service(get_container())


def get_horizon_tracking_service() -> ImplementationTrackingService:
    """ImplementationTrackingService(tracker, tracer, audit, review_router)."""
    return build_horizon_tracking_service(get_container())


def build_horizon_scan_service(container: Container) -> HorizonScanService:
    """Assemble a :class:`HorizonScanService` from an explicit Container."""
    return HorizonScanService(
        ledger=container.ledger,
        source_catalog=container.source_catalog,
        llm=container.llm,
        tracer=container.tracer,
        audit=container.audit,
        tracker=container.horizon_tracker,
        policy=HorizonPolicy(container.settings.horizon),
        gap_service=build_gap_service(container),
        review_router=container.review_router,
    )


def build_horizon_tracking_service(container: Container) -> ImplementationTrackingService:
    """Assemble an :class:`ImplementationTrackingService` from an explicit Container."""
    return ImplementationTrackingService(
        tracker=container.horizon_tracker,
        tracer=container.tracer,
        audit=container.audit,
        review_router=container.review_router,
    )


class CorpusService:
    """Stable ``.refresh()`` / ``.status()`` surface over the corpus pipeline + ledger.

    ``refresh`` re-fetches and re-ingests expired sources (the 7-day TTL pass) into Agent
    Search; ``status`` returns the AlloyDB freshness ledger. Used by the API ``/corpus``
    endpoints and the ``compliance corpus`` CLI commands.
    """

    def __init__(self, container: Container) -> None:
        self._container = container

    def refresh(self, *, full: bool = False):  # -> pipelines.ingest.RefreshSummary
        from ..pipelines import ingest as ingest_mod

        if full:
            return ingest_mod.refresh_all(self._container)
        return ingest_mod.refresh_expired(self._container)

    def status(self):  # -> list[FreshnessRecord]
        return self._container.ledger.all()


def build_corpus_service(container: Container) -> CorpusService:
    """Build the corpus orchestration service from an explicit Container."""
    return CorpusService(container)
