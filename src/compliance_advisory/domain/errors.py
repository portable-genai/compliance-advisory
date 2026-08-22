"""Domain exceptions for the Compliance Assistant (system C1).

Pure-Python exception hierarchy raised by the orchestration services. The domain
layer never imports Google Cloud, ADK, or any framework — these errors let callers
(API, CLI, the Agent Runtime adapter) react to domain-level failures without
coupling to any vendor SDK error type.
"""

from __future__ import annotations


class ComplianceError(Exception):
    """Base class for all domain-level errors raised by C1 services."""


class GuardrailBlockedError(ComplianceError):
    """Raised/flagged when the A1 guardrail blocks an input or output.

    The Q&A pipeline generally degrades gracefully (returns a blocked ``Answer``
    with ``requires_human_review=True``) rather than raising, but the consequential
    generators (checklist / test cases / regulator questions) raise this so a
    blocked unsafe request never yields a partial artifact.
    """


class RetrievalEmptyError(ComplianceError):
    """Raised when retrieval returns no grounding passages for a query.

    Services that *must* be grounded (the consequential artifact generators) treat
    an empty corpus as a hard error; the Q&A path instead returns a low-confidence,
    caveated ``Answer`` flagged for human review.
    """


class GroundingDisabledError(ComplianceError):
    """Raised when public-web grounding is requested but switched off.

    Grounding is gated by ``grounding_enabled`` (SPEC §2). Callers that explicitly
    require web grounding can raise this rather than silently skipping it.
    """
