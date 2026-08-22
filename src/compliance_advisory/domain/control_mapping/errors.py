"""Domain exceptions for the control-mapping capability (merged from system C2).

Pure-Python exception hierarchy raised by the control-mapping orchestration services.
The domain layer never imports Google Cloud, ADK, or any framework — these errors let
callers (API routers, the Agent Runtime adapter) react to domain-level failures without
coupling to any vendor SDK error type.
"""

from __future__ import annotations


class ControlMappingError(Exception):
    """Base class for all domain-level errors raised by the control-mapping services."""


class RequirementsEmptyError(ControlMappingError):
    """Raised when the requirement source returns no obligations for a scope.

    The mapping/evidence/gap services treat an empty requirement set as a hard
    error: there is nothing to map, so producing an evidence pack would be
    misleading. Callers should refine the scope or check the reg-KB dependency.
    """


class PostureUnavailableError(ControlMappingError):
    """Raised when the live control posture cannot be observed for a scope.

    Mapping requirements to controls without any observed posture would assert
    coverage the toolkit cannot evidence, so the consequential generators raise
    rather than emit an unsupported evidence pack.
    """
