# C1 Compliance Assistant — developer Makefile.
#
# The default test/lint targets run under the LOCAL profile: a WORKING offline stack
# (SQLite FTS5 + deterministic LLM) that needs NO Google Cloud SDK. Override PROFILE=gcp
# for the managed stack, or PROFILE=onprem for the fail-fast migration target.

PYTHON      ?= python3
PYTHON      := $(if $(wildcard .venv/bin/python),.venv/bin/python,$(PYTHON))
PIP         ?= pip
PROFILE     ?= local
SRC         := src/compliance_advisory
TESTS       := tests
API_APP     := compliance_advisory.api.app:app
API_HOST    ?= 127.0.0.1  # no-auth local dev binds loopback; override deliberately
API_PORT    ?= 8080
UI_DIR      := ui
TF_DIR      := infra/terraform

export COMPLIANCE_PROFILE := $(PROFILE)

DEMO_PORT   ?= 8088

.DEFAULT_GOAL := help
.PHONY: help install install-gcp fmt lint test eval check ui-install ui-check smoke-local run-api run-ui tf-validate tf-plan clean demo demo-selftest demo-server

help: ## Show this help.
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

install: ## Install the package + dev tooling (NO GCP SDK — onprem/test profile).
	$(PIP) install -e ".[dev]"

install-gcp: ## Install with the managed-stack extra (google-adk, genai, discoveryengine, ...).
	$(PIP) install -e ".[gcp,dev]"

fmt: ## Auto-format and auto-fix lint issues.
	ruff format $(SRC) $(TESTS)
	ruff check --fix $(SRC) $(TESTS)

lint: ## Lint (ruff), check formatting, and type-check (mypy).
	ruff check $(SRC) $(TESTS)
	ruff format --check $(SRC) $(TESTS)
	mypy $(SRC)

test: ## Run unit + contract tests on the local profile (no GCP SDK required).
	COMPLIANCE_PROFILE=local pytest -m 'not integration' -q

eval: ## Run the A4 eval gate (groundedness / citations / faithfulness / safety).
	$(PYTHON) eval/run_eval.py

portability: ## Execute the bounded offline/profile portability proof.
	PYTHONPATH=src $(PYTHON) scripts/portability_demo.py

check: lint test eval portability demo-selftest tf-validate ## Run the full offline quality gate.

ui-install: ## Install the console's locked dependencies (proves package-lock.json is valid).
	npm ci --prefix $(UI_DIR)

ui-check: ## Console gate: types, policy unit tests, build, then HYDRATION against the built server.
	npm --prefix $(UI_DIR) run lint
	npm --prefix $(UI_DIR) test
	NEXT_TELEMETRY_DISABLED=1 npm --prefix $(UI_DIR) run build
	npm --prefix $(UI_DIR) run assert-hydratable

smoke-local: ## End-to-end offline smoke: answer a question under the local profile.
	COMPLIANCE_PROFILE=local compliance ask \
		"What cloud outsourcing controls does MAS expect before onboarding a cloud provider?" \
		--regulator MAS

demo: ## Offline demo: run the four-artifact flow + render static audit-first HTML (./out).
	PYTHONPATH=src:$(TESTS) $(PYTHON) scripts/compliance_demo.py compliance_demo.json
	PYTHONPATH=src:$(TESTS) $(PYTHON) scripts/render_compliance_ui.py compliance_demo.json ./out

demo-selftest: ## Run the real demo and assert every artifact, citation, review flag and panel.
	PYTHONPATH=src:$(TESTS):scripts $(PYTHON) scripts/demo_selftest.py

demo-server: ## Live, presenter-controlled demo server (offline) on :$(DEMO_PORT).
	PYTHONPATH=src:$(TESTS) $(PYTHON) scripts/compliance_demo_server.py --port $(DEMO_PORT)

run-api: ## Run the FastAPI service (PROFILE=$(PROFILE)).
	uvicorn $(API_APP) --host $(API_HOST) --port $(API_PORT) --reload

run-ui: ## Run the React / Next.js UI (dev server).
	cd $(UI_DIR) && npm install && npm run dev

tf-plan: ## Terraform plan for the asia-southeast1 infrastructure.
	cd $(TF_DIR) && terraform init -input=false && terraform plan

tf-validate: ## Offline Terraform format and schema validation; no cloud credentials.
	cd $(TF_DIR) && terraform fmt -check -recursive -diff \
		&& terraform init -backend=false -input=false \
		&& terraform validate -no-color

clean: ## Remove caches and build artefacts.
	rm -rf build dist *.egg-info .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
