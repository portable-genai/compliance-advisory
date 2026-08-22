# Contributing to Rsk1: Compliance Assistant

Thanks for your interest. This is a public Apache-2.0 reference build. Contributions that
improve correctness, clarity, test coverage, or the documentation are welcome.

> Reminder: this project is **not affiliated with Google**. Keep that disclaimer intact in
> any docs you touch.

---

## 1. Ground rules (the contract is authoritative)

The contract layer is the single source of truth and **must not be changed** in a routine
contribution:

- [`SPEC.md`](SPEC.md)
- [`src/compliance_advisory/domain/models.py`](src/compliance_advisory/domain/models.py)
- [`src/compliance_advisory/ports/`](src/compliance_advisory/ports/)
- [`config.py`](src/compliance_advisory/config.py) and
  [`config/settings.yaml`](config/settings.yaml)
- [`pyproject.toml`](pyproject.toml)

Implement *against* the contract; do not edit it. If you believe the contract itself is wrong,
open an issue first and make the case, don't bundle a contract change into a feature PR.

---

## 2. Coding standards

- **Python 3.12.** Every module starts with `from __future__ import annotations`.
- **Full type hints**, everywhere.
- **ruff-clean**, line length **≤ 100** (configured in `pyproject.toml`). Lint rules:
  `E, F, I, UP, B, SIM`.
- **mypy-clean** under `python_version = 3.12`.
- **Adapter convention:** every adapter is
  `class X:\n    def __init__(self, settings: Settings) -> None: ...`.
- **Lazy GCP imports:** all `google-cloud-*` / ADK / `google-genai` imports go **inside**
  `__init__`/methods, never at module top level, so the `onprem`/test profile imports with
  **no** GCP SDK installed.
- **Region pinned** to `asia-southeast1`. **Models pinned** to the ids in `settings.yaml` /
  SPEC (`gemini-3.5-flash`, `gemini-3.1-flash-lite`). Never use the floating ADK default
  model or `gemini-2.0-flash`.
- **No secrets in code.** Use env vars / `settings.yaml` interpolation.
- Use current product names in docstrings: "Gemini Enterprise Agent Platform", "Agent
  Search", "Agent Runtime".

---

## 3. Local setup

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"            # core + dev; no GCP SDK needed for the onprem profile
make install                       # convenience target for the same
```

For the managed stack add the `gcp` extra: `pip install -e ".[gcp,dev]"`.

---

## 4. Test, lint, format

All tests must pass under the **`onprem` profile with no Google Cloud SDKs installed**.
Contract tests assert interface parity; unit tests use in-memory fakes.

```bash
make fmt        # ruff format + ruff --fix
make lint       # ruff check + mypy
make test       # COMPLIANCE_PROFILE=onprem pytest -m 'not integration'
make eval       # the Hrz4 eval gate (eval/run_eval.py)
```

Tests that require live Google Cloud credentials are marked `integration` and are deselected
by default (`-m 'not integration'`). Do not make the default suite depend on cloud access.

---

## 5. Pull requests

1. Branch off `main`; keep PRs focused.
2. `make fmt lint test` must be green; add/extend tests for any behaviour change.
3. If you add a new adapter, add (or extend) its **contract test** so all three families stay
   in interface parity.
4. If you touch a compliance-relevant path, update [`COMPLIANCE.md`](COMPLIANCE.md) so the
   principle→control mapping stays accurate.
5. CI runs ruff + mypy + `pytest -m 'not integration'` on the `onprem` profile
   ([`.github/workflows/ci.yaml`](.github/workflows/ci.yaml)) and the eval gate
   ([`.github/workflows/eval-gate.yaml`](.github/workflows/eval-gate.yaml)). Both must pass.

### Commit / DCO
- Write clear, imperative commit messages.
- By contributing you agree your contribution is licensed under Apache-2.0.

---

## 6. Reporting issues

- **Bugs / features:** open a GitHub issue with repro steps (the `onprem` CLI path is the
  fastest repro, it needs no cloud access).
- **Security:** do **not** open a public issue for a vulnerability. Email the maintainer
  privately and allow time to remediate before disclosure.

---

## 7. Project layout

See [`README.md`](README.md) §10 for the repository layout and [`ARCHITECTURE.md`](ARCHITECTURE.md)
for the ports/adapters map. The fastest way to understand the system is to read
`domain/models.py`, then the ports, then `config/settings.yaml`.

---

## 8. Adding an adapter or port

Adding an adapter:

1. Implement one existing Protocol using only domain models and `Adapter(settings)`.
2. Keep optional/cloud imports inside construction or methods.
3. Add its dotted binding to every applicable profile in `config/settings.yaml`; placeholders
   must construct and fail explicitly rather than return empty success.
4. Extend `tests/contract/test_port_parity.py` construction coverage and
   `tests/contract/test_behavioral_parity.py` for observable behavior.
5. Add unit/fault tests, update the ports-to-adapters table in `ARCHITECTURE.md`, and run
   `make check` plus `make ui-check` when the surface is affected.

Adding a port or sub-service:

1. Declare one `@runtime_checkable Protocol` under `ports/` and re-export it once from
   `ports/__init__.py`.
2. Add it to the contract suite's Protocol map; the set-equality assertion must match the
   settings adapter keys exactly.
3. Provide `local`, managed/platform as applicable, and fail-fast `onprem` bindings.
4. Add the `Container` property and wire the domain service in `api/deps.py`; domain code may
   depend on the Protocol and kernel envelopes only.
5. Define API/CLI schemas only after the domain contract, and add behavioral parity, API and
   failure-path tests.
6. Update SPEC, ARCHITECTURE, COMPLIANCE, portability proof, demo/self-test and changelog.
7. Run `make check`, `make ui-check`, and any relevant live integration test separately.

Skipping any touch point is an incomplete change: the structural set-equality test catches an
unregistered port, behavioral parity catches profile drift, and the demo self-test catches a
surface that no longer renders the domain result.
