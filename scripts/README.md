# Demo scripts - `compliance-advisory` grounded compliance, four cited artifacts

All scripts are SDK-free and run against the in-process `local` stack (no Google Cloud,
no API key). They drive the real `compliance-advisory` services - retrieval, guardrail, PII redaction,
grounded generation, maker-checker gating and WORM audit - over the built-in synthetic
MAS / HKMA / APRA corpus, swapping only the managed adapters for offline equivalents.

Run them from the repo root with the domain package and the test fixtures on the path:

```bash
export PYTHONPATH=src:tests
```

The scripts pin the local SQLite stores to `:memory:` so each run is hermetic and
deterministic regardless of what is on the presenter's laptop.

| Script | What it does |
|--------|--------------|
| `compliance_demo.py` | Runs the synthetic cloud-onboarding use case through the `compliance-advisory` pipeline, prints a per-artifact summary, and writes the audit-view JSON (the four cited artifacts + the WORM audit trail). |
| `render_compliance_ui.py` | Renders that JSON into static, audit-first HTML pages (one per artifact + an audit-trail page) for slides and screenshots. |
| `compliance_demo_server.py` | A **live, presenter-controlled** server that runs the *real* services and reveals one artifact per click, rendering the audit-first console at each step. |
| `compliance_demo_playwright.py` | A **presenter-controlled** Playwright walkthrough of the live server: it narrates each step and waits for you to press Enter before performing it. |

## Static artifacts (slides / screenshots)

```bash
python scripts/compliance_demo.py compliance_demo.json
python scripts/render_compliance_ui.py compliance_demo.json ./out
# -> ./out/answer.html, checklist.html, testcases.html, regulator-questions.html, audit.html
```

## Live, presenter-controlled demo

Two terminals:

```bash
# 1) the live demo server  (http://localhost:8088)
PYTHONPATH=src:tests python scripts/compliance_demo_server.py

# 2) the guided walkthrough  (a real Chrome window opens)
pip install playwright && playwright install chromium      # one-time
python scripts/compliance_demo_playwright.py
```

The walkthrough is **paced by you**: it prints what the next step will do, waits for you
to press **Enter**, then clicks **Next ▶** and spotlights the panel to look at. The five
steps are: Answer (grounded + cited) -> Control checklist -> Test cases -> Regulator
questions -> WORM audit trail.

You can also just open `http://localhost:8088` and click **Next ▶** / **Restart** by hand
- the server holds the live run, so the buttons drive the same real services.

## Ports

The demo server defaults to **8088**, deliberately distinct from the FastAPI API port
(**8080**) and the Next.js console port (**3000**), so all three can run side by side
during a demo. Override with `--port`.

## Environment overrides for `compliance_demo_playwright.py`

| Var | Default | Purpose |
|-----|---------|---------|
| `DEMO_URL` | `http://127.0.0.1:8088` | server base URL (point at `:3000` to overlay the live console) |
| `HEADLESS=1` | off | run without a window (self-test / recording) |
| `DEMO_AUTO=1` | off | don't wait for Enter - advance automatically (self-test / recording) |
| `SLOWMO_MS` | `250` headed | per-action slow motion |
| `CHROME_PATH` | - | explicit Chromium / Chrome binary (else Playwright's own) |
| `lock.py` | Compiles both lockfiles and puts the header back, because `uv pip compile` REPLACES the output file: it writes its own two-line provenance comment and destroys the `tag = commit` map the pin tests check against. `make lock` runs this rather than uv directly. |

The two demo scripts (`compliance_demo.py`, `compliance_demo_server.py`) also honour the
local-store env vars (`COMPLIANCE_LOCAL_DB`, `COMPLIANCE_LOCAL_AUDIT`,
`COMPLIANCE_LOCAL_LEDGER`); they default to `:memory:` here.

> **Note.** `playwright` is a demo-time `pip install`, never a core or `[gcp]` dependency.
> The other three scripts need nothing beyond the package's own (already-installed) core
> dependencies. The `scripts/` directory is outside the CI gate (`ruff check src tests`,
> `ruff format --check src tests`, `mypy src`, `pytest -m 'not integration'`), so these
> scripts may import `playwright` at the top and embed long HTML strings.
