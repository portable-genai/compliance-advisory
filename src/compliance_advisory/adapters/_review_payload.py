"""Shared conversion from an escalated compliance answer to an ``review-kit`` Review payload.

Lives in the adapter layer (not the pure domain) because it depends on the kit. Redacts the subject
descriptor, summary and every citation snippet before they leave the process (R1 / P-04 boundary) so
no raw customer identifier reaches human-review-console over the wire; human-review-console redacts
again before its own audit write (defense in depth). This repo has neither ``pii-kit`` nor a
``domain/pii_patterns`` module, so the redactor mirrors the same identifier set the local DLP
stand-in masks (:class:`~compliance_advisory.adapters.local.redaction.LocalRegexRedactionAdapter`:
SG NRIC/FIN, email, SG phone). The maker (the verified actor who originated the answer) and the
tenant are asserted here and trusted by human-review-console because this is an authenticated S2S
caller (per-hop OBO is the deferred next layer).
"""

from __future__ import annotations

import hashlib
import re

from review_kit import Citation as KitCitation
from review_kit import Review

from ..domain.control_mapping.models import Coverage, EvidencePack
from ..domain.horizon.models import HorizonAssessment, ImplementationItem, MaterialityBand
from ..domain.models import Answer, Citation, Severity

# Cap the citations carried on the wire: enough to let a reviewer trace the answer without
# copying the entire evidence set into the review console.
_MAX_CITATIONS = 8

# Identifier patterns masked before the wire. Mirrors the local DLP stand-in
# (LocalRegexRedactionAdapter) so the same de-identification runs regardless of profile: the
# review console is a shared sink and must never receive a raw identifier.
_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("NRIC", re.compile(r"\b[STFGM]\d{7}[A-Z]\b")),
    ("EMAIL", re.compile(r"\b[\w.%+-]+@[\w.-]+\.[A-Za-z]{2,}\b")),
    ("PHONE", re.compile(r"\b(?:\+?65[\s-]?)?[689]\d{3}[\s-]?\d{4}\b")),
)

# Cited regulatory themes that are high-stakes enough to raise the review severity to HIGH,
# matching the Q&A service's own escalation heuristic (qa_service._severity_for).
_HIGH_RISK_TOPICS = (
    "operational-resilience",
    "outsourcing",
    "incident",
    "cyber",
    "aml",
    "sanctions",
    "third-party",
)


def _redact(text: str) -> str:
    """Mask SG NRIC/FIN, email and SG phone identifiers, then collapse whitespace."""
    redacted = text
    for label, pattern in _PATTERNS:
        redacted = pattern.sub(f"[{label}]", redacted)
    return re.sub(r"\s+", " ", redacted).strip()


def _severity(answer: Answer) -> Severity:
    """The answer's risk signal: a high-risk cited topic or low confidence maps to HIGH.

    Mirrors the maker-checker triggers on :class:`Answer` (a high/critical cited topic, or
    confidence below the review floor), so the console severity reflects why review was required.
    """
    for c in answer.citations:
        text = f"{c.title} {c.snippet}".lower()
        if any(k in text for k in _HIGH_RISK_TOPICS):
            return Severity.HIGH
    if answer.confidence < 0.4:
        return Severity.HIGH
    if answer.confidence < 0.6:
        return Severity.MEDIUM
    return Severity.LOW


def _kit_citations(answer: Answer) -> tuple[KitCitation, ...]:
    seen: set[str] = set()
    out: list[KitCitation] = []
    for c in answer.citations:
        if c.source_id in seen:
            continue
        seen.add(c.source_id)
        out.append(KitCitation(source_id=c.source_id, title=c.title, snippet=_redact(c.snippet)))
        if len(out) >= _MAX_CITATIONS:
            break
    return tuple(out)


def answer_to_review(answer: Answer, *, maker: str, tenant: str = "") -> Review:
    """Build the review a producer submits to human-review-console when a compliance answer
    escalates.
    """
    redacted_question = _redact(answer.question)
    descriptor = f"Compliance answer to: {redacted_question}"
    summary = (
        f"confidence={answer.confidence:.2f}; citations={len(answer.citations)}; "
        f"web_citations={len(answer.web_citations)}; caveats={len(answer.caveats)}"
    )
    severity = _severity(answer)
    # Dual control for the strongest band (a high-risk topic or a low-confidence answer).
    dual = severity in (Severity.HIGH, Severity.CRITICAL)
    # Answer carries no id; a stable, PII-free case_ref is a digest of the redacted question.
    case_ref = f"qa-{hashlib.sha256(redacted_question.encode('utf-8')).hexdigest()[:12]}"
    return Review(
        action="compliance_answer:ask",
        subject=_redact(descriptor),
        maker=maker,
        tenant=tenant,
        summary=_redact(summary),
        severity=severity.value,
        required_approvals=2 if dual else 1,
        sod_group="compliance-maker-checker",
        case_ref=case_ref,
        citations=_kit_citations(answer),
    )


# --------------------------------------------------------------------------- #
# Evidence-pack review payload (control-mapping capability, merged from C2)
# --------------------------------------------------------------------------- #
# The second R8 path against the SAME human-review-console contract: an evidence pack is ALWAYS
# routed for
# human review. Redaction here is deliberately a whitespace-normalising clean, NOT PII
# masking: the control-mapping capability reasons over the bank's OWN cloud control posture
# plus public regulatory text, never customer/PII data (R1 / P-04 = N/A; see COMPLIANCE.md).
# The subject is a GCP resource scope, the summary is coverage counts, and citation snippets
# quote published regulator obligations — none carry a customer identifier.

# Ordered weakest -> strongest so ``max`` picks the pack's most severe gap.
_SEVERITY_ORDER: tuple[Severity, ...] = (
    Severity.LOW,
    Severity.MEDIUM,
    Severity.HIGH,
    Severity.CRITICAL,
)


def _clean(text: str) -> str:
    """Collapse whitespace before the wire (control mapping carries no customer PII)."""
    return re.sub(r"\s+", " ", text).strip()


def _pack_severity(pack: EvidencePack) -> Severity:
    """The pack's most severe gap, or a coverage-derived floor when it carries none."""
    gap_severities = [g.severity for g in pack.gaps if g.severity in _SEVERITY_ORDER]
    if gap_severities:
        return max(gap_severities, key=_SEVERITY_ORDER.index)
    if pack.coverage_summary.get(Coverage.NONE.value, 0):
        return Severity.HIGH
    if pack.coverage_summary.get(Coverage.PARTIAL.value, 0):
        return Severity.MEDIUM
    return Severity.LOW


def _pack_escalated(pack: EvidencePack) -> bool:
    """Strongest band: any uncovered (NONE) requirement or any HIGH/CRITICAL gap."""
    if pack.coverage_summary.get(Coverage.NONE.value, 0):
        return True
    return _pack_severity(pack) in (Severity.HIGH, Severity.CRITICAL)


def _pack_citations(pack: EvidencePack) -> list[Citation]:
    out: list[Citation] = []
    for mapping in pack.mappings:
        out.extend(mapping.citations)
    for gap in pack.gaps:
        out.extend(gap.citations)
    return out


def _pack_kit_citations(pack: EvidencePack) -> tuple[KitCitation, ...]:
    seen: set[str] = set()
    out: list[KitCitation] = []
    for c in _pack_citations(pack):
        if c.source_id in seen:
            continue
        seen.add(c.source_id)
        out.append(KitCitation(source_id=c.source_id, title=c.title, snippet=_clean(c.snippet)))
        if len(out) >= _MAX_CITATIONS:
            break
    return tuple(out)


def pack_to_review(pack: EvidencePack, *, maker: str, tenant: str = "") -> Review:
    """Build the review a producer submits to human-review-console when an
    evidence pack escalates.
    """
    descriptor = (
        f"Evidence pack for scope {pack.scope} "
        f"({len(pack.mappings)} mappings, {len(pack.gaps)} gaps)"
    )
    summary = "; ".join(f"{k}={v}" for k, v in pack.coverage_summary.items())
    summary = f"coverage: {summary}; gaps={len(pack.gaps)}"
    severity = _pack_severity(pack)
    # Dual control for the strongest bands or any escalation (uncovered requirement / HIGH+ gap).
    dual = _pack_escalated(pack) or severity in (Severity.HIGH, Severity.CRITICAL)
    return Review(
        action="evidence_pack:build",
        subject=_clean(descriptor),
        maker=maker,
        tenant=tenant,
        summary=_clean(summary),
        severity=severity.value,
        required_approvals=2 if dual else 1,
        sod_group="control-mapping-maker-checker",
        case_ref=pack.scope,
        citations=_pack_kit_citations(pack),
    )


# --------------------------------------------------------------------------- #
# Horizon-scanning review payloads
# --------------------------------------------------------------------------- #
# The third and fourth R8 paths against the SAME human-review-console contract: an assessed
# regulatory
# change (ownership routing plus a consequential materiality call) and a closure of a
# tracked change (implemented / accepted risk). As with control mapping, redaction here is
# a whitespace-normalising clean, NOT PII masking: horizon scanning reasons over published
# regulatory instruments and the bank's own implementation state, never customer data
# (R1 / P-04 = N-A; see COMPLIANCE.md).

#: Materiality band -> review severity. The bands are the bank's own configured risk
#: language, so they map one-for-one onto the console's severity ladder.
_BAND_SEVERITY: dict[MaterialityBand, Severity] = {
    MaterialityBand.LOW: Severity.LOW,
    MaterialityBand.MEDIUM: Severity.MEDIUM,
    MaterialityBand.HIGH: Severity.HIGH,
    MaterialityBand.CRITICAL: Severity.CRITICAL,
}


def _horizon_kit_citations(citations: tuple[Citation, ...]) -> tuple[KitCitation, ...]:
    seen: set[str] = set()
    out: list[KitCitation] = []
    for c in citations:
        if c.source_id in seen:
            continue
        seen.add(c.source_id)
        out.append(KitCitation(source_id=c.source_id, title=c.title, snippet=_clean(c.snippet)))
        if len(out) >= _MAX_CITATIONS:
            break
    return tuple(out)


def assessment_to_review(assessment: HorizonAssessment, *, maker: str, tenant: str = "") -> Review:
    """Build the review submitted to human-review-console when an assessed regulatory change
    escalates.

    The summary carries the DECIDED numbers and the driver arithmetic behind them, so the
    checker can verify the materiality call without rerunning the scan.
    """
    change = assessment.change
    descriptor = (
        f"Regulatory change: {change.title} "
        f"({change.regulator.value} {change.jurisdiction.value}, {change.kind.value})"
    )
    drivers = ", ".join(f"{d.name}={d.points:+d}" for d in assessment.drivers)
    owner = assessment.owner.owner if assessment.owner else "unassigned"
    summary = (
        f"applicability={assessment.applicability.value}; "
        f"materiality={assessment.materiality_score} [{assessment.materiality_band.value}]; "
        f"owner={owner}; drivers: {drivers}"
    )
    severity = _BAND_SEVERITY.get(assessment.materiality_band, Severity.MEDIUM)
    dual = severity in (Severity.HIGH, Severity.CRITICAL)
    return Review(
        action="horizon_change:assess",
        subject=_clean(descriptor),
        maker=maker,
        tenant=tenant,
        summary=_clean(summary),
        severity=severity.value,
        required_approvals=2 if dual else 1,
        sod_group="horizon-maker-checker",
        case_ref=assessment.id,
        citations=_horizon_kit_citations(assessment.citations),
    )


def implementation_to_review(item: ImplementationItem, *, maker: str, tenant: str = "") -> Review:
    """Build the review submitted to human-review-console when a tracked change is closed.

    ``implemented`` and ``accepted_risk`` both assert the bank's regulatory position, so
    both are maker-checker events rather than a unilateral status edit.
    """
    descriptor = (
        f"Regulatory change closure: {item.change_id} ({item.source_id}) -> {item.status.value}"
    )
    summary = (
        f"status={item.status.value}; owner={item.owner or 'unassigned'}; "
        f"materiality={item.materiality_band.value}; "
        f"controls={', '.join(item.control_ids) or 'none linked'}"
    )
    severity = _BAND_SEVERITY.get(item.materiality_band, Severity.MEDIUM)
    dual = severity in (Severity.HIGH, Severity.CRITICAL)
    return Review(
        action=f"horizon_change:{item.status.value}",
        subject=_clean(descriptor),
        maker=maker,
        tenant=tenant,
        summary=_clean(summary),
        severity=severity.value,
        required_approvals=2 if dual else 1,
        sod_group="horizon-maker-checker",
        case_ref=item.change_id,
        citations=(),
    )
