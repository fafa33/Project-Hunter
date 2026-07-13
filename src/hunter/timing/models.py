from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import MappingProxyType

from hunter.execution.canonicalization import normalize

TIMING_CLASSIFICATIONS: tuple[str, ...] = (
    "STRONG_ACCUMULATION",
    "ACCUMULATION",
    "WAIT",
    "REDUCE",
    "STRONG_REDUCE",
    "INSUFFICIENT_EVIDENCE",
)


@dataclass(frozen=True)
class TimingAssessment:
    assessment_id: str
    project_id: str
    generated_at: datetime
    entry_score: float
    exit_score: float
    accumulation_score: float
    distribution_score: float
    risk_reward_score: float
    cycle_position: str
    market_regime: str
    timing_confidence: float
    evidence_quality: float
    freshness: float
    classification: str
    source_engines: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    repository_ids: tuple[str, ...]
    reasoning_chain: tuple[str, ...]
    missing_evidence: tuple[str, ...] = ()
    stale_evidence: tuple[str, ...] = ()
    raw_inputs: dict[str, float] = field(default_factory=dict)
    normalized_factors: dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "generated_at", _aware(self.generated_at))
        for name in (
            "entry_score",
            "exit_score",
            "accumulation_score",
            "distribution_score",
            "risk_reward_score",
            "timing_confidence",
            "evidence_quality",
            "freshness",
        ):
            object.__setattr__(self, name, _clamp(getattr(self, name)))
        if self.classification not in TIMING_CLASSIFICATIONS:
            msg = f"unsupported timing classification: {self.classification}"
            raise ValueError(msg)
        for name in (
            "source_engines",
            "evidence_ids",
            "repository_ids",
            "reasoning_chain",
            "missing_evidence",
            "stale_evidence",
        ):
            object.__setattr__(self, name, tuple(sorted(str(item) for item in getattr(self, name))))
        normalize(self.raw_inputs)
        normalize(self.normalized_factors)
        object.__setattr__(
            self, "raw_inputs", MappingProxyType({str(k): _clamp(v) for k, v in self.raw_inputs.items()})
        )
        object.__setattr__(
            self,
            "normalized_factors",
            MappingProxyType({str(k): _clamp(v) for k, v in self.normalized_factors.items()}),
        )


@dataclass(frozen=True)
class TimingDependencySnapshot:
    generation_timestamp: datetime
    dependency_timestamps: dict[str, datetime] = field(default_factory=dict)
    dependency_fingerprints: dict[str, str] = field(default_factory=dict)
    protocol_evidence_timestamp: datetime | None = None
    narrative_evidence_timestamp: datetime | None = None
    developer_evidence_timestamp: datetime | None = None
    graph_timestamp: datetime | None = None
    macro_timestamp: datetime | None = None
    whale_timestamp: datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "generation_timestamp", _aware(self.generation_timestamp))
        timestamps = {str(key): _aware(value) for key, value in self.dependency_timestamps.items()}
        fingerprints = {str(key): str(value) for key, value in self.dependency_fingerprints.items()}
        object.__setattr__(self, "dependency_timestamps", MappingProxyType(dict(sorted(timestamps.items()))))
        object.__setattr__(self, "dependency_fingerprints", MappingProxyType(dict(sorted(fingerprints.items()))))
        for name in (
            "protocol_evidence_timestamp",
            "narrative_evidence_timestamp",
            "developer_evidence_timestamp",
            "graph_timestamp",
            "macro_timestamp",
            "whale_timestamp",
        ):
            value = getattr(self, name)
            object.__setattr__(self, name, _aware(value) if value is not None else None)


@dataclass(frozen=True)
class TimingRebuildStatus:
    status: str
    stale_dependencies: tuple[str, ...] = ()
    saved_generation_timestamp: datetime | None = None
    current_generation_timestamp: datetime | None = None

    @property
    def is_stale(self) -> bool:
        return self.status != "CURRENT"


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        msg = "timing timestamps must be timezone-aware"
        raise ValueError(msg)
    return value.astimezone(UTC)


def _clamp(value: float) -> float:
    return round(max(0.0, min(1.0, float(value))), 4)
