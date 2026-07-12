from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(frozen=True)
class EngineBacktestMetric:
    engine: str
    historical_coverage: float
    hit_rate: float
    precision: float
    recall: float
    false_positives: int
    false_negatives: int
    top_n_accuracy: float
    ranking_correlation: float
    decision_stability: float
    confidence_calibration: float
    evidence_completeness: float
    historical_consistency: float
    prediction_reliability: float
    scenario_reliability: float
    evidence_ids: tuple[str, ...]
    repository_ids: tuple[str, ...]


@dataclass(frozen=True)
class ProjectBacktestMetric:
    project_id: str
    historical_coverage: float
    confidence: float
    evidence_completeness: float
    historical_consistency: float
    engines_available: int
    engines_missing: int
    evidence_ids: tuple[str, ...]
    repository_ids: tuple[str, ...]


@dataclass(frozen=True)
class CalibrationReport:
    calibration_id: str
    generated_at: datetime
    confidence_calibration: float
    evidence_quality: float
    coverage_gaps: tuple[str, ...]
    weak_engines: tuple[str, ...]
    strong_engines: tuple[str, ...]
    historical_drift: float
    recommended_weight_adjustments: dict[str, float]

    def __post_init__(self) -> None:
        object.__setattr__(self, "generated_at", self.generated_at.astimezone(UTC))


@dataclass(frozen=True)
class BacktestRun:
    run_id: str
    generated_at: datetime
    historical_runs: int
    projects_evaluated: int
    engines_evaluated: int
    coverage: float
    historical_consistency: float
    calibration_completeness: float
    engine_metrics: tuple[EngineBacktestMetric, ...]
    project_metrics: tuple[ProjectBacktestMetric, ...]
    calibration: CalibrationReport

    def __post_init__(self) -> None:
        object.__setattr__(self, "generated_at", self.generated_at.astimezone(UTC))
