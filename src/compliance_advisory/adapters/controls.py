"""The runtime-control seam: what a switched-off control binds, and what a request reports.

Two halves, both profile-independent, so they live beside the adapter families rather than in
one of them.

**Disabled adapters.** When a deployment switches a cheap runtime control off
(``COMPLIANCE_GUARDRAIL``, ``COMPLIANCE_PII_REDACTION``, ``COMPLIANCE_REVIEW_ROUTING``), the
container binds one of these instead of the profile's class. Each satisfies its port and does
nothing, so no service grows a ``None`` branch, and the container logs the posture at startup.

**Request-scoped disclosure.** The API wraps the bound redaction and review-router adapters
per request, so the response can tell the user what the controls actually did:

* :class:`DisclosingRedaction` notes whether redaction changed the user's input, which the
  console discloses rather than answering a question the user did not quite ask.
* :class:`RecordingReviewRouter` records whether a required hand-off reached the console,
  failed, or was switched off. A failure is logged and absorbed here, because an already
  audited decision must not fail on a console outage, but it is no longer invisible: the
  response carries ``review_routing``.
"""

from __future__ import annotations

import logging
from enum import StrEnum
from typing import Any

from ..config import Settings
from ..domain.models import Direction, GuardrailVerdict, RedactionResult

_log = logging.getLogger(__name__)


class ReviewRouting(StrEnum):
    """What happened to the human-review hand-off for one response."""

    ROUTED = "routed"
    FAILED = "failed"
    OFF = "off"
    NOT_REQUIRED = "not_required"


# --------------------------------------------------------------------------- #
# Disabled adapters
# --------------------------------------------------------------------------- #
class DisabledGuardrail:
    """GuardrailPort with the guardrail switched off: allows everything, text unchanged."""

    enabled = False

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def screen(self, text: str, direction: Direction) -> GuardrailVerdict:
        return GuardrailVerdict(
            allowed=True, direction=direction, sanitized_text=text, reason="guardrail off"
        )


class DisabledRedaction:
    """PIIRedactionPort with redaction switched off: text unchanged, no findings."""

    enabled = False

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def redact(self, text: str) -> RedactionResult:
        return RedactionResult(text=text, findings=())


class DisabledReviewRouter:
    """ReviewRouterPort with routing switched off: nothing is submitted anywhere."""

    enabled = False

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def route(self, answer: Any, *, maker: str, tenant: str = "") -> None:
        return None


# --------------------------------------------------------------------------- #
# Request-scoped disclosure
# --------------------------------------------------------------------------- #
class DisclosingRedaction:
    """Wraps the bound redaction adapter for one request and notes whether it changed input."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.changed = False

    def redact(self, text: str) -> RedactionResult:
        result: RedactionResult = self._inner.redact(text)
        if result.text != text:
            self.changed = True
        return result


class RecordingReviewRouter:
    """Wraps the bound review router for one request and records each hand-off's outcome."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self._outcomes: list[ReviewRouting] = []

    def route(self, answer: Any, *, maker: str, tenant: str = "") -> None:
        if not getattr(self._inner, "enabled", True):
            self._outcomes.append(ReviewRouting.OFF)
            return
        try:
            self._inner.route(answer, maker=maker, tenant=tenant)
        except Exception as exc:  # noqa: BLE001 - the outcome is reported, never raised
            _log.warning("human-review hand-off failed: %s", type(exc).__name__)
            self._outcomes.append(ReviewRouting.FAILED)
            return
        self._outcomes.append(ReviewRouting.ROUTED)

    @property
    def outcome(self) -> ReviewRouting:
        """One value for the response: any failure wins, then off, then routed."""
        for worst in (ReviewRouting.FAILED, ReviewRouting.OFF, ReviewRouting.ROUTED):
            if worst in self._outcomes:
                return worst
        return ReviewRouting.NOT_REQUIRED
