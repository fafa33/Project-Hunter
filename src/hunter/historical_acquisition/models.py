from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

HistoricalValidationStatus = Literal["valid", "invalid", "duplicate", "future", "corrupted"]


@dataclass(frozen=True)
class HistoricalProviderMetadata:
    name: str
    collector: str
    supported_metrics: tuple[str, ...]
    authenticated: bool = False


@dataclass(frozen=True)
class RawHistoricalEvidence:
    provider: str
    collector: str
    raw_source_id: str
    case_id: str
    project_id: str
    metric: str
    event_timestamp: datetime
    publication_timestamp: datetime
    data_availability_timestamp: datetime
    retrieval_timestamp: datetime
    payload: dict[str, object]
    source_url: str
    repository_id: str

    def __post_init__(self) -> None:
        for field in ("event_timestamp", "publication_timestamp", "data_availability_timestamp", "retrieval_timestamp"):
            object.__setattr__(self, field, getattr(self, field).astimezone(UTC))


@dataclass(frozen=True)
class NormalizedHistoricalEvidence:
    evidence_id: str
    repository_id: str
    provider: str
    collector: str
    raw_source_id: str
    case_id: str
    project_id: str
    engine: str
    metric: str
    event_timestamp: datetime
    publication_timestamp: datetime
    data_availability_timestamp: datetime
    retrieval_timestamp: datetime
    raw_metrics: dict[str, object]
    normalized_metrics: dict[str, float]
    source_url: str
    confidence: float
    freshness: float

    def __post_init__(self) -> None:
        for field in ("event_timestamp", "publication_timestamp", "data_availability_timestamp", "retrieval_timestamp"):
            object.__setattr__(self, field, getattr(self, field).astimezone(UTC))


@dataclass(frozen=True)
class HistoricalEvidenceValidation:
    evidence_id: str
    status: HistoricalValidationStatus
    validated_at: datetime
    reason: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "validated_at", self.validated_at.astimezone(UTC))


@dataclass(frozen=True)
class HistoricalAcquisitionRun:
    run_id: str
    provider: str
    started_at: datetime
    finished_at: datetime
    raw_count: int
    normalized_count: int
    valid_count: int
    invalid_count: int
    duplicate_count: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "started_at", self.started_at.astimezone(UTC))
        object.__setattr__(self, "finished_at", self.finished_at.astimezone(UTC))
