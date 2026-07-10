class WhaleIntelligenceError(Exception):
    """Base exception for Whale Intelligence Engine failures."""


class WhaleConfigurationError(WhaleIntelligenceError):
    """Raised when whale engine configuration is invalid."""


class WhaleCollectionError(WhaleIntelligenceError):
    """Raised when whale data collection fails."""


class WhaleAnalysisError(WhaleIntelligenceError):
    """Raised when whale analysis fails."""

