"""Firestore corpus-freshness ledger (``CorpusLedgerPort``): the scale-to-zero managed store.

Implements :class:`~compliance_advisory.ports.corpus.CorpusLedgerPort` against Firestore
Native in the deploy region, and it is the ``gcp`` profile's binding. The ledger is one small
document per regulatory source, written by the refresh job and read on a request, and
Firestore bills that load by the operation and the stored byte: it costs nothing while nobody
asks. An AlloyDB primary bills by the hour whether or not a single row moves, which is why
``adapters/gcp/alloydb_ledger.py`` is bound under ``platform`` instead and its Terraform is
off unless a deployment turns it on. Both hold the identical record.

One document per source, keyed by ``source_id``, carrying the superseded generation
(``previous_*``) that horizon scanning diffs. Every query here touches ONE field, so none
needs a composite index; ``tests/contract/test_firestore_provisioning.py`` fails if a query
ever does and ``infra/terraform/firestore.tf`` has not declared its index.

The database is named for the deploy region (``_firestore.database_for``) and the Google
Cloud SDK is imported lazily, on first use, so the SDK-free profiles import this module with
no ``google-cloud-firestore`` installed.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ...config import Settings
from ...domain.models import FreshnessRecord, FreshnessStatus, utcnow
from . import _firestore

#: The collection holding one document per regulatory source.
COLLECTION = "corpus_freshness"


class FirestoreLedgerAdapter:
    """Persist :class:`FreshnessRecord` documents in the region's Firestore database."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._database = _firestore.database_for(settings.region)
        # Built lazily on first access so construction needs no SDK and no credentials.
        self._client: Any | None = None

    def _collection(self) -> Any:
        if self._client is None:
            from google.cloud import firestore  # lazy: gcp profile only

            self._client = firestore.Client(
                project=self._settings.project_id, database=self._database
            )
        return self._client.collection(COLLECTION)

    # -- CorpusLedgerPort -------------------------------------------------- #
    def get(self, source_id: str) -> FreshnessRecord | None:
        snapshot = self._collection().document(_firestore.document_id(source_id)).get()
        if not snapshot.exists:
            return None
        return _to_record(snapshot.to_dict() or {})

    def upsert(self, record: FreshnessRecord) -> None:
        document = self._collection().document(_firestore.document_id(record.source_id))
        document.set(_to_document(record))

    def list_expired(self, now: datetime | None = None) -> list[FreshnessRecord]:
        cutoff = now or utcnow()
        query = self._collection().where(filter=_firestore.field_filter("expires_at", "<=", cutoff))
        # Ordered by source id, as every other ledger adapter answers, rather than by the
        # range field Firestore returns them in: the port's callers diff and display this.
        records = [_to_record(snapshot.to_dict() or {}) for snapshot in query.stream()]
        return sorted(records, key=lambda record: record.source_id)

    def all(self) -> list[FreshnessRecord]:
        query = self._collection().order_by("source_id")
        return [_to_record(snapshot.to_dict() or {}) for snapshot in query.stream()]


# -- document <-> record mapping ------------------------------------------ #
def _to_document(record: FreshnessRecord) -> dict[str, Any]:
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


def _to_record(data: dict[str, Any]) -> FreshnessRecord:
    previous_fetched_at = data.get("previous_fetched_at")
    previous_status = data.get("previous_status")
    return FreshnessRecord(
        source_id=str(data["source_id"]),
        url=str(data["url"]),
        version=str(data["version"]),
        fetched_at=_firestore.as_utc(data["fetched_at"]),
        expires_at=_firestore.as_utc(data["expires_at"]),
        checksum=str(data.get("checksum") or ""),
        status=FreshnessStatus(str(data["status"])),
        previous_version=str(data.get("previous_version") or ""),
        previous_checksum=str(data.get("previous_checksum") or ""),
        previous_fetched_at=(
            _firestore.as_utc(previous_fetched_at) if previous_fetched_at is not None else None
        ),
        previous_status=FreshnessStatus(str(previous_status)) if previous_status else None,
    )
