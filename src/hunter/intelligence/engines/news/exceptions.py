from __future__ import annotations


class NewsIntelligenceError(Exception):
    """Base error for the News Intelligence Engine."""


class NewsCollectionError(NewsIntelligenceError):
    """Raised when news data cannot be collected or interpreted."""


class NewsValidationError(NewsIntelligenceError, ValueError):
    """Raised when canonical news data violates required contracts."""
