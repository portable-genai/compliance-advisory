"""The header an assertion arrives under when this service is embedded, and the honest refusal.

**The contradiction this module exists to resolve.** ``test_iap_claim_half.py`` already asserts
that a MAPPED machine caller resolves to the tenant it was given, and those tests pass. The
deployed service still answered ``401 {"detail":"authentication required"}`` to exactly such a
caller. Both are true, and the reason is that every test in that file hands the adapter its
assertion like this:

    adapter.resolve(RequestContext(headers={IAP_ASSERTION_HEADER: _token()}))

``IAP_ASSERTION_HEADER`` is ``x-goog-iap-jwt-assertion``, the one header production never
delivers to an embedded application. ``x-goog-*`` is Google's reserved namespace and the
serverless frontend REMOVES those headers from a request entering a service, so an embedding host
behind IAP cannot forward what its own edge handed it: the host sets the reserved name, the
frontend drops it, and the service refuses "request did not pass through IAP" about a request
that passed through IAP one hop earlier. The broker sends the same value as
``x-portal-iap-assertion`` as well, precisely because that name is not reserved.

So the claim-half suite models the CLAIMS faithfully, service-account rows included, and models
the TRANSPORT wrongly. It is right about what it asserts and silent about the half that fails.
The machine map added in #61 is correct and necessary and it was never reached, because the
request was refused before any policy was consulted.

**What was observed failing first, on the live deployment.** On 2026-09-12, reached through the
portal's IAP edge on both hosts as a service account, a ``GET`` of this service's corpus-status
route answered 401 while the sibling application ``credit-memo-drafting`` answered from its own
application for the SAME caller through BOTH hosts, deployed earlier against an OLDER kit and
carrying no machine map at all. So the edge, the host, the audience and the proxy were ruled out
by execution, and the difference was one line. The refusal was also not machine-specific: behind
the portal the reserved header is absent for EVERY caller, so a human in a mapped domain was
refused exactly as hard, which the second test below pins.

**The second half: the refusal was dishonest.** ``api/security.py`` mapped every
``IdentityError`` to ``401 "authentication required"``. That sentence was false in the live
failure, and it stays false for any refusal the claim half makes AFTER the signature has been
accepted: a reviewed allowlist, or a tenant the maps decline to resolve. Those callers are
authenticated and unentitled, which is a 403. The first consumer of this service is another
application calling it as its own runtime identity, and a status is the only part of a refusal
such a caller can act on.
"""

from __future__ import annotations

import base64
import json as _json
from typing import Any

import pytest
from hex_service_kit import federation as kit_federation
from hex_service_kit.federation import IAP_ISSUER, FederationPolicy

from compliance_advisory.adapters.gcp import iap_identity
from compliance_advisory.adapters.gcp.iap_identity import IapIdentityAdapter
from compliance_advisory.domain.identity import IdentityError, RequestContext
from compliance_advisory.ports.identity import (
    AuthorizationRefusedError,
    EndUserAuthUnavailableError,
)

_AUDIENCE = "/projects/1234567890/global/backendServices/42"

#: The first consumer of this service, in the shape a real IAP assertion carries it: an
#: ``@...iam.gserviceaccount.com`` address, NO ``hd`` claim at all (a machine belongs to no
#: hosted domain), and a ``sub`` of the provider's own shape.
_MACHINE = "journey-caller@demo-project.iam.gserviceaccount.com"
_MACHINE_SUB = "accounts.google.com:117000000000000000001"


def _token() -> str:
    """A structurally real compact JWS. Only the header is read, and nothing is signed."""
    header = (
        base64.urlsafe_b64encode(_json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
        .decode()
        .rstrip("=")
    )
    return f"{header}.{base64.urlsafe_b64encode(b'{}').decode().rstrip('=')}.c2ln"


def _machine_claims(**overrides: Any) -> dict[str, Any]:
    claims: dict[str, Any] = {
        "iss": IAP_ISSUER,
        "aud": _AUDIENCE,
        "sub": _MACHINE_SUB,
        "email": _MACHINE,
        "exp": 4102444800,
    }
    claims.update(overrides)
    return claims


def _adapter(audience: str = _AUDIENCE) -> IapIdentityAdapter:
    """The adapter with only the deployment configuration ``resolve`` reads."""
    adapter = object.__new__(IapIdentityAdapter)
    adapter._settings = None
    adapter._audience = audience
    adapter._audience_configured_empty = False
    return adapter


def _resolve(headers: dict[str, str], claims: dict[str, Any] | None = None) -> Any:
    """Run the shipped adapter over ``headers``, with only the cryptography stubbed.

    Stubbing ``_verify`` is what makes the half under test reachable with no network, no
    credential and no cloud SDK. It skips no check the adapter owns: the algorithm pin, the
    required claims, the issuer and the audience are all still evaluated here, and every refusal
    the verifier itself owns is exercised by the crypto suite instead.
    """
    adapter = _adapter()
    object.__setattr__(adapter, "_verify", lambda assertion: dict(claims or _machine_claims()))
    return adapter.resolve(RequestContext(headers=headers))


# --------------------------------------------------------------------------------------- #
# The transport. One assertion, two names, and only one of them survives the hop.
# --------------------------------------------------------------------------------------- #
def test_the_forwarded_header_name_is_the_commons_value_and_is_not_reserved() -> None:
    """Rebound from the kit, never re-declared, and outside the stripped namespace.

    Putting the fallback back inside ``x-goog-*`` would reintroduce the exact defect it fixes,
    silently, because the frontend strips the whole namespace rather than one name.
    """
    assert iap_identity._PORTAL_ASSERTION_HEADER == kit_federation.PORTAL_ASSERTION_HEADER
    assert iap_identity._PORTAL_ASSERTION_HEADER == "x-portal-iap-assertion"
    assert not iap_identity._PORTAL_ASSERTION_HEADER.startswith("x-goog-")


def test_the_mapped_machine_caller_resolves_when_the_host_forwarded_the_assertion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The offline reproduction of the live 401, and the resolution of the contradiction.

    This is the SAME assertion, the SAME claims and the SAME reviewed map as
    ``test_iap_claim_half.py::test_a_mapped_machine_caller_resolves_to_the_tenant_it_was_given``,
    which passed throughout. The only difference is the header name, and the header name is the
    whole defect: under the forwarded name, which is the only one an embedded application ever
    sees, the adapter as it shipped refused with "missing IAP assertion header" and the map below
    was never read by anything.
    """
    monkeypatch.setenv(
        iap_identity._IAP_MACHINE_TENANTS_ENV, _json.dumps({_MACHINE: "reference-bank"})
    )
    monkeypatch.delenv(iap_identity._IAP_TENANT_DOMAINS_ENV, raising=False)

    principal = _resolve({iap_identity._PORTAL_ASSERTION_HEADER: _token()})

    assert principal.tenant == "reference-bank"
    assert principal.subject == _MACHINE
    assert principal.source == "gcp-iap"


def test_a_human_is_refused_by_the_same_transport_defect_and_admitted_by_the_same_fix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The defect was never machine-only, and saying so is the point of this test.

    The repository's own notes said a human in the deployment's Cloud Identity domain "would have
    resolved through ``hd`` and seen nothing wrong". Behind the portal that is false: the reserved
    header is absent for every caller, so the human who appears to work is the one whose console
    is only calling routes that take no principal.
    """
    monkeypatch.delenv(iap_identity._IAP_MACHINE_TENANTS_ENV, raising=False)
    monkeypatch.setenv(
        iap_identity._IAP_TENANT_DOMAINS_ENV, _json.dumps({"bank.example": "reference-bank"})
    )

    human = _machine_claims(
        email="avery.stone@bank.example", hd="bank.example", sub="accounts.google.com:1"
    )
    assert _resolve({iap_identity._PORTAL_ASSERTION_HEADER: _token()}, human).tenant == (
        "reference-bank"
    )


@pytest.mark.parametrize(
    "header",
    [kit_federation.IAP_ASSERTION_HEADER, kit_federation.PORTAL_ASSERTION_HEADER],
    ids=["edge-injected", "host-forwarded"],
)
def test_both_names_take_the_identical_verification_path(
    monkeypatch: pytest.MonkeyPatch, header: str
) -> None:
    """The fallback is TRANSPORT and not a second trust path.

    An assertion under either name is handed to the same verifier and judged by the same
    reviewed policy, so a caller gains nothing by choosing one. The assertion is a stub token
    and ``_verify`` is NOT stubbed here, so the only two acceptable outcomes are a refusal from
    the verifier and a refusal because the verifier is absent; a returned identity would mean
    the header bought a bypass, and a "missing IAP assertion header" would mean it was ignored.
    """
    monkeypatch.delenv(iap_identity._IAP_MACHINE_TENANTS_ENV, raising=False)
    with pytest.raises((IdentityError, ModuleNotFoundError)) as caught:
        result = _adapter().resolve(RequestContext(headers={header: _token()}))
        raise AssertionError(f"an unverified assertion produced an identity: {result!r}")
    assert "missing IAP assertion header" not in str(caught.value)


def test_the_edge_injected_name_still_wins_when_both_are_present() -> None:
    """Precedence is about diagnosis, not trust: the direct edge's assertion needs no forwarding.

    Both are verified identically, so nothing turns on the order; pinning it keeps the common
    path the simple one when a service is reached both directly and through a host.
    """
    edge = _token()
    ctx = RequestContext(
        headers={
            kit_federation.IAP_ASSERTION_HEADER: edge,
            kit_federation.PORTAL_ASSERTION_HEADER: "forwarded-and-different",
        }
    )
    seen: list[str] = []
    adapter = _adapter()
    object.__setattr__(
        adapter, "_verify", lambda assertion: seen.append(assertion) or _machine_claims()
    )
    adapter.resolve(ctx)
    assert seen == [edge]


def test_neither_name_present_is_still_a_missing_assertion() -> None:
    """The fix must not swallow the ordinary case, and the refusal must name BOTH headers.

    An operator who reads only "missing IAP assertion header" looks at the load balancer. The
    one who reads which two names were examined looks at the hop that dropped one of them.
    """
    with pytest.raises(IdentityError) as caught:
        _adapter().resolve(RequestContext(headers={}))
    message = str(caught.value)
    assert "missing IAP assertion header" in message
    assert kit_federation.IAP_ASSERTION_HEADER in message
    assert kit_federation.PORTAL_ASSERTION_HEADER in message


@pytest.mark.parametrize("blank", ["   ", "\t", "\n"])
def test_a_whitespace_only_forwarded_header_is_an_absent_one(blank: str) -> None:
    """A blank value is truthy, so without stripping it would be refused as a malformed token."""
    with pytest.raises(IdentityError, match="missing IAP assertion header"):
        _adapter().resolve(RequestContext(headers={iap_identity._PORTAL_ASSERTION_HEADER: blank}))


# --------------------------------------------------------------------------------------- #
# The honest refusal. "Authenticate" is a lie told to somebody who already did.
# --------------------------------------------------------------------------------------- #
def test_an_allowlisted_deployment_refuses_an_unnamed_machine_with_403_not_401(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A reviewed allowlist refuses a caller who AUTHENTICATED. That is 403, and it always was.

    Against the adapter as it shipped this was a bare ``401 "authentication required"``, which
    sends an operator hunting for a credential that already worked, and tells a machine caller to
    retry with a better one, which can never succeed.
    """
    monkeypatch.delenv(iap_identity._IAP_MACHINE_TENANTS_ENV, raising=False)
    policy = FederationPolicy(
        tenant_from_hosted_domain=True,
        allowed_machine_subjects=("someone-else@demo-project.iam.gserviceaccount.com",),
    )
    monkeypatch.setattr(iap_identity, "_federation_policy", lambda: policy)

    with pytest.raises(AuthorizationRefusedError) as caught:
        _resolve({iap_identity._PORTAL_ASSERTION_HEADER: _token()})
    assert caught.value.http_status == 403
    assert isinstance(caught.value, IdentityError)
    assert not isinstance(caught.value, EndUserAuthUnavailableError)
    assert "authentication required" not in str(caught.value)
    assert _MACHINE in str(caught.value)


def test_a_deployment_that_refuses_an_unmapped_tenant_says_403_rather_than_401(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The other post-verification refusal, and the same answer.

    This deployment leaves ``refuse_unmapped_tenant`` off, so the knob is exercised here rather
    than in production. The status must not depend on which knob was turned: both refuse a
    caller whose identity is established.
    """
    policy = FederationPolicy(refuse_unmapped_tenant=True)
    monkeypatch.setattr(iap_identity, "_federation_policy", lambda: policy)

    with pytest.raises(AuthorizationRefusedError) as caught:
        _resolve({iap_identity._PORTAL_ASSERTION_HEADER: _token()})
    assert caught.value.http_status == 403


def test_an_assertion_this_adapter_will_not_accept_is_still_a_401(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The split must not relabel an AUTHENTICATION failure as an entitlement one.

    A claim set from another issuer is refused by ``_refuse_unpinned_claims`` before the claim
    half runs, and the classifier re-runs that same first defence rather than reasoning about
    call order, so even with the first call deliberately removed the answer stays 401.
    """
    monkeypatch.delenv(iap_identity._IAP_MACHINE_TENANTS_ENV, raising=False)
    adapter = _adapter()
    object.__setattr__(
        adapter,
        "_verify",
        lambda assertion: _machine_claims(iss="https://accounts.google.com"),
    )
    with pytest.raises(IdentityError) as caught:
        adapter.resolve(RequestContext(headers={iap_identity._PORTAL_ASSERTION_HEADER: _token()}))
    assert not isinstance(caught.value, AuthorizationRefusedError)


def test_the_api_answers_an_authorization_refusal_with_403_and_a_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A type nothing reads is decoration. This is the read, and it is the caller-visible half."""
    from fastapi import HTTPException
    from starlette.requests import Request

    from compliance_advisory.api import security

    class Refusing:
        def resolve(self, ctx: RequestContext) -> object:
            raise AuthorizationRefusedError("no reviewed tenant for 'runner@demo.test'")

    class Container:
        identity = Refusing()

    monkeypatch.setattr(security.deps, "get_container", lambda: Container())
    request = Request({"type": "http", "headers": [], "method": "GET", "path": "/"})
    with pytest.raises(HTTPException) as caught:
        security.get_principal(request)
    assert caught.value.status_code == 403
    assert "authentication required" not in str(caught.value.detail)
    assert "no reviewed tenant" in str(caught.value.detail)
