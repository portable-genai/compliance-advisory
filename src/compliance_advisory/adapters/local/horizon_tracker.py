"""Local HorizonTrackerPort adapter — SQLite implementation-tracking store.

The ``local`` profile's stand-in for the AlloyDB tracker: a small SQLite table holding one
row per assessed regulatory change, keyed by ``change_id`` and carrying the owning
``tenant`` as the authorization partition. Seedable, deterministic and SDK-free.

``get`` returns the row regardless of tenant BY DESIGN: the tenant check belongs in the
domain service so a cross-tenant read is denied with an explicit 403 rather than a 404
that hides whether the change exists. ``list`` is tenant-scoped in SQL, so a listing can
never emit another tenant's row.

Thread-safety mirrors the freshness ledger: ``check_same_thread=False`` plus a lock, since
under ``local serve`` the container is process-wide but sync endpoints run in Starlette's
worker threadpool.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime
from pathlib import Path

from ...config import Settings
from ...domain.horizon.models import (
    ImplementationItem,
    ImplementationStatus,
    MaterialityBand,
)
from . import _sqlite

_DEFAULT_DB_DIR = Path.home() / ".compliance_advisory"
_DEFAULT_TRACKER_PATH = _DEFAULT_DB_DIR / "horizon.db"


class LocalHorizonTrackerAdapter:
    """SQLite implementation tracker for the SDK-free ``local`` profile."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        path = getattr(getattr(settings, "local", None), "horizon_path", "") or str(
            _DEFAULT_TRACKER_PATH
        )
        self._lock = threading.Lock()
        self._conn = _sqlite.connect(path)
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS horizon_tracking (
                change_id TEXT PRIMARY KEY,
                tenant TEXT NOT NULL,
                source_id TEXT NOT NULL,
                status TEXT NOT NULL,
                owner TEXT NOT NULL DEFAULT '',
                materiality_band TEXT NOT NULL DEFAULT 'low',
                due_within_days INTEGER NOT NULL DEFAULT 0,
                control_ids TEXT NOT NULL DEFAULT '',
                note TEXT NOT NULL DEFAULT '',
                updated_by TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL
            )
            """
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS horizon_tracking_tenant_idx ON horizon_tracking (tenant)"
        )
        self._conn.commit()

    # ------------------------------------------------------------------ #
    # HorizonTrackerPort
    # ------------------------------------------------------------------ #
    def upsert(self, item: ImplementationItem) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO horizon_tracking "
                "(change_id, tenant, source_id, status, owner, materiality_band, "
                " due_within_days, control_ids, note, updated_by, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(change_id) DO UPDATE SET "
                "tenant=excluded.tenant, source_id=excluded.source_id, "
                "status=excluded.status, owner=excluded.owner, "
                "materiality_band=excluded.materiality_band, "
                "due_within_days=excluded.due_within_days, "
                "control_ids=excluded.control_ids, note=excluded.note, "
                "updated_by=excluded.updated_by, updated_at=excluded.updated_at",
                (
                    item.change_id,
                    item.tenant,
                    item.source_id,
                    item.status.value,
                    item.owner,
                    item.materiality_band.value,
                    int(item.due_within_days),
                    ",".join(item.control_ids),
                    item.note,
                    item.updated_by,
                    item.updated_at.isoformat(),
                ),
            )
            self._conn.commit()

    def get(self, change_id: str) -> ImplementationItem | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM horizon_tracking WHERE change_id = ?", (change_id,)
            ).fetchone()
        return self._row_to_item(row) if row else None

    def list(self, tenant: str) -> list[ImplementationItem]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM horizon_tracking WHERE tenant = ? ORDER BY change_id ASC",
                (tenant,),
            ).fetchall()
        return [self._row_to_item(row) for row in rows]

    # ------------------------------------------------------------------ #
    # Row mapping
    # ------------------------------------------------------------------ #
    @staticmethod
    def _row_to_item(row: sqlite3.Row) -> ImplementationItem:
        control_ids = tuple(c for c in str(row["control_ids"] or "").split(",") if c)
        return ImplementationItem(
            change_id=row["change_id"],
            tenant=row["tenant"],
            source_id=row["source_id"],
            status=ImplementationStatus(row["status"]),
            owner=row["owner"] or "",
            materiality_band=MaterialityBand(row["materiality_band"]),
            due_within_days=int(row["due_within_days"] or 0),
            control_ids=control_ids,
            note=row["note"] or "",
            updated_by=row["updated_by"] or "",
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )
