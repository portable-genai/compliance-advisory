"""AlloyDB HorizonTrackerPort adapter — the implementation journey for assessed changes.

Implements :class:`~compliance_advisory.ports.horizon.HorizonTrackerPort` against
**AlloyDB for PostgreSQL**, alongside the freshness ledger it shares a database with, so
the horizon journey and the corpus state stay in one regional store inside Singapore for
MAS / HKMA / APRA / FSA residency.

Connectivity mirrors :mod:`compliance_advisory.adapters.gcp.alloydb_ledger` exactly: the
AlloyDB Python connector (PRIVATE IP) as a SQLAlchemy creator over pg8000, with the
connector, engine and every import built lazily on first use so the on-prem and test
profiles import this module with no GCP SDK installed.

``get`` is tenant-agnostic by design (the domain performs the fail-closed tenant check and
returns 403); ``list`` filters by tenant in SQL.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ...config import Settings
from ...domain.horizon.models import (
    ImplementationItem,
    ImplementationStatus,
    MaterialityBand,
)

_COLUMNS = (
    "change_id",
    "tenant",
    "source_id",
    "status",
    "owner",
    "materiality_band",
    "due_within_days",
    "control_ids",
    "note",
    "updated_by",
    "updated_at",
)


class AlloyDBHorizonTrackerAdapter:
    """Persist :class:`ImplementationItem` rows in an AlloyDB ``horizon_tracking`` table."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._alloydb = settings.alloydb
        self._table = settings.alloydb.horizon_table
        self._connector: Any | None = None
        self._engine: Any | None = None
        self._schema_ready = False

    # -- engine / connection ---------------------------------------------- #
    def _get_engine(self) -> Any:
        if self._engine is not None:
            return self._engine

        import sqlalchemy  # lazy
        from google.cloud.alloydb.connector import Connector, IPTypes  # lazy

        ip_type = IPTypes.PUBLIC if self._alloydb.ip_type.upper() == "PUBLIC" else IPTypes.PRIVATE
        connector = Connector()
        self._connector = connector

        def _getconn() -> Any:
            # verify: https://docs.cloud.google.com/alloydb/docs/connect-language-connectors
            return connector.connect(
                self._alloydb.instance_uri,
                "pg8000",
                user=self._alloydb.user,
                db=self._alloydb.database,
                ip_type=ip_type,
                enable_iam_auth=True,
            )

        self._engine = sqlalchemy.create_engine(
            "postgresql+pg8000://",
            creator=_getconn,
            pool_pre_ping=True,
        )
        return self._engine

    def _ensure_ready(self) -> Any:
        engine = self._get_engine()
        if not self._schema_ready:
            self.ensure_schema()
        return engine

    # -- schema ------------------------------------------------------------ #
    def ensure_schema(self) -> None:
        """Create the tracking table if it does not already exist (idempotent)."""
        import sqlalchemy  # lazy

        engine = self._get_engine()
        ddl = sqlalchemy.text(
            f"""
            CREATE TABLE IF NOT EXISTS {self._table} (
                change_id        TEXT PRIMARY KEY,
                tenant           TEXT NOT NULL,
                source_id        TEXT NOT NULL,
                status           TEXT NOT NULL DEFAULT 'not_started',
                owner            TEXT NOT NULL DEFAULT '',
                materiality_band TEXT NOT NULL DEFAULT 'low',
                due_within_days  INTEGER NOT NULL DEFAULT 0,
                control_ids      TEXT NOT NULL DEFAULT '',
                note             TEXT NOT NULL DEFAULT '',
                updated_by       TEXT NOT NULL DEFAULT '',
                updated_at       TIMESTAMPTZ NOT NULL
            )
            """
        )
        index_ddl = sqlalchemy.text(
            f"CREATE INDEX IF NOT EXISTS {self._table}_tenant_idx ON {self._table} (tenant)"
        )
        with engine.begin() as conn:
            conn.execute(ddl)
            conn.execute(index_ddl)
        self._schema_ready = True

    # -- HorizonTrackerPort ------------------------------------------------ #
    def upsert(self, item: ImplementationItem) -> None:
        import sqlalchemy  # lazy

        engine = self._ensure_ready()
        stmt = sqlalchemy.text(
            f"""
            INSERT INTO {self._table} ({", ".join(_COLUMNS)})
            VALUES
                (:change_id, :tenant, :source_id, :status, :owner, :materiality_band,
                 :due_within_days, :control_ids, :note, :updated_by, :updated_at)
            ON CONFLICT (change_id) DO UPDATE SET
                tenant           = EXCLUDED.tenant,
                source_id        = EXCLUDED.source_id,
                status           = EXCLUDED.status,
                owner            = EXCLUDED.owner,
                materiality_band = EXCLUDED.materiality_band,
                due_within_days  = EXCLUDED.due_within_days,
                control_ids      = EXCLUDED.control_ids,
                note             = EXCLUDED.note,
                updated_by       = EXCLUDED.updated_by,
                updated_at       = EXCLUDED.updated_at
            """
        )
        with engine.begin() as conn:
            conn.execute(stmt, self._item_to_params(item))

    def get(self, change_id: str) -> ImplementationItem | None:
        import sqlalchemy  # lazy

        engine = self._ensure_ready()
        stmt = sqlalchemy.text(
            f"SELECT {', '.join(_COLUMNS)} FROM {self._table} WHERE change_id = :change_id"
        )
        with engine.connect() as conn:
            row = conn.execute(stmt, {"change_id": change_id}).mappings().first()
        return self._row_to_item(row) if row is not None else None

    def list(self, tenant: str) -> list[ImplementationItem]:
        import sqlalchemy  # lazy

        engine = self._ensure_ready()
        stmt = sqlalchemy.text(
            f"SELECT {', '.join(_COLUMNS)} FROM {self._table} "
            f"WHERE tenant = :tenant ORDER BY change_id ASC"
        )
        with engine.connect() as conn:
            rows = conn.execute(stmt, {"tenant": tenant}).mappings().all()
        return [self._row_to_item(row) for row in rows]

    # -- row <-> item mapping ---------------------------------------------- #
    @staticmethod
    def _item_to_params(item: ImplementationItem) -> dict[str, Any]:
        return {
            "change_id": item.change_id,
            "tenant": item.tenant,
            "source_id": item.source_id,
            "status": item.status.value,
            "owner": item.owner,
            "materiality_band": item.materiality_band.value,
            "due_within_days": int(item.due_within_days),
            "control_ids": ",".join(item.control_ids),
            "note": item.note,
            "updated_by": item.updated_by,
            "updated_at": item.updated_at,
        }

    @staticmethod
    def _row_to_item(row: Any) -> ImplementationItem:
        raw_controls = str(row["control_ids"] or "")
        return ImplementationItem(
            change_id=row["change_id"],
            tenant=row["tenant"],
            source_id=row["source_id"],
            status=ImplementationStatus(row["status"]),
            owner=row["owner"] or "",
            materiality_band=MaterialityBand(row["materiality_band"]),
            due_within_days=int(row["due_within_days"] or 0),
            control_ids=tuple(c for c in raw_controls.split(",") if c),
            note=row["note"] or "",
            updated_by=row["updated_by"] or "",
            updated_at=_as_aware(row["updated_at"]),
        )


def _as_aware(value: datetime) -> datetime:
    """Coerce a DB timestamp to a timezone-aware UTC datetime."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
