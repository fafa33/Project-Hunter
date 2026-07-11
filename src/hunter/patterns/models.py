from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

from hunter.intelligence.fusion.models import FrozenFloatMap, FrozenScalarMap
from hunter.persistence.records import (
    EvidenceRecord,
    FusedIntelligenceRecord,
    IntelligenceRecord,
    OpportunityTimingAssessmentRecord,
    SnapshotRecord,
)

PatternLabel = Literal[
    "Exceptional Match",
    "Very Strong Match",
    "Strong Match",
    "Moderate Match",
    "Weak Match",
    "No Reliable Match",
    "Insufficient Evidence",
]


@dataclass(frozen=True)
class HistoricalProjectPattern:
    project_id: str
    name: str
    outcome: str
    dimensions: FrozenFloatMap | Mapping[str, float]
    context_dimensions: FrozenFloatMap | Mapping[str, float] = field(default_factory=FrozenFloatMap)
    warning_patterns: tuple[str, ...] = ()
    metadata: FrozenScalarMap | Mapping[str, object] = field(default_factory=FrozenScalarMap)

    def __post_init__(self) -> None:
        _require_text("project_id", self.project_id)
        _require_text("name", self.name)
        _require_text("outcome", self.outcome)
        object.__setattr__(self, "dimensions", FrozenFloatMap(self.dimensions))
        object.__setattr__(self, "context_dimensions", FrozenFloatMap(self.context_dimensions))
        object.__setattr__(self, "warning_patterns", tuple(sorted(str(item) for item in self.warning_patterns)))
        object.__setattr__(self, "metadata", FrozenScalarMap(self.metadata))

    @property
    def is_negative(self) -> bool:
        return self.outcome.lower() in {"unsuccessful", "weak", "negative", "failed", "warning"}


@dataclass(frozen=True)
class PatternInputSet:
    target_id: str
    effective_at: datetime
    intelligence: tuple[IntelligenceRecord, ...] = ()
    fused_intelligence: tuple[FusedIntelligenceRecord, ...] = ()
    opportunity_timing: tuple[OpportunityTimingAssessmentRecord, ...] = ()
    evidence: tuple[EvidenceRecord, ...] = ()
    snapshots: tuple[SnapshotRecord, ...] = ()

    def __post_init__(self) -> None:
        _require_text("target_id", self.target_id)
        if self.effective_at.tzinfo is None:
            msg = "effective_at must be timezone-aware"
            raise ValueError(msg)
        object.__setattr__(self, "effective_at", self.effective_at.astimezone(UTC))
        for name in ("intelligence", "fused_intelligence", "opportunity_timing", "evidence", "snapshots"):
            object.__setattr__(self, name, tuple(getattr(self, name)))


@dataclass(frozen=True)
class PatternSimilarityBreakdown:
    dimensions: FrozenFloatMap | Mapping[str, float]
    overall_similarity: float
    historical_similarity: float
    context_similarity: float
    confidence: float
    context_dimensions: FrozenFloatMap | Mapping[str, float] = field(default_factory=FrozenFloatMap)

    def __post_init__(self) -> None:
        object.__setattr__(self, "dimensions", FrozenFloatMap(self.dimensions))
        object.__setattr__(self, "context_dimensions", FrozenFloatMap(self.context_dimensions))
        object.__setattr__(self, "overall_similarity", _clamp01(self.overall_similarity))
        object.__setattr__(self, "historical_similarity", _clamp01(self.historical_similarity))
        object.__setattr__(self, "context_similarity", _clamp01(self.context_similarity))
        object.__setattr__(self, "confidence", _clamp01(self.confidence))


@dataclass(frozen=True)
class PatternMatch:
    project_id: str
    project_name: str
    outcome: str
    similarity: float
    similarity_percent: float
    historical_similarity: float
    context_similarity: float
    label: PatternLabel
    breakdown: PatternSimilarityBreakdown
    strengths: tuple[str, ...]
    weaknesses: tuple[str, ...]
    matching_factors: tuple[str, ...]
    differing_factors: tuple[str, ...]
    warning_patterns: tuple[str, ...]
    confidence: float

    def __post_init__(self) -> None:
        for name in ("project_id", "project_name", "outcome", "label"):
            _require_text(name, getattr(self, name))
        object.__setattr__(self, "similarity", _clamp01(self.similarity))
        object.__setattr__(self, "similarity_percent", round(_clamp01(self.similarity) * 100.0, 2))
        object.__setattr__(self, "historical_similarity", _clamp01(self.historical_similarity))
        object.__setattr__(self, "context_similarity", _clamp01(self.context_similarity))
        object.__setattr__(self, "confidence", _clamp01(self.confidence))
        for name in ("strengths", "weaknesses", "matching_factors", "differing_factors", "warning_patterns"):
            object.__setattr__(self, name, tuple(str(item) for item in getattr(self, name)))

    @property
    def is_negative(self) -> bool:
        return self.outcome.lower() in {"unsuccessful", "weak", "negative", "failed", "warning"}


@dataclass(frozen=True)
class PatternMatchingAssessment:
    assessment_id: str
    target_id: str
    effective_at: datetime
    source_record_ids: tuple[str, ...]
    top_matches: tuple[PatternMatch, ...]
    positive_matches: tuple[PatternMatch, ...]
    negative_matches: tuple[PatternMatch, ...]
    historical_similarity: float
    context_similarity: float
    overall_similarity: float
    historical_confidence: float
    missing_evidence: tuple[str, ...]
    metadata: FrozenScalarMap | Mapping[str, object] = field(default_factory=FrozenScalarMap)

    def __post_init__(self) -> None:
        _require_text("assessment_id", self.assessment_id)
        _require_text("target_id", self.target_id)
        if self.effective_at.tzinfo is None:
            msg = "effective_at must be timezone-aware"
            raise ValueError(msg)
        object.__setattr__(self, "effective_at", self.effective_at.astimezone(UTC))
        object.__setattr__(self, "source_record_ids", tuple(sorted(set(self.source_record_ids))))
        object.__setattr__(self, "top_matches", tuple(self.top_matches))
        object.__setattr__(self, "positive_matches", tuple(self.positive_matches))
        object.__setattr__(self, "negative_matches", tuple(self.negative_matches))
        object.__setattr__(self, "historical_similarity", _clamp01(self.historical_similarity))
        object.__setattr__(self, "context_similarity", _clamp01(self.context_similarity))
        object.__setattr__(self, "overall_similarity", _clamp01(self.overall_similarity))
        object.__setattr__(self, "historical_confidence", _clamp01(self.historical_confidence))
        object.__setattr__(self, "missing_evidence", tuple(sorted(set(self.missing_evidence))))
        object.__setattr__(self, "metadata", FrozenScalarMap(self.metadata))


def _require_text(name: str, value: str) -> None:
    if not value.strip():
        msg = f"{name} is required"
        raise ValueError(msg)


def _clamp01(value: float) -> float:
    return round(max(0.0, min(1.0, float(value))), 4)
