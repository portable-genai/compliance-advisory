"""Control-mapping domain capability (merged from system C2 into the assistant).

Maps GCP technical controls (VPC-SC, CMEK, Assured Workloads, ...) to each regulator's
requirements and produces a regulator-grade evidence pack showing coverage and gaps.

The three orchestration services live one-per-module (``mapping_service``,
``evidence_service``, ``gap_service``) so each stays focused. This package is the single
import surface the wiring layers (``api`` routers, ``agent`` tools) use, so they never
need to know which module a service lives in.

Posture note: this capability reasons over the bank's OWN cloud control posture plus
public regulatory text, never customer/PII data — so its pipeline is wired WITHOUT the
assistant's guardrail / DLP-redaction ports by design (R1 / P-04 = N/A; see
COMPLIANCE.md). The services below take no guardrail/redaction port at all.
"""

from __future__ import annotations

from .errors import (
    ControlMappingError,
    PostureUnavailableError,
    RequirementsEmptyError,
)
from .evidence_service import EvidencePackService
from .gap_service import GapAnalysisService
from .mapping_service import ControlMappingService

__all__ = [
    "ControlMappingService",
    "EvidencePackService",
    "GapAnalysisService",
    "ControlMappingError",
    "PostureUnavailableError",
    "RequirementsEmptyError",
]
