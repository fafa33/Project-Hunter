from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

HistoricalCaseType = Literal[
    "EARLY_WINNER",
    "ESTABLISHED_WINNER",
    "FAILED_PROJECT",
    "COLLAPSED_PROJECT",
    "DELISTED_PROJECT",
    "MIGRATED_TOKEN",
    "RENAMED_PROJECT",
    "HACKED_PROTOCOL",
    "ABANDONED_PROJECT",
    "UNDERPERFORMER",
    "SECTOR_LEADER",
    "FALSE_NARRATIVE_WINNER",
    "TEMPORARY_WINNER",
    "RECOVERY_CASE",
    "NEUTRAL_CONTROL",
]

SuccessLabel = Literal[
    "OUTPERFORMED_BENCHMARK",
    "OUTPERFORMED_PEERS",
    "TOP_QUARTILE",
    "SURVIVED",
    "MAJOR_WINNER",
    "MODERATE_WINNER",
    "NEUTRAL",
    "UNDERPERFORMER",
    "SEVERE_UNDERPERFORMER",
    "COLLAPSED",
    "DELISTED",
    "INSUFFICIENT_OUTCOME_DATA",
]


@dataclass(frozen=True)
class HistoricalValidationCase:
    case_id: str
    project_id: str
    project_slug: str
    project_name: str
    symbol: str
    sector: str
    case_type: HistoricalCaseType
    evaluation_timestamp: datetime
    historical_cutoff_timestamp: datetime
    project_lifecycle_state: str
    token_lifecycle_state: str
    current_project_id: str | None = None
    historical_token_id: str | None = None
    current_token_id: str | None = None
    migration_ratio: float | None = None
    migration_date: datetime | None = None
    continuity_status: str = "continuous"

    def __post_init__(self) -> None:
        object.__setattr__(self, "evaluation_timestamp", self.evaluation_timestamp.astimezone(UTC))
        object.__setattr__(self, "historical_cutoff_timestamp", self.historical_cutoff_timestamp.astimezone(UTC))
        if self.migration_date is not None:
            object.__setattr__(self, "migration_date", self.migration_date.astimezone(UTC))


@dataclass(frozen=True)
class HistoricalEvidenceRecord:
    source_provider: str
    source_record_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    repository_ids: tuple[str, ...]
    event_timestamp: datetime
    publication_timestamp: datetime
    ingestion_timestamp: datetime
    evaluation_cutoff_timestamp: datetime
    confidence: float
    freshness: float
    validation_status: str
    engine: str
    raw_metrics: dict[str, object]
    normalized_metrics: dict[str, float]
    data_availability_timestamp: datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_timestamp", self.event_timestamp.astimezone(UTC))
        object.__setattr__(self, "publication_timestamp", self.publication_timestamp.astimezone(UTC))
        object.__setattr__(self, "ingestion_timestamp", self.ingestion_timestamp.astimezone(UTC))
        object.__setattr__(self, "evaluation_cutoff_timestamp", self.evaluation_cutoff_timestamp.astimezone(UTC))
        if self.data_availability_timestamp is not None:
            object.__setattr__(
                self,
                "data_availability_timestamp",
                self.data_availability_timestamp.astimezone(UTC),
            )


@dataclass(frozen=True)
class HistoricalEvidenceSnapshot:
    snapshot_id: str
    case_id: str
    version: int
    finalized: bool
    created_at: datetime
    previous_snapshot_id: str | None
    correction_reason: str | None
    correction_timestamp: datetime | None
    changed_fields: tuple[str, ...]
    evidence: tuple[HistoricalEvidenceRecord, ...]
    missing_evidence: tuple[str, ...]
    unavailable_engines: tuple[str, ...]
    stale_engines: tuple[str, ...]
    validation_warnings: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "created_at", self.created_at.astimezone(UTC))
        if self.correction_timestamp is not None:
            object.__setattr__(self, "correction_timestamp", self.correction_timestamp.astimezone(UTC))


@dataclass(frozen=True)
class HistoricalEngineOutput:
    case_id: str
    engine: str
    score: float
    confidence: float
    evidence_ids: tuple[str, ...]
    repository_ids: tuple[str, ...]
    status: str


@dataclass(frozen=True)
class HistoricalCommitteeAssessment:
    case_id: str
    committee_decision: str
    committee_confidence: float
    champion_project_id: str | None
    no_qualified_candidate: bool


@dataclass(frozen=True)
class HistoricalRankingSnapshot:
    case_id: str
    project_id: str
    historical_rank: int
    sector_rank: int
    hunter_score: float


@dataclass(frozen=True)
class HistoricalOutcomeWindow:
    window_days: int
    start_price: float | None
    end_price: float | None
    maximum_price: float | None
    minimum_price: float | None
    simple_return: float | None
    log_return: float | None
    maximum_drawdown: float | None
    maximum_favorable_excursion: float | None
    maximum_adverse_excursion: float | None
    volatility: float | None
    liquidity_change: float | None
    market_cap_change: float | None
    fdv_change: float | None
    rank_change: int | None
    survival_status: str
    delisting_status: str
    exploit_status: str
    collapse_status: str


@dataclass(frozen=True)
class HistoricalOutcome:
    case_id: str
    project_id: str
    windows: tuple[HistoricalOutcomeWindow, ...]
    final_success_label: SuccessLabel


@dataclass(frozen=True)
class HistoricalBenchmarkOutcome:
    case_id: str
    benchmark_id: str
    window_days: int
    absolute_return: float | None
    excess_return: float | None
    benchmark_relative_return: float | None
    peer_relative_return: float | None
    percentile_outcome: float | None
    rank_improvement: int | None
    rank_deterioration: int | None


@dataclass(frozen=True)
class HistoricalBiasValidation:
    case_id: str
    leakage_passed: bool
    survivorship_passed: bool
    violations: tuple[str, ...]


@dataclass(frozen=True)
class HistoricalEngineMetric:
    engine: str
    historical_availability: float
    predictive_association: float | str
    hit_rate_when_positive: float | str
    hit_rate_when_negative: float | str
    false_positive_contribution: int
    false_negative_contribution: int
    agreement_with_success: float | str
    disagreement_with_success: float | str
    marginal_contribution: float | str
    evidence_quality: float
    freshness_quality: float
    sample_count: int


@dataclass(frozen=True)
class HistoricalCalibrationMetric:
    metric_id: str
    brier_score: float | str
    calibration_error: float | str
    reliability_buckets: tuple[tuple[str, int, float | str], ...]
    sample_size_status: str


@dataclass(frozen=True)
class HistoricalChallengeResult:
    case_id: str
    project_id: str
    evaluation_timestamp: datetime
    historical_cutoff_timestamp: datetime
    hunter_decision: str
    historical_rank: int | None
    committee_decision: str
    probability: float
    opportunity: float
    risk: float
    positive_drivers: tuple[str, ...]
    negative_drivers: tuple[str, ...]
    warning_signals: tuple[str, ...]
    realized_outcome: SuccessLabel
    benchmark_outcome: str
    excess_return: float | None
    maximum_drawdown: float | None
    was_hunter_correct: str
    correctness_reason: str
    leakage_validation: str
    survivorship_validation: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "evaluation_timestamp", self.evaluation_timestamp.astimezone(UTC))
        object.__setattr__(self, "historical_cutoff_timestamp", self.historical_cutoff_timestamp.astimezone(UTC))


@dataclass(frozen=True)
class HistoricalBacktestRun:
    run_id: str
    generated_at: datetime
    cases: tuple[HistoricalValidationCase, ...]
    snapshots: tuple[HistoricalEvidenceSnapshot, ...]
    engine_outputs: tuple[HistoricalEngineOutput, ...]
    committee_assessments: tuple[HistoricalCommitteeAssessment, ...]
    ranking_snapshots: tuple[HistoricalRankingSnapshot, ...]
    outcomes: tuple[HistoricalOutcome, ...]
    benchmark_outcomes: tuple[HistoricalBenchmarkOutcome, ...]
    calibration_metrics: tuple[HistoricalCalibrationMetric, ...]
    engine_metrics: tuple[HistoricalEngineMetric, ...]
    challenge_results: tuple[HistoricalChallengeResult, ...]
    bias_validations: tuple[HistoricalBiasValidation, ...]
    historical_coverage: float
    leakage_passed: bool
    survivorship_passed: bool
    sample_size_status: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "generated_at", self.generated_at.astimezone(UTC))
