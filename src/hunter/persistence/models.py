from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

RecordKind = Literal[
    "pipeline-run",
    "operational-attempt",
    "evidence",
    "signal",
    "observation",
    "insight",
    "intelligence",
    "fused-intelligence",
    "snapshot",
    "configuration",
    "engine-manifest",
]

SortDirection = Literal["asc", "desc"]


@dataclass(frozen=True)
class QueryFilter:
    field: str
    value: Any


@dataclass(frozen=True)
class QuerySpec:
    record_kind: RecordKind | None = None
    filters: tuple[QueryFilter, ...] = ()
    limit: int | None = None
    sort_by: str = "effective_at"
    direction: SortDirection = "desc"

    def __post_init__(self) -> None:
        object.__setattr__(self, "filters", tuple(self.filters))
        if self.limit is not None and self.limit < 1:
            raise ValueError("Query limit must be positive")


@dataclass(frozen=True)
class HistorySpec:
    identity: str
    as_of: datetime | None = None
    limit: int | None = None

    def __post_init__(self) -> None:
        if not self.identity.strip():
            raise ValueError("History identity is required")
        if self.limit is not None and self.limit < 1:
            raise ValueError("History limit must be positive")


@dataclass(frozen=True)
class SnapshotSpec:
    target_id: str
    snapshot_type: str
    effective_at: datetime
    metadata: dict[str, str | int | float | bool | None] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.target_id.strip():
            raise ValueError("Snapshot target id is required")
        if not self.snapshot_type.strip():
            raise ValueError("Snapshot type is required")
        object.__setattr__(self, "metadata", dict(self.metadata))


@dataclass(frozen=True)
class RecordBatch:
    records: tuple[Any, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "records", tuple(self.records))
