"""Horizon change detection over the EXISTING freshness ledger.

Proves the "extend, do not shadow" decision end to end:

* :func:`carry_forward` rolls the superseded generation into the record the ingest
  pipeline writes, and a byte-identical re-fetch does NOT erase the diff base,
* the local SQLite ledger persists and reloads those fields (an old DB is migrated), and
* :func:`detect_changes` classifies new / republished / re-versioned / withdrawn sources
  and stays silent on an unchanged one.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from compliance_advisory.adapters.local.ledger import LocalLedgerAdapter
from compliance_advisory.config import LocalSettings, Settings
from compliance_advisory.domain.horizon import (
    ChangeKind,
    carry_forward,
    change_id,
    classify,
    detect_change,
    detect_changes,
)
from compliance_advisory.domain.models import (
    DocType,
    FreshnessRecord,
    FreshnessStatus,
    Jurisdiction,
    RegSource,
    Regulator,
)

_T0 = datetime(2026, 1, 1, tzinfo=UTC)

SOURCE = RegSource(
    id="mas-trm-guidelines",
    regulator=Regulator.MAS,
    jurisdiction=Jurisdiction.SG,
    title="MAS Technology Risk Management Guidelines",
    url="https://example.test/mas/trm",
    doc_type=DocType.GUIDELINE,
    version="2021",
    topics=("technology-risk", "cloud"),
)


def _record(
    *,
    version: str = "2021",
    checksum: str = "aaaa1111",
    status: FreshnessStatus = FreshnessStatus.FRESH,
    fetched_at: datetime = _T0,
) -> FreshnessRecord:
    return FreshnessRecord(
        source_id=SOURCE.id,
        url=SOURCE.url,
        version=version,
        fetched_at=fetched_at,
        expires_at=fetched_at + timedelta(days=7),
        checksum=checksum,
        status=status,
    )


# --------------------------------------------------------------------------- #
# carry_forward
# --------------------------------------------------------------------------- #
def test_first_ingest_has_no_history() -> None:
    rolled = carry_forward(None, _record())
    assert not rolled.has_history
    assert classify(rolled) is ChangeKind.NEW_SOURCE


def test_carry_forward_records_the_superseded_generation() -> None:
    first = _record(version="2021", checksum="aaaa1111")
    second = carry_forward(first, _record(version="2026", checksum="bbbb2222"))

    assert second.previous_version == "2021"
    assert second.previous_checksum == "aaaa1111"
    assert second.previous_fetched_at == first.fetched_at
    assert second.previous_status is FreshnessStatus.FRESH


def test_identical_refetch_does_not_erase_the_diff_base() -> None:
    """A no-op re-fetch inside the TTL cycle must not hide an unscanned change."""
    first = _record(version="2021", checksum="aaaa1111")
    revised = carry_forward(first, _record(version="2026", checksum="bbbb2222"))
    # Same bytes, same version, fetched again a day later.
    refetch = carry_forward(
        revised,
        _record(version="2026", checksum="bbbb2222", fetched_at=_T0 + timedelta(days=1)),
    )

    assert refetch.previous_checksum == "aaaa1111"
    assert classify(refetch) is ChangeKind.CONTENT_REVISED


# --------------------------------------------------------------------------- #
# classify / detect_change
# --------------------------------------------------------------------------- #
def test_content_revision_is_detected_with_a_citation() -> None:
    record = carry_forward(_record(), _record(version="2021", checksum="bbbb2222"))
    change = detect_change(record, SOURCE)

    assert change is not None
    assert change.kind is ChangeKind.CONTENT_REVISED
    assert change.previous_checksum == "aaaa1111"
    assert change.current_checksum == "bbbb2222"
    # Every detected change carries regulator-grade provenance.
    assert change.citation is not None
    assert change.citation.source_id == SOURCE.id
    assert change.citation.regulator is Regulator.MAS


def test_version_bump_is_distinguished_from_a_content_revision() -> None:
    record = carry_forward(_record(version="2021"), _record(version="2026"))
    change = detect_change(record, SOURCE)

    assert change is not None
    assert change.kind is ChangeKind.VERSION_BUMP


def test_unchanged_source_is_not_a_horizon_event() -> None:
    """A record whose superseded generation equals the current one is not an event."""
    from dataclasses import replace

    settled = replace(_record(), previous_version="2021", previous_checksum="aaaa1111")
    assert classify(settled) is ChangeKind.UNCHANGED
    assert detect_change(settled, SOURCE) is None


def test_a_new_source_stays_an_event_until_its_content_moves() -> None:
    """Deliberate: a never-superseded source keeps reporting as NEW_SOURCE.

    Nothing in the ledger records that a human has looked at it, so detection must not
    forget it after one re-fetch. Re-scanning is safe because the change id is stable and
    the tracking journey is keyed on it, so repeat scans update one row instead of piling
    up duplicates.
    """
    first = carry_forward(None, _record())
    refetched = carry_forward(first, _record(fetched_at=_T0 + timedelta(days=8)))

    assert classify(refetched) is ChangeKind.NEW_SOURCE
    a = detect_change(first, SOURCE)
    b = detect_change(refetched, SOURCE)
    assert a is not None and b is not None
    assert a.id == b.id


def test_failed_source_is_a_withdrawal_even_when_the_checksum_moved() -> None:
    record = carry_forward(_record(), _record(checksum="", status=FreshnessStatus.FAILED))
    change = detect_change(record, SOURCE)

    assert change is not None
    assert change.kind is ChangeKind.WITHDRAWN


def test_unregistered_ledger_row_is_skipped_rather_than_guessed() -> None:
    """No registry entry means no provenance, so the row is never assessed."""
    assert detect_change(_record(), None) is None
    assert detect_changes([_record()], {}) == []


def test_change_ids_are_stable_across_rescans() -> None:
    record = carry_forward(_record(), _record(checksum="bbbb2222"))
    first = detect_change(record, SOURCE)
    second = detect_change(record, SOURCE)

    assert first is not None and second is not None
    assert first.id == second.id
    assert first.id == change_id(SOURCE.id, ChangeKind.CONTENT_REVISED, "bbbb2222")


def test_detect_changes_is_ordered_by_source_id() -> None:
    other = RegSource(
        id="apra-cps-230",
        regulator=Regulator.APRA,
        jurisdiction=Jurisdiction.AU,
        title="APRA CPS 230",
        url="https://example.test/apra/cps230",
        doc_type=DocType.STANDARD,
        topics=("operational-resilience",),
    )
    records = [
        _record(),
        FreshnessRecord(
            source_id=other.id,
            url=other.url,
            version="2025",
            fetched_at=_T0,
            expires_at=_T0 + timedelta(days=7),
            checksum="cccc3333",
        ),
    ]
    changes = detect_changes(records, {SOURCE.id: SOURCE, other.id: other})
    assert [c.source_id for c in changes] == ["apra-cps-230", "mas-trm-guidelines"]


# --------------------------------------------------------------------------- #
# The ledger adapter persists the diff base
# --------------------------------------------------------------------------- #
def _ledger() -> LocalLedgerAdapter:
    return LocalLedgerAdapter(
        Settings(profile="local", local=LocalSettings(ledger_path=":memory:"))
    )


def test_local_ledger_round_trips_the_superseded_generation() -> None:
    ledger = _ledger()
    ledger.upsert(_record())
    rolled = carry_forward(ledger.get(SOURCE.id), _record(checksum="bbbb2222"))
    ledger.upsert(rolled)

    reloaded = ledger.get(SOURCE.id)
    assert reloaded is not None
    assert reloaded.previous_checksum == "aaaa1111"
    assert reloaded.previous_status is FreshnessStatus.FRESH
    assert classify(reloaded) is ChangeKind.CONTENT_REVISED


def test_pre_existing_ledger_table_is_migrated_in_place() -> None:
    """An older ledger.db (no history columns) gains them instead of being shadowed."""
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE freshness (source_id TEXT PRIMARY KEY, url TEXT NOT NULL, "
        "version TEXT NOT NULL, fetched_at TEXT NOT NULL, expires_at TEXT NOT NULL, "
        "checksum TEXT NOT NULL, status TEXT NOT NULL)"
    )
    conn.commit()

    adapter = _ledger()
    # Drive the migration against the legacy connection.
    adapter._conn = conn  # noqa: SLF001 - exercising the in-place migration path
    adapter._conn.row_factory = sqlite3.Row
    adapter._migrate_history_columns()  # noqa: SLF001
    adapter._conn.commit()

    columns = {row["name"] for row in conn.execute("PRAGMA table_info(freshness)")}
    assert {"previous_version", "previous_checksum", "previous_status"} <= columns

    adapter.upsert(carry_forward(None, _record()))
    assert adapter.get(SOURCE.id) is not None
