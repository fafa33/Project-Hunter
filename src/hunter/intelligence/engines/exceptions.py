class IntelligenceEngineError(Exception):
    """Base exception for Intelligence Engine Framework failures."""


class IntelligenceEngineRegistrationError(IntelligenceEngineError):
    """Raised when an engine cannot be registered."""


class IntelligenceEngineFactoryError(IntelligenceEngineError):
    """Raised when an engine cannot be created by the factory."""


class IntelligenceEngineExecutionError(IntelligenceEngineError):
    """Raised when an engine lifecycle step fails."""


class IntelligenceEngineValidationError(IntelligenceEngineError):
    """Raised when engine metadata or outputs are invalid."""
