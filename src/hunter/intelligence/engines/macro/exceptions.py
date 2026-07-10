class MacroIntelligenceError(Exception):
    """Base exception for Macro Intelligence Engine failures."""


class MacroConfigurationError(MacroIntelligenceError):
    """Raised when macro engine configuration is invalid."""


class MacroCollectionError(MacroIntelligenceError):
    """Raised when macro data collection fails."""


class MacroAnalysisError(MacroIntelligenceError):
    """Raised when macro analysis fails."""

