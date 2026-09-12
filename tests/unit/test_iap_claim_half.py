"""The CLAIM half of the IAP adapter: one reviewed decision, not a local copy of one.

Signature verification stays in the adapter, because it needs a cloud SDK and
``hex_service_kit``'s core is pure standard library. Everything AFTER the signature -- which
string is the subject, which partition is the tenant, which entitlement principals the caller
holds, what assurance the audit record carries -- is a single authorization decision that was
replicated in fifty adapters, and it is now
:func:`hex_service_kit.federation.principal_from_iap_claims`.

**What was observed failing first, before the adapter changed.** Every test below was run
against the adapter exactly as it shipped:

* ``test_the_claim_half_is_the_commons_decision_and_not_a_local_copy`` failed on the ``acr``
  row and on the mixed-case ``hd`` row. The shipped tail hardcoded ``assurance="iap"`` and
  ignored an ``acr`` the commons prefers, and it passed the ``hd`` claim through with only
  ``.strip()`` where the commons lower-cases it. Both are recorded as findings in
  ``docs/practices-audit.md``: they are behaviour changes, not formatting.
* ``test_the_hosted_domain_passthrough_is_an_opt_in_this_deployment_made`` failed with
  ``AttributeError``: there was no policy object to read, which is exactly the state in which
  a load-bearing tenant can be emptied by a default nobody wrote down.
* ``test_a_claim_set_from_another_issuer_cannot_become_a_principal`` failed by RETURNING a
  principal. With the claim-set precheck removed, the hand-rolled tail read ``claims["email"]``
  and built a verified identity out of a token issued by somebody else. The commons refuses it
  a second time, which is what makes the precheck a defence in depth rather than the only
  defence.

The knobs were also flipped deliberately after the change: setting
``include_subject_principal=False`` turns
``test_this_family_grants_the_verified_subject_its_own_principal`` red, and clearing
``tenant_from_hosted_domain`` turns the passthrough test and the ``hd`` rows of the
comparison red. A guard that cannot be made to fail is not a guard.
"""

from __future__ import annotations

import base64
import json as _json
from typing import Any

import pytest
from hex_service_kit.federation import (
    IAP_ASSERTION_HEADER,
    IAP_ISSUER,
    FederationPolicy,
    principal_from_iap_claims,
)

from compliance_advisory.adapters.gcp.iap_identity import IapIdentityAdapter, _federation_policy
from compliance_advisory.domain.identity import IdentityError, RequestContext

_AUDIENCE = "/projects/1234567890/global/backendServices/42"
_EMAIL = "avery.stone@example-bank.test"
_SUB = "accounts.google.com:100000000000000000001"


def _token() -> str:
    """A structurally real compact JWS. Only the header is read, and nothing is signed."""
    header = (
        base64.urlsafe_b64encode(_json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
        .decode()
        .rstrip("=")
    )
    return f"{header}.{base64.urlsafe_b64encode(b'{}').decode().rstrip('=')}.c2ln"


def _claims(**overrides: Any) -> dict[str, Any]:
    claims: dict[str, Any] = {
        "iss": IAP_ISSUER,
        "aud": _AUDIENCE,
        "sub": _SUB,
        "email": _EMAIL,
        "hd": "example-bank.test",
        "exp": 4102444800,
    }
    claims.update(overrides)
    return {name: value for name, value in claims.items() if value is not None}


def _adapter(audience: str = _AUDIENCE) -> IapIdentityAdapter:
    """The adapter with only the deployment configuration ``resolve`` reads.

    Built without ``Settings`` so this module depends on the identity binding and on nothing
    else in the container. The same idiom the refusal-split suite uses.
    """
    adapter = object.__new__(IapIdentityAdapter)
    adapter._settings = None
    adapter._audience = audience
    adapter._audience_configured_empty = False
    return adapter


def _resolve(claims: dict[str, Any], adapter: IapIdentityAdapter | None = None) -> Any:
    """Run the shipped adapter's claim half over ``claims``, with the cryptography stubbed.

    The signature check is the half that stays in the adapter and needs google-auth, which the
    offline gate does not install. Stubbing ``_verify`` is what makes the half under test
    reachable without a network, a credential or a cloud SDK; it is not a way of skipping a
    check, because every refusal the verifier owns is exercised by the crypto suite instead.
    """
    adapter = adapter or _adapter()
    object.__setattr__(adapter, "_verify", lambda assertion: dict(claims))
    return adapter.resolve(RequestContext(headers={IAP_ASSERTION_HEADER: _token()}))


def _fields(principal: Any) -> tuple[Any, ...]:
    """Compare by VALUE, not by identity of the class.

    Some repositories in this family still declare their own look-alike ``Principal`` instead
    of re-exporting the commons one, so ``==`` between the two answers would be False for a
    reason that has nothing to do with the decision under test.
    """
    return (
        principal.subject,
        principal.principals,
        principal.tenant,
        principal.assurance,
        principal.source,
    )


# --------------------------------------------------------------------------------------- #
# The comparison. Executed against the commons, never read off it.
# --------------------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "name,claims",
    [
        ("workspace user", _claims()),
        ("no hosted domain", _claims(hd=None, email="someone@personal.test")),
        ("service account", _claims(hd=None, email="runner@demo.iam.gserviceaccount.com")),
        ("assertion carrying acr", _claims(acr="urn:mace:incommon:iap:silver")),
        ("hosted domain in mixed case", _claims(hd="Example-Bank.TEST")),
    ],
)
def test_the_claim_half_is_the_commons_decision_and_not_a_local_copy(
    name: str, claims: dict[str, Any]
) -> None:
    """Every field of the resolved principal is what the commons decides, for every shape.

    A copy that agrees today is a copy that can drift tomorrow with nothing to notice, which
    is the whole reason the decision moved. Comparing the ANSWERS rather than the source is
    what turns that from a claim into a check.
    """
    assert _fields(_resolve(claims)) == _fields(
        principal_from_iap_claims(
            claims,
            _federation_policy(),
            source="gcp-iap",
            include_subject_principal=True,
        )
    ), name


# --------------------------------------------------------------------------------------- #
# The one authorization decision the two adapter families make differently.
# --------------------------------------------------------------------------------------- #
def test_this_family_grants_the_verified_subject_its_own_principal() -> None:
    """This family grants the verified subject its own ``user:<subject>`` principal.

    The other adapter family leaves the tuple to the group map alone, and both are
    deliberate. Adopting the commons without saying which one this repository is would
    have silently moved a verified identity into or out of its own entitlement.

    ``include_subject_principal`` is a parameter precisely because the two families disagree
    on purpose. Flipping it here is a one-character edit, so it is asserted directly rather
    than inferred from a passing suite.
    """
    assert _resolve(_claims()).principals == (f"user:{_EMAIL}",)


# --------------------------------------------------------------------------------------- #
# The tenancy boundary. A domain nobody asserted is not an organisation.
# --------------------------------------------------------------------------------------- #
def test_an_assertion_with_no_hosted_domain_resolves_to_no_tenant() -> None:
    """The mail domain is DERIVED and asserts nothing, so it must never become a tenant.

    A personal account the edge admits, or an external federated identity, carries no ``hd``
    at all. Reading its address instead would make anyone able to receive mail at a domain
    they control a tenant of that domain, silently, at a tenancy boundary. The commons shipped
    exactly that defect once and removed it; this is the repository-side guard that it stays
    removed.
    """
    principal = _resolve(_claims(hd=None, email="someone@personal.test"))
    assert principal.tenant == ""


def test_the_hosted_domain_passthrough_is_an_opt_in_this_deployment_made() -> None:
    """The policy is load-bearing, and the test proves it by turning it off.

    With passthrough cleared, the SAME verified assertion resolves to no tenant. That is
    fail-closed and it is closed for every user, which is why the choice is written down as a
    reviewed policy object rather than left to a default. No domain is mapped, because this
    deployment has no reviewed domain map: an unmapped domain must therefore get its tenant
    from the opt-in or from nothing.
    """
    policy = _federation_policy()
    assert policy.tenant_from_hosted_domain is True
    assert dict(policy.domain_tenants) == {}
    assert dict(policy.domain_groups) == {}

    without = FederationPolicy()
    assert without.tenant_for("example-bank.test", email_domain="example-bank.test") == ""
    assert policy.tenant_for("example-bank.test") == "example-bank.test"

    # The tenant a verified user actually receives, which is the half an attribute assertion
    # cannot see. The comparison test above evaluates BOTH sides under this same policy, so it
    # agrees whichever policy is configured; only a resolved value can catch an emptied tenant,
    # and an emptied tenant is precisely the failure that fails closed for everybody at once.
    assert _resolve(_claims()).tenant == "example-bank.test"


# --------------------------------------------------------------------------------------- #
# The attacker's side: a verified signature is not a verified issuer.
# --------------------------------------------------------------------------------------- #
def test_a_claim_set_from_another_issuer_cannot_become_a_principal() -> None:
    """Defence in depth, asserted with the first defence deliberately removed.

    ``_refuse_unpinned_claims`` already refuses a foreign issuer. This test takes it away, so
    that what is being measured is the claim half itself: a token that satisfied a signature
    check and named somebody else as its issuer must produce NO principal, rather than a
    verified identity built out of its ``email``.
    """
    adapter = _adapter()
    object.__setattr__(adapter, "_refuse_unpinned_claims", lambda claims: None)
    with pytest.raises(IdentityError):
        _resolve(_claims(iss="https://accounts.google.com"), adapter)


def test_a_verified_assertion_that_names_nobody_is_refused() -> None:
    """An empty subject is not an anonymous caller; it is an assertion with no actor to audit."""
    adapter = _adapter()
    object.__setattr__(adapter, "_refuse_unpinned_claims", lambda claims: None)
    with pytest.raises(IdentityError):
        _resolve(_claims(sub="   "), adapter)


# --------------------------------------------------------------------------------------- #
# The machine caller: the half this deployment shipped without, and the half its first
# consumer needs.
# --------------------------------------------------------------------------------------- #
#
# Found by execution on 2026-09-12, hours after this service was deployed as an embedded app.
# Its console answered, a route needing no identity answered 200 through the portal's proxy, and
# every route taking a principal answered 401 to a SERVICE ACCOUNT. A human in the deployment's
# own Cloud Identity domain would have resolved through `hd` and seen nothing wrong, so the
# defect was invisible to a browser walkthrough and total for machine callers.
#
# The cause is the kit's rule, working as designed: a machine caller's tenant comes only from an
# exact-address map, never from a domain, because every service account in a project shares one
# and keying tenancy on it would put unrelated machines in a single tenant. With no map at all,
# that rule resolves no tenant and the request is refused. The first consumer of this service is
# another application calling `/ask` as its own runtime identity through the same edge.
_MACHINE = "journey-a-cdd-so-604cef@portable-genai-sg.iam.gserviceaccount.com"
_MACHINE_CLAIMS = {"email": _MACHINE, "hd": None, "sub": f"accounts.google.com:{_MACHINE}"}


def test_an_unmapped_machine_caller_is_still_refused_a_tenant(monkeypatch: Any) -> None:
    """Unset maps nobody, which is the right default for a browser-only installation.

    This is the state the service was deployed in, asserted rather than remembered: the caller
    verifies, and resolves to no tenant, which every tenant-scoped read refuses.
    """
    monkeypatch.delenv("COMPLIANCE_IAP_MACHINE_TENANTS_JSON", raising=False)
    monkeypatch.delenv("COMPLIANCE_IAP_TENANT_DOMAINS_JSON", raising=False)
    assert _resolve(_claims(**_MACHINE_CLAIMS)).tenant == ""


def test_a_mapped_machine_caller_resolves_to_the_tenant_it_was_given(monkeypatch: Any) -> None:
    """An exact address, reviewed by the deployment, is the only thing that admits a machine."""
    monkeypatch.setenv(
        "COMPLIANCE_IAP_MACHINE_TENANTS_JSON", _json.dumps({_MACHINE: "reference-bank"})
    )
    principal = _resolve(_claims(**_MACHINE_CLAIMS))
    assert principal.tenant == "reference-bank"


def test_a_machine_address_the_map_does_not_name_stays_refused(monkeypatch: Any) -> None:
    """A map admits what it names and nothing adjacent: not the domain, not a sibling account."""
    monkeypatch.setenv(
        "COMPLIANCE_IAP_MACHINE_TENANTS_JSON",
        _json.dumps({"someone-else@portable-genai-sg.iam.gserviceaccount.com": "reference-bank"}),
    )
    assert _resolve(_claims(**_MACHINE_CLAIMS)).tenant == ""


def test_an_emptied_map_is_refused_rather_than_read_as_mapping_nobody(monkeypatch: Any) -> None:
    """Three states, never two. An emptied variable looks configured and names nothing.

    Treated as unset, a deployment that meant to map its callers and blanked the value would get
    a service that refuses every machine caller while its configuration says otherwise.
    """
    monkeypatch.setenv("COMPLIANCE_IAP_MACHINE_TENANTS_JSON", "")
    with pytest.raises(ValueError, match="COMPLIANCE_IAP_MACHINE_TENANTS_JSON"):
        _federation_policy()


def test_a_wildcard_key_is_refused_because_nobody_reviewed_what_it_admits(
    monkeypatch: Any,
) -> None:
    monkeypatch.setenv("COMPLIANCE_IAP_MACHINE_TENANTS_JSON", _json.dumps({"*": "reference-bank"}))
    with pytest.raises(ValueError, match="name each domain or account exactly"):
        _federation_policy()


def test_a_mapped_domain_wins_over_the_hosted_domain_passthrough(monkeypatch: Any) -> None:
    """The human half keeps working, and a reviewed domain map overrides the opt-in."""
    monkeypatch.setenv(
        "COMPLIANCE_IAP_TENANT_DOMAINS_JSON", _json.dumps({"example-bank.test": "reference-bank"})
    )
    assert _resolve(_claims()).tenant == "reference-bank"


def test_an_unmapped_domain_still_falls_back_to_its_hosted_domain(monkeypatch: Any) -> None:
    """Unchanged behaviour for every installation that maps no domain, which is the default."""
    monkeypatch.setenv(
        "COMPLIANCE_IAP_TENANT_DOMAINS_JSON", _json.dumps({"other-bank.test": "other"})
    )
    assert _resolve(_claims()).tenant == "example-bank.test"
