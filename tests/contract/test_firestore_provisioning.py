"""The Firestore store the gcp profile binds is provisioned, named alike, and fully indexed.

Pinned here, each watched failing first:

* **the store exists.** A managed adapter bound in ``config/settings.yaml`` with no database,
  API, role or key behind it fails at its first request on a deployment and passes every
  offline gate. ``marketing-compliance-gate`` shipped exactly that for a year;
* **the adapters and the Terraform name the same database**, built from the same region,
  because a database nobody creates is one the adapters resolve and then fail on;
* **every composite query has its index, and no declared index is dead.** The adapters are
  driven through ``tests/fixtures/fake_firestore.py``, which refuses an undeclared composite
  query and records every composite query that ran, so the declared set is compared with what
  the code actually asks for rather than with a hand-written list;
* **the gcp profile binds no always-on store, and AlloyDB stays reachable** under ``platform``,
  so the toggle's other side is a binding that exists rather than a variable with nothing
  behind it.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

import pytest

from compliance_advisory.adapters.gcp import _firestore
from compliance_advisory.adapters.gcp.firestore_horizon_tracker import (
    FirestoreHorizonTrackerAdapter,
)
from compliance_advisory.adapters.gcp.firestore_ledger import FirestoreLedgerAdapter
from compliance_advisory.config import Settings
from compliance_advisory.domain.horizon.models import ImplementationItem
from compliance_advisory.domain.models import FreshnessRecord
from tests.fixtures import fake_firestore
from tests.fixtures.fake_firestore import (
    FailedPreconditionError,
    FakeFirestoreClient,
    FieldFilter,
    declared_composite_indexes,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
TF_DIR = REPO_ROOT / "infra" / "terraform"
T0 = datetime(2026, 9, 1, tzinfo=UTC)


def _tf(name: str) -> str:
    path = TF_DIR / name
    assert path.exists(), f"infra/terraform/{name} is missing"
    return path.read_text(encoding="utf-8")


def _exercise_every_query(client: FakeFirestoreClient) -> None:
    """Call every port method of both Firestore adapters against ``client``."""
    settings = Settings.load("config/settings.yaml")
    ledger = fake_firestore.bind(FirestoreLedgerAdapter(settings), client)
    tracker = fake_firestore.bind(FirestoreHorizonTrackerAdapter(settings), client)
    ledger.upsert(FreshnessRecord("s", "https://r.example/s", "1", T0, T0))
    ledger.get("s")
    ledger.all()
    ledger.list_expired(T0)
    tracker.upsert(ImplementationItem("c", "t", "s"))
    tracker.get("c")
    tracker.list("t")


# --------------------------------------------------------------------------- #
# Provisioned at all
# --------------------------------------------------------------------------- #
def test_the_database_the_adapters_bind_to_is_created_with_its_api_role_and_key() -> None:
    text = _tf("firestore.tf")
    assert 'resource "google_firestore_database" "compliance"' in text
    assert 'type        = "FIRESTORE_NATIVE"' in text
    assert '"firestore.googleapis.com"' in _tf("apis.tf"), "the Firestore API is not enabled"
    iam = _tf("iam.tf")
    assert "roles/datastore.user" in iam, "the serving identity cannot read or write documents"
    assert "roles/datastore.user" in _tf("agent_runtime.tf")
    assert 'service  = "firestore.googleapis.com"' in _tf("kms.tf"), (
        "no Firestore service identity for the CMEK grant, so a project admitted to Firestore "
        "CMEK could not encrypt the database under this stack's key"
    )


def test_firestore_cmek_is_a_stated_choice_because_it_is_allowlist_gated() -> None:
    """An unconditional cmek_config fails the apply on a project Google has not admitted."""
    text = _tf("firestore.tf")
    assert 'dynamic "cmek_config"' in text
    assert "var.firestore_cmek_enabled" in text


# --------------------------------------------------------------------------- #
# One name, built from one region, in both places
# --------------------------------------------------------------------------- #
def test_terraform_and_the_adapters_name_the_same_regional_database() -> None:
    text = _tf("firestore.tf")
    declared = re.search(r'firestore_database = "([^"]+)\$\{var\.region\}"', text)
    assert declared is not None, "firestore.tf no longer derives the database name from the region"
    assert declared.group(1) == _firestore.DATABASE_PREFIX
    assert "location_id = var.region" in text
    assert _firestore.database_for("asia-southeast1") == "compliance-advisory-asia-southeast1"


# --------------------------------------------------------------------------- #
# The indexes, compared with the queries the code runs
# --------------------------------------------------------------------------- #
def test_every_composite_query_has_a_declared_index_and_none_is_dead(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_firestore, "field_filter", FieldFilter)
    declared = declared_composite_indexes()
    client = FakeFirestoreClient(database=_firestore.database_for("asia-southeast1"))
    _exercise_every_query(client)

    ran = {(collection, fields) for collection, fields in client.composite_queries_run}
    declared_pairs = {(c, f) for c, all_fields in declared.items() for f in all_fields}
    assert ran, "no composite query ran, so this comparison would pass over nothing"
    assert ran == declared_pairs, (
        f"queries needing an index: {sorted(ran)}; indexes declared: {sorted(declared_pairs)}"
    )


def test_an_undeclared_index_fails_the_query_the_way_firestore_does(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fake's refusal is what makes the comparison above able to go red."""
    monkeypatch.setattr(_firestore, "field_filter", FieldFilter)
    client = FakeFirestoreClient(
        database=_firestore.database_for("asia-southeast1"), composite_indexes={}
    )
    with pytest.raises(FailedPreconditionError, match="composite index"):
        _exercise_every_query(client)


def test_the_index_is_declared_once_per_field_list_on_the_database_the_adapters_use() -> None:
    text = _tf("firestore.tf")
    assert 'resource "google_firestore_index" "composite"' in text
    assert "database    = google_firestore_database.compliance.name" in text


# --------------------------------------------------------------------------- #
# The bindings: nothing always-on under gcp, AlloyDB still reachable under platform
# --------------------------------------------------------------------------- #
def test_the_gcp_profile_binds_no_always_on_store() -> None:
    adapters = Settings.load("config/settings.yaml").adapters
    assert adapters["ledger"]["gcp"].endswith(":FirestoreLedgerAdapter")
    assert adapters["horizon_tracker"]["gcp"].endswith(":FirestoreHorizonTrackerAdapter")
    hourly = {
        port: binding["gcp"] for port, binding in adapters.items() if "alloydb" in binding["gcp"]
    }
    assert not hourly, f"the gcp profile binds an hourly-billed store: {hourly}"


def test_alloydb_stays_selectable_under_the_platform_profile() -> None:
    adapters = Settings.load("config/settings.yaml").adapters
    assert adapters["ledger"]["platform"].endswith(":AlloyDBLedgerAdapter")
    assert adapters["horizon_tracker"]["platform"].endswith(":AlloyDBHorizonTrackerAdapter")
    variables = _tf("variables.tf")
    block = variables[variables.index('variable "enable_alloydb"') :]
    block = block[: block.index("\n}\n")]
    assert re.search(r"default\s*=\s*false", block), "AlloyDB must be off unless a deployment asks"
