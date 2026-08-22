"""RequirementSourcePort — the regulatory knowledge base (reg KB) for control mapping.

Supplies the regulatory obligations (with regulator-grade citations) that controls are
mapped to. In the merged assistant this port binds IN-PROCESS to Rsk1's own retrieval
port (the ``compliance-reg-kb`` Agent Search store / local FTS5 index) — there is one
regulatory knowledge base, shared between the assistant's answer path and the
control-mapping capability. The old standalone Gemini File Search store, the HTTP hop to
C1's ``/ask``, and the duplicate FTS5 reg seed are retired.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..domain.control_mapping.models import RegRequirement


@runtime_checkable
class RequirementSourcePort(Protocol):
    def fetch(self, scope: str, regulator: str | None = None) -> list[RegRequirement]:
        """Return the regulatory requirements for ``scope`` (optionally one regulator)."""
        ...
