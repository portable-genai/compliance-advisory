"""Corpus freshness policy — the 7-day fetch-at-runtime model (SPEC §2).

Pure decision logic over the AlloyDB-backed freshness ledger. Documents live in
Agent Search; this policy decides, from a ``FreshnessRecord``, whether a source is
stale and must be re-fetched and re-ingested before it is used to answer. The TTL is
configurable (``CorpusSettings.ttl_days``, default 7) but defaults are baked in so the
domain can compute expiry without importing config.

No Google Cloud / framework imports — only the domain models and the standard library.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from .models import FreshnessRecord, FreshnessStatus, utcnow


@dataclass(frozen=True, slots=True)
class FreshnessPolicy:
    """Compute expiry and staleness for regulatory sources.

    Args:
        ttl_days: freshness window in days. A source fetched more than ``ttl_days``
            ago (or not in FRESH status) is stale and must be re-fetched. Default 7.
    """

    ttl_days: int = 7

    def expires_at(self, fetched_at: datetime) -> datetime:
        """Return the instant at which a source fetched at ``fetched_at`` expires."""
        return fetched_at + timedelta(days=self.ttl_days)

    def is_stale(self, record: FreshnessRecord, now: datetime | None = None) -> bool:
        """Whether ``record`` must be re-fetched before use.

        A record is stale if its status is not FRESH (EXPIRED / MISSING / FAILED) or
        if the current time is at/after the TTL-derived expiry. The TTL is recomputed
        from ``fetched_at`` so the policy's own ``ttl_days`` is authoritative even if
        a stored ``expires_at`` was written under a different window.
        """
        now = now or utcnow()
        if record.status is not FreshnessStatus.FRESH:
            return True
        return now >= self.expires_at(record.fetched_at)

    def stale_source_ids(
        self,
        records: list[FreshnessRecord],
        now: datetime | None = None,
    ) -> list[str]:
        """Return the source_ids of every stale record, in input order."""
        now = now or utcnow()
        return [r.source_id for r in records if self.is_stale(r, now)]
