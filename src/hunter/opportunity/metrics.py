from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime

from hunter.intelligence.fusion.models import FrozenFloatMap, FrozenScalarMap


@dataclass(frozen=True)
class OpportunityMetricSnapshot:
    project_id: str
    effective_at: datetime
    values: FrozenFloatMap | Mapping[str, float]
    evidence_ids: tuple[str, ...] = ()
    missing_evidence: tuple[str, ...] = ()
    metadata: FrozenScalarMap | Mapping[str, str | int | float | bool | None] = field(default_factory=FrozenScalarMap)

    def __post_init__(self) -> None:
        if not self.project_id.strip():
            msg = "project_id is required"
            raise ValueError(msg)
        if self.effective_at.tzinfo is None:
            msg = "effective_at must be timezone-aware"
            raise ValueError(msg)
        object.__setattr__(self, "effective_at", self.effective_at.astimezone(UTC))
        object.__setattr__(self, "values", FrozenFloatMap(self.values))
        object.__setattr__(self, "evidence_ids", tuple(sorted(str(item) for item in self.evidence_ids)))
        object.__setattr__(self, "missing_evidence", tuple(sorted(str(item) for item in self.missing_evidence)))
        object.__setattr__(self, "metadata", FrozenScalarMap(self.metadata))


POSITIVE_FACTORS: tuple[str, ...] = (
    "valuation_discount",
    "relative_valuation",
    "historical_discount",
    "whale_accumulation",
    "smart_money_positioning",
    "developer_momentum",
    "macro_tailwinds",
    "future_demand",
    "sector_strength",
    "capital_formation",
    "evidence_freshness",
    "confidence",
    "backtesting_quality",
    "historical_opportunity_similarity",
)

NEGATIVE_FACTORS: tuple[str, ...] = (
    "risk",
    "missing_evidence",
)

GATING_FACTORS: tuple[str, ...] = ("validation_health",)
