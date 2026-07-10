from __future__ import annotations


class NarrativeIntelligenceError(Exception):
    """Base error for the Narrative Intelligence Engine."""


class NarrativeCollectionError(NarrativeIntelligenceError):
    """Raised when narrative records cannot be collected or interpreted."""


class NarrativeValidationError(NarrativeIntelligenceError, ValueError):
    """Raised when canonical narrative data violates required contracts."""
