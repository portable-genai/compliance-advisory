"""Shared SQLite connection helper for the ``local`` profile stores.

The three local stores (retrieval index, freshness ledger, horizon tracker) all accept a
path from `LocalSettings`, and all three accept the same three shapes: a filesystem path,
``:memory:``, or a ``file:...`` URI. The URI form is what lets a test or a demo point two
adapters at ONE shared in-memory database (``file:name?mode=memory&cache=shared``), and it
only works when ``sqlite3.connect`` is told the string is a URI. Without that flag SQLite
treats the whole URI as a literal filename and silently creates a file called
``file:name?mode=memory&cache=shared`` in the working directory.

Centralising the rule here keeps the three adapters honest about the same contract.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path


def connect(path: str) -> sqlite3.Connection:
    """Open a local SQLite store, honouring ``file:`` URIs and creating parent dirs.

    ``check_same_thread=False`` is paired with a lock in every caller: under ``local
    serve`` the container is process-wide but sync endpoints run in Starlette's worker
    threadpool, so one connection is used from several threads.
    """
    is_uri = path.startswith("file:")
    if path not in (":memory:", "") and not is_uri:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False, uri=is_uri)
    conn.row_factory = sqlite3.Row
    return conn
