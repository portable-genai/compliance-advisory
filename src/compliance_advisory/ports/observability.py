"""Observability ports — the A5 (audit/trace) and A4 (eval gate) concerns.

Primary GCP adapters: **Cloud Logging locked WORM bucket** for immutable audit,
**Cloud Trace via OpenTelemetry** for reasoning-loop traces (message content capture
OFF so PII never reaches a span), and the **Gen AI evaluation service** for the
promotion gate (groundedness, citation accuracy, faithfulness, safety).

Two of the three ports are RE-EXPORTED rather than declared. ``ObservabilityTracerPort`` and
``TokenUsage`` come from :mod:`hex_service_kit.observability`; ``EvaluationGatePort`` comes from
:mod:`agent_eval_kit`. Sixteen repositories had each hand-copied those Protocols, and by the time
anyone compared them they disagreed: one had dropped the eval port entirely, two had dropped its
``gate`` method, which is the half that can refuse a promotion. A Protocol copied into N repos is
N Protocols, and only one of them gets fixed when a defect is found. Importing them retires that
drift class outright, and ``tests/contract/test_port_parity.py`` asserts object IDENTITY (``is``,
not ``isinstance``) so a future hand-copy fails the gate instead of passing it structurally.

``AuditSinkPort`` stays declared here on purpose: it is typed in this repo's own vocabulary
(:class:`~compliance_advisory.domain.models.AuditEvent`), so it is not a shared shape.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from agent_eval_kit import EvaluationGatePort
from hex_service_kit.observability import ObservabilityTracerPort, TokenUsage

from ..domain.models import AuditEvent


@runtime_checkable
class AuditSinkPort(Protocol):
    def record(self, event: AuditEvent) -> None:
        """Write an immutable, already-redacted audit record (WORM)."""
        ...


__all__ = [
    "AuditSinkPort",
    "EvaluationGatePort",
    "ObservabilityTracerPort",
    "TokenUsage",
]
