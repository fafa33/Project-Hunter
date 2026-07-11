from __future__ import annotations

from dataclasses import dataclass

from hunter.intelligence.observation import Observation


@dataclass(frozen=True)
class Insight:
    id: str
    title: str
    explanation: str
    supporting_observations: tuple[Observation, ...]
    confidence: float
    priority: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "supporting_observations", tuple(self.supporting_observations))
