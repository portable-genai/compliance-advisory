# Security FAQ

For an application-security team reviewing this repo before adopting it as a base.
Cross-references: [`ARCHITECTURE.md`](../../ARCHITECTURE.md), [`COMPLIANCE.md`](../../COMPLIANCE.md),
[`docs/embedding-and-identity.md`](../embedding-and-identity.md).

### How is a request authenticated? Can a client spoof its identity?

No. Identity is resolved **server-side** from the transport context by an `IdentityPort`
adapter (`api/security.py::get_principal` -> `domain/identity.py`), never from the request
body. The request schemas carry no `actor` field, and any client-asserted actor or ACL is
ignored (`api/schemas.py` documents this explicitly). The audit actor is `principal.actor`,
the verified subject. Per profile: `local` = seeded dev personas (no IdP, offline only),
`gcp` / `platform` = the IAP-injected signed assertion verified by `IapIdentityAdapter`
(auth configured ON the fronting service). An unresolved principal fails closed (401).

### Is there a web login flow to review?

No. The repo owns no login flow (no PKCE / OIDC / JWKS code in `src/`). Secure-mode identity
is the IAP-injected assertion the fronting service verifies; `local` uses seeded personas.
That keeps the attack surface small, but it also means the IdP / IAP hardening is a
deployment responsibility, wired per the adoption checklist, not something this repo
implements.

### Is there object-level authorization / tenant isolation?

Not in the retrieval path, by design. The knowledge base is a **shared corpus of public
regulatory guidance** (MAS/HKMA/APRA/FSA), identical for every authenticated user, so there
is no tenant-private or case-private evidence to isolate. `ports/retrieval.py` carries no ACL
contract and the retrieval adapters do no tenant filtering. `Principal` still carries
`tenant` / `principals` fields for the audit actor and any future governed source, and
identity is verified server-side. A fork that adds a private source must add the ACL and
subset-match filtering at that point.

### What about the service-to-service calls in the `platform` profile?

The platform adapters source the shared `hex_service_kit.s2s` via `adapters/platform/_s2s.py`.
All four delegates (`remote_audit`, `remote_guardrail`, `remote_registry`, `remote_evaluation`)
validate their base URL at construction (https-only outside loopback, rejected otherwise),
attach an S2S bearer from `HRZ_S2S_TOKEN`, and propagate the verified end-user actor as a
signed header (`HRZ_S2S_SIGNING_KEY`, headers `X-Ca-Actor` / `-Sig`) rather than a trust-me
JSON field. The receiving platform services own verification.

### Is the demo/dev server safe? Does anything bind 0.0.0.0 by default?

Under the `local` profile the API and `make run-api` bind **loopback (127.0.0.1)** by
default via `hex_service_kit.resolve_bind_host`; serving the no-auth persona adapter on a
non-loopback interface requires `COMPLIANCE_ALLOW_INSECURE_DEMO=1`. Secure profiles keep the
container-friendly `0.0.0.0` (ingress is fronted by the platform). Proven in
`tests/unit/test_netdefaults.py`.

### What HTTP security headers are set?

The API middleware (`api/app.py`) and the UI (`ui/next.config.mjs`) set
`Content-Security-Policy: frame-ancestors ...` (plus `X-Frame-Options: SAMEORIGIN` when
self). CORS never uses `*`: it is an explicit `cors_allowlist` (`COMPLIANCE_CORS_ORIGINS`),
with the localhost dev-origin fallback local-profile-only. Note the header baseline is
currently frame-ancestors-only; `X-Content-Type-Options: nosniff`, `Referrer-Policy`, HSTS on
secure profiles, and a full UI CSP are a known gap tracked as practices-audit check C6 and
should be closed before exposure.

### How tamper-evident is the audit trail? What are its limits?

The `local` audit store wraps the shared `hex_service_kit.audit.HashChainedAuditLog`:
`entry_hash = SHA-256(prev_hash + record)` over canonical JSON, with SQLite `UPDATE` /
`DELETE` rejected by triggers, JSONL export/restore with per-line verification, and a
`verify_chain()` method. The honest limit is stated in the module docstring: the chain alone
carries no secret, so it detects in-place edits but a full-rewrite needs an external anchor
or the WORM bucket. In production the `gcp` profile uses a locked Cloud Logging WORM bucket,
and the enterprise WORM audit system is the sibling **Hrz5** (this repo does not replace it).
Proven in `tests/unit/test_audit_chain.py`.

### Supply chain: are dependencies pinned and scanned?

Yes. Committed lockfiles (`requirements-dev.lock`, `requirements-gcp.lock`, uv-compiled for
py3.12) are installed in CI and the Docker build; the base image is pinned by digest; GitHub
Actions are SHA-pinned; `.github/dependabot.yml` proposes bumps; and `pip-audit` is a hard CI
gate. `ruff` is pinned exactly. The shared commons packages (`hex-service-kit`,
`agent-eval-kit`, `review-kit`) are pinned by tag in `pyproject.toml` and by exact SHA in
the lockfiles.

### Where are secrets? Are any committed?

No secret values are in the repo. `config/settings.yaml` stores only the **names** of env
vars holding secrets (e.g. `COMPLIANCE_KMS_KEY`, `HRZ_S2S_TOKEN`, the DLP template envs) and
resource ids; values are read at construction time and never logged. A literal-secret grep
over `config/` is clean, and every fixture is obviously-fictional.

### What is explicitly out of scope / a residual risk?

- The full security-header baseline (nosniff / Referrer-Policy / HSTS / full UI CSP) is not
  yet set on every surface (check C6).
- There is no in-app edge rate limiter; production is expected to enforce that at the edge
  (IAP / Apigee / LB).
- The hash chain needs the external anchor (or the WORM bucket) to resist a full rewrite.
- This is a reference build: run your own pen-test, threat model, and model-risk review
  before any live-data deployment (stated throughout the docs).
