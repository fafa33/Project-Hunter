from __future__ import annotations


class ProtocolIntelligenceError(Exception):
    """Base error for the Protocol Intelligence Engine."""


class ProtocolCollectionError(ProtocolIntelligenceError):
    """Raised when protocol data cannot be collected or interpreted."""


class ProtocolValidationError(ProtocolIntelligenceError, ValueError):
    """Raised when canonical protocol data violates required contracts."""
