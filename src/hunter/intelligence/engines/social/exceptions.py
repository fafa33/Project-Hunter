from __future__ import annotations


class SocialIntelligenceError(Exception):
    """Base error for the Social Intelligence Engine."""


class SocialCollectionError(SocialIntelligenceError):
    """Raised when social data cannot be collected or interpreted."""


class SocialValidationError(SocialIntelligenceError, ValueError):
    """Raised when canonical social data violates required contracts."""
