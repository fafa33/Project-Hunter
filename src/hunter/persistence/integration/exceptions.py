from __future__ import annotations


class PipelinePersistenceError(RuntimeError):
    """Base error for pipeline persistence integration failures."""


class LifecycleTransitionError(PipelinePersistenceError):
    """Raised when a pipeline run lifecycle transition is invalid."""


class StalePipelineRunIdentityError(PipelinePersistenceError):
    """Raised when context inputs no longer match the frozen run identity."""


class EngineManifestError(PipelinePersistenceError):
    """Raised when emitted intelligence does not match the declared engine manifest."""


class ArtifactPersistenceError(PipelinePersistenceError):
    """Raised when emitted analytical artifacts cannot be persisted."""


class PipelinePersistenceConfigurationError(PipelinePersistenceError):
    """Raised when the adapter is configured with incompatible dependencies."""
