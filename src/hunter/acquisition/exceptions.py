from __future__ import annotations


class AcquisitionError(Exception):
    """Base acquisition framework error."""


class ProviderUnavailableError(AcquisitionError):
    """Raised when a provider cannot supply requested evidence."""


class AcquisitionValidationError(AcquisitionError):
    """Raised when acquired evidence fails validation."""


class AcquisitionConfigurationError(AcquisitionError):
    """Raised when acquisition configuration is invalid."""


class AcquisitionRegistryError(AcquisitionError):
    """Raised when provider registration is invalid."""
