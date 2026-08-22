"""Mapping review (maker-checker) policy — General Principle P-06.

C2 is a *decision-support* toolkit, never an autonomous sign-off: an evidence pack
goes to an auditor/regulator, so a human reviews any consequential or low-assurance
output before it is relied upon. This module centralises that gate so every service
applies identical rules and the threshold is auditable in one place.

Policy (SPEC §5):
* An ``EvidencePack`` is always human-reviewed: it is the regulator deliverable, so a
  maker (the toolkit) proposes and a checker (a qualified human) disposes.
* A single ``ControlMapping`` requires review when its coverage is PARTIAL or NONE —
  a less-than-full coverage claim is exactly what a regulator scrutinises.
* A ``ControlGap`` requires review when its severity is HIGH or CRITICAL — high-stakes
  gaps get a second pair of eyes before they shape remediation.
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import Coverage, Severity

#: Coverage verdicts that force review of a single mapping.
_REVIEW_COVERAGES: frozenset[Coverage] = frozenset({Coverage.PARTIAL, Coverage.NONE})

#: Severities considered high-stakes enough to force review of a gap.
_HIGH_SEVERITIES: frozenset[Severity] = frozenset({Severity.HIGH, Severity.CRITICAL})


@dataclass(frozen=True, slots=True)
class MappingReviewPolicy:
    """Maker-checker gate (P-06) for C2. Pure decision logic; no side effects."""

    def mapping_requires_review(self, coverage: Coverage) -> bool:
        """A mapping with PARTIAL or NONE coverage must be human-reviewed."""
        return coverage in _REVIEW_COVERAGES

    def gap_requires_review(self, severity: Severity) -> bool:
        """A HIGH/CRITICAL-severity gap must be human-reviewed."""
        return severity in _HIGH_SEVERITIES

    def pack_requires_review(self) -> bool:
        """An evidence pack is always human-reviewed (it is the regulator deliverable)."""
        return True
