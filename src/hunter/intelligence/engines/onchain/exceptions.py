from __future__ import annotations


class OnchainIntelligenceError(Exception):
    """Base exception for On-chain Intelligence Engine failures."""


class OnchainValidationError(OnchainIntelligenceError):
    """Raised when canonical on-chain records are invalid."""


class OnchainCollectionError(OnchainIntelligenceError):
    """Raised when on-chain collection fails."""
