"""An in-memory stand-in for the slice of ``google.cloud.firestore`` the two adapters call.

Not a mock that answers whatever it is asked. It holds the Firestore rules an adapter could
break without anything offline noticing:

* a timestamp comes back timezone-aware in UTC and as a ``datetime`` SUBCLASS, the way
  ``DatetimeWithNanoseconds`` does; a tuple comes back as a list; a value Firestore cannot
  store is refused;
* a document id Firestore would reject, or read as a path, is refused;
* ``where`` takes only the keyword ``filter=`` form, because the positional form is
  deprecated in the real client;
* a query over more than one field needs a composite index, and one the Terraform does not
  DECLARE fails the way Firestore fails it, with FAILED_PRECONDITION when the query runs. The
  declared set is read from ``infra/terraform/firestore.tf``, so every suite that drives an
  adapter through this client also proves the Terraform declares what that adapter needs;
* a range filter on one field ordered by a different one is refused, as Firestore refuses it.

Every composite query that actually runs is recorded, so a test can also prove no declared
index is dead configuration.
"""

from __future__ import annotations

import copy
import operator
import re
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
FIRESTORE_TF = REPO_ROOT / "infra" / "terraform" / "firestore.tf"

_OPERATORS: dict[str, Callable[[Any, Any], bool]] = {
    "==": operator.eq,
    "!=": operator.ne,
    "<": operator.lt,
    "<=": operator.le,
    ">": operator.gt,
    ">=": operator.ge,
}
_RANGE_OPERATORS = frozenset({"!=", "<", "<=", ">", ">="})


class FailedPreconditionError(Exception):
    """What Firestore raises for a query whose composite index does not exist."""


@dataclass(frozen=True)
class FieldFilter:
    """The attribute names of ``google.cloud.firestore_v1.base_query.FieldFilter``."""

    field_path: str
    op_string: str
    value: Any


class FirestoreTimestamp(datetime):
    """Stands in for ``DatetimeWithNanoseconds``: a datetime subclass, always UTC."""


def declared_composite_indexes(path: Path = FIRESTORE_TF) -> dict[str, set[tuple[str, ...]]]:
    """``collection -> {ordered field tuples}`` exactly as ``firestore.tf`` declares them."""
    text = path.read_text(encoding="utf-8")
    block = re.search(r"firestore_composite_indexes = \[(.*?)\n  \]", text, flags=re.DOTALL)
    assert block is not None, "firestore.tf no longer declares firestore_composite_indexes"
    declared: dict[str, set[tuple[str, ...]]] = {}
    for collection, fields in re.findall(
        r'\{\s*collection\s*=\s*"(\w+)"\s*,\s*fields\s*=\s*\[([^\]]*)\]\s*\}', block.group(1)
    ):
        declared.setdefault(collection, set()).add(tuple(re.findall(r'"(\w+)"', fields)))
    return declared


def _document_id(value: str) -> str:
    if (
        not value
        or value in {".", ".."}
        or "/" in value
        or re.match(r"^__.*__$", value)
        or len(value.encode("utf-8")) > 1500
    ):
        raise ValueError(f"Firestore refuses the document id {value!r}")
    return value


def _encode(value: Any) -> Any:
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        return value.encode("utf-8").decode("utf-8")  # a StrEnum member stores as its text
    if isinstance(value, datetime):
        aware = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
        return FirestoreTimestamp(
            aware.year,
            aware.month,
            aware.day,
            aware.hour,
            aware.minute,
            aware.second,
            aware.microsecond,
            tzinfo=UTC,
        )
    if isinstance(value, list | tuple):
        return [_encode(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _encode(item) for key, item in value.items()}
    raise TypeError(f"Firestore cannot store a {type(value).__name__}")


class _Snapshot:
    def __init__(self, doc_id: str, data: dict[str, Any] | None) -> None:
        self.id = doc_id
        self.exists = data is not None
        self._data = data

    def to_dict(self) -> dict[str, Any] | None:
        return copy.deepcopy(self._data)


class _DocumentReference:
    def __init__(self, client: FakeFirestoreClient, collection: str, doc_id: str) -> None:
        self._client = client
        self._collection = collection
        self.id = _document_id(doc_id)

    def get(self) -> _Snapshot:
        return _Snapshot(self.id, self._client._documents(self._collection).get(self.id))

    def set(self, data: Mapping[str, Any]) -> None:
        self._client._documents(self._collection)[self.id] = _encode(dict(data))


class _Query:
    def __init__(
        self,
        client: FakeFirestoreClient,
        collection: str,
        filters: tuple[FieldFilter, ...] = (),
        orders: tuple[str, ...] = (),
    ) -> None:
        self._client = client
        self._collection = collection
        self._filters = filters
        self._orders = orders

    def where(self, *, filter: FieldFilter) -> _Query:  # noqa: A002 - the real keyword
        if filter.op_string not in _OPERATORS:
            raise ValueError(f"operator {filter.op_string!r} is not supported by this fake")
        return _Query(self._client, self._collection, (*self._filters, filter), self._orders)

    def order_by(self, field_path: str) -> _Query:
        return _Query(self._client, self._collection, self._filters, (*self._orders, field_path))

    def _index_fields(self) -> tuple[str, ...]:
        equalities = [f.field_path for f in self._filters if f.op_string == "=="]
        ranges = [f.field_path for f in self._filters if f.op_string in _RANGE_OPERATORS]
        if len(set(ranges)) > 1:
            raise FailedPreconditionError("range filters on more than one field")
        if ranges and self._orders and self._orders[0] != ranges[0]:
            raise ValueError(
                f"a range filter on {ranges[0]!r} must be ordered by that field first, "
                f"not by {self._orders[0]!r}"
            )
        fields: list[str] = []
        for name in (*equalities, *ranges, *self._orders):
            if name not in fields:
                fields.append(name)
        return tuple(fields)

    def stream(self) -> Iterator[_Snapshot]:
        fields = self._index_fields()
        if len(fields) > 1:
            self._client.composite_queries_run.add((self._collection, fields))
            if fields not in self._client.composite_indexes.get(self._collection, set()):
                raise FailedPreconditionError(
                    f"FAILED_PRECONDITION: the query on {self._collection} over {list(fields)} "
                    "requires a composite index that infra/terraform/firestore.tf does not declare"
                )
        ranges = [f.field_path for f in self._filters if f.op_string in _RANGE_OPERATORS]
        sort_fields = list(self._orders) or ranges
        matched = []
        for doc_id, data in self._client._documents(self._collection).items():
            if any(name not in data for name in (*sort_fields, *fields)):
                continue  # Firestore omits a document that lacks an indexed field
            if all(
                _OPERATORS[f.op_string](data[f.field_path], _encode(f.value)) for f in self._filters
            ):
                matched.append((doc_id, data))
        matched.sort(key=lambda item: (*(item[1][name] for name in sort_fields), item[0]))
        for doc_id, data in matched:
            yield _Snapshot(doc_id, data)


class _CollectionReference(_Query):
    def document(self, doc_id: str) -> _DocumentReference:
        return _DocumentReference(self._client, self._collection, doc_id)


class FakeFirestoreClient:
    """One named database, holding documents per collection."""

    def __init__(
        self,
        *,
        database: str,
        composite_indexes: Mapping[str, Iterable[tuple[str, ...]]] | None = None,
    ) -> None:
        self.database = database
        indexes = declared_composite_indexes() if composite_indexes is None else composite_indexes
        self.composite_indexes = {name: set(fields) for name, fields in indexes.items()}
        self.composite_queries_run: set[tuple[str, tuple[str, ...]]] = set()
        self._store: dict[str, dict[str, dict[str, Any]]] = {}

    def collection(self, name: str) -> _CollectionReference:
        return _CollectionReference(self, name)

    def _documents(self, collection: str) -> dict[str, dict[str, Any]]:
        return self._store.setdefault(collection, {})


def bind(adapter: Any, client: FakeFirestoreClient) -> Any:
    """Point a Firestore adapter at ``client``, refusing one built for another database."""
    assert adapter._database == client.database, (  # noqa: SLF001 - test seam
        f"the adapter names database {adapter._database!r}, the fake holds {client.database!r}"
    )
    adapter._client = client  # noqa: SLF001 - test seam
    return adapter
