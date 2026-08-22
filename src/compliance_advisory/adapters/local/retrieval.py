"""Local retrieval adapter (RetrievalPort) — SQLite FTS5 over the regulatory corpus.

The ``local`` profile's stand-in for **Agent Search**: a ``sqlite3`` database with an
**FTS5** virtual table over the passages, queried with BM25 (``ORDER BY rank``). It is
SDK-free, deterministic and **seedable**, so the same code grounds the offline CLI run
and the unit tests. There is no Google emulator for Agent Search, so this path is
unconditional (no emulator branch).

The adapter returns the same :class:`RetrievedPassage` objects with page-level
:class:`Citation` provenance as the managed adapter, preserving interface parity. It
self-seeds from the built-in synthetic corpus on first use so an out-of-the-box local
run grounds answers without any ingestion step; callers may also ``seed(passages)`` or
``ingest(...)`` a corpus of their own.

Default DB path is under a per-package local dir (``~/.compliance_advisory/local.db``);
tests pass ``:memory:`` for an ephemeral, deterministic index.
"""

from __future__ import annotations

import re
import sqlite3
import threading
from pathlib import Path

from ...config import Settings
from ...domain.models import (
    Citation,
    FetchedDocument,
    IngestResult,
    Jurisdiction,
    Regulator,
    RetrievalQuery,
    RetrievedPassage,
)
from . import _sqlite
from ._seed import SEED_PASSAGES

# Default on-disk location for the local index (overridable via settings.local.db_path).
_DEFAULT_DB_DIR = Path.home() / ".compliance_advisory"
_DEFAULT_DB_PATH = _DEFAULT_DB_DIR / "local.db"

# FTS5 query syntax is strict; keep only word characters so a free-text question never
# trips an "fts5: syntax error" (e.g. on punctuation), and OR the terms for recall.
_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")


class LocalFtsRetrievalAdapter:
    """Retrieve grounded passages from a local SQLite FTS5 index (BM25 ranked)."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        db_path = getattr(getattr(settings, "local", None), "db_path", "") or str(_DEFAULT_DB_PATH)
        self._db_path = db_path
        # ``check_same_thread=False`` + a re-entrant lock: under ``local serve`` the
        # container is process-wide (deps.get_container is lru_cached) but the sync /ask
        # endpoint runs in Starlette's anyio worker threadpool, so retrieve()/ingest() are
        # called from worker threads other than the one that opened the connection. The lock
        # serialises access (single-writer); an RLock lets seed() call _insert() re-entrantly.
        self._lock = threading.RLock()
        self._conn = self._connect(db_path)
        self._init_schema()
        self._maybe_seed()

    def _maybe_seed(self) -> None:
        """Self-seed the built-in fictional corpus so a local run grounds out of the box.

        Never under the ``live`` profile: live serves ONLY real ingested instruments
        (``pipelines.refresh_job``), and this guard covers every construction path,
        including the ingestion adapter's internal retrieval instance.
        """
        if self._settings.profile != "live" and self._is_empty():
            self.seed(SEED_PASSAGES)

    # ------------------------------------------------------------------ #
    # Connection / schema
    # ------------------------------------------------------------------ #
    @staticmethod
    def _connect(db_path: str) -> sqlite3.Connection:
        # check_same_thread=False (paired with the adapter's RLock) keeps the single
        # process-wide connection usable from FastAPI's worker threadpool under serve;
        # ``file:`` URIs are honoured so two adapters can share one in-memory database.
        return _sqlite.connect(db_path)

    def _init_schema(self) -> None:
        # One FTS5 table holds the searchable text; citation metadata rides alongside as
        # UNINDEXED columns so a single query returns everything needed to cite a hit.
        with self._lock:
            self._conn.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS passages USING fts5(
                    text,
                    source_id UNINDEXED,
                    regulator UNINDEXED,
                    jurisdiction UNINDEXED,
                    title UNINDEXED,
                    url UNINDEXED,
                    version UNINDEXED,
                    page UNINDEXED,
                    score UNINDEXED
                )
                """
            )
            self._conn.commit()

    def _is_empty(self) -> bool:
        with self._lock:
            row = self._conn.execute("SELECT count(*) AS n FROM passages").fetchone()
        return int(row["n"]) == 0

    # ------------------------------------------------------------------ #
    # Seeding / ingestion
    # ------------------------------------------------------------------ #
    def seed(self, passages: tuple[RetrievedPassage, ...] | list[RetrievedPassage]) -> int:
        """Replace the index contents with ``passages`` (deterministic test/CLI seed)."""
        with self._lock:
            self._conn.execute("DELETE FROM passages")
            return self._insert(list(passages))

    def add(self, passages: list[RetrievedPassage]) -> int:
        """Append ``passages`` to the index without clearing existing rows."""
        return self._insert(passages)

    def _insert(self, passages: list[RetrievedPassage]) -> int:
        rows = []
        for p in passages:
            c = p.citation
            rows.append(
                (
                    p.text,
                    c.source_id,
                    c.regulator.value,
                    c.jurisdiction.value,
                    c.title,
                    c.url,
                    c.version,
                    "" if c.page is None else str(c.page),
                    f"{p.score:.6f}",
                )
            )
        with self._lock:
            self._conn.executemany(
                "INSERT INTO passages "
                "(text, source_id, regulator, jurisdiction, title, url, version, page, score) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
            self._conn.commit()
        return len(rows)

    # ------------------------------------------------------------------ #
    # RetrievalPort
    # ------------------------------------------------------------------ #
    def retrieve(self, query: RetrievalQuery) -> list[RetrievedPassage]:
        """Return ranked passages with regulator-grade citations for ``query``."""
        match = self._build_match(query.text)
        regulator = (query.filters or {}).get("regulator")

        if not match:
            # No usable query terms: fall back to a score-ordered scan so the pipeline
            # still gets something deterministic rather than an FTS5 syntax error.
            sql = (
                "SELECT * FROM passages "
                + ("WHERE regulator = ? " if regulator else "")
                + "ORDER BY score DESC LIMIT ?"
            )
            params: list[object] = ([regulator] if regulator else []) + [max(query.top_k, 1)]
        else:
            sql = (
                "SELECT * FROM passages WHERE passages MATCH ? "
                + ("AND regulator = ? " if regulator else "")
                + "ORDER BY rank LIMIT ?"
            )
            params = [match] + ([regulator] if regulator else []) + [max(query.top_k, 1)]

        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [self._row_to_passage(row) for row in rows]

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _build_match(text: str) -> str:
        """Build a safe FTS5 MATCH expression: OR of the alphanumeric query tokens."""
        tokens = _TOKEN_RE.findall(text or "")
        if not tokens:
            return ""
        # Quote each token so reserved words (AND/OR/NOT/NEAR) are treated as literals.
        return " OR ".join(f'"{t}"' for t in tokens)

    @staticmethod
    def _row_to_passage(row: sqlite3.Row) -> RetrievedPassage:
        page_raw = row["page"]
        page = int(page_raw) if page_raw not in (None, "") else None
        try:
            score = float(row["score"])
        except (TypeError, ValueError):
            score = 0.0
        citation = Citation(
            source_id=row["source_id"],
            regulator=LocalFtsRetrievalAdapter._parse_regulator(row["regulator"]),
            jurisdiction=LocalFtsRetrievalAdapter._parse_jurisdiction(row["jurisdiction"]),
            title=row["title"],
            url=row["url"],
            version=row["version"] or "unknown",
            page=page,
            snippet=(row["text"] or "")[:280],
            score=score,
        )
        return RetrievedPassage(text=row["text"], citation=citation, score=score)

    @staticmethod
    def _parse_regulator(value: str | None) -> Regulator:
        try:
            return Regulator(str(value).upper())
        except (ValueError, AttributeError):
            return Regulator.CROSS

    @staticmethod
    def _parse_jurisdiction(value: str | None) -> Jurisdiction:
        try:
            return Jurisdiction(str(value).upper())
        except (ValueError, AttributeError):
            return Jurisdiction.GLOBAL


class LocalIngestionAdapter:
    """Local CorpusIngestionPort: index a fetched document into the SQLite FTS5 store.

    Shares the same on-disk DB as :class:`LocalFtsRetrievalAdapter` so a ``corpus
    refresh`` makes documents searchable in the same local run. The document body is
    parsed to plain text by the local document parser, split into page-sized passages,
    and inserted with page-level citations.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._retrieval = LocalFtsRetrievalAdapter(settings)

    def ingest(self, document: FetchedDocument) -> IngestResult:
        from .document import LocalDocumentParser

        parser = LocalDocumentParser(self._settings)
        extract = parser.parse(document)
        src = document.source
        passages: list[RetrievedPassage] = []
        for page_no, page_text in enumerate(extract.pages, start=1):
            text = page_text.strip()
            if not text:
                continue
            passages.append(
                RetrievedPassage(
                    text=text,
                    citation=Citation(
                        source_id=src.id,
                        regulator=src.regulator,
                        jurisdiction=src.jurisdiction,
                        title=src.title,
                        url=src.url,
                        version=src.version,
                        page=page_no,
                        snippet=text[:280],
                        score=0.5,
                    ),
                    score=0.5,
                )
            )
        # Re-index this source: drop any prior rows for it, then add the new passages.
        # Hold the retrieval adapter's lock around the direct store access so the delete +
        # add stays serialised with concurrent retrieve()/seed() (RLock allows add() to
        # re-acquire). Mirrors the cross-thread safety of the retrieval adapter itself.
        with self._retrieval._lock:  # noqa: SLF001 - same-package store access
            self._retrieval._conn.execute(  # noqa: SLF001
                "DELETE FROM passages WHERE source_id = ?", (src.id,)
            )
            self._retrieval._conn.commit()  # noqa: SLF001
            n = self._retrieval.add(passages)
        return IngestResult(
            source_id=src.id,
            document_id=f"local-{src.id}",
            chunks=n,
            ok=True,
            detail=f"indexed {n} passages into local FTS5",
        )

    def delete(self, source_id: str) -> None:
        with self._retrieval._lock:  # noqa: SLF001 - same-package store access
            self._retrieval._conn.execute(  # noqa: SLF001
                "DELETE FROM passages WHERE source_id = ?", (source_id,)
            )
            self._retrieval._conn.commit()  # noqa: SLF001
