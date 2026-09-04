# Embedding and identity: client integration guide (`compliance-advisory` compliance-advisory)

How to drop the `compliance-advisory` into an existing web application (or run it
standalone) with the user journey intact and identity enforced server-side. The pattern
is the catalog-wide `embeddable-secure-ui` slice; the fuller reference implementation
(including the designed cross-origin modes) lives in `cdd-sow-research`.

The assistant is two pieces:

- **Backend**: FastAPI service (`compliance_advisory.api.app`, port 8080 via
  `make run-api`) exposing `/ask`, `/checklist`, `/testcases`, `/regulator-questions`,
  `/corpus/*`, `/healthz`, `/personas`, and the A2A card.
- **UI**: Next.js console (`ui/`), a pure presentation layer over that API.

## 1. The identity contract (read this first)

**Any client-supplied `actor` is ignored.** The request schemas no longer carry an
`actor` field, and a legacy body that still includes one is discarded by the API.
Instead, every artifact route resolves a verified `Principal` server-side through the
`IdentityPort` (`src/compliance_advisory/ports/identity.py`):

| Profile | Adapter | How identity is established |
|---------|---------|-----------------------------|
| `local` | `adapters/local/identity.py` `LocalPersonaIdentityAdapter` | Seeded dev personas, selected by the `X-Dev-Persona` header. No IdP, AD, or LDAP: demos and tests run offline. |
| `gcp`, `platform` | `adapters/gcp/iap_identity.py` `IapIdentityAdapter` | Verifies the Cloud IAP-injected `x-goog-iap-jwt-assertion` JWT (signature, audience, issuer, expiry). Auth is configured ON the GCP service. |
| `onprem` | `adapters/onprem/identity.py` `OnPremIdentityAdapter` | Fail-fast placeholder: implement your enterprise IdP (OIDC/SAML) verification here. |

The verified `Principal` supplies:

- the **audit actor** (`principal.actor`, the verified subject) written into every WORM
  `AuditEvent`, and
- the **entitlement principals and tenant** for authorization decisions. `compliance-advisory`'s
  retrieval corpus is public regulatory guidance (no per-case ACL partition), so the
  principals are not yet fed into a retrieval ACL; repos with governed, ACL-tagged
  knowledge bases (see `cdd-sow-research`) merge them into every KB query.

A request whose identity cannot be resolved (unknown persona, missing or invalid IAP
assertion) gets **401**. `GET /healthz`, `GET /personas`, and the agent card stay
unauthenticated: the probe, the picker bootstrap, and discovery must work pre-login.

## 2. The three deployment shapes

### 2.1 Local development: no auth, seeded personas

```bash
# Backend (repo root); profile defaults to local, no GCP SDK needed
make run-api                                   # FastAPI on :8080

# UI (in ./ui)
NEXT_PUBLIC_API_BASE=http://localhost:8080 npm run dev   # console on :3000
```

Because `/healthz` reports `profile: "local"`, the console shows a **Demo identity**
picker (left rail) listing the four seeded personas from `GET /personas`:

| Persona id | Subject | Tenant | Groups |
|------------|---------|--------|--------|
| `analyst` (default) | `demo.analyst@bank.example` | `demo-bank` | compliance-analyst, risk |
| `approver` | `demo.approver@bank.example` | `demo-bank` | analyst groups + compliance-approver |
| `auditor` | `demo.auditor@bank.example` | `demo-bank` | audit |
| `other-tenant` | `user@other-tenant.example` | `other-bank` | compliance-analyst |

The picker sets the `X-Dev-Persona` request header; the backend resolves the persona
and uses its subject as the audit actor. Unknown persona ids are rejected with 401.
The cross-tenant persona exists so per-tenant behavior is demoable offline. Secure
profiles ignore the header entirely and `/personas` returns `[]`, so the picker never
renders outside local mode.

```bash
# Same thing over curl: no actor in the body, persona in the header (local only)
curl -s localhost:8080/ask \
  -H 'content-type: application/json' -H 'X-Dev-Persona: auditor' \
  -d '{"question": "What does MAS expect for cloud outsourcing?"}'
```

### 2.2 Standalone, secure: behind Cloud IAP (gcp profile)

Deploy the backend with `COMPLIANCE_PROFILE=gcp` behind an HTTPS load balancer with
**Identity-Aware Proxy** enabled (see `docs/runbook.md` and `infra/terraform/`). IAP
authenticates every request against your IdP (Google Workspace, or an external/client
IdP federated via **Workforce Identity Federation**) before it reaches the service,
and injects a signed assertion that the `IapIdentityAdapter` re-verifies in-process
(defense in depth: never trust the header without verifying the JWT).

Required setting: `COMPLIANCE_IAP_AUDIENCE`, the IAP audience string of the protected
resource (`/projects/<NUM>/global/backendServices/<ID>` for an HTTPS LB). Missing or
unverifiable assertions are 401s. The assertion is never logged.

### 2.3 Embedded in an existing app: same-origin reverse proxy

Serve the assistant **under the host application's own origin** so the iframe is
first-party: no third-party-cookie problems and no CORS at all.

Host-side nginx (the host page lives on `https://portal.client.example`):

```nginx
# UI: Next.js built with NEXT_PUBLIC_BASE_PATH=/assistant
location /assistant/ {
    proxy_pass http://compliance-ui:3000;
    proxy_set_header Host $host;
}
# API: the UI calls NEXT_PUBLIC_API_BASE=/assistant/api
location /assistant/api/ {
    proxy_pass http://compliance-api:8080/;   # trailing slash strips the prefix
    proxy_set_header Host $host;
}
```

UI build-time environment for this shape:

```bash
NEXT_PUBLIC_BASE_PATH=/assistant   # mounts the app + assets under the sub-path
NEXT_PUBLIC_API_BASE=/assistant/api
NEXT_PUBLIC_EMBED=1                # drop the console chrome; the host owns it
```

(For local dev parity you can instead add a Next `rewrites()` entry proxying
`/assistant/api/:path*` to `http://localhost:8080/:path*`; in production prefer the
host reverse proxy above.)

Host page iframe:

```html
<iframe
  src="/assistant/"
  title="Compliance Assistant"
  style="width: 100%; height: 800px; border: 0"
></iframe>
```

Backend framing policy: the API (and, behind the same proxy, the UI) emits
`Content-Security-Policy: frame-ancestors ...` from `COMPLIANCE_FRAME_ANCESTORS`.
The variable resolves in **three** states, because an emptied allowlist is a
configuration and not an omission: unset keeps `'self'`; **set and empty resolves to
`'none'`**, the operator's expressed intent that nobody may frame this and the most
restrictive value the directive has; set names exactly those parent origins. The
legacy `X-Frame-Options` backstop is emitted for both values it can express,
`SAMEORIGIN` for `'self'` and `DENY` for `'none'`; a multi-origin allowlist is left
to CSP alone, which is the only header that can express it. Before this, an emptied
variable emitted `frame-ancestors` with no value, which browsers discard as a parse
error, and skipped the `X-Frame-Options` branch as well, so the clickjacking control
disappeared from both headers at once.

Same-origin embedding works with the default. If the UI is ever framed from a
different origin, list the parent origins explicitly, space-separated:

```bash
export COMPLIANCE_FRAME_ANCESTORS="https://portal.client.example https://admin.client.example"
```

### 2.4 The console's own Content-Security-Policy

The framing rule above is only one directive. The console document itself is served by
Next.js, not by the API, so it needs a full policy of its own. A console that ships exactly
one directive, `frame-ancestors`, with no `default-src`, no `script-src`, no `object-src` and
no `base-uri` at all, has no policy worth the name.

The console builds its policy in ONE place, `ui/lib/csp.mjs`, and emits it from ONE
place, `ui/proxy.ts`. Both halves matter:

- **One policy module.** `ui/next.config.mjs` does not emit a
  `Content-Security-Policy` at all; it carries only the two headers a static table can
  express (`X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`). Two layers
  each setting a CSP would hand the browser two policies to intersect, and the stricter one
  wins per directive, which quietly reinstates whatever the looser layer was there to relax.
- **A per-request nonce, on a dynamically rendered route.** `script-src` is
  `'self' 'nonce-<per-request>' 'strict-dynamic'`. Next serves its hydration bootstrap as an
  INLINE script carrying the Flight payload, so under a bare `script-src 'self'` the browser
  blocks it, `__next_f` never fills, React never attaches, and every control on the page is
  dead markup while the headers, the build and the type-check all stay green.

  The nonce is set on the REQUEST headers (where Next reads the value it stamps onto every
  script tag) as well as on the RESPONSE (what the browser enforces). A nonce on the response
  alone blocks the very scripts it was added to allow.

  There is a trap in the half-configured state, and it is worse than the bare policy above: a
  nonce on a STATICALLY prerendered route means nothing in the HTML carries it, and
  `'strict-dynamic'` switches off the `'self'` fallback that would otherwise at least load the
  chunk scripts. `app/layout.tsx` therefore sets `export const dynamic = "force-dynamic"`,
  and `next.config.mjs` REFUSES to build or boot without it.

`frame-ancestors` in the console mirrors the service's three-state read exactly, under the
build-time variable `NEXT_PUBLIC_FRAME_ANCESTORS`: unset keeps `'self'`, set-but-naming-nothing
resolves to `'none'`, and a set value names exactly those origins. The two halves of the
embedding posture have to agree, so the console does not get a two-state shortcut of its own.
Set it to the same origins as `COMPLIANCE_FRAME_ANCESTORS`.

Only a browser executing the page can tell the working case from the broken one, because the
CSP header is byte-identical in both. `ui/scripts/assert-hydratable.mjs` starts the BUILT
server, fetches the served document and asserts every script tag carries the served nonce; it
is the last step of `make ui-check` and a required CI step for that reason.

CORS is only relevant when the browser calls the API **cross-origin** (e.g. the dev
console on :3000 against the API on :8080). `COMPLIANCE_CORS_ORIGINS` is an explicit
comma-separated allowlist (default: the localhost dev origins; never `*`), with
methods pinned to `GET, POST, OPTIONS` and headers to
`Content-Type, Authorization, X-Dev-Persona`.

## 3. Configuration reference

| Knob | Where | Default | Meaning |
|------|-------|---------|---------|
| `COMPLIANCE_PROFILE` | backend env | `local` | Adapter family: `local`, `gcp`, `platform`, `onprem`. Binds the identity adapter too. |
| `COMPLIANCE_IAP_AUDIENCE` | backend env | empty | Expected IAP JWT audience; required in gcp/platform profiles. |
| `COMPLIANCE_CORS_ORIGINS` | backend env | unset: the localhost dev origins under a deliberate `local` profile, nothing otherwise | Comma-separated browser-origin allowlist for cross-origin API calls. Never `*`. Set and empty trusts no origin at all. |
| `COMPLIANCE_FRAME_ANCESTORS` | backend env | unset: `'self'` | CSP `frame-ancestors` allowlist: which parent origins may iframe the assistant. Set and empty resolves to `'none'`, so nobody may frame it. |
| `NEXT_PUBLIC_API_BASE` | UI build env | `http://localhost:8000` | Backend base URL (use `/assistant/api` behind a reverse proxy, `:8080` in dev). |
| `NEXT_PUBLIC_BASE_PATH` | UI build env | empty | Sub-path mount (`basePath`/`assetPrefix`); blank keeps standalone behavior. |
| `NEXT_PUBLIC_EMBED` | UI build env | unset | `1` renders the console without its own chrome. |
| `NEXT_PUBLIC_FRAME_ANCESTORS` | UI runtime env | unset: `'self'` | CSP `frame-ancestors` for the console DOCUMENT (the API knob above covers API responses). Three-state, mirroring `COMPLIANCE_FRAME_ANCESTORS`: set and empty resolves to `'none'`. Read per request in `ui/proxy.ts`, so it is a deploy-time value, not baked into the build. |
| `X-Dev-Persona` | request header | unset | Local profile only: selects a seeded persona. Ignored by secure profiles. |

## 4. Client integration checklist

- [ ] Choose the shape: same-origin embed (preferred), standalone behind IAP, or local.
- [ ] Embed: reverse-proxy `/assistant/` (UI) and `/assistant/api/` (API) under the host
      origin; build the UI with `NEXT_PUBLIC_BASE_PATH`, `NEXT_PUBLIC_API_BASE`,
      `NEXT_PUBLIC_EMBED=1`.
- [ ] Do not send `actor` in request bodies; it is ignored. Remove it from any client.
- [ ] Secure profiles: enable IAP on the load balancer, set `COMPLIANCE_IAP_AUDIENCE`,
      and federate the client IdP via Workforce Identity Federation if needed.
- [ ] Set `COMPLIANCE_FRAME_ANCESTORS` to the exact parent origins (keep `'self'` for
      same-origin embedding).
- [ ] Set `COMPLIANCE_CORS_ORIGINS` only if the browser must call the API cross-origin.
- [ ] Verify `/healthz` reports the intended profile before going live.

## 5. Security checklist

- [ ] Identity is verified server-side per request; a 401 is returned when it is not.
- [ ] The IAP assertion is re-verified in-process (audience pinned), not trusted as a
      plain header, and never logged.
- [ ] The audit trail records the verified subject for every artifact (WORM sink).
- [ ] CORS allowlist is explicit (no `*`), methods and headers pinned.
- [ ] `frame-ancestors` limits framing to known parents; `X-Frame-Options: SAMEORIGIN`
      accompanies the `'self'` default for older agents.
- [ ] The console document ships the FULL policy (`default-src 'self'`, `object-src 'none'`,
      `base-uri 'self'`, nonce-based `script-src`), from one module and one enforcement point.
- [ ] `npm run assert-hydratable` passes against the built console: the served nonce is on
      every script tag, so the page actually hydrates rather than merely looking right.
- [ ] Local personas are obviously fictional and bound ONLY under the local profile.
- [ ] PEP is defense-in-depth: edge (IAP) -> `agent-guardrail-gateway` -> this per-backend check.

## 6. Further layers (documented, not built in this slice)

- **Mode 6 "launch in new tab" OIDC login** (Authorization Code + PKCE, self-issued
  session cookie, 401-to-login redirects) and the designed **cross-origin embed modes**
  (custom-element loader, postMessage bus, bearer/JWKS verification, per-tenant runtime
  `frame-ancestors`): reference implementation and design in
  `cdd-sow-research/docs/embedding-and-identity.md`.
- **Per-hop OAuth2 token exchange (on-behalf-of) + Workload Identity + mTLS** to the
  horizontal-platform services, DPoP / step-up auth for high-value actions: the next
  hardening layer once service-to-service identity is prioritized.
- **On-prem IdP integration**: replace the fail-fast `OnPremIdentityAdapter` with your
  OIDC/SAML verification; nothing else changes (see `docs/onprem-migration.md`).
