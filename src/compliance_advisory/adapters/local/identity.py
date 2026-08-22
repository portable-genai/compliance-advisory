"""Local IdentityPort adapter: seeded dev personas, NO IdP / AD / LDAP.

The SDK-free ``local`` profile must run with zero authentication so demos and tests work
fully offline. This adapter resolves a :class:`Principal` from a small set of seeded
personas, selected by the ``X-Dev-Persona`` request header (the UI's persona picker),
defaulting to the first persona when none is supplied. It lets you exercise per-user
authorization (different entitlement principals and tenants, including a cross-tenant
persona) without standing up any identity provider. It is bound ONLY under the no-auth
profiles (``local`` and ``live``, which differ only in generator and corpus); secure mode
uses the IAP adapter, which verifies a real assertion.

These personas are an UNAUTHENTICATED grant of the compliance-approver entitlement, so this
adapter refuses to construct unless a no-auth profile was chosen deliberately: the profile
must actually be one of them AND (when the settings came from the environment)
``COMPLIANCE_PROFILE`` must have been set rather than inherited from the fallback. A missing
env var therefore fails closed instead of serving the assistant with dev approvers. The
refusal is pure stdlib and happens at construction, before any credential is inspected, so
it does not depend on a cloud SDK being installed.
"""

from __future__ import annotations

from ...config import NO_AUTH_PROFILES, Settings
from ...domain.identity import IdentityError, Principal, RequestContext
from ...ports.identity import CLIENT_ASSERTED

_PERSONA_HEADER = "x-dev-persona"

# Seeded dev personas. Ordered; the first entry is the default when no persona is selected.
# The persona id is the suffix of ``source`` after the colon. Ids, subjects and tenants are
# shared across the catalog repos so demos stay uniform.
_PERSONAS: tuple[Principal, ...] = (
    Principal(
        subject="demo.analyst@bank.example",
        principals=("group:compliance-analyst", "group:risk"),
        tenant="demo-bank",
        assurance="local-demo",
        source="local-persona:analyst",
    ),
    Principal(
        subject="demo.approver@bank.example",
        principals=("group:compliance-analyst", "group:risk", "group:compliance-approver"),
        tenant="demo-bank",
        assurance="local-demo",
        source="local-persona:approver",
    ),
    Principal(
        subject="demo.auditor@bank.example",
        principals=("group:audit",),
        tenant="demo-bank",
        assurance="local-demo",
        source="local-persona:auditor",
    ),
    Principal(
        subject="user@other-tenant.example",
        principals=("group:compliance-analyst",),
        tenant="other-bank",
        assurance="local-demo",
        source="local-persona:other-tenant",
    ),
)


def _persona_id(principal: Principal) -> str:
    _, _, suffix = principal.source.partition(":")
    return suffix or principal.subject


class LocalPersonaProfileError(IdentityError):
    """Raised when seeded dev personas would be served under a non-deliberate no-auth profile."""


class LocalPersonaIdentityAdapter:
    """Resolve a Principal from a seeded dev persona (no-auth profiles only, no auth)."""

    #: The persona rides the ``X-Dev-Persona`` header the CALLER wrote, so this adapter
    #: authenticates nobody. Declared on the class so the exposure guard can read the posture
    #: without constructing the adapter, which refuses to construct under an inherited profile.
    end_user_auth = CLIENT_ASSERTED

    def __init__(self, settings: Settings) -> None:
        if settings.profile not in NO_AUTH_PROFILES:
            expected = ", ".join(sorted(NO_AUTH_PROFILES))
            raise LocalPersonaProfileError(
                f"seeded dev personas are {expected}-profile only; "
                f"refusing to serve them under profile {settings.profile!r}"
            )
        if not settings.profile_explicit:
            raise LocalPersonaProfileError(
                "COMPLIANCE_PROFILE is not set, so the local profile was inherited rather than "
                "chosen; seeded dev personas grant the compliance-approver entitlement with no "
                "authentication and are refused. Set COMPLIANCE_PROFILE=local deliberately for "
                "a dev or demo run, or COMPLIANCE_PROFILE=gcp for a real deployment."
            )
        self._settings = settings
        self._by_id: dict[str, Principal] = {_persona_id(p): p for p in _PERSONAS}
        self._default: Principal = _PERSONAS[0]

    def resolve(self, ctx: RequestContext) -> Principal:
        chosen = ctx.header(_PERSONA_HEADER).strip()
        if not chosen:
            return self._default
        persona = self._by_id.get(chosen)
        if persona is None:
            raise IdentityError(
                f"unknown dev persona {chosen!r}; valid personas: {sorted(self._by_id)}"
            )
        return persona

    def personas(self) -> tuple[dict[str, str], ...]:
        """List the seeded personas for the local persona picker (id, subject, tenant)."""
        return tuple(
            {
                "id": _persona_id(p),
                "subject": p.subject,
                "tenant": p.tenant,
                "principals": ", ".join(p.principals),
            }
            for p in _PERSONAS
        )
