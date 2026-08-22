"""Change detection over the EXISTING corpus freshness ledger (SPEC §7.1).

Horizon scanning does not keep a second copy of the corpus. It diffs the ledger this repo
already maintains for the 7-day fetch-at-runtime model
(:class:`~compliance_advisory.ports.corpus.CorpusLedgerPort`), which was extended with the
superseded generation (``previous_version`` / ``previous_checksum`` / ``previous_fetched_at``
/ ``previous_status``) rather than shadowed by a parallel store.

Two pure functions carry the whole mechanism:

* :func:`carry_forward` — called by the ingest pipeline on every upsert, it rolls the
  current generation into the ``previous_*`` fields of the record about to be written. It
  only advances the diff base when the content actually moved, so repeated no-op re-fetches
  inside the TTL cycle never erase the last real change.
* :func:`detect_change` — reads one ledger record and classifies it into a
  :class:`CorpusChange`, or ``None`` when nothing moved.

No ports, no I/O, no framework imports: the classification is a total function of the record
and the registry entry, which is what makes it replayable and unit-testable.
"""

from __future__ import annotations

from dataclasses import replace

from ..models import FreshnessRecord, FreshnessStatus, RegSource
from .models import ChangeKind, Citation, CorpusChange

#: How many checksum characters go into a change id. Long enough to be unique per
#: republication, short enough to stay readable in an audit line.
_CHECKSUM_KEY_LEN = 12


def carry_forward(previous: FreshnessRecord | None, new: FreshnessRecord) -> FreshnessRecord:
    """Return ``new`` with the superseded generation attached, ready to upsert.

    The diff base advances only when the ingest actually observed a different document
    (checksum or publisher version moved) or a different status. A byte-identical re-fetch
    keeps the existing ``previous_*`` values, so a change detected on Monday is still
    detectable on Friday if nobody has scanned in between: the ledger records the last
    *movement*, not the last *fetch*.
    """
    if previous is None:
        return new
    moved = (
        previous.checksum != new.checksum
        or previous.version != new.version
        or previous.status is not new.status
    )
    if not moved:
        return replace(
            new,
            previous_version=previous.previous_version,
            previous_checksum=previous.previous_checksum,
            previous_fetched_at=previous.previous_fetched_at,
            previous_status=previous.previous_status,
        )
    return replace(
        new,
        previous_version=previous.version,
        previous_checksum=previous.checksum,
        previous_fetched_at=previous.fetched_at,
        previous_status=previous.status,
    )


def change_id(source_id: str, kind: ChangeKind, checksum: str) -> str:
    """Stable, content-derived key for a detected change (idempotent across re-scans)."""
    marker = (checksum or "none")[:_CHECKSUM_KEY_LEN]
    return f"{source_id}:{kind.value}:{marker}"


def classify(record: FreshnessRecord) -> ChangeKind:
    """Classify one ledger record into a :class:`ChangeKind` (pure, total).

    Precedence is deliberate: a source that stopped resolving is a WITHDRAWN event even if
    its checksum also moved, because the compliance action (chase the publisher) differs
    from a republication. A record with no history is a NEW_SOURCE the first time it lands.
    """
    if record.status in (FreshnessStatus.FAILED, FreshnessStatus.MISSING):
        return ChangeKind.WITHDRAWN
    if not record.has_history:
        return ChangeKind.NEW_SOURCE
    if record.checksum != record.previous_checksum:
        return ChangeKind.CONTENT_REVISED
    if record.version != record.previous_version:
        return ChangeKind.VERSION_BUMP
    return ChangeKind.UNCHANGED


def detect_change(record: FreshnessRecord, source: RegSource | None) -> CorpusChange | None:
    """Turn one ledger record into a :class:`CorpusChange`, or ``None`` if nothing moved.

    ``source`` is the registry entry for the record (regulator, jurisdiction, doc type,
    topics). It is required: without it the change carries no regulator-grade provenance,
    so an unregistered ledger row is skipped rather than assessed on guessed metadata.
    """
    if source is None:
        return None
    kind = classify(record)
    if kind is ChangeKind.UNCHANGED:
        return None

    citation = Citation(
        source_id=source.id,
        regulator=source.regulator,
        jurisdiction=source.jurisdiction,
        title=source.title,
        url=source.url,
        version=record.version or source.version,
        snippet=_snippet(source, kind),
    )
    return CorpusChange(
        id=change_id(record.source_id, kind, record.checksum),
        source_id=record.source_id,
        kind=kind,
        regulator=source.regulator,
        jurisdiction=source.jurisdiction,
        title=source.title,
        url=source.url,
        doc_type=source.doc_type,
        topics=tuple(source.topics),
        previous_version=record.previous_version,
        current_version=record.version,
        previous_checksum=record.previous_checksum,
        current_checksum=record.checksum,
        detected_at=record.fetched_at,
        citation=citation,
        detail=_detail(record, kind),
    )


def detect_changes(
    records: list[FreshnessRecord],
    sources: dict[str, RegSource],
) -> list[CorpusChange]:
    """Diff a whole ledger snapshot, in stable source-id order."""
    changes: list[CorpusChange] = []
    for record in sorted(records, key=lambda r: r.source_id):
        change = detect_change(record, sources.get(record.source_id))
        if change is not None:
            changes.append(change)
    return changes


def _snippet(source: RegSource, kind: ChangeKind) -> str:
    return f"{source.title} ({source.regulator.value} {source.jurisdiction.value}): {kind.value}"


def _detail(record: FreshnessRecord, kind: ChangeKind) -> str:
    if kind is ChangeKind.WITHDRAWN:
        return f"source no longer resolves (ledger status {record.status.value})"
    if kind is ChangeKind.NEW_SOURCE:
        return f"first ingest at version {record.version or 'unknown'}"
    if kind is ChangeKind.VERSION_BUMP:
        return f"version {record.previous_version or 'unknown'} -> {record.version or 'unknown'}"
    return (
        f"content republished: checksum {record.previous_checksum[:12] or 'none'} -> "
        f"{record.checksum[:12] or 'none'}"
    )
