"""Concurrency regression: the local SQLite adapters are safe across worker threads.

Under ``local serve`` the FastAPI container is process-wide (``deps.get_container`` is
``lru_cache``d, so one adapter instance with one ``sqlite3`` connection) while the sync
endpoints run in Starlette's anyio worker threadpool. A request handled on a worker
thread therefore calls ``record()`` / ``upsert()`` / ``retrieve()`` on a connection
opened by a *different* thread. Without ``check_same_thread=False`` plus a lock, sqlite3
raises "SQLite objects created in a thread can only be used in that same thread",
dropping the WORM audit event and 500-ing the request under concurrency.

These tests construct each adapter **once** and then drive interleaved writes and reads
from many threads via a ``ThreadPoolExecutor``, asserting no exception is raised and the
final row counts are exact. They fail before the ``check_same_thread=False`` + lock fix
(with the cross-thread ``ProgrammingError``) and pass after it.
"""

from __future__ import annotations

import concurrent.futures

from compliance_advisory.adapters.local.audit import LocalAppendOnlyAuditAdapter
from compliance_advisory.adapters.local.ledger import LocalLedgerAdapter
from compliance_advisory.adapters.local.retrieval import LocalFtsRetrievalAdapter
from compliance_advisory.config import LocalSettings, Settings
from compliance_advisory.domain.models import (
    AuditEvent,
    Citation,
    Decision,
    FreshnessRecord,
    FreshnessStatus,
    Jurisdiction,
    Regulator,
    RetrievalQuery,
    RetrievedPassage,
    utcnow,
)

_THREADS = 8
_PER_THREAD = 25


def _settings() -> Settings:
    # A single shared in-memory connection (in-memory DBs are per-connection, so the one
    # connection opened in __init__ is the one all worker threads must reach).
    return Settings(
        profile="local",
        local=LocalSettings(db_path=":memory:", audit_path=":memory:", ledger_path=":memory:"),
    )


def _drive(fn) -> list:
    """Run ``fn(i)`` for i in range(_THREADS * _PER_THREAD) across a thread pool, raising
    the first exception any worker hit (so a cross-thread sqlite3 error fails the test)."""
    n = _THREADS * _PER_THREAD
    with concurrent.futures.ThreadPoolExecutor(max_workers=_THREADS) as pool:
        futures = [pool.submit(fn, i) for i in range(n)]
        return [f.result() for f in concurrent.futures.as_completed(futures)]


def test_audit_adapter_is_thread_safe() -> None:
    adapter = LocalAppendOnlyAuditAdapter(_settings())

    def work(i: int) -> int:
        adapter.record(
            AuditEvent(
                action="ask",
                actor=f"user-{i}",
                decision=Decision.ALLOWED,
                redacted_prompt="q",
                redacted_response="a",
            )
        )
        # Interleave a read on the same connection from the worker thread too.
        return len(adapter.read_all())

    _drive(work)

    assert len(adapter.read_all()) == _THREADS * _PER_THREAD


def test_ledger_adapter_is_thread_safe() -> None:
    adapter = LocalLedgerAdapter(_settings())
    now = utcnow()

    def work(i: int) -> object:
        rec = FreshnessRecord(
            source_id=f"src-{i}",
            url=f"https://example.test/{i}",
            version="v1",
            fetched_at=now,
            expires_at=now,
            checksum="abc",
            status=FreshnessStatus.FRESH,
        )
        adapter.upsert(rec)
        return adapter.get(rec.source_id)

    _drive(work)

    assert len(adapter.all()) == _THREADS * _PER_THREAD


def test_retrieval_adapter_is_thread_safe() -> None:
    adapter = LocalFtsRetrievalAdapter(_settings())

    def passage(i: int) -> RetrievedPassage:
        return RetrievedPassage(
            text=f"capital adequacy buffer requirement clause {i}",
            citation=Citation(
                source_id=f"reg-{i}",
                regulator=Regulator.MAS,
                jurisdiction=Jurisdiction.SG,
                title=f"Notice {i}",
                url=f"https://mas.test/{i}",
                version="2026",
                page=1,
            ),
            score=0.5,
        )

    def work(i: int) -> int:
        # Mix writes (add) and reads (retrieve) on the shared connection.
        adapter.add([passage(i)])
        hits = adapter.retrieve(RetrievalQuery(text="capital adequacy requirement", top_k=5))
        return len(hits)

    results = _drive(work)

    # Every retrieve ran without a cross-thread error and returned ranked rows.
    assert all(r >= 0 for r in results)
    assert any(r > 0 for r in results)


# --------------------------------------------------------------------------- #
# ``file:`` URIs must be honoured, not treated as a literal filename
# --------------------------------------------------------------------------- #
def test_shared_in_memory_uri_is_shared_and_creates_no_file(tmp_path, monkeypatch) -> None:
    """Two adapters pointed at one shared-cache URI must see the SAME database.

    Without ``sqlite3.connect(..., uri=True)`` SQLite treats the whole URI as a filename
    and silently creates a file called ``file:name?mode=memory&cache=shared`` in the
    working directory, while each adapter quietly gets its own empty store. Both halves
    are asserted here: the sharing works, and nothing is written to disk.
    """
    from compliance_advisory.adapters.local.horizon_tracker import (
        LocalHorizonTrackerAdapter,
    )
    from compliance_advisory.domain.horizon.models import (
        ImplementationItem,
        ImplementationStatus,
    )

    monkeypatch.chdir(tmp_path)
    uri = "file:shared-tracker-test?mode=memory&cache=shared"
    settings = Settings(profile="local", local=LocalSettings(horizon_path=uri))

    writer = LocalHorizonTrackerAdapter(settings)
    reader = LocalHorizonTrackerAdapter(settings)
    writer.upsert(
        ImplementationItem(
            change_id="mas-trm:content_revised:abc",
            tenant="demo-bank",
            source_id="mas-trm",
            status=ImplementationStatus.IN_PROGRESS,
        )
    )

    assert reader.get("mas-trm:content_revised:abc") is not None
    assert [p.name for p in tmp_path.iterdir()] == []
