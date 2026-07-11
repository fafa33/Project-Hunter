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

ProbabilityLabel = Literal[
    "Exceptional Probability",
    "Very High Probability",
    "High Probability",
    "Moderately Positive",
    "Neutral",
    "Speculative",
    "Low Probability",
    "Very Low Probability",
    "Insufficient Evidence",
]


@dataclass(frozen=True)
class ProbabilityInputSet:
    target_id: str
    effective_at: datetime
    fused_intelligence: tuple[FusedIntelligenceRecord, ...] = ()
    opportunity_timing: tuple[OpportunityTimingAssessmentRecord, ...] = ()
    intelligence: tuple[IntelligenceRecord, ...] = ()
    evidence: tuple[EvidenceRecord, ...] = ()
    snapshots: tuple[SnapshotRecord, ...] = ()

    def __post_init__(self) -> None:
        if not self.target_id.strip():
            msg = "target_id is required"
            raise ValueError(msg)
        if self.effective_at.tzinfo is None:
            msg = "effective_at must be timezone-aware"
            raise ValueError(msg)
        object.__setattr__(self, "effective_at", self.effective_at.astimezone(UTC))
        for name in ("fused_intelligence", "opportunity_timing", "intelligence", "evidence", "snapshots"):
            object.__setattr__(self, name, tuple(getattr(self, name)))


@dataclass(frozen=True)
class ProbabilityComponent:
    name: str
    value: float
    weight: float
    contribution: float
    source_record_ids: tuple[str, ...] = ()
    explanation: str = ""

    def __post_init__(self) -> None:
        if not self.name.strip():
            msg = "component name is required"
            raise ValueError(msg)
        object.__setattr__(self, "value", _clamp01(self.value))
        object.__setattr__(self, "weight", max(0.0, float(self.weight)))
        object.__setattr__(self, "contribution", round(float(self.contribution), 4))
        object.__setattr__(self, "source_record_ids", tuple(sorted(set(self.source_record_ids))))


@dataclass(frozen=True)
class ProbabilityAssessment:
    assessment_id: str
    target_id: str
    effective_at: datetime
    source_record_ids: tuple[str, ...]
    probability_score: float
    success_probability: float
    failure_probability: float
    probability_label: ProbabilityLabel
    evidence_robustness: float
    historical_reliability: float
    decision_confidence: float
    consensus_score: float
    conflict_score: float
    components: tuple[ProbabilityComponent, ...]
    largest_positive_contributors: tuple[ProbabilityComponent, ...]
    largest_negative_contributors: tuple[ProbabilityComponent, ...]
    supporting_engines: tuple[str, ...]
    conflicting_engines: tuple[str, ...]
    supporting_evidence: tuple[str, ...]
    weak_evidence: tuple[str, ...]
    missing_evidence: tuple[str, ...]
    explanation: tuple[str, ...]
    metadata: FrozenScalarMap | Mapping[str, object] = field(default_factory=FrozenScalarMap)

    def __post_init__(self) -> None:
        if not self.assessment_id.strip():
            msg = "assessment_id is required"
            raise ValueError(msg)
        if not self.target_id.strip():
            msg = "target_id is required"
            raise ValueError(msg)
        if self.effective_at.tzinfo is None:
            msg = "effective_at must be timezone-aware"
            raise ValueError(msg)
        object.__setattr__(self, "effective_at", self.effective_at.astimezone(UTC))
        for name in (
            "probability_score",
            "success_probability",
            "failure_probability",
            "evidence_robustness",
            "historical_reliability",
            "decision_confidence",
            "consensus_score",
            "conflict_score",
        ):
            object.__setattr__(self, name, _clamp01(getattr(self, name)))
        for name in (
            "source_record_ids",
            "supporting_engines",
            "conflicting_engines",
            "supporting_evidence",
            "weak_evidence",
            "missing_evidence",
            "explanation",
        ):
            object.__setattr__(self, name, tuple(str(item) for item in getattr(self, name)))
        object.__setattr__(self, "components", tuple(self.components))
        object.__setattr__(self, "largest_positive_contributors", tuple(self.largest_positive_contributors))
        object.__setattr__(self, "largest_negative_contributors", tuple(self.largest_negative_contributors))
        object.__setattr__(self, "metadata", FrozenScalarMap(self.metadata))

    @property
    def confidence(self) -> FrozenFloatMap:
        return FrozenFloatMap(
            {
                "evidence_robustness": self.evidence_robustness,
                "historical_reliability": self.historical_reliability,
                "decision_confidence": self.decision_confidence,
                "consensus_score": self.consensus_score,
                "conflict_score": self.conflict_score,
            }
        )


def _clamp01(value: float) -> float:
    return round(max(0.0, min(1.0, float(value))), 4)
