"""Local corpus-freshness ledger adapter (CorpusLedgerPort) — SQLite store.

The ``local`` profile's stand-in for the **AlloyDB** freshness ledger: a small SQLite
table tracking when each source was fetched and when it expires (the 7-day TTL model),
seedable and deterministic. When the Firestore emulator is opted in
(``FIRESTORE_EMULATOR_HOST`` set AND the client lib imports), the adapter routes to it;
the google client is imported lazily, only on that branch, so the default path imports
no google-cloud package.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime
from pathlib import Path

from ...config import Settings
from ...domain.models import FreshnessRecord, FreshnessStatus, utcnow
from . import _sqlite
from ._emulator import firestore_emulator_active

_DEFAULT_DB_DIR = Path.home() / ".compliance_advisory"
_DEFAULT_LEDGER_PATH = _DEFAULT_DB_DIR / "ledger.db"


class LocalLedgerAdapter:
    """SQLite freshness ledger (Firestore-emulator-backed when opted in)."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        path = getattr(getattr(settings, "local", None), "ledger_path", "") or str(
            _DEFAULT_LEDGER_PATH
        )
        # ``check_same_thread=False`` + a lock: under ``local serve`` the container is
        # process-wide (deps.get_container is lru_cached) but sync endpoints run in
        # Starlette's anyio worker threadpool, so get()/upsert() are called from worker
        # threads other than the one that opened the connection. The lock serialises access
        # (single-writer) so cross-thread use does not raise.
        self._lock = threading.Lock()
        self._conn = _sqlite.connect(path)
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS freshness (
                source_id TEXT PRIMARY KEY,
                url TEXT NOT NULL,
                version TEXT NOT NULL,
                fetched_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                checksum TEXT NOT NULL,
                status TEXT NOT NULL
            )
            """
        )
        self._migrate_history_columns()
        self._conn.commit()
        self._fs = None
        if firestore_emulator_active():
            from google.cloud import firestore  # noqa: PLC0415

            self._fs = firestore.Client(project=settings.project_id or "local")

    # The superseded generation (the horizon-scanning diff base) was added to the ledger
    # after the first releases, so an existing local ledger.db is migrated in place rather
    # than shadowed by a second store. ``ALTER TABLE ... ADD COLUMN`` is idempotent here
    # because the columns are checked first.
    _HISTORY_COLUMNS: tuple[tuple[str, str], ...] = (
        ("previous_version", "TEXT NOT NULL DEFAULT ''"),
        ("previous_checksum", "TEXT NOT NULL DEFAULT ''"),
        ("previous_fetched_at", "TEXT NOT NULL DEFAULT ''"),
        ("previous_status", "TEXT NOT NULL DEFAULT ''"),
    )

    def _migrate_history_columns(self) -> None:
        existing = {row["name"] for row in self._conn.execute("PRAGMA table_info(freshness)")}
        for column, ddl in self._HISTORY_COLUMNS:
            if column not in existing:
                self._conn.execute(f"ALTER TABLE freshness ADD COLUMN {column} {ddl}")

    def get(self, source_id: str) -> FreshnessRecord | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM freshness WHERE source_id = ?", (source_id,)
            ).fetchone()
        return self._row_to_record(row) if row else None

    def upsert(self, record: FreshnessRecord) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO freshness "
                "(source_id, url, version, fetched_at, expires_at, checksum, status, "
                " previous_version, previous_checksum, previous_fetched_at, previous_status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(source_id) DO UPDATE SET "
                "url=excluded.url, version=excluded.version, fetched_at=excluded.fetched_at, "
                "expires_at=excluded.expires_at, checksum=excluded.checksum, "
                "status=excluded.status, previous_version=excluded.previous_version, "
                "previous_checksum=excluded.previous_checksum, "
                "previous_fetched_at=excluded.previous_fetched_at, "
                "previous_status=excluded.previous_status",
                (
                    record.source_id,
                    record.url,
                    record.version,
                    record.fetched_at.isoformat(),
                    record.expires_at.isoformat(),
                    record.checksum,
                    record.status.value,
                    record.previous_version,
                    record.previous_checksum,
                    record.previous_fetched_at.isoformat() if record.previous_fetched_at else "",
                    record.previous_status.value if record.previous_status else "",
                ),
            )
            self._conn.commit()

    def list_expired(self, now: datetime | None = None) -> list[FreshnessRecord]:
        now = now or utcnow()
        return [r for r in self.all() if r.expires_at <= now]

    def all(self) -> list[FreshnessRecord]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM freshness ORDER BY source_id ASC").fetchall()
        return [self._row_to_record(row) for row in rows]

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> FreshnessRecord:
        keys = row.keys()
        previous_fetched_at = row["previous_fetched_at"] if "previous_fetched_at" in keys else ""
        previous_status = row["previous_status"] if "previous_status" in keys else ""
        return FreshnessRecord(
            source_id=row["source_id"],
            url=row["url"],
            version=row["version"],
            fetched_at=datetime.fromisoformat(row["fetched_at"]),
            expires_at=datetime.fromisoformat(row["expires_at"]),
            checksum=row["checksum"],
            status=FreshnessStatus(row["status"]),
            previous_version=(row["previous_version"] if "previous_version" in keys else "") or "",
            previous_checksum=(
                (row["previous_checksum"] if "previous_checksum" in keys else "") or ""
            ),
            previous_fetched_at=(
                datetime.fromisoformat(previous_fetched_at) if previous_fetched_at else None
            ),
            previous_status=FreshnessStatus(previous_status) if previous_status else None,
        )
