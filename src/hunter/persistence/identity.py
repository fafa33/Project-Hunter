from __future__ import annotations

from hunter.persistence.exceptions import PersistenceValidationError


def preserve_identity(stable_identity: str) -> str:
    """Return an existing analytical identity after validating it is explicit."""

    if not stable_identity.strip():
        raise PersistenceValidationError("Persistence records must preserve a non-empty analytical identity")
    return stable_identity


def require_same_identity(expected: str, actual: str) -> None:
    if preserve_identity(expected) != preserve_identity(actual):
        raise PersistenceValidationError("Persistence serialization must preserve analytical identity exactly")
