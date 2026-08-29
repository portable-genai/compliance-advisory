"""Platform ReviewRouterPort: submit the routed answer review to Hrz7 via ``review-kit``.

Builds the review from the escalated answer and submits it to the Hrz7 service intake
(``POST /v1/service/reviews``), S2S-authenticated. The Hrz7 base URL comes from the environment
(``HUMAN_REVIEW_URL``) and the S2S credentials from this repo's shared env-var names
(``S2S_TOKEN`` / ``S2S_SIGNING_KEY``, the same pair the other platform delegates use). No
cloud SDK is involved (the kit uses stdlib ``urllib`` + wire-compatible S2S headers), so this
module imports cleanly with no GCP SDK; it is bound under the ``gcp`` and ``platform`` profiles
because it makes a real network call to a sibling service.
"""

from __future__ import annotations

from review_kit import ReviewClient

from ...config import Settings
from ...domain.control_mapping.models import EvidencePack
from ...domain.horizon.models import HorizonAssessment, ImplementationItem
from ...domain.models import Answer
from ...envread import read_env_setting
from .._review_payload import (
    answer_to_review,
    assessment_to_review,
    implementation_to_review,
    pack_to_review,
)
from ._s2s import SIGNING_KEY_ENV, TOKEN_ENV

_URL_ENV = "HUMAN_REVIEW_URL"


class PlatformReviewRouter:
    """Submit escalated items to Hrz7 (rule R8), reusing the shared submit client.

    Serves every R8 path against the single Hrz7 contract: an escalated compliance
    :class:`Answer`, a control-mapping :class:`EvidencePack`, a horizon
    :class:`HorizonAssessment` and a horizon :class:`ImplementationItem` closure,
    dispatched by type.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def route(
        self,
        answer: Answer | EvidencePack | HorizonAssessment | ImplementationItem,
        *,
        maker: str,
        tenant: str = "",
    ) -> None:  # pragma: no cover - needs live Hrz7
        base_url = read_env_setting(_URL_ENV).value
        if not base_url:
            raise RuntimeError(f"{_URL_ENV} must be set to route reviews to Hrz7")
        client = ReviewClient(base_url, token_env=TOKEN_ENV, signing_key_env=SIGNING_KEY_ENV)
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
