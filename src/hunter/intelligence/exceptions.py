class IntelligenceError(Exception):
    """Base exception for Intelligence Layer failures."""


class IntelligenceValidationError(IntelligenceError):
    """Raised when intelligence objects fail validation."""


class IntelligenceRegistryError(IntelligenceError):
    """Raised when intelligence registry operations fail."""


class IntelligenceAggregationError(IntelligenceError):
    """Raised when intelligence aggregation fails."""
