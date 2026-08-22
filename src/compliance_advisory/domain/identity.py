"""Identity value objects for server-side, verified principals.

The assistant never trusts a client-asserted ``actor`` or ACL. A :class:`Principal` is
resolved server-side by an :class:`~compliance_advisory.ports.identity.IdentityPort`
adapter (local dev persona, GCP IAP-verified assertion, or an on-prem client IdP) from the
inbound transport context, and becomes the audit actor for every generated artifact. Pure
stdlib: nothing here imports a web framework or a cloud SDK.
"""

from __future__ import annotations

from dataclasses import dataclass, field


class IdentityError(Exception):
    """Raised when a verified principal cannot be resolved (maps to HTTP 401)."""


@dataclass(frozen=True, slots=True)
class RequestContext:
    """The inbound transport context an IdentityPort resolves a Principal from.

    ``headers`` keys are lower-cased; adapters read only what they need (a GCP IAP
    assertion header, or the local ``x-dev-persona`` selector) without depending on the
    web layer, so the domain stays framework-free.
    """

    headers: dict[str, str] = field(default_factory=dict)

    def header(self, name: str) -> str:
        return self.headers.get(name.lower(), "")


@dataclass(frozen=True, slots=True)
class Principal:
    """A verified end-user identity (never client-asserted)."""

    subject: str  # stable user id (email / sub); becomes the audit actor
    principals: tuple[str, ...] = ()  # entitlement principals/groups for governed ACL
    tenant: str = ""  # tenant / issuer partition (multi-tenant isolation)
    assurance: str = ""  # acr/amr auth-strength hint (optional, for step-up checks)
    source: str = ""  # which adapter resolved it (audit/debug)

    @property
    def actor(self) -> str:
        """The audit actor is the verified subject (non-repudiation, MAS TRM / CPS 234)."""
        return self.subject


# A safe non-identity used only where an adapter explicitly opts out; never the default
# in secure mode (the IdentityPort raises instead of returning this).
ANONYMOUS = Principal(subject="anonymous", source="none")
