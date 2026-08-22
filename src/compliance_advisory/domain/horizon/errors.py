"""Domain errors for horizon scanning.

Input-shaped failures the API turns into a 422 (never a 500) and an authorization failure
the API turns into a 403. Keeping them here means the domain never imports FastAPI.
"""

from __future__ import annotations


class HorizonError(Exception):
    """Base class for horizon-scanning domain errors."""


class CorpusLedgerEmptyError(HorizonError):
    """The freshness ledger holds no records, so there is nothing to diff.

    Operationally this means the corpus has never been ingested: run the refresh job
    (``compliance corpus refresh --full``) before scanning the horizon.
    """


class ImplementationItemNotFoundError(HorizonError):
    """No tracked implementation item exists for the requested change id."""


class TenantMismatchError(HorizonError):
    """The verified principal's tenant does not own the requested item (fail-closed 403)."""
