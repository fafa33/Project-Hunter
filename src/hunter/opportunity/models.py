from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from hunter.intelligence.fusion.models import FrozenFloatMap, FrozenScalarMap, FusionTarget

OpportunityPhase = Literal[
    "too_early",
    "forming",
    "early_entry",
    "confirmed_entry",
    "expansion",
    "mature",
    "crowded",
    "deteriorating",
    "exit_risk",
    "invalidated",
    "insufficient_evidence",
]
OpportunityWindow = Literal["closed", "watch", "opening", "open", "strengthening", "weakening", "closing", "invalid"]
ExpectedHorizon = Literal[
    "days", "weeks", "1-3 months", "3-6 months", "6-12 months", "12-24 months", "24-36 months", "indeterminate"
]
OpportunityLabel = Literal[
    "Exceptional Opportunity",
    "High Conviction",
    "Strong Opportunity",
    "Accumulation",
    "Watch Closely",
    "Neutral",
    "Wait",
    "Weak Opportunity",
    "Avoid",
]
RiskRewardBalance = Literal["Low", "Moderate", "High", "Extreme"]
OpportunityEntryWindow = Literal["Very Early", "Early", "Developing", "Established", "Late", "Very Late", "Unknown"]


@dataclass(frozen=True)
class FrozenStringTupleMap(Mapping[str, tuple[str, ...]]):
    entries: tuple[tuple[str, tuple[str, ...]], ...] = ()

    def __init__(
        self, values: Mapping[str, tuple[str, ...] | list[str]] | tuple[tuple[str, tuple[str, ...]], ...] | None = None
    ) -> None:
        raw = values.items() if isinstance(values, Mapping) else values or ()
        object.__setattr__(
            self, "entries", tuple(sorted((str(key), tuple(str(item) for item in value)) for key, value in raw))
        )

    def __getitem__(self, key: str) -> tuple[str, ...]:
        for item_key, value in self.entries:
            if item_key == key:
                return value
        raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        return (key for key, _ in self.entries)

    def __len__(self) -> int:
        return len(self.entries)

    def as_dict(self) -> dict[str, tuple[str, ...]]:
        return dict(self.entries)


@dataclass(frozen=True)
class TemporalComparison:
    historical_depth: int
    current_score: float
    previous_score: float | None
    change: float
    persistence: float
    structural_change: bool
    deterioration: bool
    reversal: bool
    one_off_event: bool
    summary: str


@dataclass(frozen=True)
class ConfirmationState:
    confirmed_categories: tuple[str, ...]
    missing_categories: tuple[str, ...]
    independent_group_count: int
    required_group_count: int
    confirmed: bool
    score: float
    summary: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "confirmed_categories", tuple(sorted(set(self.confirmed_categories))))
        object.__setattr__(self, "missing_categories", tuple(sorted(set(self.missing_categories))))
        object.__setattr__(self, "score", _clamp(self.score))


@dataclass(frozen=True)
class AccelerationState:
    state: str
    value: float
    summary: str


@dataclass(frozen=True)
class DivergenceState:
    divergences: tuple[str, ...]
    severity: float
    summary: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "divergences", tuple(sorted(set(self.divergences))))
        object.__setattr__(self, "severity", _clamp(self.severity))


@dataclass(frozen=True)
class RiskState:
    risks: tuple[str, ...]
    score: float
    summary: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "risks", tuple(sorted(set(self.risks))))
        object.__setattr__(self, "score", _clamp(self.score))


@dataclass(frozen=True)
class HistoricalComparison:
    prior_phases: tuple[str, ...]
    phase_transitions: tuple[str, ...]
    window_duration: int
    false_starts: int
    prior_confirmations: int
    deterioration_after_confirmation: bool
    similarity_summary: str

    def __post_init__(self) -> None:
        for name in ("prior_phases", "phase_transitions"):
            object.__setattr__(self, name, tuple(str(item) for item in getattr(self, name)))


@dataclass(frozen=True)
class OpportunityTimingAssessment:
    assessment_id: str
    target: FusionTarget
    effective_at: datetime
    source_fused_intelligence_ids: tuple[str, ...]
    source_run_ids: tuple[str, ...]
    opportunity_phase: OpportunityPhase
    opportunity_window: OpportunityWindow
    timing_score: float
    confidence: FrozenFloatMap | Mapping[str, float]
    evidence_quality: float
    confirmation_state: ConfirmationState
    acceleration_state: AccelerationState
    divergence_state: DivergenceState
    risk_state: RiskState
    expected_horizon: ExpectedHorizon
    supporting_factors: tuple[str, ...]
    opposing_factors: tuple[str, ...]
    contradictions: tuple[str, ...]
    missing_evidence: tuple[str, ...]
    invalidation_conditions: tuple[str, ...]
    canonical_evidence_refs: tuple[str, ...]
    historical_comparisons: tuple[HistoricalComparison, ...]
    metadata: FrozenScalarMap | Mapping[str, Any] = field(default_factory=FrozenScalarMap)

    def __post_init__(self) -> None:
        object.__setattr__(self, "effective_at", _aware(self.effective_at))
        object.__setattr__(
            self, "source_fused_intelligence_ids", tuple(sorted(set(self.source_fused_intelligence_ids)))
        )
        object.__setattr__(self, "source_run_ids", tuple(sorted(set(self.source_run_ids))))
        object.__setattr__(self, "timing_score", round(max(0.0, min(100.0, float(self.timing_score))), 4))
        object.__setattr__(self, "confidence", FrozenFloatMap(self.confidence))
        object.__setattr__(self, "evidence_quality", _clamp(self.evidence_quality))
        for name in (
            "supporting_factors",
            "opposing_factors",
            "contradictions",
            "missing_evidence",
            "invalidation_conditions",
            "canonical_evidence_refs",
            "historical_comparisons",
        ):
            object.__setattr__(self, name, tuple(getattr(self, name)))
        object.__setattr__(self, "metadata", FrozenScalarMap(self.metadata))


@dataclass(frozen=True)
class OpportunityFactor:
    name: str
    value: float | None
    weight: float
    contribution: float
    evidence_id: str | None = None
    explanation: str = ""

    def __post_init__(self) -> None:
        _require_text("name", self.name)
        if self.value is not None:
            object.__setattr__(self, "value", _clamp(self.value))
        object.__setattr__(self, "weight", _clamp(self.weight))
        object.__setattr__(self, "contribution", round(float(self.contribution), 4))


@dataclass(frozen=True)
class OpportunityAssessment:
    assessment_id: str
    project_id: str
    effective_at: datetime
    opportunity_score: float
    opportunity_label: OpportunityLabel
    conviction_score: float
    conviction_explanation: str
    risk_reward_balance: RiskRewardBalance
    opportunity_window: OpportunityEntryWindow
    positive_factors: tuple[str, ...]
    negative_factors: tuple[str, ...]
    largest_contributors: tuple[OpportunityFactor, ...]
    largest_risks: tuple[OpportunityFactor, ...]
    supporting_evidence: tuple[str, ...]
    missing_evidence: tuple[str, ...]
    confidence: FrozenFloatMap | Mapping[str, float]
    metadata: FrozenScalarMap | Mapping[str, Any] = field(default_factory=FrozenScalarMap)

    def __post_init__(self) -> None:
        _require_text("assessment_id", self.assessment_id)
        _require_text("project_id", self.project_id)
        object.__setattr__(self, "effective_at", _aware(self.effective_at))
        object.__setattr__(self, "opportunity_score", _clamp(self.opportunity_score))
        object.__setattr__(self, "conviction_score", _clamp(self.conviction_score))
        for name in (
            "positive_factors",
            "negative_factors",
            "supporting_evidence",
            "missing_evidence",
        ):
            object.__setattr__(self, name, tuple(str(item) for item in getattr(self, name)))
        object.__setattr__(self, "largest_contributors", tuple(self.largest_contributors))
        object.__setattr__(self, "largest_risks", tuple(self.largest_risks))
        object.__setattr__(self, "confidence", FrozenFloatMap(self.confidence))
        object.__setattr__(self, "metadata", FrozenScalarMap(self.metadata))


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        msg = "opportunity timing timestamps must be timezone-aware"
        raise ValueError(msg)
    return value.astimezone(UTC)


def _require_text(name: str, value: str) -> None:
    if not value.strip():
        msg = f"{name} is required"
        raise ValueError(msg)


def _clamp(value: float) -> float:
    return round(max(0.0, min(1.0, float(value))), 4)
