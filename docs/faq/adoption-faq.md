# Adoption FAQ

For an engineering lead forking this repo as their institution's base. The step-by-step is
[`docs/ADOPTING.md`](../ADOPTING.md); this answers the "will it hurt later?" questions.

### How do I rebrand it for my institution?

`scripts/rename_fork.py` rewrites the package name (`compliance_advisory`), the CLI entry
point (`compliance`), the `COMPLIANCE_` env prefix, and the `compliance-advisory` resource
ids in one pass (preview with `--dry-run`, apply with `--yes`). Then recreate the venv,
`pip install -e ".[dev]"`, and run `make lint test eval`. The script does the mechanical
rename; the human decisions (region, IdP, source registry, freshness/review policy,
fixtures, eval golden set) are the checklist in `ADOPTING.md`.

### If five banks fork this, how does each take upstream security fixes?

Track upstream via **git tags** (semver). The repo declares a **core-vs-adopter-owned boundary** (ADOPTING section 2): upstream
owns the vertical-neutral domain types and decision logic, `ports/`, `tests/contract/`, the
eval harness mechanics and CI; you own `config/settings.yaml` values, the source registry,
fixtures, `adapters/onprem/*`, UI theming, and the eval golden set. Rebase your adopter-owned
changes onto each release rather than merging `main` continuously, and conflicts stay in the
files you were told to expect.

### How do I add a new outbound dependency (a new port)?

Define the `@runtime_checkable` Protocol under `ports/` and re-export it once from
`ports/__init__.py`; implement one adapter per profile (at least `local` and `onprem`); bind
all of them in the `adapters:` map in `config/settings.yaml`; add a `cached_property` on the
`Container`; and wire it in `api/deps.py`. The contract test
(`tests/contract/test_port_parity.py`) fails loudly if a port lacks its `local`/`onprem`
bindings. Full instructions in [`CONTRIBUTING.md`](../../CONTRIBUTING.md). Note: the drift
guard is currently one-directional (protocol -> settings), so registering a NEW binding in
settings for a port that does not exist would not fail the suite (practices-audit check A6).

### How do I add a new answer artifact or sub-service?

A sub-service is pure domain: add `domain/<name>_service.py` (stdlib only), re-export it from
`domain/services.py`, thread any tunable constants through config rather than hard-coding
them, construct it in `api/deps.py` and add a CLI command in `cli/main.py`, and unit-test it.
The existing four services (`qa_service`, `checklist_service`, `testcase_service`,
`regulator_questions_service`) are the templates.

### How do I change the taxonomy (regulators, doc types, severities)?

They are `StrEnum`s (`Regulator`, `Jurisdiction`, `DocType`, `Severity`,
`GuardrailCategory`, ...) and members ARE their wire values, so you extend the vocabulary by
editing the enum and the source registry without hunting through the engines, which are typed
on `str`. To retarget the corpus wholesale, replace `pipelines/sources/registry.yaml`.

### How do I retune the review / freshness policy without touching code?

The corpus freshness window is config-reachable (`corpus.ttl_days` -> `FreshnessPolicy`). Be
aware that the maker-checker thresholds (the answer-confidence floor, the high-severity
bands) currently live as dataclass defaults and module constants in `domain/hitl.py` rather
than a dedicated `policy:` settings section (practices-audit check B4); a fork that needs
these under config should lift them into settings as an explicit adoption step.

### Does the CI run for my fork out of the box?

Yes. CI (`ci.yaml`, `COMPLIANCE_PROFILE=local`) runs ruff check + ruff format --check + mypy
+ pytest, and the eval gate (`eval-gate.yaml`, `COMPLIANCE_PROFILE=onprem`) runs
`eval/run_eval.py`, both with **no cloud credentials and no org secrets**, so a fork's build
is green immediately. You add secrets only when you wire the `gcp` / `platform` profiles. The
eval gate measures the *reference* corpus until you rebuild the golden set; that is an
explicit adoption step, not a silent pass.

### Will the demo rot after I diverge?

Be aware there is currently no demo self-test wired into CI (practices-audit check F2), so a
refactor that breaks `make demo` will not fail the PR. If your team relies on the demo,
adding a browserless demo test is a recommended first hardening step; the portability claim,
by contrast, IS gated (`scripts/portability_demo.py` exits non-zero if any portability check
fails).
