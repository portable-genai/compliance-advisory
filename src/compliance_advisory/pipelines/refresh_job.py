"""Entrypoint for the scheduled corpus freshness refresh job.

This is the out-of-band half of the 7-day fetch-at-runtime model (SPEC §2). It is
deployed as a **Cloud Run job** (see ``pipelines/Dockerfile``) and triggered on a
schedule (Cloud Scheduler) more often than the TTL — e.g. daily for a 7-day TTL — so
that by the time a read references a source it is almost always already fresh in
**Agent Search**, with its freshness recorded in the **AlloyDB** ledger.

Default behaviour refreshes only sources that are expired in the ledger or not yet
ingested (:func:`ingest.refresh_expired`). ``--full`` forces a re-ingest of the entire
registry (:func:`ingest.refresh_all`) — useful for the first deploy or after changing the
ingestion adapter. The job is idempotent: re-running it on a fresh corpus is a cheap
series of ledger reads.
"""

from __future__ import annotations

import argparse
import logging
import sys

from ..config import Container, Settings
from . import ingest

logger = logging.getLogger("compliance_advisory.pipelines.refresh_job")


def run(*, full: bool = False, settings: Settings | None = None) -> ingest.RefreshSummary:
    """Build the Container from settings and run one refresh pass.

    Returns the :class:`~compliance_advisory.pipelines.ingest.RefreshSummary` so callers
    (tests, the CLI below) can assert on counts without parsing logs.
    """
    container = Container(settings or Settings.load())
    region = container.settings.region
    mode = "full" if full else "expired-only"
    logger.info(
        "corpus refresh starting: mode=%s profile=%s region=%s ttl_days=%d",
        mode,
        container.settings.profile,
        region,
        container.settings.corpus.ttl_days,
    )

    summary = ingest.refresh_all(container) if full else ingest.refresh_expired(container)

    logger.info(
        "corpus refresh complete: total=%d ingested=%d skipped=%d failed=%d",
        summary.total,
        summary.ingested,
        summary.skipped,
        summary.failed,
    )
    for outcome in summary.outcomes:
        if outcome.action == "failed":
            logger.error("  FAILED %s: %s", outcome.source_id, outcome.detail)
        else:
            logger.info(
                "  %-14s %s (chunks=%d)",
                outcome.action,
                outcome.source_id,
                outcome.chunks,
            )
    return summary


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="compliance-refresh-job",
        description=(
            "Refresh the C1 regulatory corpus: re-fetch expired sources into Agent "
            "Search and update the AlloyDB freshness ledger."
        ),
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="re-ingest every source in the registry, not just expired/missing ones.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="logging level (DEBUG, INFO, WARNING, ERROR). Defaults to INFO.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI / container entrypoint. Returns a non-zero exit code if any source failed."""
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    summary = run(full=bool(args.full))
    # Surface partial failures to the scheduler/operator via the process exit code while
    # still completing the rest of the batch (each source is isolated in ingest_source).
    return 1 if summary.failed else 0


if __name__ == "__main__":
    sys.exit(main())
