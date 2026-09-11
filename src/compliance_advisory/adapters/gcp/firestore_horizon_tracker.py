"""Firestore ``HorizonTrackerPort`` adapter: the implementation journey, scale-to-zero.

Implements :class:`~compliance_advisory.ports.horizon.HorizonTrackerPort` against Firestore
Native, in the same regional database as the freshness ledger
(``adapters/gcp/firestore_ledger.py``), so the corpus state and the journey built on it stay
in one store that costs nothing while idle. The AlloyDB tracker it replaces on the ``gcp``
profile stays bound under ``platform``.

One document per assessed change, keyed by ``change_id``, carrying the owning ``tenant`` as
the authorization partition. ``get`` returns the document whatever its tenant, BY DESIGN: the
tenant check belongs in the domain so a cross-tenant read is an explicit 403 rather than a 404
that hides whether the change exists. ``list`` filters by tenant on the server, so a listing
can never carry another tenant's row even transiently.

``list`` is the one query here that needs a composite index (``tenant`` equality ordered by
``change_id``). ``infra/terraform/firestore.tf`` declares it, and
``tests/contract/test_firestore_provisioning.py`` holds the declaration against the query:
without the index Firestore refuses the query with FAILED_PRECONDITION at request time, on the
deployment, and nowhere else.

The Google Cloud SDK is imported lazily, on first use, so the SDK-free profiles import this
module with no ``google-cloud-firestore`` installed.
"""

from __future__ import annotations

from typing import Any

from ...config import Settings
from ...domain.horizon.models import (
    ImplementationItem,
    ImplementationStatus,
    MaterialityBand,
)
from . import _firestore

#: The collection holding one document per tracked change.
COLLECTION = "horizon_tracking"


class FirestoreHorizonTrackerAdapter:
    """Persist :class:`ImplementationItem` documents in the region's Firestore database."""

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

    # -- HorizonTrackerPort ------------------------------------------------ #
    def upsert(self, item: ImplementationItem) -> None:
        document = self._collection().document(_firestore.document_id(item.change_id))
        document.set(_to_document(item))

    def get(self, change_id: str) -> ImplementationItem | None:
        snapshot = self._collection().document(_firestore.document_id(change_id)).get()
        if not snapshot.exists:
            return None
        return _to_item(snapshot.to_dict() or {})

    def list(self, tenant: str) -> list[ImplementationItem]:
        query = (
            self._collection()
            .where(filter=_firestore.field_filter("tenant", "==", tenant))
            .order_by("change_id")
        )
        return [_to_item(snapshot.to_dict() or {}) for snapshot in query.stream()]


# -- document <-> item mapping -------------------------------------------- #
def _to_document(item: ImplementationItem) -> dict[str, Any]:
    return {
        "change_id": item.change_id,
        "tenant": item.tenant,
        "source_id": item.source_id,
        "status": item.status.value,
        "owner": item.owner,
        "materiality_band": item.materiality_band.value,
        "due_within_days": int(item.due_within_days),
        "control_ids": list(item.control_ids),
        "note": item.note,
        "updated_by": item.updated_by,
        "updated_at": item.updated_at,
    }


def _to_item(data: dict[str, Any]) -> ImplementationItem:
    return ImplementationItem(
        change_id=str(data["change_id"]),
        tenant=str(data["tenant"]),
        source_id=str(data["source_id"]),
        status=ImplementationStatus(str(data["status"])),
        owner=str(data.get("owner") or ""),
        materiality_band=MaterialityBand(str(data["materiality_band"])),
        due_within_days=int(data.get("due_within_days") or 0),
        control_ids=tuple(str(control) for control in (data.get("control_ids") or ())),
        note=str(data.get("note") or ""),
        updated_by=str(data.get("updated_by") or ""),
        updated_at=_firestore.as_utc(data["updated_at"]),
    )
