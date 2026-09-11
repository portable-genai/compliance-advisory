"""Shared plumbing for the two Firestore adapters: the database name, filters and timestamps.

The freshness ledger and the horizon tracker live in ONE named Firestore Native database per
deploy region, ``compliance-advisory-<region>``. A project holds a single immutable
``(default)`` database whose location is fixed when it is created, and this service is
deployed into projects that already run other applications, so it never touches that one:
the default database belongs to whichever application created it first.

``infra/terraform/firestore.tf`` builds the same name from the same region, and
``tests/contract/test_firestore_provisioning.py`` holds the two together. A database the
Terraform does not create is one the adapters would name and then fail on at the first
request.

Nothing here imports a Google Cloud SDK at module scope. :func:`field_filter` imports lazily,
inside the call, so the SDK-free profiles and the offline gate import both adapters without
``google-cloud-firestore`` installed.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

#: Every database this service creates starts with this. The region follows it.
DATABASE_PREFIX = "compliance-advisory-"

#: Firestore reserves ids of this shape for itself.
_RESERVED_ID = re.compile(r"^__.*__$")

#: Firestore's own ceiling on a document id, in bytes.
_MAX_ID_BYTES = 1500


def database_for(region: str) -> str:
    """The Firestore database serving ``region``. See :data:`DATABASE_PREFIX`."""
    resolved = region.strip()
    if not resolved:
        raise ValueError(
            "no deploy region is configured, so there is no Firestore database to name. An "
            "unresolved region must never fall back to the project's (default) database, which "
            "belongs to whichever application in the project created it first."
        )
    return f"{DATABASE_PREFIX}{resolved}"


def document_id(value: str) -> str:
    """``value`` as a document id, refused before any call if Firestore would reject it.

    Source ids and change ids are slugs here, so this never refuses a real one. It exists so
    that an id which would have been read as a PATH (a ``/`` splits it into a sub-collection)
    fails loudly in this process instead of writing a record somewhere nothing reads.
    """
    if (
        not value
        or value in {".", ".."}
        or "/" in value
        or _RESERVED_ID.match(value)
        or len(value.encode("utf-8")) > _MAX_ID_BYTES
    ):
        raise ValueError(f"{value!r} cannot be a Firestore document id")
    return value


def field_filter(field_path: str, op_string: str, value: Any) -> Any:
    """A server-side ``where`` filter, in the keyword form the current client requires.

    Positional ``where(field, op, value)`` is deprecated in ``google-cloud-firestore`` and warns
    on every call; ``where(filter=FieldFilter(...))`` is its replacement.
    """
    from google.cloud.firestore_v1.base_query import FieldFilter  # lazy: gcp profile only

    return FieldFilter(field_path, op_string, value)


def as_utc(value: datetime) -> datetime:
    """A plain, timezone-aware UTC ``datetime`` from whatever Firestore handed back.

    Firestore returns ``DatetimeWithNanoseconds``, a ``datetime`` subclass, already in UTC. It
    is rebuilt as a plain ``datetime`` so a domain record read from Firestore compares,
    hashes and serialises exactly like one read from any other store. A naive value is taken
    to be UTC, which is how Firestore stores one.
    """
    aware = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return datetime(
        aware.year,
        aware.month,
        aware.day,
        aware.hour,
        aware.minute,
        aware.second,
        aware.microsecond,
        tzinfo=UTC,
    )
