"""IdentityPort — resolve a verified Principal from inbound transport context.

The hexagon boundary for authentication. The API layer hands the adapter a
:class:`RequestContext` (the request headers) and gets back a verified
:class:`Principal`, or an :class:`IdentityError`. The active profile picks the adapter:

* ``local`` resolves a seeded dev persona (no IdP/AD/LDAP) so demos and tests run offline,
* ``gcp`` verifies the Identity-Aware-Proxy-injected signed assertion (auth configured on
  the GCP service), and
* ``onprem`` is the placeholder for the client's own enterprise IdP (OIDC/SAML).

This keeps the per-user identity decision swappable by configuration, exactly like every
other port (P-02), and is the single seam where the client-asserted actor/ACL is replaced
by a server-verified one.

Each adapter also DECLARES what it does for end-user authentication, because the profile
string cannot answer that question on its own: ``local`` and a rebound ``onprem`` both name a
posture, not a verification. :func:`declared_end_user_auth` reads the declaration, and
``config.end_user_auth_kind`` resolves it for the ACTIVE binding. The exposure guard in
``api/app.py`` is the consumer.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..domain.identity import Principal, RequestContext


@runtime_checkable
class IdentityPort(Protocol):
    def resolve(self, ctx: RequestContext) -> Principal:
        """Resolve a VERIFIED principal from ``ctx``; raise ``IdentityError`` if missing/invalid."""
        ...


#: The adapter verifies the end user against an issuer it does not control (a signed
#: assertion, checked for signature, issuer, audience and expiry).
VERIFIED = "verified"
#: The adapter takes the caller's word for who they are: the seeded personas arrive on the
#: ``X-Dev-Persona`` header the caller wrote, which authenticates nobody.
CLIENT_ASSERTED = "client-asserted"
#: The adapter resolves nobody: a placeholder for an identity provider not yet bound.
UNIMPLEMENTED = "unimplemented"

#: Every declaration this service understands. Anything else is read as :data:`CLIENT_ASSERTED`.
END_USER_AUTH_KINDS: frozenset[str] = frozenset({VERIFIED, CLIENT_ASSERTED, UNIMPLEMENTED})

#: The class attribute an identity adapter sets to one of the values above. A CLASS attribute,
#: not an instance one, because the posture has to be readable WITHOUT constructing the adapter:
#: the seeded-persona adapter refuses to construct under an inherited profile, and a posture
#: that can only be computed by constructing something disappears exactly when it matters most.
END_USER_AUTH_ATTR = "end_user_auth"


def declared_end_user_auth(adapter: object) -> str:
    """What ``adapter`` (a class or an instance) declares, defaulting to :data:`CLIENT_ASSERTED`.

    The default is the fail-closed one in BOTH directions this value is read: it withholds the
    "authenticated" verdict the exposure guard would relax on, and it claims nothing about an
    adapter that never spoke. An unrecognised value lands in the same place, so a typo in a
    declaration cannot read as a verification claim.
    """
    declared = getattr(adapter, END_USER_AUTH_ATTR, None)
    if isinstance(declared, str) and declared in END_USER_AUTH_KINDS:
        return declared
    return CLIENT_ASSERTED
