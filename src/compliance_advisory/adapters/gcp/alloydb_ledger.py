"""AlloyDB corpus-freshness ledger adapter (the 7-day fetch-at-runtime model).

Implements :class:`CorpusLedgerPort` against **AlloyDB for PostgreSQL**. Documents
themselves live in Agent Search; this ledger tracks *freshness* — for each registered
regulatory source it records when it was fetched, when it expires (fetch time + TTL),
its checksum, and status. On a read, fresh sources (< TTL) are served from the store;
expired sources are re-fetched and re-ingested before answering.

Connectivity uses the AlloyDB Python connector (PRIVATE IP) as a SQLAlchemy creator
over the pg8000 driver, so traffic stays on the VPC inside Singapore for MAS / HKMA /
APRA / FSA residency. The connector, SQLAlchemy engine, and all imports are built
lazily on first use so the on-prem and test profiles import this module with no GCP
SDK installed.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ...config import Settings
from ...domain.models import FreshnessRecord, FreshnessStatus

_COLUMNS = (
    "source_id",
    "url",
    "version",
    "fetched_at",
    "expires_at",
    "checksum",
    "status",
    # The superseded generation: the diff base horizon scanning reads. Extending the
    # existing ledger keeps ONE store of corpus state instead of a shadow copy.
    "previous_version",
    "previous_checksum",
    "previous_fetched_at",
    "previous_status",
)

#: Columns added after the first release; applied idempotently on schema setup so an
#: existing ledger table is migrated in place rather than recreated.
_HISTORY_COLUMNS: tuple[tuple[str, str], ...] = (
    ("previous_version", "TEXT NOT NULL DEFAULT ''"),
    ("previous_checksum", "TEXT NOT NULL DEFAULT ''"),
    ("previous_fetched_at", "TIMESTAMPTZ"),
    ("previous_status", "TEXT"),
)


class AlloyDBLedgerAdapter:
    """Persist :class:`FreshnessRecord` rows in an AlloyDB ``corpus_freshness`` table."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._alloydb = settings.alloydb
        self._table = settings.alloydb.table
        # Built lazily on first DB access so import works with no GCP SDK present.
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
        """Create the freshness table if it does not already exist (idempotent)."""
        import sqlalchemy  # lazy

        engine = self._get_engine()
        ddl = sqlalchemy.text(
            f"""
            CREATE TABLE IF NOT EXISTS {self._table} (
                source_id  TEXT PRIMARY KEY,
                url        TEXT NOT NULL,
                version    TEXT NOT NULL DEFAULT 'unknown',
                fetched_at TIMESTAMPTZ NOT NULL,
                expires_at TIMESTAMPTZ NOT NULL,
                checksum   TEXT NOT NULL DEFAULT '',
                status     TEXT NOT NULL DEFAULT 'fresh',
                previous_version    TEXT NOT NULL DEFAULT '',
                previous_checksum   TEXT NOT NULL DEFAULT '',
                previous_fetched_at TIMESTAMPTZ,
                previous_status     TEXT
            )
            """
        )
        index_ddl = sqlalchemy.text(
            f"CREATE INDEX IF NOT EXISTS {self._table}_expires_at_idx ON {self._table} (expires_at)"
        )
        with engine.begin() as conn:
            conn.execute(ddl)
            conn.execute(index_ddl)
            for column, column_type in _HISTORY_COLUMNS:
                conn.execute(
                    sqlalchemy.text(
                        f"ALTER TABLE {self._table} ADD COLUMN IF NOT EXISTS {column} {column_type}"
                    )
                )
        self._schema_ready = True

    # -- CorpusLedgerPort -------------------------------------------------- #
    def get(self, source_id: str) -> FreshnessRecord | None:
        import sqlalchemy  # lazy

        engine = self._ensure_ready()
        stmt = sqlalchemy.text(
            f"SELECT {', '.join(_COLUMNS)} FROM {self._table} WHERE source_id = :source_id"
        )
        with engine.connect() as conn:
            row = conn.execute(stmt, {"source_id": source_id}).mappings().first()
        return self._row_to_record(row) if row is not None else None

    def upsert(self, record: FreshnessRecord) -> None:
        import sqlalchemy  # lazy

        engine = self._ensure_ready()
        stmt = sqlalchemy.text(
            f"""
            INSERT INTO {self._table} ({", ".join(_COLUMNS)})
            VALUES
                (:source_id, :url, :version, :fetched_at, :expires_at, :checksum, :status,
                 :previous_version, :previous_checksum, :previous_fetched_at,
                 :previous_status)
            ON CONFLICT (source_id) DO UPDATE SET
                url                 = EXCLUDED.url,
                version             = EXCLUDED.version,
                fetched_at          = EXCLUDED.fetched_at,
                expires_at          = EXCLUDED.expires_at,
                checksum            = EXCLUDED.checksum,
                status              = EXCLUDED.status,
                previous_version    = EXCLUDED.previous_version,
                previous_checksum   = EXCLUDED.previous_checksum,
                previous_fetched_at = EXCLUDED.previous_fetched_at,
                previous_status     = EXCLUDED.previous_status
            """
        )
        with engine.begin() as conn:
            conn.execute(stmt, self._record_to_params(record))

    def list_expired(self, now: datetime | None = None) -> list[FreshnessRecord]:
        import sqlalchemy  # lazy

        cutoff = now or datetime.now(UTC)
        engine = self._ensure_ready()
        stmt = sqlalchemy.text(
            f"SELECT {', '.join(_COLUMNS)} FROM {self._table} "
            f"WHERE expires_at <= :cutoff ORDER BY expires_at ASC"
        )
        with engine.connect() as conn:
            rows = conn.execute(stmt, {"cutoff": cutoff}).mappings().all()
        return [self._row_to_record(row) for row in rows]

    def all(self) -> list[FreshnessRecord]:
        import sqlalchemy  # lazy

        engine = self._ensure_ready()
        stmt = sqlalchemy.text(
            f"SELECT {', '.join(_COLUMNS)} FROM {self._table} ORDER BY source_id ASC"
        )
        with engine.connect() as conn:
            rows = conn.execute(stmt).mappings().all()
        return [self._row_to_record(row) for row in rows]

    # -- row <-> record mapping ------------------------------------------- #
    @staticmethod
    def _record_to_params(record: FreshnessRecord) -> dict[str, Any]:
        return {
            "source_id": record.source_id,
            "url": record.url,
            "version": record.version,
            "fetched_at": record.fetched_at,
            "expires_at": record.expires_at,
            "checksum": record.checksum,
            "status": record.status.value,
            "previous_version": record.previous_version,
            "previous_checksum": record.previous_checksum,
            "previous_fetched_at": record.previous_fetched_at,
            "previous_status": record.previous_status.value if record.previous_status else None,
        }

    @staticmethod
    def _row_to_record(row: Any) -> FreshnessRecord:
        return FreshnessRecord(
            source_id=row["source_id"],
            url=row["url"],
            version=row["version"],
            fetched_at=_as_aware(row["fetched_at"]),
            expires_at=_as_aware(row["expires_at"]),
            checksum=row["checksum"] or "",
            status=FreshnessStatus(row["status"]),
            previous_version=row["previous_version"] or "",
            previous_checksum=row["previous_checksum"] or "",
            previous_fetched_at=(
                _as_aware(row["previous_fetched_at"]) if row["previous_fetched_at"] else None
            ),
            previous_status=(
                FreshnessStatus(row["previous_status"]) if row["previous_status"] else None
            ),
        )


def _as_aware(value: datetime) -> datetime:
    """Coerce a DB timestamp to a timezone-aware UTC datetime."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
