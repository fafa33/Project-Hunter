from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import MappingProxyType

from hunter.execution.canonicalization import normalize

Scalar = str | int | float | bool | None


@dataclass(frozen=True)
class ProjectValidationTarget:
    project_id: str
    name: str
    sector: str
    metadata: dict[str, Scalar] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _text("project_id", self.project_id)
        _text("name", self.name)
        _text("sector", self.sector)
        normalize(self.metadata)
        object.__setattr__(self, "metadata", MappingProxyType({str(k): v for k, v in self.metadata.items()}))


@dataclass(frozen=True)
class ProjectValidationResult:
    result_id: str
    run_id: str
    project_id: str
    project_name: str
    sector: str
    rank: int
    sector_rank: int
    hunter_score: float
    risk: float
    confidence: float
    valuation: float
    comparative_valuation: float
    mispricing: float
    asymmetry: float
    whale_intelligence: float
    macro_intelligence: float
    future_demand: float
    opportunity_timing: float
    probability: float
    pattern_matching: float
    technology_necessity: float
    capital_rotation: float
    necessity_gap: float
    committee_decision: str
    committee_confidence: float
    missing_evidence: tuple[str, ...] = ()
    stale_evidence: tuple[str, ...] = ()
    data_freshness: float = 1.0
    validation_health: float = 1.0
    strongest_positive_drivers: tuple[str, ...] = ()
    strongest_negative_drivers: tuple[str, ...] = ()
    reasons_for_ranking: tuple[str, ...] = ()
    validation_warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("result_id", "run_id", "project_id", "project_name", "sector", "committee_decision"):
            _text(name, getattr(self, name))
        for name in (
            "hunter_score",
            "risk",
            "confidence",
            "valuation",
            "comparative_valuation",
            "mispricing",
            "asymmetry",
            "whale_intelligence",
            "macro_intelligence",
            "future_demand",
            "opportunity_timing",
            "probability",
            "pattern_matching",
            "technology_necessity",
            "capital_rotation",
            "necessity_gap",
            "committee_confidence",
            "data_freshness",
            "validation_health",
        ):
            object.__setattr__(self, name, _clamp(getattr(self, name)))
        for name in (
            "missing_evidence",
            "stale_evidence",
            "strongest_positive_drivers",
            "strongest_negative_drivers",
            "reasons_for_ranking",
            "validation_warnings",
        ):
            object.__setattr__(self, name, tuple(sorted(str(item) for item in getattr(self, name))))

    @property
    def score_breakdown(self) -> dict[str, float]:
        return {
            "valuation": self.valuation,
            "comparative_valuation": self.comparative_valuation,
            "mispricing": self.mispricing,
            "asymmetry": self.asymmetry,
            "whale_intelligence": self.whale_intelligence,
            "macro_intelligence": self.macro_intelligence,
            "future_demand": self.future_demand,
            "opportunity_timing": self.opportunity_timing,
            "probability": self.probability,
            "pattern_matching": self.pattern_matching,
            "technology_necessity": self.technology_necessity,
            "capital_rotation": self.capital_rotation,
            "necessity_gap": self.necessity_gap,
            "validation_health": self.validation_health,
        }


@dataclass(frozen=True)
class MarketValidationRun:
    run_id: str
    effective_at: datetime
    project_results: tuple[ProjectValidationResult, ...]
    champion_project_id: str | None
    runner_up_project_id: str | None
    no_qualified_candidate: bool
    created_at: datetime
    metadata: dict[str, Scalar] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _text("run_id", self.run_id)
        object.__setattr__(self, "created_at", _aware(self.created_at))
        object.__setattr__(self, "effective_at", _aware(self.effective_at))
        object.__setattr__(self, "project_results", tuple(self.project_results))
        normalize(self.metadata)
        object.__setattr__(self, "metadata", MappingProxyType({str(k): v for k, v in self.metadata.items()}))


@dataclass(frozen=True)
class ProjectValidationDelta:
    project_id: str
    rank_change: int
    score_change: float
    confidence_change: float
    committee_change: str
    evidence_change: int


@dataclass(frozen=True)
class MarketValidationComparison:
    left_run_id: str
    right_run_id: str
    champion_change: str
    project_deltas: tuple[ProjectValidationDelta, ...]


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
