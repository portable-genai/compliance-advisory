"""Local agent-runtime adapter (AgentRuntimePort) — in-process reasoning loop.

The ``local`` profile's stand-in for **Agent Runtime** (the hosted reasoning engine):
rather than calling a deployed endpoint, it assembles the domain ``ComplianceQAService``
from the other local adapters and answers in-process. Under ``local`` the platform
client runs the app itself (a laptop runs one app, not the whole platform). SDK-free and
unconditional.
"""

from __future__ import annotations

from ...config import Settings
from ...domain.models import Answer, Session


class LocalAgentRuntimeAdapter:
    """In-process agent runtime: answers via the local ComplianceQAService."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._service = None

    def _qa_service(self):  # type: ignore[no-untyped-def]
        if self._service is None:
            from ...domain.qa_service import ComplianceQAService
            from .audit import LocalAppendOnlyAuditAdapter
            from .grounding import LocalDisabledGroundingAdapter
            from .guardrail import LocalHeuristicGuardrailAdapter
            from .llm import LocalDeterministicLLMAdapter
            from .redaction import LocalRegexRedactionAdapter
            from .retrieval import LocalFtsRetrievalAdapter
            from .tracer import LocalNoopTracerAdapter

            self._service = ComplianceQAService(
                retrieval=LocalFtsRetrievalAdapter(self._settings),
                llm=LocalDeterministicLLMAdapter(self._settings),
                guardrail=LocalHeuristicGuardrailAdapter(self._settings),
                redaction=LocalRegexRedactionAdapter(self._settings),
                grounding=LocalDisabledGroundingAdapter(self._settings),
                tracer=LocalNoopTracerAdapter(self._settings),
                audit=LocalAppendOnlyAuditAdapter(self._settings),
            )
        return self._service

    def query(self, session: Session, message: str) -> Answer:
        return self._qa_service().answer(message, actor=session.user_id)

    def health(self) -> bool:
        return True
