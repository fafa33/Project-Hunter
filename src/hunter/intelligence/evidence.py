from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from hunter.intelligence.metadata import IntelligenceMetadata, normalize_metadata


@dataclass(frozen=True)
class Evidence:
    id: str
    source: str
    collected_at: datetime
    reliability: float
    freshness: float
    reference: str
    raw_data: Any
    metadata: IntelligenceMetadata | dict[str, Any] = field(default_factory=IntelligenceMetadata)

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", normalize_metadata(self.metadata))
