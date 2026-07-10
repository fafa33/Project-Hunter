from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from hunter.intelligence.metadata import IntelligenceMetadata, normalize_metadata


@dataclass(frozen=True)
class Signal:
    id: str
    source: str
    timestamp: datetime
    category: str
    strength: float
    confidence: float
    severity: float
    metadata: IntelligenceMetadata = field(default_factory=IntelligenceMetadata)

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", normalize_metadata(self.metadata))
