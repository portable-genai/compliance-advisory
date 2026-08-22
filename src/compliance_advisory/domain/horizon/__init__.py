"""Regulatory horizon scanning: detect, assess, route, track (SPEC §7).

The fourth Rsk1 capability, built ON the corpus and freshness ledger this repo already
maintains rather than beside them. Public surface:

* :class:`HorizonScanService` — the pipeline (ledger diff -> deterministic assessment ->
  advisory narration -> tracking -> audit -> Hrz7 routing).
* :class:`ImplementationTrackingService` — advance a tracked change to closure, fail-closed
  on the verified tenant.
* :class:`HorizonPolicy` / :class:`HorizonPolicyConfig` — the pure decision engine and the
  bank-owned numbers behind it (B4).
* :mod:`.detection` — the pure ledger diff, including :func:`carry_forward`, which the
  ingest pipeline uses to keep the ledger diffable.
"""

from __future__ import annotations

from .detection import carry_forward, change_id, classify, detect_change, detect_changes
from .errors import (
    CorpusLedgerEmptyError,
    HorizonError,
    ImplementationItemNotFoundError,
    TenantMismatchError,
)
from .models import (
    MATERIALITY_BAND_ORDER,
    OPEN_IMPLEMENTATION_STATES,
    Applicability,
    ChangeKind,
    CorpusChange,
    HorizonAssessment,
    HorizonScan,
    ImplementationItem,
    ImplementationStatus,
    MaterialityBand,
    MaterialityDriver,
    OwnerAssignment,
)
from .policy import (
    ApplicabilityVerdict,
    HorizonPolicy,
    HorizonPolicyConfig,
    MaterialityVerdict,
)
from .scan_service import HorizonScanService
from .tracking_service import ImplementationTrackingService

__all__ = [
    "MATERIALITY_BAND_ORDER",
    "OPEN_IMPLEMENTATION_STATES",
    "Applicability",
    "ApplicabilityVerdict",
    "ChangeKind",
    "CorpusChange",
    "CorpusLedgerEmptyError",
    "HorizonAssessment",
    "HorizonError",
    "HorizonPolicy",
    "HorizonPolicyConfig",
    "HorizonScan",
    "HorizonScanService",
    "ImplementationItem",
    "ImplementationItemNotFoundError",
    "ImplementationStatus",
    "ImplementationTrackingService",
    "MaterialityBand",
    "MaterialityDriver",
    "MaterialityVerdict",
    "OwnerAssignment",
    "TenantMismatchError",
    "carry_forward",
    "change_id",
    "classify",
    "detect_change",
    "detect_changes",
]
