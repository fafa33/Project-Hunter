from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

FusionTargetType = Literal["project", "asset", "protocol", "chain", "sector", "narrative", "ecosystem"]


@dataclass(frozen=True)
class FusionTarget:
    target_type: FusionTargetType
    target_id: str
    label: str | None = None
    metadata: dict[str, str | int | float | bool | None] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_text("target_type", self.target_type)
        _require_text("target_id", self.target_id)
        object.__setattr__(self, "metadata", _metadata(self.metadata))


@dataclass(frozen=True)
class FusionInput:
    intelligence_id: str
    engine_id: str
    engine_version: str | None
    plugin_id: str | None
    plugin_version: str | None
    run_id: str | None
    project: str
    generated_at: datetime
    effective_at: datetime
    confidence_score: float
    evidence_ids: tuple[str, ...]
    evidence_references: tuple[str, ...]
    signal_ids: tuple[str, ...]
    signal_categories: tuple[str, ...]
    signal_strengths: tuple[float, ...]
    signal_confidences: tuple[float, ...]
    signal_severities: tuple[float, ...]
    observation_ids: tuple[str, ...]
    observation_descriptions: tuple[str, ...]
    insight_ids: tuple[str, ...]
    insight_titles: tuple[str, ...]
    insight_explanations: tuple[str, ...]
    metadata: dict[str, str | int | float | bool | None] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("intelligence_id", "engine_id", "project"):
            _require_text(name, getattr(self, name))
        object.__setattr__(self, "generated_at", _aware(self.generated_at))
        object.__setattr__(self, "effective_at", _aware(self.effective_at))
        object.__setattr__(self, "confidence_score", _clamp(self.confidence_score))
        for name in (
            "evidence_ids",
            "evidence_references",
            "signal_ids",
            "signal_categories",
            "signal_strengths",
            "signal_confidences",
            "signal_severities",
            "observation_ids",
            "observation_descriptions",
            "insight_ids",
            "insight_titles",
            "insight_explanations",
        ):
            object.__setattr__(self, name, tuple(getattr(self, name)))
        object.__setattr__(self, "metadata", _metadata(self.metadata))


@dataclass(frozen=True)
class EngineContribution:
    engine_id: str
    engine_version: str | None
    plugin_id: str | None
    plugin_version: str | None
    intelligence_ids: tuple[str, ...]
    evidence_count: int
    signal_count: int
    observation_count: int
    insight_count: int
    weight: float
    confidence: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "intelligence_ids", tuple(sorted(self.intelligence_ids)))
        object.__setattr__(self, "weight", _clamp(self.weight))
        object.__setattr__(self, "confidence", _clamp(self.confidence))


@dataclass(frozen=True)
class CorroborationAssessment:
    corroborated_categories: tuple[str, ...]
    corroborating_engine_ids: tuple[str, ...]
    score: float
    explanation: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "corroborated_categories", tuple(sorted(set(self.corroborated_categories))))
        object.__setattr__(self, "corroborating_engine_ids", tuple(sorted(set(self.corroborating_engine_ids))))
        object.__setattr__(self, "score", _clamp(self.score))


@dataclass(frozen=True)
class ContradictionAssessment:
    contradicted_categories: tuple[str, ...]
    severity: float
    explanation: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "contradicted_categories", tuple(sorted(set(self.contradicted_categories))))
        object.__setattr__(self, "severity", _clamp(self.severity))


@dataclass(frozen=True)
class DependencyAssessment:
    dependent_engine_ids: tuple[str, ...]
    dependency_edges: tuple[tuple[str, str, str], ...]
    penalty: float
    explanation: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "dependent_engine_ids", tuple(sorted(set(self.dependent_engine_ids))))
        object.__setattr__(self, "dependency_edges", tuple(sorted(self.dependency_edges)))
        object.__setattr__(self, "penalty", _clamp(self.penalty))


@dataclass(frozen=True)
class MissingEvidenceAssessment:
    missing_categories: tuple[str, ...]
    severity: float
    explanation: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "missing_categories", tuple(sorted(set(self.missing_categories))))
        object.__setattr__(self, "severity", _clamp(self.severity))


@dataclass(frozen=True)
class UnifiedSignal:
    id: str
    category: str
    strength: float
    confidence: float
    severity: float
    source_signal_ids: tuple[str, ...]
    engine_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "strength", _clamp(self.strength))
        object.__setattr__(self, "confidence", _clamp(self.confidence))
        object.__setattr__(self, "severity", _clamp(self.severity))
        object.__setattr__(self, "source_signal_ids", tuple(sorted(set(self.source_signal_ids))))
        object.__setattr__(self, "engine_ids", tuple(sorted(set(self.engine_ids))))
        object.__setattr__(self, "evidence_ids", tuple(sorted(set(self.evidence_ids))))


@dataclass(frozen=True)
class UnifiedObservation:
    id: str
    description: str
    importance: float
    source_observation_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    engine_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "importance", _clamp(self.importance))
        object.__setattr__(self, "source_observation_ids", tuple(sorted(set(self.source_observation_ids))))
        object.__setattr__(self, "evidence_ids", tuple(sorted(set(self.evidence_ids))))
        object.__setattr__(self, "engine_ids", tuple(sorted(set(self.engine_ids))))


@dataclass(frozen=True)
class UnifiedInsight:
    id: str
    title: str
    explanation: str
    confidence: float
    priority: float
    source_insight_ids: tuple[str, ...]
    observation_ids: tuple[str, ...]
    engine_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "confidence", _clamp(self.confidence))
        object.__setattr__(self, "priority", _clamp(self.priority))
        object.__setattr__(self, "source_insight_ids", tuple(sorted(set(self.source_insight_ids))))
        object.__setattr__(self, "observation_ids", tuple(sorted(set(self.observation_ids))))
        object.__setattr__(self, "engine_ids", tuple(sorted(set(self.engine_ids))))


@dataclass(frozen=True)
class UnifiedNarrative:
    summary: str
    key_points: tuple[str, ...]
    uncertainty: str
    source_insight_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "key_points", tuple(self.key_points))
        object.__setattr__(self, "source_insight_ids", tuple(sorted(set(self.source_insight_ids))))


@dataclass(frozen=True)
class IntelligenceGraphNode:
    id: str
    node_type: str
    label: str
    metadata: dict[str, str | int | float | bool | None] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", _metadata(self.metadata))


@dataclass(frozen=True)
class IntelligenceGraphEdge:
    id: str
    source_id: str
    target_id: str
    edge_type: str
    weight: float
    metadata: dict[str, str | int | float | bool | None] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "weight", _clamp(self.weight))
        object.__setattr__(self, "metadata", _metadata(self.metadata))


@dataclass(frozen=True)
class FusedIntelligence:
    id: str
    target: FusionTarget
    source_intelligence_ids: tuple[str, ...]
    contributions: tuple[EngineContribution, ...]
    corroboration: CorroborationAssessment
    contradictions: ContradictionAssessment
    dependencies: DependencyAssessment
    missing_evidence: MissingEvidenceAssessment
    signals: tuple[UnifiedSignal, ...]
    observations: tuple[UnifiedObservation, ...]
    insights: tuple[UnifiedInsight, ...]
    narrative: UnifiedNarrative
    graph_nodes: tuple[IntelligenceGraphNode, ...]
    graph_edges: tuple[IntelligenceGraphEdge, ...]
    confidence: dict[str, float]
    effective_at: datetime
    created_at: datetime
    metadata: dict[str, str | int | float | bool | None] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_intelligence_ids", tuple(sorted(set(self.source_intelligence_ids))))
        for name in ("contributions", "signals", "observations", "insights", "graph_nodes", "graph_edges"):
            object.__setattr__(self, name, tuple(getattr(self, name)))
        object.__setattr__(self, "confidence", {str(key): _clamp(float(value)) for key, value in sorted(self.confidence.items())})
        object.__setattr__(self, "effective_at", _aware(self.effective_at))
        object.__setattr__(self, "created_at", _aware(self.created_at))
        object.__setattr__(self, "metadata", _metadata(self.metadata))


def _require_text(name: str, value: str) -> None:
    if not str(value).strip():
        msg = f"{name} is required"
        raise ValueError(msg)


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        msg = "fusion timestamps must be timezone-aware"
        raise ValueError(msg)
    return value.astimezone(UTC)


def _clamp(value: float) -> float:
    return round(min(max(float(value), 0.0), 1.0), 4)


def _metadata(metadata: dict[str, Any]) -> dict[str, str | int | float | bool | None]:
    normalized: dict[str, str | int | float | bool | None] = {}
    for key, value in metadata.items():
        if not isinstance(value, str | int | float | bool) and value is not None:
            msg = "fusion metadata values must be JSON scalar values"
            raise ValueError(msg)
        normalized[str(key)] = value
    return normalized
