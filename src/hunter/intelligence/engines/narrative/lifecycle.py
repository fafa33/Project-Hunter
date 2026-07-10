from __future__ import annotations

from hunter.intelligence.engines.narrative.configuration import NarrativeEngineConfiguration
from hunter.intelligence.engines.narrative.models import NarrativeLifecycle, NarrativeTrend


class NarrativeLifecycleModel:
    def __init__(self, configuration: NarrativeEngineConfiguration | None = None) -> None:
        self.configuration = configuration or NarrativeEngineConfiguration()

    def assign(self, trends: tuple[NarrativeTrend, ...]) -> tuple[NarrativeLifecycle, ...]:
        return tuple(self._assign_one(trend) for trend in trends)

    def _assign_one(self, trend: NarrativeTrend) -> NarrativeLifecycle:
        phase = "unknown"
        reason = "insufficient evidence"
        if trend.ignored:
            phase = "emerging"
            reason = "ignored but present evidence"
        elif trend.saturation >= self.configuration.saturation_threshold and trend.growth >= 0.65:
            phase = "saturation"
            reason = "high evidence density indicates saturation"
        elif trend.saturation >= 0.65:
            phase = "crowded"
            reason = "crowded evidence cluster"
        elif trend.acceleration >= self.configuration.acceleration_threshold:
            phase = "acceleration"
            reason = "recent evidence is accelerating"
        elif trend.growth >= self.configuration.expansion_threshold:
            phase = "expansion"
            reason = "recent evidence is expanding"
        elif trend.growth >= self.configuration.emerging_threshold:
            phase = "early_expansion"
            reason = "narrative has early growth"
        elif trend.persistence >= 0.5 and trend.growth < 0.2:
            phase = "decline"
            reason = "persistent narrative has weak recent growth"
        return NarrativeLifecycle(
            narrative_id=trend.narrative_id,
            category=trend.category,
            phase=phase,
            previous_phase="unknown",
            reason=reason,
        )
