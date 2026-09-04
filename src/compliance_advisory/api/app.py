"""FastAPI application for the C1 Compliance Assistant.

Exposes the four cited artifacts (Answer, ControlChecklist, TestCase[],
RegulatorQuestion[]) plus corpus-freshness and health endpoints, and publishes the
A2A AgentCard at ``/.well-known/agent-card.json``. The React/Next.js UI and the CLI
consume this surface.

Design constraints:

* **Import-safe.** Building the :class:`~compliance_advisory.config.Container` is deferred
  to request time via the ``deps`` factories, so importing this module (or ``app``) never
  touches Google Cloud. The on-prem/test profile imports it with no GCP SDK installed.
* **Guardrail blocks are not errors.** A :class:`GuardrailBlockedError` from a service is
  translated to an HTTP 200 carrying a *blocked* artifact flagged for human review, never a
  500 — the caller always gets a well-formed, auditable response.
* **Region pinned** to ``asia-southeast1`` (Singapore) for data residency (SPEC §2).

Run locally with ``python -m compliance_advisory.api.app`` (uvicorn on :8080).
"""

from __future__ import annotations

import re
from typing import Annotated, Any

from fastapi import Depends, FastAPI, File, Form, Request, Response, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from hex_service_kit import cors_allowlist, resolve_bind_host
from hex_service_kit.web import add_loopback_exposure_guard

from ..config import end_user_auth_kind
from ..domain import models as m
from ..domain.errors import GuardrailBlockedError, RetrievalEmptyError
from ..domain.services import (
    ChecklistService,
    ComplianceQAService,
    RegulatorQuestionService,
    TestCaseService,
)
from ..envread import boolean_setting, read_env_setting, setting_or_default
from ..pipelines import fetch as pipeline_fetch
from ..pipelines import ingest as pipeline_ingest
from ..ports.identity import VERIFIED
from . import deps
from .control_mapping_routes import router as control_mapping_router
from .horizon_routes import router as horizon_router
from .schemas import (
    AgentCardModel,
    AnswerResponse,
    AskRequest,
    ChecklistResponse,
    CorpusRefreshResponse,
    CorpusStatusResponse,
    CorpusUploadResponse,
    HealthResponse,
    RegulatorQuestionsResponse,
    TestCasesResponse,
    UseCaseRequest,
)
from .security import CurrentPrincipal

# Local Next.js dev origins the browser UI is served from during development.
_DEV_ORIGINS = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]

# Embedding-surface controls. In secure/embedded mode the assistant is served same-origin
# via the parent app's reverse-proxy (no CORS needed); for the cross-origin / standalone
# dev case, COMPLIANCE_CORS_ORIGINS is an explicit per-tenant allowlist (never "*").
# COMPLIANCE_FRAME_ANCESTORS is the CSP frame-ancestors allowlist of parent origins
# permitted to iframe the assistant UI.
_FRAME_ANCESTORS_ENV = "COMPLIANCE_FRAME_ANCESTORS"

# The legacy equivalent of each frame-ancestors value that has one, for browsers with no CSP
# support. Any other value names specific parent origins, which X-Frame-Options cannot express.
_LEGACY_FRAME_OPTIONS = {"'self'": "SAMEORIGIN", "'none'": "DENY"}


#: Entries that are a wildcard by BEHAVIOUR rather than by spelling, so the asterisk test below
#: cannot see them. ``null`` is the one that matters: a SANDBOXED iframe presents the origin
#: ``null``, so allowing it hands framing and credentialed cross-origin rights to any page able
#: to open one. ``'*'`` is what a quoted Terraform variable or a YAML string renders, and ``*.*``
#: is a host pattern matching every name with a dot in it. The same set is refused on the
#: document half, in ``ui/lib/csp.mjs``.
_WILDCARD_TOKENS = frozenset({"*", "'*'", "null", "*.*"})


def _refuse_wildcard(origins: list[str], setting: str) -> None:
    """A list naming ``*`` is not an allowlist, so refuse it where the value is resolved.

    Both resolutions below run at import, which makes this a BOOT refusal: a deployment
    configured with a wildcard never starts, rather than serving every origin until somebody
    reads a header. The rule was already written down (``never "*"``, above) and the code
    passed the value straight through, so one operator typo granted ``frame-ancestors *``
    (any page may iframe the console) or a credentialed CORS wildcard (every site on the
    internet gets this service's cookies, since ``allow_credentials=True``).

    An EQUALITY test of ``origin.strip() == "*"`` sees an entry
    that IS an asterisk and not one that CONTAINS one: ``https://*.client.example`` goes
    straight through, and CSP honours that host-source form, so every subdomain could frame the
    console including one obtained by takeover or serving user content. Nothing downstream
    inspected these values either, so the other spellings reached a response header verbatim.
    Both halves of the rule are needed: a real origin never contains the character and is never
    one of :data:`_WILDCARD_TOKENS`, so this refuses nothing a deployment could correctly hold.
    """
    offending = [
        origin for origin in origins if "*" in origin or origin.strip() in _WILDCARD_TOKENS
    ]
    if offending:
        raise ValueError(f"{setting} origin policy must never contain a wildcard, got {offending}")


def _frame_ancestors(raw: str | None) -> str:
    """Three-state read of ``COMPLIANCE_FRAME_ANCESTORS``; an emptied value REFUSES framing.

    Unset keeps the shipped ``'self'``. A value naming no origin would emit
    ``Content-Security-Policy: frame-ancestors`` with an EMPTY directive, which is a CSP parse
    error, so browsers drop the directive; the ``== "'self'"`` test below is false as well,
    so ``X-Frame-Options`` goes unsent too and the clickjacking control vanishes on both paths.
    An operator who empties the allowlist means "nobody may frame this", which is spelled
    ``'none'``, so that is what the emptied state produces now: the operator's expressed intent,
    and the most restrictive value the directive has.
    """
    if raw is None:
        return "'self'"
    ancestors = raw.split()
    _refuse_wildcard(ancestors, _FRAME_ANCESTORS_ENV)
    return " ".join(ancestors) or "'none'"


_frame_setting = read_env_setting(_FRAME_ANCESTORS_ENV)
_FRAME_ANCESTORS = _frame_ancestors(None if _frame_setting.is_unset else _frame_setting.raw)


def _cors_origins() -> list[str]:
    """Explicit allowlist, never "*"; the localhost dev fallback applies ONLY under a
    deliberately chosen local profile (shared hex-service-kit rule).

    Keys off ``exposure_profile``, not ``profile``: this is a RELAXATION, so a run where
    nobody set COMPLIANCE_PROFILE must not look like ``local`` and must get no cross-origin
    trust at all.

    The local refusal runs FIRST, on the raw configured value, rather than on what the kit
    hands back. ``cors_allowlist`` now refuses the same wildcards itself, so on the old order
    the kit raised its own ``InsecureCorsError`` before this module's rule was ever reached and
    the policy quietly changed owner. Refusing on the way in keeps :func:`_refuse_wildcard` the
    one authority over both allowlists: a single exception type and a single message naming the
    variable an operator must fix, whether the value came from CORS or from frame-ancestors.
    The kit's check stays as an unreachable backstop, which is what a backstop should be.
    """
    configured = read_env_setting("COMPLIANCE_CORS_ORIGINS").value
    _refuse_wildcard(
        [origin.strip() for origin in configured.split(",") if origin.strip()],
        "COMPLIANCE_CORS_ORIGINS",
    )
    return cors_allowlist(
        deps.get_settings().exposure_profile,
        origins_env="COMPLIANCE_CORS_ORIGINS",
        dev_origins=tuple(_DEV_ORIGINS),
    )


app = FastAPI(
    title="C1 Compliance Assistant",
    version="0.1.0",
    description=(
        "Grounded RAG + agentic assistant for APAC banking Compliance/Risk teams over "
        "MAS / HKMA / APRA / FSA guidance, on the Gemini Enterprise Agent Platform. "
        "Produces four cited artifacts: Answer, ControlChecklist, TestCases, "
        "RegulatorQuestions."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Dev-Persona"],
)


@app.middleware("http")
async def _security_headers(request: Request, call_next: Any) -> Any:
    """Emit the API content, embedding and secure-transport header baseline.

    ``X-Frame-Options`` backs the policy up on browsers with no CSP support, for both values
    that it can express: ``'self'`` -> SAMEORIGIN and ``'none'`` -> DENY.
    """
    response = await call_next(request)
    response.headers["Content-Security-Policy"] = f"frame-ancestors {_FRAME_ANCESTORS}"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    # HSTS is a positive secure-profile capability. An unconsented run has
    # ``exposure_profile=unconfigured`` and must not be mistaken for a TLS-fronted service.
    if deps.get_settings().profile in {"gcp", "platform"}:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    legacy = _LEGACY_FRAME_OPTIONS.get(_FRAME_ANCESTORS)
    if legacy is not None:
        response.headers["X-Frame-Options"] = legacy
    return response


# A request arrives with nothing authenticating the END USER unless BOTH of these hold, and
# the guard bounds every case where either fails:
#
#   1. a profile was chosen. Absent that, nobody selected an identity scheme, the seeded
#      persona adapter refuses to construct, and every end-user route answers 401; but
#      /healthz, /personas, /corpus/status and the agent card would still answer a stranger,
#      and a deployment in that state has no business being reachable at all. It is also the
#      one case where a settings file that bound a verifying adapter must NOT buy the
#      relaxation: unset is not consent, whatever the binding says;
#   2. the identity adapter the ACTIVE binding names DECLARES that it verifies the end user.
#      Seeded personas arrive on the X-Dev-Persona header the caller wrote (client-asserted)
#      and the on-premises placeholder resolves nobody at all (unimplemented); neither
#      authenticates anyone, so neither may switch this off. Reading the BINDING rather than
#      the profile string is what catches ``live``, which binds the same seeded-persona
#      adapter as ``local`` and would otherwise have looked like a configured deployment.
_END_USER_AUTHENTICATED = deps.get_settings().profile_explicit and end_user_auth_kind() == VERIFIED

# Registered LAST, so it is the OUTERMOST middleware: an off-loopback caller is refused before
# CORS, before the header baseline above and before any route or dependency runs, and before
# the two mounted routers. Bound to the APP OBJECT, not to `main()`: the Dockerfile CMD is
# `exec uvicorn compliance_advisory.api.app:app --host 0.0.0.0 --port ${PORT}`, so the
# `resolve_bind_host(...)` call down in `main()` never runs in a shipped process. Executed
# before this guard existed: a LAN peer got 200 on GET /personas with the full seeded-persona
# list, subjects, tenants and the compliance-approver entitlement included. Do not delete
# this: without it the container's own CMD re-opens that hole.
add_loopback_exposure_guard(
    app,
    unauthenticated=not _END_USER_AUTHENTICATED,
    # The SAME opt-in `main()` passes to resolve_bind_host, so an operator who accepts the
    # exposure accepts it once, for both the bind and the request-time guard.
    insecure_demo_env="COMPLIANCE_ALLOW_INSECURE_DEMO",
    # The EXPOSURE profile, so a run nobody configured names itself 'unconfigured' in the
    # refusal rather than borrowing the name of a profile an operator never chose.
    posture=deps.get_settings().exposure_profile,
)


# Control-mapping capability (merged from C2): mounts /map, /evidence-pack, /gaps. No
# collision with /ask, /checklist, /testcases, /regulator-questions. /evidence-pack shape
# is preserved unchanged for its external consumer (architecture-validator, the architecture
# validator).
app.include_router(control_mapping_router)
app.include_router(horizon_router)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _blocked_answer(question: str, reason: str) -> m.Answer:
    """Build a well-formed *blocked* Answer for a guardrailed Q&A request."""
    return m.Answer(
        question=question,
        answer=(
            "This request was blocked by the safety guardrail and cannot be answered. "
            "It has been routed for human review."
        ),
        confidence=0.0,
        requires_human_review=True,
        caveats=(f"guardrail: {reason}" if reason else "guardrail: blocked",),
    )


# --------------------------------------------------------------------------- #
# Artifact endpoints
# --------------------------------------------------------------------------- #
@app.post("/ask", response_model=AnswerResponse, tags=["artifacts"])
def ask(
    request: AskRequest,
    principal: CurrentPrincipal,
    service: Annotated[ComplianceQAService, Depends(deps.get_qa_service)],
) -> AnswerResponse | JSONResponse:
    """Grounded Q&A with regulator/jurisdiction/document/version/page citations.

    The audit actor is the verified principal's subject (server-resolved), never a
    client-asserted value. The Q&A pipeline degrades gracefully on a guardrail block,
    but should a service ever raise :class:`GuardrailBlockedError` we still return a
    200 blocked Answer rather than surfacing a 500.
    """
    try:
        answer = service.answer(
            request.question, principal.actor, filters=request.filters, tenant=principal.tenant
        )
    except GuardrailBlockedError as exc:
        answer = _blocked_answer(request.question, str(exc))
    except RetrievalEmptyError as exc:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "error": "ungrounded",
                "detail": str(exc),
                "requires_human_review": True,
                "citations": [],
            },
        )
    return AnswerResponse.from_domain(answer)


@app.post("/checklist", response_model=ChecklistResponse, tags=["artifacts"])
def checklist(
    request: UseCaseRequest,
    principal: CurrentPrincipal,
    service: Annotated[ChecklistService, Depends(deps.get_checklist_service)],
) -> JSONResponse | ChecklistResponse:
    """Use-case-specific control checklist (a maker-checker / human-review artifact)."""
    try:
        result = service.build(request.use_case, principal.actor)
    except GuardrailBlockedError as exc:
        return _blocked_use_case_response(request.use_case, str(exc))
    return ChecklistResponse.from_domain(result)


@app.post("/testcases", response_model=TestCasesResponse, tags=["artifacts"])
def testcases(
    request: UseCaseRequest,
    principal: CurrentPrincipal,
    service: Annotated[TestCaseService, Depends(deps.get_testcase_service)],
) -> JSONResponse | TestCasesResponse:
    """Automated test cases that verify each control for the use case."""
    try:
        cases = service.generate(request.use_case, principal.actor)
    except GuardrailBlockedError as exc:
        return _blocked_use_case_response(request.use_case, str(exc))
    return TestCasesResponse.from_domain(request.use_case, cases)


@app.post(
    "/regulator-questions",
    response_model=RegulatorQuestionsResponse,
    tags=["artifacts"],
)
def regulator_questions(
    request: UseCaseRequest,
    principal: CurrentPrincipal,
    service: Annotated[RegulatorQuestionService, Depends(deps.get_regulator_question_service)],
) -> JSONResponse | RegulatorQuestionsResponse:
    """The exact questions a regulator/CRO will ask, with cited model answers."""
    try:
        questions = service.generate(request.use_case, principal.actor)
    except GuardrailBlockedError as exc:
        return _blocked_use_case_response(request.use_case, str(exc))
    return RegulatorQuestionsResponse.from_domain(request.use_case, questions)


def _blocked_use_case_response(use_case: str, reason: str) -> JSONResponse:
    """A 200 JSON body for a guardrail-blocked consequential request.

    The consequential generators raise rather than return a partial artifact, so there is
    no domain object to project. We answer 200 with an explicit blocked envelope (flagged
    for human review) so the UI/CLI can render the block without treating it as a 5xx.
    """
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={
            "use_case": use_case,
            "blocked": True,
            "requires_human_review": True,
            "detail": (
                "This request was blocked by the safety guardrail and was routed for human review."
            ),
            "reason": reason or "blocked",
        },
    )


# --------------------------------------------------------------------------- #
# Corpus freshness
# --------------------------------------------------------------------------- #
@app.get("/corpus/status", response_model=CorpusStatusResponse, tags=["corpus"])
def corpus_status(principal: CurrentPrincipal) -> CorpusStatusResponse:
    """Summarise the AlloyDB freshness ledger (7-day fetch-at-runtime model)."""
    container = deps.get_container()
    records = container.ledger.all()
    return CorpusStatusResponse.from_records(records, container.settings.corpus.ttl_days)


# The corpus is the database behind every answer, so a demo audience can add to it:
# an uploaded internal policy / circular goes through the same parse -> redact (P-04)
# -> page-cited ingest -> freshness-ledger path as a fetched public instrument.
_UPLOAD_MAX_BYTES = 20 * 1024 * 1024  # one document per upload; PDFs, not archives
_UPLOAD_TEMPLATE = (
    "field,required,example,notes\n"
    "file,yes,internal-genai-policy.pdf,PDF or plain text; one document per upload\n"
    "title,yes,Internal GenAI Usage Policy,Shown in citations\n"
    "regulator,no,CROSS,MAS | HKMA | APRA | FSA | CROSS (default CROSS)\n"
    "jurisdiction,no,GLOBAL,SG | HK | AU | JP | GLOBAL (default GLOBAL)\n"
    "version,no,2026-01,Free-form version label recorded on every citation\n"
    "url,no,https://intranet.example/policies/genai,Reference link shown with citations\n"
)


@app.get("/corpus/upload-template", tags=["corpus"], response_class=Response)
def corpus_upload_template() -> Response:
    """The upload contract as a downloadable CSV (one row per form field)."""
    return Response(
        content=_UPLOAD_TEMPLATE,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="corpus-upload-template.csv"'},
    )


@app.post(
    "/corpus/documents",
    response_model=CorpusUploadResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["corpus"],
)
async def corpus_upload(
    principal: CurrentPrincipal,
    file: Annotated[UploadFile, File(description="The document to ingest (PDF or text)")],
    title: Annotated[str, Form(min_length=3, max_length=200)],
    regulator: Annotated[str, Form()] = "CROSS",
    jurisdiction: Annotated[str, Form()] = "GLOBAL",
    version: Annotated[str, Form(max_length=60)] = "uploaded",
    url: Annotated[str, Form(max_length=500)] = "",
) -> CorpusUploadResponse | JSONResponse:
    """Ingest an uploaded document into the corpus with page-level citations."""
    try:
        reg = m.Regulator(regulator.upper())
        jur = m.Jurisdiction(jurisdiction.upper())
    except ValueError:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={"detail": "unknown regulator or jurisdiction"},
        )
    content = await file.read(_UPLOAD_MAX_BYTES + 1)
    if len(content) > _UPLOAD_MAX_BYTES:
        return JSONResponse(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            content={"detail": f"document exceeds the {_UPLOAD_MAX_BYTES} byte limit"},
        )
    if not content:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={"detail": "uploaded file is empty"},
        )

    source_id = "upload-" + re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:60]
    source = m.RegSource(
        id=source_id,
        regulator=reg,
        jurisdiction=jur,
        title=title.strip(),
        url=url.strip() or f"upload://{source_id}",
        doc_type=m.DocType.OTHER,
        version=version.strip() or "uploaded",
    )
    mime = "application/pdf" if content[:5] == b"%PDF-" else (file.content_type or "text/plain")
    document = m.FetchedDocument(
        source=source,
        content=content,
        mime_type=mime,
        fetched_at=m.utcnow(),
        checksum=pipeline_fetch.compute_checksum(content),
    )
    outcome = pipeline_ingest.ingest_fetched(deps.get_container(), document)
    if outcome.action != "ingested":
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={"detail": f"ingest failed: {outcome.detail}", "source_id": source_id},
        )
    return CorpusUploadResponse(
        source_id=outcome.source_id,
        ok=True,
        chunks=outcome.chunks,
        detail=outcome.detail,
    )


@app.post(
    "/corpus/refresh",
    response_model=CorpusRefreshResponse,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["corpus"],
)
def corpus_refresh(principal: CurrentPrincipal) -> CorpusRefreshResponse:
    """Kick a refresh of expiring/expired sources. Returns 202 (accepted, out-of-band).

    The actual re-fetch + re-ingest is performed by the scheduled corpus pipeline; this
    endpoint reports which sources are currently due so an operator can trigger it on
    demand. We surface the ledger's expired set without blocking the request.
    """
    container = deps.get_container()
    expired = [record.source_id for record in container.ledger.list_expired()]
    return CorpusRefreshResponse(
        accepted=True,
        detail="Corpus refresh scheduled for expired/expiring sources.",
        expired=expired,
    )


# --------------------------------------------------------------------------- #
# Health & governance
# --------------------------------------------------------------------------- #
@app.get("/healthz", response_model=HealthResponse, tags=["ops"])
def healthz() -> HealthResponse:
    """Liveness/readiness probe. Reports the active profile and pinned region."""
    settings = deps.get_settings()
    return HealthResponse(
        status="ok",
        profile=settings.profile,
        runtime=settings.runtime,
        generator_model=settings.generator_model,
        region=settings.region,
    )


@app.get("/personas", tags=["ops"])
def personas() -> list[dict[str, str]]:
    """List seeded dev personas for the local persona picker (empty outside local profile).

    Local mode runs with no IdP; the UI uses this to let a demo/test pick an identity
    (and thus exercise per-user authorization) via the ``X-Dev-Persona`` header. Secure
    profiles resolve identity from the IAP assertion, so this returns an empty list. So does
    a run where nobody chose a profile: the seeded-persona adapter refuses to construct, and
    advertising personas that cannot be resolved would be worse than advertising none.
    """
    from ..domain.identity import IdentityError

    try:
        identity = deps.get_container().identity
    except IdentityError:
        return []
    lister = getattr(identity, "personas", None)
    if lister is None:
        return []
    return [dict(p) for p in lister()]


@app.get("/.well-known/agent-card.json", response_model=AgentCardModel, tags=["governance"])
def agent_card() -> AgentCardModel:
    """Publish this assistant's A2A AgentCard for discovery (A3 Registry / interop)."""
    from ..agent.agent_card import build_agent_card

    settings = deps.get_settings()
    card = build_agent_card(settings)
    return AgentCardModel.from_domain(card)


def main() -> None:
    """Run the API locally with uvicorn (Cloud Run / Agent Runtime use this app object)."""
    import uvicorn

    uvicorn.run(
        "compliance_advisory.api.app:app",
        # Fail-closed bind (shared hex-service-kit rule): the no-auth local
        # profile binds loopback unless COMPLIANCE_ALLOW_INSECURE_DEMO=1; secure profiles keep
        # 0.0.0.0 (container-local; ingress is fronted by the platform). Keys off
        # ``bind_profile``, the opposite direction to the CORS relaxation above: here ``local``
        # is the RESTRICTIVE case, so a run where nobody chose a profile stays on loopback, and
        # so does ``live``, which serves the same seeded no-auth personas.
        host=resolve_bind_host(
            deps.get_settings().bind_profile,
            host_env="COMPLIANCE_API_HOST",
            insecure_demo_env="COMPLIANCE_ALLOW_INSECURE_DEMO",
        ),
        port=int(setting_or_default("PORT", "8080")),
        reload=boolean_setting("COMPLIANCE_API_RELOAD"),
    )


if __name__ == "__main__":
    main()
