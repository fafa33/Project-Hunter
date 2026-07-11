from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Literal

Scalar = str | int | float | bool | None
DashboardPanelKind = Literal["summary", "table", "timeline"]


@dataclass(frozen=True)
class DashboardMetric:
    key: str
    label: str
    value: Scalar
    metadata: Mapping[str, Scalar] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _text("key", self.key)
        _text("label", self.label)
        object.__setattr__(self, "metadata", _metadata(self.metadata))


@dataclass(frozen=True)
class DashboardRow:
    row_id: str
    values: Mapping[str, Scalar]

    def __post_init__(self) -> None:
        _text("row_id", self.row_id)
        object.__setattr__(self, "values", _metadata(self.values))


@dataclass(frozen=True)
class DashboardPanel:
    panel_id: str
    title: str
    kind: DashboardPanelKind
    metrics: tuple[DashboardMetric, ...] = ()
    rows: tuple[DashboardRow, ...] = ()
    metadata: Mapping[str, Scalar] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _text("panel_id", self.panel_id)
        _text("title", self.title)
        object.__setattr__(self, "metrics", tuple(self.metrics))
        object.__setattr__(self, "rows", tuple(self.rows))
        object.__setattr__(self, "metadata", _metadata(self.metadata))


@dataclass(frozen=True)
class DashboardView:
    view_id: str
    title: str
    generated_at: datetime
    panels: tuple[DashboardPanel, ...]
    metadata: Mapping[str, Scalar] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _text("view_id", self.view_id)
        _text("title", self.title)
        if self.generated_at.tzinfo is None:
            msg = "generated_at must be timezone-aware"
            raise ValueError(msg)
        object.__setattr__(self, "generated_at", self.generated_at.astimezone(UTC))
        object.__setattr__(self, "panels", tuple(self.panels))
        object.__setattr__(self, "metadata", _metadata(self.metadata))


def _metadata(values: Mapping[str, Scalar]) -> Mapping[str, Scalar]:
    return MappingProxyType({str(key): value for key, value in values.items()})


def _text(name: str, value: str) -> None:
    if not value.strip():
        msg = f"{name} is required"
        raise ValueError(msg)
