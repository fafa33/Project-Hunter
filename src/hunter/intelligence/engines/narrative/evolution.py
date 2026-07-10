from __future__ import annotations

from datetime import timedelta

from hunter.intelligence.engines.narrative.configuration import NarrativeEngineConfiguration
from hunter.intelligence.engines.narrative.models import NarrativeCluster, NarrativeTrend


class NarrativeEvolutionTracker:
    def __init__(self, configuration: NarrativeEngineConfiguration | None = None) -> None:
        self.configuration = configuration or NarrativeEngineConfiguration()

    def trends(self, clusters: tuple[NarrativeCluster, ...]) -> tuple[NarrativeTrend, ...]:
        return tuple(self._trend(cluster) for cluster in clusters)

    def _trend(self, cluster: NarrativeCluster) -> NarrativeTrend:
        if not cluster.evidence:
            return NarrativeTrend(cluster.id, cluster.category, 0.0, 0.0, 0.0, 0.0, ignored=True)
        latest = max(item.timestamp for item in cluster.evidence)
        recent_cutoff = latest - timedelta(days=self.configuration.freshness_days)
        recent = [item for item in cluster.evidence if item.timestamp >= recent_cutoff]
        older = [item for item in cluster.evidence if item.timestamp < recent_cutoff]
        recent_strength = sum(item.strength for item in recent)
        older_strength = sum(item.strength for item in older)
        total_strength = recent_strength + older_strength
        growth = _ratio(recent_strength, total_strength)
        acceleration = _ratio(recent_strength - older_strength, max(total_strength, 1.0))
        saturation = min(len(recent) / 8, 1.0)
        persistence = min((latest - min(item.timestamp for item in cluster.evidence)).days / 180, 1.0)
        return NarrativeTrend(
            narrative_id=cluster.id.replace("narrative-cluster", "narrative", 1),
            category=cluster.category,
            growth=round(growth, 4),
            acceleration=round(acceleration, 4),
            saturation=round(saturation, 4),
            persistence=round(persistence, 4),
            ignored=len(cluster.evidence) <= 1 and recent_strength < self.configuration.emerging_threshold,
        )


def _ratio(numerator: float, denominator: float) -> float:
    if denominator <= 0:
        return 0.0
    return min(max(numerator / denominator, 0.0), 1.0)
