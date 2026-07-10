from __future__ import annotations


class ExecutionIdentityError(Exception):
    """Base error for deterministic execution identity failures."""


class CanonicalizationError(ExecutionIdentityError):
    """Raised when a value cannot be safely canonicalized."""

