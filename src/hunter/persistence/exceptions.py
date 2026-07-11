from __future__ import annotations


class PersistenceError(Exception):
    """Base error for persistence contract failures."""


class PersistenceValidationError(PersistenceError):
    """Raised when a persistence record violates the canonical contract."""


class PersistenceSerializationError(PersistenceError):
    """Raised when a persistence record cannot be serialized or restored."""
