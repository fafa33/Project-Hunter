class PluginError(Exception):
    """Base exception for plugin infrastructure failures."""


class PluginDiscoveryError(PluginError):
    """Raised when plugin discovery fails."""


class PluginLoadError(PluginError):
    """Raised when a plugin cannot be loaded."""


class PluginValidationError(PluginError):
    """Raised when plugin validation fails."""


class PluginDependencyError(PluginValidationError):
    """Raised when plugin dependency validation fails."""


class PluginLifecycleError(PluginError):
    """Raised when plugin lifecycle execution fails."""

