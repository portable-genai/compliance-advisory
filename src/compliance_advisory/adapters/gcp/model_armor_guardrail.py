"""Model Armor guardrail adapter (A1 Guardrail Gateway, primary GCP backend).

Implements :class:`GuardrailPort` against **Model Armor**, the runtime AI-safety
service of the Gemini Enterprise Agent Platform. Inbound prompts are screened with
``:sanitizeUserPrompt`` and outbound model responses with ``:sanitizeModelResponse``
on the regional endpoint ``modelarmor.asia-southeast1.rep.googleapis.com`` so all
screening stays inside Singapore for MAS/HKMA/APRA/FSA residency.

The adapter parses ``sanitizationResult.filterResults`` — the prompt-injection /
jailbreak, Sensitive Data Protection (SDP), malicious-URI and Responsible-AI (RAI)
filters — into :class:`GuardrailFinding` records.

The verdict FAILS CLOSED. It is *allowed* only when ``sanitizationResult.filterMatchState``
is ``NO_MATCH_FOUND`` AND ``sanitizationResult.invocationResult`` is ``SUCCESS``, each
compared as the enum NAME the REST (proto3 JSON) mapping carries. Everything else blocks:

- ``MATCH_FOUND``, however many filters ran;
- an absent or empty ``sanitizationResult``, an absent ``filterMatchState`` and
  ``FILTER_MATCH_STATE_UNSPECIFIED`` (proto3 JSON omits a zero enum, so UNSPECIFIED usually
  arrives as an absent field): a screen that did not say "no match" has not said "allowed";
- ``NO_MATCH_FOUND`` with ``invocationResult`` ``PARTIAL`` (some filters were skipped or
  failed), ``FAILURE`` (all were), unspecified or absent. A skipped filter reports
  ``EXECUTION_SKIPPED`` and no match, so padding a prompt past the prompt-injection filter's
  token limit would otherwise get it through unscreened.

An API error, including the per-call deadline, propagates to the domain, which refuses the
request.

All Google Cloud / auth SDK imports are lazy (inside ``__init__`` / methods) so the
on-prem and test profiles import this module with no GCP SDK installed.
"""

from __future__ import annotations

from typing import Any, TypeGuard

from ...config import Settings
from ...domain.models import (
    Direction,
    GuardrailCategory,
    GuardrailFinding,
    GuardrailVerdict,
)

_MATCH_FOUND = "MATCH_FOUND"
#: The one filter state that allows, compared by enum NAME.
_NO_MATCH_FOUND = "NO_MATCH_FOUND"
#: The one invocation result that allows: every configured filter ran. Compared by NAME.
_ALL_FILTERS_RAN = "SUCCESS"
#: Per-call deadline in seconds, so a stalled backend refuses the request instead of holding it.
_TIMEOUT_SECONDS = 30.0

# RAI sub-type key (as returned by Model Armor) -> domain GuardrailCategory.
_RAI_CATEGORY: dict[str, GuardrailCategory] = {
    "hate_speech": GuardrailCategory.HATE,
    "harassment": GuardrailCategory.HARASSMENT,
    "sexually_explicit": GuardrailCategory.SEXUAL,
    "dangerous": GuardrailCategory.DANGEROUS,
}


class ModelArmorGuardrailAdapter:
    """Screen prompts and responses through Model Armor's REST API."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._armor = settings.model_armor
        self._project = settings.project_id
        self._region = settings.region
        # httpx and google-auth are resolved lazily on first screen() call.
        self._client: Any | None = None
        self._credentials: Any | None = None
        self._auth_request: Any | None = None

    # -- public API -------------------------------------------------------- #
    def screen(self, text: str, direction: Direction) -> GuardrailVerdict:
        """Screen ``text`` and return a verdict; blocks on any filter match."""
        verb = "sanitizeUserPrompt" if direction is Direction.INPUT else "sanitizeModelResponse"
        payload = self._build_payload(text, direction)
        url = (
            f"https://{self._armor.host}/v1/projects/{self._project}"
            f"/locations/{self._region}/templates/{self._armor.template_id}:{verb}"
        )
        response = self._post(url, payload)
        return self._parse(response, direction, text)

    # -- request construction ---------------------------------------------- #
    def _build_payload(self, text: str, direction: Direction) -> dict[str, Any]:
        # The request body keys the data object by direction.
        # verify: https://docs.cloud.google.com/model-armor/sanitize-prompts-responses
        if direction is Direction.INPUT:
            return {"userPromptData": {"text": text}}
        return {"modelResponseData": {"text": text}}

    def _post(self, url: str, payload: dict[str, Any]) -> Any:
        client = self._http_client()
        token = self._bearer_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        resp = client.post(url, json=payload, headers=headers, timeout=_TIMEOUT_SECONDS)
        resp.raise_for_status()
        # Returned unvalidated: _parse treats any shape but a complete, clean screen as a block.
        return resp.json()

    def _http_client(self) -> Any:
        import httpx  # lazy

        if self._client is None:
            self._client = httpx.Client()
        return self._client

    def _bearer_token(self) -> str:
        # google.auth.default() yields ADC credentials; refresh via a transport
        # request to mint a short-lived OAuth2 bearer token for the REST call.
        import google.auth  # lazy
        from google.auth.transport.requests import Request  # lazy

        if self._credentials is None:
            self._credentials, _ = google.auth.default(
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
            self._auth_request = Request()
        if not self._credentials.valid:
            self._credentials.refresh(self._auth_request)
        token: str = self._credentials.token
        return token

    # -- response parsing -------------------------------------------------- #
    def _parse(self, response: Any, direction: Direction, original_text: str) -> GuardrailVerdict:
        """Map a sanitize response to a verdict: allowed ONLY on a complete, clean screen."""
        result = response.get("sanitizationResult") if isinstance(response, dict) else None
        if not isinstance(result, dict):
            result = {}
        filter_results = result.get("filterResults")
        if not isinstance(filter_results, dict):
            filter_results = {}
        match_state = result.get("filterMatchState")
        invocation = result.get("invocationResult")

        if match_state == _NO_MATCH_FOUND and invocation == _ALL_FILTERS_RAN:
            return GuardrailVerdict(
                allowed=True,
                direction=direction,
                findings=(),
                sanitized_text=self._extract_sanitized_text(filter_results, original_text),
                reason="No blocking Model Armor filter matched.",
            )

        findings: list[GuardrailFinding] = []
        if match_state == _MATCH_FOUND:
            # A match is a match however many filters ran: the block needs no complete screen.
            findings.extend(self._parse_pi_jailbreak(filter_results))
            findings.extend(self._parse_sensitive_data(filter_results))
            findings.extend(self._parse_malicious_uris(filter_results))
            findings.extend(self._parse_rai(filter_results))
            if not findings:
                findings.append(self._blocking_finding("Model Armor filter match."))
            categories = ", ".join(sorted({f.category.value for f in findings}))
            reason = f"Blocked by Model Armor: {categories}."
        elif match_state == _NO_MATCH_FOUND:
            findings.append(
                self._blocking_finding(
                    f"invocationResult={invocation or 'absent'}: not every filter ran."
                )
            )
            reason = "Blocked: Model Armor returned no complete filter decision."
        else:
            findings.append(self._blocking_finding(f"filterMatchState={match_state or 'absent'}."))
            reason = "Blocked: Model Armor returned no filter decision."
        return GuardrailVerdict(
            allowed=False,
            direction=direction,
            findings=tuple(findings),
            sanitized_text=None,
            reason=reason,
        )

    @staticmethod
    def _blocking_finding(detail: str) -> GuardrailFinding:
        return GuardrailFinding(category=GuardrailCategory.OTHER, confidence="high", detail=detail)

    @staticmethod
    def _is_match(node: Any) -> TypeGuard[dict[str, Any]]:
        return isinstance(node, dict) and node.get("matchState") == _MATCH_FOUND

    def _parse_pi_jailbreak(self, filter_results: dict[str, Any]) -> list[GuardrailFinding]:
        node = (filter_results.get("pi_and_jailbreak") or {}).get("piAndJailbreakFilterResult")
        if not self._is_match(node):
            return []
        confidence = str(node.get("confidenceLevel", "")).lower() or "high"
        return [
            GuardrailFinding(
                category=GuardrailCategory.PROMPT_INJECTION,
                confidence=confidence,
                detail="Model Armor prompt-injection / jailbreak filter matched.",
            )
        ]

    def _parse_sensitive_data(self, filter_results: dict[str, Any]) -> list[GuardrailFinding]:
        inspect = (filter_results.get("sdp") or {}).get("sdpFilterResult", {}).get("inspectResult")
        if not self._is_match(inspect):
            return []
        info_types = sorted(
            {
                str(f.get("infoType", ""))
                for f in (inspect.get("findings") or [])
                if f.get("infoType")
            }
        )
        detail = (
            f"Sensitive data detected: {', '.join(info_types)}."
            if info_types
            else "Model Armor Sensitive Data Protection filter matched."
        )
        return [
            GuardrailFinding(
                category=GuardrailCategory.SENSITIVE_DATA,
                confidence="high",
                detail=detail,
            )
        ]

    def _parse_malicious_uris(self, filter_results: dict[str, Any]) -> list[GuardrailFinding]:
        node = (filter_results.get("malicious_uris") or {}).get("maliciousUriFilterResult")
        if not self._is_match(node):
            return []
        return [
            GuardrailFinding(
                category=GuardrailCategory.MALICIOUS_URL,
                confidence="high",
                detail="Model Armor malicious-URI filter matched.",
            )
        ]

    def _parse_rai(self, filter_results: dict[str, Any]) -> list[GuardrailFinding]:
        rai = (filter_results.get("rai") or {}).get("raiFilterResult")
        if not self._is_match(rai):
            return []
        sub_results = rai.get("raiFilterTypeResults", {}) or {}
        findings: list[GuardrailFinding] = []
        for key, category in _RAI_CATEGORY.items():
            sub = sub_results.get(key)
            if not self._is_match(sub):
                continue
            confidence = str(sub.get("confidenceLevel", "")).lower() or "medium"
            findings.append(
                GuardrailFinding(
                    category=category,
                    confidence=confidence,
                    detail=f"Model Armor Responsible-AI filter matched: {key}.",
                )
            )
        if not findings:
            # RAI matched but no recognised sub-type — record a generic finding.
            findings.append(
                GuardrailFinding(
                    category=GuardrailCategory.OTHER,
                    confidence="medium",
                    detail="Model Armor Responsible-AI filter matched.",
                )
            )
        return findings

    def _extract_sanitized_text(
        self, filter_results: dict[str, Any], original_text: str
    ) -> str | None:
        # When SDP de-identification is configured on the template, Model Armor
        # returns the redacted text under sdp.sdpFilterResult.deidentifyResult.data.
        deidentify = (
            (filter_results.get("sdp") or {}).get("sdpFilterResult", {}).get("deidentifyResult")
        )
        if isinstance(deidentify, dict):
            data = deidentify.get("data") or {}
            text = data.get("text")
            if isinstance(text, str) and text:
                return text
        return original_text
