"""Platform ReviewRouterPort: submit the routed answer review to human-review-console via
``review-kit``.

Builds the review from the escalated answer and submits it to the human-review-console service
intake (``POST /v1/service/reviews``). The base URL comes from ``HUMAN_REVIEW_URL`` and the signed
actor from ``S2S_SIGNING_KEY``; the bearer depends on how the console is reached:

* **Through the portal's IAP edge** (``gcp``): the deployed console is an embedded app behind
  `journey-portal`, so ``HUMAN_REVIEW_URL`` is its edge path
  (``https://<edge-host>/apps/human-review-console/api``) and the edge accepts only a
  Google-signed ID token minted for the IAP OAuth client id, named by
  ``HUMAN_REVIEW_IAP_AUDIENCE``. The router mints one per submission with this service's
  workload identity (:func:`._s2s.fetch_id_token`), so an expiring token is never reused. The
  console authenticates this service from the IAP assertion the edge forwards, not from the
  portal's bearer that replaces this one.
* **Directly** (audience unset): the static ``S2S_TOKEN`` bearer, the same pair the other
  platform delegates use.

``HUMAN_REVIEW_IAP_AUDIENCE`` is read in three states: unset keeps the static bearer, emptied
refuses at construction, and a backend-service path pasted where the client id belongs refuses
by name. Under ``gcp`` the boot check in :mod:`compliance_advisory.config` requires it beside the
URL while routing is on. The kit itself uses stdlib ``urllib``; ``google-auth`` is imported only
when a token is minted.
"""

from __future__ import annotations

from review_kit import ReviewClient

from ...config import HUMAN_REVIEW_IAP_AUDIENCE_ENV, Settings, iap_audience_or_refuse
from ...domain.control_mapping.models import EvidencePack
from ...domain.horizon.models import HorizonAssessment, ImplementationItem
from ...domain.models import Answer
from ...envread import optional_setting, read_env_setting
from .._review_payload import (
    answer_to_review,
    assessment_to_review,
    implementation_to_review,
    pack_to_review,
)
from . import _s2s
from ._s2s import SIGNING_KEY_ENV, TOKEN_ENV

_URL_ENV = "HUMAN_REVIEW_URL"


class PlatformReviewRouter:
    """Submit escalated items to human-review-console (rule R8), reusing the shared submit client.

    Serves every R8 path against the single human-review-console contract: an escalated compliance
    :class:`Answer`, a control-mapping :class:`EvidencePack`, a horizon
    :class:`HorizonAssessment` and a horizon :class:`ImplementationItem` closure,
    dispatched by type.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        audience = optional_setting(HUMAN_REVIEW_IAP_AUDIENCE_ENV)
        self._audience = (
            None
            if audience is None
            else iap_audience_or_refuse(HUMAN_REVIEW_IAP_AUDIENCE_ENV, audience)
        )

    def route(
        self,
        answer: Answer | EvidencePack | HorizonAssessment | ImplementationItem,
        *,
        maker: str,
        tenant: str = "",
    ) -> None:
        client = self._client()
        if isinstance(answer, EvidencePack):
            client.submit(
                pack_to_review(answer, maker=maker, tenant=tenant), actor="rsk1-control-mapping"
            )
            return
        if isinstance(answer, HorizonAssessment):
            client.submit(
                assessment_to_review(answer, maker=maker, tenant=tenant), actor="rsk1-horizon"
            )
            return
        if isinstance(answer, ImplementationItem):
            client.submit(
                implementation_to_review(answer, maker=maker, tenant=tenant), actor="rsk1-horizon"
            )
            return
        client.submit(answer_to_review(answer, maker=maker, tenant=tenant), actor="rsk1-compliance")

    def _client(self) -> ReviewClient:
        base_url = read_env_setting(_URL_ENV).value
        if not base_url:
            raise RuntimeError(f"{_URL_ENV} must be set to route reviews to human-review-console")
        audience = self._audience
        if audience is None:
            return ReviewClient(base_url, token_env=TOKEN_ENV, signing_key_env=SIGNING_KEY_ENV)
        return ReviewClient(
            base_url,
            token_env=TOKEN_ENV,
            signing_key_env=SIGNING_KEY_ENV,
            bearer_provider=lambda: _s2s.fetch_id_token(audience),
        )
