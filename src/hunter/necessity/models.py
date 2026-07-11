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

NecessityLabel = Literal[
    "Critical Infrastructure",
    "Emerging Necessity",
    "Growing Necessity",
    "Established",
    "Mature",
    "Declining",
    "Legacy",
    "Low Necessity",
    "Insufficient Evidence",
]


@dataclass(frozen=True)
class TechnologyNecessityInputSet:
    technology_id: str
    effective_at: datetime
    intelligence: tuple[IntelligenceRecord, ...] = ()
    fused_intelligence: tuple[FusedIntelligenceRecord, ...] = ()
    opportunity_timing: tuple[OpportunityTimingAssessmentRecord, ...] = ()
    evidence: tuple[EvidenceRecord, ...] = ()
    snapshots: tuple[SnapshotRecord, ...] = ()

    def __post_init__(self) -> None:
        _require_text("technology_id", self.technology_id)
        if self.effective_at.tzinfo is None:
            msg = "effective_at must be timezone-aware"
            raise ValueError(msg)
        object.__setattr__(self, "effective_at", self.effective_at.astimezone(UTC))
        for name in ("intelligence", "fused_intelligence", "opportunity_timing", "evidence", "snapshots"):
            object.__setattr__(self, name, tuple(getattr(self, name)))


@dataclass(frozen=True)
class TechnologyNecessityComponent:
    name: str
    value: float
    weight: float
    contribution: float
    evidence_ids: tuple[str, ...] = ()
    explanation: str = ""

    def __post_init__(self) -> None:
        _require_text("name", self.name)
        object.__setattr__(self, "value", _clamp01(self.value))
        object.__setattr__(self, "weight", max(0.0, float(self.weight)))
        object.__setattr__(self, "contribution", round(float(self.contribution), 4))
        object.__setattr__(self, "evidence_ids", tuple(sorted(set(self.evidence_ids))))


@dataclass(frozen=True)
class TechnologyNecessityAssessment:
    assessment_id: str
    technology_id: str
    effective_at: datetime
    source_record_ids: tuple[str, ...]
    technology_necessity_score: float
    capital_rotation_score: float
    infrastructure_criticality: float
    dependency_strength: float
    replacement_difficulty: float
    necessity_gap: float
    overall_necessity: float
    label: NecessityLabel
    components: tuple[TechnologyNecessityComponent, ...]
    technology_position: tuple[str, ...]
    supporting_evidence: tuple[str, ...]
    missing_evidence: tuple[str, ...]
    confidence: float
    metadata: FrozenScalarMap | Mapping[str, object] = field(default_factory=FrozenScalarMap)

    def __post_init__(self) -> None:
        _require_text("assessment_id", self.assessment_id)
        _require_text("technology_id", self.technology_id)
        if self.effective_at.tzinfo is None:
            msg = "effective_at must be timezone-aware"
            raise ValueError(msg)
        object.__setattr__(self, "effective_at", self.effective_at.astimezone(UTC))
        for name in (
            "technology_necessity_score",
            "capital_rotation_score",
            "infrastructure_criticality",
            "dependency_strength",
            "replacement_difficulty",
            "necessity_gap",
            "overall_necessity",
            "confidence",
        ):
            object.__setattr__(self, name, _clamp01(getattr(self, name)))
        for name in ("source_record_ids", "technology_position", "supporting_evidence", "missing_evidence"):
            object.__setattr__(self, name, tuple(sorted(str(item) for item in getattr(self, name))))
        object.__setattr__(self, "components", tuple(self.components))
        object.__setattr__(self, "metadata", FrozenScalarMap(self.metadata))

    @property
    def scores(self) -> FrozenFloatMap:
        return FrozenFloatMap(
            {
                "technology_necessity": self.technology_necessity_score,
                "capital_rotation": self.capital_rotation_score,
                "infrastructure_criticality": self.infrastructure_criticality,
                "dependency_strength": self.dependency_strength,
                "replacement_difficulty": self.replacement_difficulty,
                "necessity_gap": self.necessity_gap,
                "overall_necessity": self.overall_necessity,
                "confidence": self.confidence,
            }
        )


def _require_text(name: str, value: str) -> None:
    if not value.strip():
        msg = f"{name} is required"
        raise ValueError(msg)


def _clamp01(value: float) -> float:
    return round(max(0.0, min(1.0, float(value))), 4)
