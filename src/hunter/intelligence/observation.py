from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from hunter.intelligence.evidence import Evidence
from hunter.intelligence.metadata import IntelligenceMetadata, normalize_metadata


@dataclass(frozen=True)
class Observation:
    id: str
    engine: str
    project: str
    description: str
    evidence: tuple[Evidence, ...]
    importance: float
    metadata: IntelligenceMetadata | dict[str, Any] = field(default_factory=IntelligenceMetadata)

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence", tuple(self.evidence))
        object.__setattr__(self, "metadata", normalize_metadata(self.metadata))
