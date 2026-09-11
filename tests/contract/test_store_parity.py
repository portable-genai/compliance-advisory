"""Behavioural parity for the two stores the ``gcp`` profile now keeps in Firestore.

``test_port_parity`` proves every SDK-free binding satisfies its Protocol, and it never
constructs a managed adapter. This suite puts the Firestore ledger and tracker through the
SAME contract as the local SQLite adapters, operation by operation, and asserts the two answer
identically: same domain objects, same ordering, same tenant scoping. Then it runs the three
things that write those stores (the ingest pipeline, the scheduled refresh pass and the
horizon scan with its tracking service) with the Firestore adapters bound, because a store
that passes a unit contract and breaks the pipeline that writes it has not been proved.

The Firestore adapters run against ``tests/fixtures/fake_firestore.py``, which enforces the
rules the real service would (UTC timestamps returned as a subclass, the keyword filter form,
refused document ids, and a FAILED_PRECONDITION for any composite query whose index
``infra/terraform/firestore.tf`` does not declare).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from compliance_advisory import ports
from compliance_advisory.adapters.gcp import _firestore
from compliance_advisory.adapters.gcp.firestore_horizon_tracker import (
    FirestoreHorizonTrackerAdapter,
)
from compliance_advisory.adapters.gcp.firestore_ledger import FirestoreLedgerAdapter
from compliance_advisory.adapters.local.horizon_tracker import LocalHorizonTrackerAdapter
from compliance_advisory.adapters.local.ledger import LocalLedgerAdapter
from compliance_advisory.api.deps import build_horizon_scan_service, build_horizon_tracking_service
from compliance_advisory.config import Container, LocalSettings, Settings
from compliance_advisory.domain.horizon import carry_forward
from compliance_advisory.domain.horizon.models import (
    ImplementationItem,
    ImplementationStatus,
    MaterialityBand,
)
from compliance_advisory.domain.models import (
    FetchedDocument,
    FreshnessRecord,
    FreshnessStatus,
    utcnow,
)
from compliance_advisory.pipelines import fetch as fetch_mod
from compliance_advisory.pipelines import ingest as ingest_mod
from tests.fixtures import fake_firestore
from tests.fixtures.fake_firestore import FakeFirestoreClient, FieldFilter

CONFIG_PATH = "config/settings.yaml"
IMPLEMENTATIONS = ("local", "firestore")
T0 = datetime(2026, 9, 1, 8, 30, 15, 123456, tzinfo=UTC)


def _settings() -> Settings:
    return replace(
        Settings.load(CONFIG_PATH),
        profile="local",
        local=LocalSettings(
            db_path=":memory:",
            audit_path=":memory:",
            ledger_path=":memory:",
            horizon_path=":memory:",
        ),
    )


@pytest.fixture
def firestore_client(monkeypatch: pytest.MonkeyPatch) -> FakeFirestoreClient:
    """A fake database named for the settings region, with the Terraform's declared indexes."""
    monkeypatch.setattr(_firestore, "field_filter", FieldFilter)
    return FakeFirestoreClient(database=_firestore.database_for(_settings().region))


@pytest.fixture
def make_ledger(firestore_client: FakeFirestoreClient) -> Callable[[str], object]:
    def build(kind: str) -> object:
        if kind == "local":
            return LocalLedgerAdapter(_settings())
        return fake_firestore.bind(FirestoreLedgerAdapter(_settings()), firestore_client)

    return build


@pytest.fixture
def make_tracker(firestore_client: FakeFirestoreClient) -> Callable[[str], object]:
    def build(kind: str) -> object:
        if kind == "local":
            return LocalHorizonTrackerAdapter(_settings())
        return fake_firestore.bind(FirestoreHorizonTrackerAdapter(_settings()), firestore_client)

    return build


def _record(source_id: str, *, expires_at: datetime, checksum: str = "c1") -> FreshnessRecord:
    return FreshnessRecord(
        source_id=source_id,
        url=f"https://regulator.example/{source_id}.pdf",
        version="2026.1",
        fetched_at=T0,
        expires_at=expires_at,
        checksum=checksum,
        status=FreshnessStatus.FRESH,
    )


def _item(change_id: str, tenant: str, **changes: object) -> ImplementationItem:
    base = ImplementationItem(
        change_id=change_id,
        tenant=tenant,
        source_id="mas-trm",
        status=ImplementationStatus.IN_PROGRESS,
        owner="ciso-office",
        materiality_band=MaterialityBand.HIGH,
        due_within_days=60,
        control_ids=("cmek-regional", "vpc-sc-perimeter"),
        note="scoped with the CISO office",
        updated_by="officer@bank.test",
        updated_at=T0,
    )
    return replace(base, **changes)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Structural: the managed adapters satisfy the same Protocols
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("adapter_class", "protocol"),
    [
        (FirestoreLedgerAdapter, ports.CorpusLedgerPort),
        (FirestoreHorizonTrackerAdapter, ports.HorizonTrackerPort),
    ],
)
def test_the_firestore_adapters_satisfy_their_ports(adapter_class: type, protocol: type) -> None:
    adapter = adapter_class(_settings())  # constructs with no SDK and no credentials
    assert isinstance(adapter, protocol)
    members = {m for m in getattr(protocol, "__protocol_attrs__", set()) if not m.startswith("_")}
    assert members, f"{protocol.__name__} declares no members, so this check proves nothing"
    declared = set().union(*(vars(klass) for klass in type(adapter).__mro__))
    assert members <= declared, f"{adapter_class.__name__} lacks {sorted(members - declared)}"


# --------------------------------------------------------------------------- #
# CorpusLedgerPort: one contract, both implementations
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("kind", IMPLEMENTATIONS)
def test_ledger_get_of_an_unknown_source_is_none(kind: str, make_ledger) -> None:
    assert make_ledger(kind).get("never-ingested") is None


@pytest.mark.parametrize("kind", IMPLEMENTATIONS)
def test_ledger_round_trips_a_record_with_its_superseded_generation(kind: str, make_ledger) -> None:
    ledger = make_ledger(kind)
    first = _record("mas-trm", expires_at=T0 + timedelta(days=7))
    ledger.upsert(first)
    second = carry_forward(ledger.get("mas-trm"), replace(first, checksum="c2", version="2026.2"))
    ledger.upsert(second)

    stored = ledger.get("mas-trm")
    assert stored == second
    assert stored.previous_checksum == "c1" and stored.previous_status is FreshnessStatus.FRESH
    for moment in (stored.fetched_at, stored.expires_at, stored.previous_fetched_at):
        assert moment is not None and moment.utcoffset() == timedelta(0)
    if kind == "firestore":
        # Firestore hands back a datetime SUBCLASS; the record must carry a plain datetime.
        assert type(stored.fetched_at) is datetime


@pytest.mark.parametrize("kind", IMPLEMENTATIONS)
def test_ledger_upsert_replaces_rather_than_duplicates(kind: str, make_ledger) -> None:
    ledger = make_ledger(kind)
    ledger.upsert(_record("mas-trm", expires_at=T0))
    ledger.upsert(_record("mas-trm", expires_at=T0 + timedelta(days=7), checksum="c2"))
    assert [(r.source_id, r.checksum) for r in ledger.all()] == [("mas-trm", "c2")]


@pytest.mark.parametrize("kind", IMPLEMENTATIONS)
def test_ledger_all_is_ordered_by_source_id(kind: str, make_ledger) -> None:
    ledger = make_ledger(kind)
    for source_id in ("hkma-tm-g-1", "apra-cps-230", "mas-trm"):
        ledger.upsert(_record(source_id, expires_at=T0))
    assert [r.source_id for r in ledger.all()] == ["apra-cps-230", "hkma-tm-g-1", "mas-trm"]


@pytest.mark.parametrize("kind", IMPLEMENTATIONS)
def test_ledger_list_expired_is_inclusive_and_ordered_by_source_id(kind: str, make_ledger) -> None:
    ledger = make_ledger(kind)
    ledger.upsert(_record("mas-trm", expires_at=T0 - timedelta(days=1)))
    ledger.upsert(_record("apra-cps-230", expires_at=T0))  # exactly at the cutoff
    ledger.upsert(_record("hkma-tm-g-1", expires_at=T0 + timedelta(seconds=1)))

    assert [r.source_id for r in ledger.list_expired(T0)] == ["apra-cps-230", "mas-trm"]
    assert ledger.list_expired(T0 - timedelta(days=2)) == []


@pytest.mark.parametrize("kind", IMPLEMENTATIONS)
def test_ledger_list_expired_defaults_to_now(kind: str, make_ledger) -> None:
    ledger = make_ledger(kind)
    ledger.upsert(_record("past", expires_at=utcnow() - timedelta(hours=1)))
    ledger.upsert(_record("future", expires_at=utcnow() + timedelta(days=1)))
    assert [r.source_id for r in ledger.list_expired()] == ["past"]


def test_ledger_parity_the_same_sequence_gives_the_same_answers(make_ledger) -> None:
    """Every read, after every write, identical across the two implementations."""
    local, firestore = make_ledger("local"), make_ledger("firestore")
    writes = [
        _record("mas-trm", expires_at=T0),
        _record("apra-cps-230", expires_at=T0 + timedelta(days=3), checksum="a"),
        replace(_record("mas-trm", expires_at=T0), status=FreshnessStatus.FAILED, checksum="x"),
        _record("fsa-cloud", expires_at=T0 - timedelta(days=9)),
    ]
    compared = 0
    for write in writes:
        for ledger in (local, firestore):
            ledger.upsert(carry_forward(ledger.get(write.source_id), write))
        assert local.all() == firestore.all()
        assert local.list_expired(T0) == firestore.list_expired(T0)
        for source_id in ("mas-trm", "apra-cps-230", "fsa-cloud", "absent"):
            assert local.get(source_id) == firestore.get(source_id)
        compared += 1
    assert compared == len(writes) and local.all(), "a parity check over nothing proves nothing"


# --------------------------------------------------------------------------- #
# HorizonTrackerPort: one contract, both implementations
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("kind", IMPLEMENTATIONS)
def test_tracker_get_of_an_unknown_change_is_none(kind: str, make_tracker) -> None:
    assert make_tracker(kind).get("mas-trm:new_source:none") is None


@pytest.mark.parametrize("kind", IMPLEMENTATIONS)
def test_tracker_round_trips_every_field(kind: str, make_tracker) -> None:
    tracker = make_tracker(kind)
    item = _item("mas-trm:content_revised:abc", "demo-bank")
    tracker.upsert(item)
    assert tracker.get(item.change_id) == item


@pytest.mark.parametrize("kind", IMPLEMENTATIONS)
def test_tracker_get_is_tenant_agnostic_and_list_is_tenant_scoped(kind: str, make_tracker) -> None:
    tracker = make_tracker(kind)
    tracker.upsert(_item("b-change", "demo-bank"))
    tracker.upsert(_item("a-change", "demo-bank"))
    tracker.upsert(_item("c-change", "other-bank"))

    # The domain, not the adapter, refuses a cross-tenant read, so get returns the row.
    assert tracker.get("c-change").tenant == "other-bank"
    assert [i.change_id for i in tracker.list("demo-bank")] == ["a-change", "b-change"]
    assert [i.change_id for i in tracker.list("other-bank")] == ["c-change"]
    assert tracker.list("nobody") == []


@pytest.mark.parametrize("kind", IMPLEMENTATIONS)
def test_tracker_upsert_replaces_including_the_tenant(kind: str, make_tracker) -> None:
    tracker = make_tracker(kind)
    tracker.upsert(_item("a-change", "demo-bank"))
    tracker.upsert(_item("a-change", "other-bank", status=ImplementationStatus.IMPLEMENTED))
    assert tracker.list("demo-bank") == []
    assert [i.status for i in tracker.list("other-bank")] == [ImplementationStatus.IMPLEMENTED]


def test_tracker_parity_the_same_sequence_gives_the_same_answers(make_tracker) -> None:
    local, firestore = make_tracker("local"), make_tracker("firestore")
    writes = [
        _item("mas-trm:content_revised:1", "demo-bank"),
        _item("apra:new_source:2", "demo-bank", control_ids=(), note=""),
        _item("mas-trm:content_revised:1", "demo-bank", status=ImplementationStatus.ACCEPTED_RISK),
        _item("hkma:withdrawn:3", "other-bank", materiality_band=MaterialityBand.CRITICAL),
    ]
    for write in writes:
        for tracker in (local, firestore):
            tracker.upsert(write)
        for tenant in ("demo-bank", "other-bank", ""):
            assert local.list(tenant) == firestore.list(tenant)
        for write_seen in writes:
            assert local.get(write_seen.change_id) == firestore.get(write_seen.change_id)
    assert local.list("demo-bank"), "a parity check over nothing proves nothing"


# --------------------------------------------------------------------------- #
# Firestore-specific refusals
# --------------------------------------------------------------------------- #
def test_the_adapters_name_the_regional_database_not_the_default() -> None:
    settings = _settings()
    for adapter in (FirestoreLedgerAdapter(settings), FirestoreHorizonTrackerAdapter(settings)):
        assert adapter._database == f"compliance-advisory-{settings.region}"  # noqa: SLF001


def test_an_unconfigured_region_refuses_rather_than_using_the_default_database() -> None:
    with pytest.raises(ValueError, match="no deploy region"):
        FirestoreLedgerAdapter(replace(_settings(), region="  "))


@pytest.mark.parametrize("bad_id", ["", ".", "..", "a/b", "__reserved__"])
def test_an_id_firestore_would_misread_is_refused_before_the_call(bad_id: str, make_ledger) -> None:
    ledger = make_ledger("firestore")
    with pytest.raises(ValueError, match="cannot be a Firestore document id"):
        ledger.get(bad_id)


# --------------------------------------------------------------------------- #
# The pipelines that WRITE these stores, with the Firestore adapters bound
# --------------------------------------------------------------------------- #
def _container_with(ledger: object, tracker: object) -> Container:
    """The local stack, except the two stores the gcp profile keeps in Firestore."""
    container = Container(_settings())
    container.__dict__["ledger"] = ledger  # cached_property: the instance value wins
    container.__dict__["horizon_tracker"] = tracker
    return container


def test_ingest_and_the_scheduled_refresh_write_the_firestore_ledger(
    make_ledger, make_tracker, monkeypatch: pytest.MonkeyPatch
) -> None:
    container = _container_with(make_ledger("firestore"), make_tracker("firestore"))
    source = container.source_catalog.sources()[0]

    def fetched(checksum: str) -> FetchedDocument:
        return FetchedDocument(
            source=source,
            content=b"Page one requires due diligence.\fPage two requires an exit plan.",
            mime_type="text/plain",
            fetched_at=utcnow() - timedelta(days=30),
            checksum=checksum,
        )

    assert ingest_mod.ingest_fetched(container, fetched("gen-1")).action == "ingested"
    assert ingest_mod.ingest_fetched(container, fetched("gen-2")).action == "ingested"
    stored = container.ledger.get(source.id)
    assert (stored.checksum, stored.previous_checksum) == ("gen-2", "gen-1")

    # The scheduled pass reads all() and list_expired() and upserts through the same binding.
    monkeypatch.setattr(fetch_mod, "fetch_source", lambda src, **_: fetched("gen-3"))
    summary = ingest_mod.refresh_expired(container)
    assert summary.total and summary.failed == 0
    assert container.ledger.get(source.id).checksum == "gen-3"
    assert container.ledger.get(source.id).previous_checksum == "gen-2"


def test_the_horizon_scan_and_tracking_run_identically_on_firestore(
    make_ledger, make_tracker
) -> None:
    def run(kind: str) -> tuple[list[str], list[ImplementationItem], ImplementationStatus]:
        container = _container_with(make_ledger(kind), make_tracker(kind))
        source = container.source_catalog.sources()[0]
        base = FreshnessRecord(
            source_id=source.id,
            url=source.url,
            version=source.version,
            fetched_at=T0,
            expires_at=T0,
            checksum="parity-1",
        )
        container.ledger.upsert(carry_forward(None, base))
        container.ledger.upsert(
            carry_forward(container.ledger.get(source.id), replace(base, checksum="parity-2"))
        )

        scan = build_horizon_scan_service(container).scan(
            "projects/acme-sg-prod", actor="parity@test", tenant="demo-bank"
        )
        assert scan.assessments, f"{kind}: the scan detected nothing"
        tracking = build_horizon_tracking_service(container)
        first = scan.assessments[0].id
        tracking.update_status(
            first, ImplementationStatus.IN_PROGRESS, actor="officer@bank.test", tenant="demo-bank"
        )
        # A re-scan refreshes policy fields and must never overwrite the human-set status.
        build_horizon_scan_service(container).scan(
            "projects/acme-sg-prod", actor="parity@test", tenant="demo-bank"
        )
        items = tracking.list_items("demo-bank")
        return [a.id for a in scan.assessments], items, tracking.get_item(first, "demo-bank").status

    local_ids, local_items, local_status = run("local")
    firestore_ids, firestore_items, firestore_status = run("firestore")

    assert firestore_ids == local_ids
    assert firestore_status is local_status is ImplementationStatus.IN_PROGRESS
    strip = [replace(i, updated_at=T0) for i in local_items]
    assert [replace(i, updated_at=T0) for i in firestore_items] == strip
