from __future__ import annotations

from hunter.persistence.exceptions import PersistenceError


class SQLPersistenceError(PersistenceError):
    """Base error for SQL persistence implementation failures."""


class PersistenceIdentityConflictError(SQLPersistenceError):
    """Raised when an immutable record id is reused for different content."""


class PersistenceRecordDeletedError(SQLPersistenceError):
    """Raised when a deleted record is written again."""
