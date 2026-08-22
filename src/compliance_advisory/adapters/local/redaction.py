"""Local PII redaction adapter (PIIRedactionPort) — regex de-identification.

The ``local`` profile's stand-in for **Sensitive Data Protection / DLP**: masks
Singapore NRIC/FIN ids and email addresses (and other obvious PII) with deterministic
regexes, returning findings. There is no Google emulator for DLP, so this path is
unconditional and imports no google-cloud package.
"""

from __future__ import annotations

import re

from ...config import Settings
from ...domain.models import RedactionFinding, RedactionResult

_NRIC_RE = re.compile(r"\b[STFGM]\d{7}[A-Z]\b")
_EMAIL_RE = re.compile(r"\b[\w.%+-]+@[\w.-]+\.[A-Za-z]{2,}\b")
_PHONE_RE = re.compile(r"\b(?:\+?65[\s-]?)?[689]\d{3}[\s-]?\d{4}\b")


class LocalRegexRedactionAdapter:
    """Mask SG NRIC/FIN, emails and SG phone numbers, like DLP de-identify."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def redact(self, text: str) -> RedactionResult:
        findings: list[RedactionFinding] = []
        redacted = text

        nric_hits = _NRIC_RE.findall(redacted)
        if nric_hits:
            redacted = _NRIC_RE.sub("[NRIC]", redacted)
            findings.append(RedactionFinding(info_type="SG_NRIC_FIN", count=len(nric_hits)))

        email_hits = _EMAIL_RE.findall(redacted)
        if email_hits:
            redacted = _EMAIL_RE.sub("[EMAIL]", redacted)
            findings.append(RedactionFinding(info_type="EMAIL_ADDRESS", count=len(email_hits)))

        phone_hits = _PHONE_RE.findall(redacted)
        if phone_hits:
            redacted = _PHONE_RE.sub("[PHONE]", redacted)
            findings.append(RedactionFinding(info_type="PHONE_NUMBER", count=len(phone_hits)))

        return RedactionResult(text=redacted, findings=tuple(findings))
