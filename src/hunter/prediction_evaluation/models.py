from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

ComparisonMode = Literal["absolute-threshold", "change-from-baseline", "benchmark-relative"]
ComparisonOperator = Literal["gt", "gte", "lt", "lte", "eq"]
LifecycleState = Literal[
    "pending",
    "due",
    "awaiting-data",
    "evaluable",
    "evaluated-correct",
    "evaluated-incorrect",
    "unevaluable",
    "invalidated",
    "superseded",
]


@dataclass(frozen=True, slots=True)
class EvaluationPolicy:
    policy_id: str
    policy_version: str
    claim_type: str
    entity_type: str
    comparison_mode: ComparisonMode
    allowed_operator: ComparisonOperator
    measurement_unit: str
    baseline_rule: str
    required_precision: int
    horizon_rule: str
    observation_window_seconds: int
    outcome_data_deadline_seconds: int
    outcome_source_type: str
    outcome_source_version: str
    benchmark_rule: str | None
    tolerance: float
    missing_data_rule: str
    ambiguous_data_rule: str
    strict_known_rule: str
    minimum_sample_size: int
    minimum_calibration_bin_size: int
    correction_policy: str
    methodology_version: str

    def __post_init__(self) -> None:
        for name in (
            "policy_id",
            "policy_version",
            "claim_type",
            "entity_type",
            "measurement_unit",
            "baseline_rule",
            "horizon_rule",
            "outcome_source_type",
            "outcome_source_version",
            "missing_data_rule",
            "ambiguous_data_rule",
            "strict_known_rule",
            "correction_policy",
            "methodology_version",
        ):
            _text(name, getattr(self, name))
        if self.required_precision < 0 or self.required_precision > 12:
            raise ValueError("required_precision must be between 0 and 12")
        if self.observation_window_seconds < 0 or self.outcome_data_deadline_seconds < 0:
            raise ValueError("policy time windows cannot be negative")
        if self.minimum_sample_size < 1 or self.minimum_calibration_bin_size < 1:
            raise ValueError("aggregate sample minimums must be positive")
        if self.tolerance < 0:
            raise ValueError("tolerance cannot be negative")
        if self.comparison_mode == "benchmark-relative" and not self.benchmark_rule:
            raise ValueError("benchmark-relative policies require benchmark_rule")


@dataclass(frozen=True, slots=True)
class PredictionPublication:
    prediction_id: str
    target_id: str
    entity_type: str
    claim_type: str
    claim: str
    operator: ComparisonOperator
    threshold: float
    condition: str
    measurement_unit: str
    baseline_value: float
    baseline_observation_id: str
    baseline_source_version: str
    baseline_evidence_references: tuple[str, ...]
    benchmark_id: str | None
    effective_at: datetime
    published_at: datetime
    due_at: datetime
    recorded_at: datetime
    known_at: datetime
    policy_id: str
    policy_version: str
    model_version: str
    methodology_version: str
    configuration_version: str
    source_record_ids: tuple[str, ...]
    source_versions: tuple[str, ...]
    evidence_references: tuple[str, ...]
    forecast_probability: float | None = None

    def __post_init__(self) -> None:
        for name in (
            "prediction_id",
            "target_id",
            "entity_type",
            "claim_type",
            "claim",
            "condition",
            "measurement_unit",
            "baseline_observation_id",
            "baseline_source_version",
            "policy_id",
            "policy_version",
            "model_version",
            "methodology_version",
            "configuration_version",
        ):
            _text(name, getattr(self, name))
        for name in ("effective_at", "published_at", "due_at", "recorded_at", "known_at"):
            object.__setattr__(self, name, _aware(name, getattr(self, name)))
        if self.known_at > self.recorded_at:
            raise ValueError("known_at cannot be later than recorded_at")
        if self.published_at > self.due_at or self.effective_at > self.due_at:
            raise ValueError("due_at must not precede publication/effective time")
        if len(self.source_record_ids) != len(self.source_versions):
            raise ValueError("source IDs and versions must correspond one-to-one")
        if not self.baseline_evidence_references or not self.evidence_references or not self.source_record_ids:
            raise ValueError("publication requires baseline, evidence, and source provenance")
        if self.forecast_probability is not None and not 0.0 <= self.forecast_probability <= 1.0:
            raise ValueError("forecast_probability must be in [0,1]")


@dataclass(frozen=True, slots=True)
class OutcomeObservation:
    observation_id: str
    target_id: str
    entity_type: str
    source_type: str
    source_version: str
    value: float
    measurement_unit: str
    effective_at: datetime
    recorded_at: datetime
    known_at: datetime | None
    evidence_references: tuple[str, ...]
    benchmark_id: str | None = None
    benchmark_value: float | None = None
    supersedes_id: str | None = None

    def __post_init__(self) -> None:
        for name in (
            "observation_id",
            "target_id",
            "entity_type",
            "source_type",
            "source_version",
            "measurement_unit",
        ):
            _text(name, getattr(self, name))
        for name in ("effective_at", "recorded_at"):
            object.__setattr__(self, name, _aware(name, getattr(self, name)))
        if self.known_at is not None:
            object.__setattr__(self, "known_at", _aware("known_at", self.known_at))
            if self.known_at > self.recorded_at:
                raise ValueError("outcome known_at cannot be later than recorded_at")
        if not self.evidence_references:
            raise ValueError("outcome provenance is required")


@dataclass(frozen=True, slots=True)
class EvaluationContext:
    recorded_at: datetime
    known_by: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "recorded_at", _aware("recorded_at", self.recorded_at))
        object.__setattr__(self, "known_by", _aware("known_by", self.known_by))
        if self.known_by > self.recorded_at:
            raise ValueError("known_by cannot be later than recorded_at")


@dataclass(frozen=True, slots=True)
class AggregateRequest:
    aggregate_id: str
    cohort: str
    filter_definition: str
    target_ids: tuple[str, ...]
    window_start: datetime
    window_end: datetime
    policy_id: str
    policy_version: str
    model_version: str
    methodology_version: str
    configuration_version: str
    evaluation_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        for name in (
            "aggregate_id",
            "cohort",
            "filter_definition",
            "policy_id",
            "policy_version",
            "model_version",
            "methodology_version",
            "configuration_version",
        ):
            _text(name, getattr(self, name))
        object.__setattr__(self, "window_start", _aware("window_start", self.window_start))
        object.__setattr__(self, "window_end", _aware("window_end", self.window_end))
        if self.window_end < self.window_start:
            raise ValueError("aggregate window is inverted")
        if len(set(self.evaluation_ids)) != len(self.evaluation_ids):
            raise ValueError("aggregate evaluation IDs must be unique")
        if len(set(self.target_ids)) != len(self.target_ids):
            raise ValueError("aggregate target IDs must be unique")


def _text(name: str, value: str) -> None:
    if not value.strip():
        raise ValueError(f"{name} is required")


def _aware(name: str, value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)
