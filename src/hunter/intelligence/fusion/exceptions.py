from __future__ import annotations


class FusionError(Exception):
    """Base error for deterministic intelligence fusion."""


class FusionInputError(FusionError):
    """Raised when fusion inputs cannot be normalized."""
