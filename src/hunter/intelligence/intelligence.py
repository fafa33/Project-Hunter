from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from hunter.intelligence.confidence import Confidence
from hunter.intelligence.evidence import Evidence
from hunter.intelligence.insight import Insight
from hunter.intelligence.metadata import IntelligenceMetadata, normalize_metadata
from hunter.intelligence.observation import Observation
from hunter.intelligence.signal import Signal


@dataclass(frozen=True)
class Intelligence:
    id: str
    project: str
    engine: str
    signals: tuple[Signal, ...]
    evidence: tuple[Evidence, ...]
    observations: tuple[Observation, ...]
    insights: tuple[Insight, ...]
    confidence: Confidence
    generated_at: datetime
    metadata: IntelligenceMetadata = field(default_factory=IntelligenceMetadata)

    def __post_init__(self) -> None:
        object.__setattr__(self, "signals", tuple(self.signals))
        object.__setattr__(self, "evidence", tuple(self.evidence))
        object.__setattr__(self, "observations", tuple(self.observations))
        object.__setattr__(self, "insights", tuple(self.insights))
        object.__setattr__(self, "metadata", normalize_metadata(self.metadata))
