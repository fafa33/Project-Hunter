from __future__ import annotations


class DeveloperIntelligenceError(Exception):
    """Base error for the Developer Intelligence Engine."""


class DeveloperCollectionError(DeveloperIntelligenceError):
    """Raised when developer data cannot be collected or interpreted."""


class DeveloperValidationError(DeveloperIntelligenceError, ValueError):
    """Raised when canonical developer data violates required contracts."""
