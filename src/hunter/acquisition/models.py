from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from typing import Any, Literal

from hunter.execution.canonicalization import normalize

EvidenceDomain = Literal[
    "market",
    "onchain",
    "github",
    "protocol",
    "developer",
    "whales",
    "macro",
    "news",
    "social",
    "future_demand",
    "technology_dependency",
    "capital_rotation",
    "pattern_matching",
]

ProviderAvailability = Literal["available", "degraded", "unavailable"]
ValidationStatus = Literal["valid", "invalid", "stale", "duplicate", "contradictory"]
SyncMode = Literal["first_sync", "incremental", "resume"]


@dataclass(frozen=True)
class RateLimit:
    requests: int
    window_seconds: int

    def __post_init__(self) -> None:
        if self.requests < 0 or self.window_seconds <= 0:
            msg = "rate limit values must be non-negative with a positive window"
            raise ValueError(msg)


@dataclass(frozen=True)
class ProviderHealth:
    provider_name: str
    availability: ProviderAvailability
    checked_at: datetime
    last_sync: datetime | None = None
    message: str = ""

    def __post_init__(self) -> None:
        _text("provider_name", self.provider_name)
        object.__setattr__(self, "checked_at", _aware(self.checked_at))
        if self.last_sync is not None:
            object.__setattr__(self, "last_sync", _aware(self.last_sync))


@dataclass(frozen=True)
class ProviderMetadata:
    name: str
    capabilities: tuple[str, ...]
    supported_metrics: tuple[str, ...]
    rate_limits: tuple[RateLimit, ...] = ()
    last_sync: datetime | None = None
    health: ProviderHealth | None = None
    availability: ProviderAvailability = "available"

    def __post_init__(self) -> None:
        _text("name", self.name)
        object.__setattr__(self, "capabilities", tuple(sorted(str(item) for item in self.capabilities)))
        object.__setattr__(self, "supported_metrics", tuple(sorted(str(item) for item in self.supported_metrics)))
        object.__setattr__(self, "rate_limits", tuple(self.rate_limits))
        if self.last_sync is not None:
            object.__setattr__(self, "last_sync", _aware(self.last_sync))


@dataclass(frozen=True)
class AcquisitionRequest:
    domain: str
    metric: str
    target_id: str
    requested_at: datetime
    mode: SyncMode = "incremental"
    checkpoint: str | None = None
    parameters: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("domain", "metric", "target_id"):
            _text(name, getattr(self, name))
        object.__setattr__(self, "requested_at", _aware(self.requested_at))
        normalize(self.parameters)
        object.__setattr__(self, "parameters", MappingProxyType({str(k): v for k, v in self.parameters.items()}))


@dataclass(frozen=True)
class RawEvidence:
    provider: str
    collector: str
    raw_source_id: str
    domain: str
    metric: str
    target_id: str
    retrieved_at: datetime
    payload: dict[str, Any]
    source_url: str = ""
    repository_id: str = ""

    def __post_init__(self) -> None:
        for name in ("provider", "collector", "raw_source_id", "domain", "metric", "target_id"):
            _text(name, getattr(self, name))
        object.__setattr__(self, "retrieved_at", _aware(self.retrieved_at))
        normalize(self.payload)
        object.__setattr__(self, "payload", MappingProxyType({str(k): v for k, v in self.payload.items()}))


@dataclass(frozen=True)
class NormalizedEvidence:
    evidence_id: str
    repository_id: str
    provider: str
    collector: str
    raw_source_id: str
    domain: str
    metric: str
    target_id: str
    value: int | float | str | bool
    raw_metrics: dict[str, Any]
    normalized_metrics: dict[str, float]
    source_url: str
    retrieved_at: datetime
    normalized_at: datetime
    confidence: float
    freshness: float
    raw_evidence_id: str = ""

    def __post_init__(self) -> None:
        for name in (
            "evidence_id",
            "repository_id",
            "provider",
            "collector",
            "raw_source_id",
            "domain",
            "metric",
            "target_id",
        ):
            _text(name, getattr(self, name))
        object.__setattr__(self, "retrieved_at", _aware(self.retrieved_at))
        object.__setattr__(self, "normalized_at", _aware(self.normalized_at))
        object.__setattr__(self, "confidence", _clamp(self.confidence))
        object.__setattr__(self, "freshness", _clamp(self.freshness))
        normalize(self.raw_metrics)
        normalize(self.normalized_metrics)
        object.__setattr__(self, "raw_metrics", MappingProxyType({str(k): v for k, v in self.raw_metrics.items()}))
        object.__setattr__(
            self,
            "normalized_metrics",
            MappingProxyType({str(k): _clamp(v) for k, v in self.normalized_metrics.items()}),
        )


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    field: str
    message: str

    def __post_init__(self) -> None:
        for name in ("code", "field", "message"):
            _text(name, getattr(self, name))


@dataclass(frozen=True)
class EvidenceValidation:
    evidence_id: str
    status: ValidationStatus
    validated_at: datetime
    confidence: float
    freshness: float
    issues: tuple[ValidationIssue, ...] = ()

    def __post_init__(self) -> None:
        _text("evidence_id", self.evidence_id)
        object.__setattr__(self, "validated_at", _aware(self.validated_at))
        object.__setattr__(self, "confidence", _clamp(self.confidence))
        object.__setattr__(self, "freshness", _clamp(self.freshness))
        object.__setattr__(self, "issues", tuple(self.issues))


@dataclass(frozen=True)
class AcquisitionCheckpoint:
    provider: str
    domain: str
    target_id: str
    cursor: str
    updated_at: datetime

    def __post_init__(self) -> None:
        for name in ("provider", "domain", "target_id", "cursor"):
            _text(name, getattr(self, name))
        object.__setattr__(self, "updated_at", _aware(self.updated_at))


@dataclass(frozen=True)
class AcquisitionRun:
    run_id: str
    request: AcquisitionRequest
    provider: str
    started_at: datetime
    finished_at: datetime
    raw_count: int
    normalized_count: int
    valid_count: int
    duplicate_count: int
    stale_count: int
    invalid_count: int
    checkpoint: AcquisitionCheckpoint | None = None
    errors: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _text("run_id", self.run_id)
        _text("provider", self.provider)
        object.__setattr__(self, "started_at", _aware(self.started_at))
        object.__setattr__(self, "finished_at", _aware(self.finished_at))
        for name in ("raw_count", "normalized_count", "valid_count", "duplicate_count", "stale_count", "invalid_count"):
            if getattr(self, name) < 0:
                msg = f"{name} must be non-negative"
                raise ValueError(msg)
        object.__setattr__(self, "errors", tuple(str(item) for item in self.errors))


@dataclass(frozen=True)
class CacheEntry:
    key: str
    raw: tuple[RawEvidence, ...]
    created_at: datetime
    ttl_seconds: int

    def __post_init__(self) -> None:
        _text("key", self.key)
        object.__setattr__(self, "raw", tuple(self.raw))
        object.__setattr__(self, "created_at", _aware(self.created_at))
        if self.ttl_seconds < 0:
            msg = "ttl_seconds must be non-negative"
            raise ValueError(msg)

    def fresh_at(self, timestamp: datetime) -> bool:
        return _aware(timestamp) <= self.created_at + timedelta(seconds=self.ttl_seconds)


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        msg = "datetime values must be timezone-aware"
        raise ValueError(msg)
    return value.astimezone(UTC)


def _text(name: str, value: str) -> None:
    if not str(value).strip():
        msg = f"{name} is required"
        raise ValueError(msg)


def _clamp(value: float) -> float:
    return round(max(0.0, min(1.0, float(value))), 4)
