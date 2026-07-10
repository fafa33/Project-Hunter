from __future__ import annotations

from statistics import mean

from hunter.intelligence.engines.narrative.clustering import NarrativeClusterer
from hunter.intelligence.engines.narrative.evolution import NarrativeEvolutionTracker
from hunter.intelligence.engines.narrative.lifecycle import NarrativeLifecycleModel
from hunter.intelligence.engines.narrative.models import (
    Narrative,
    NarrativeAnalysis,
    NarrativeCluster,
    NarrativeDataset,
    NarrativeEvent,
    NarrativeLifecycle,
    NarrativeRelationship,
    NarrativeSignal,
    NarrativeTrend,
)


class NarrativeAnalyzer:
    def __init__(
        self,
        *,
        clusterer: NarrativeClusterer | None = None,
        evolution: NarrativeEvolutionTracker | None = None,
        lifecycle: NarrativeLifecycleModel | None = None,
    ) -> None:
        self._clusterer = clusterer or NarrativeClusterer()
        self._evolution = evolution or NarrativeEvolutionTracker()
        self._lifecycle = lifecycle or NarrativeLifecycleModel()

    def analyze(self, dataset: NarrativeDataset) -> NarrativeAnalysis:
        clusters = self._clusterer.cluster(dataset)
        trends = self._evolution.trends(clusters)
        lifecycles = self._lifecycle.assign(trends)
        narratives = tuple(_narrative(cluster) for cluster in clusters)
        signals = tuple(_signal(trend, lifecycle) for trend, lifecycle in zip(trends, lifecycles, strict=True))
        events = tuple(_event(signal, cluster) for signal, cluster in zip(signals, clusters, strict=True))
        relationships = _relationships(clusters, lifecycles)
        strengths = tuple(sorted({lifecycle.category for lifecycle in lifecycles if lifecycle.phase in {"acceleration", "expansion"}}))
        risks = tuple(sorted({lifecycle.category for lifecycle in lifecycles if lifecycle.phase in {"crowded", "saturation", "decline"}}))
        return NarrativeAnalysis(
            narratives=narratives,
            clusters=clusters,
            signals=signals,
            trends=trends,
            events=events,
            lifecycles=lifecycles,
            relationships=relationships,
            strengths=strengths,
            risks=risks,
            missing_evidence=dataset.missing_fields,
            metadata={
                "duplicate_count": str(len(dataset.duplicates)),
                "filtered_count": str(len(dataset.filtered)),
                "cluster_count": str(len(clusters)),
            },
        )


def _narrative(cluster: NarrativeCluster) -> Narrative:
    first = min((item.timestamp for item in cluster.evidence), default=None)
    return Narrative(
        id=cluster.id.replace("narrative-cluster", "narrative", 1),
        category=cluster.category,
        name=cluster.category.replace("_", " ").title(),
        description=f"{cluster.category} narrative cluster with {len(cluster.evidence)} evidence records.",
        created_at=first,
        evidence_ids=tuple(item.id for item in cluster.evidence),
    )


def _signal(trend: NarrativeTrend, lifecycle: NarrativeLifecycle) -> NarrativeSignal:
    strength = mean((trend.growth, trend.acceleration, 1.0 - trend.saturation if lifecycle.phase in {"crowded", "saturation"} else trend.persistence))
    return NarrativeSignal(
        narrative_id=trend.narrative_id,
        category=trend.category,
        signal_type=lifecycle.phase,
        strength=round(strength, 4),
        confidence=round(mean((trend.growth, trend.persistence, 1.0 - (0.2 if trend.ignored else 0.0))), 4),
        timestamp=_now_from_trend(trend),
        metadata={"reason": lifecycle.reason},
    )


def _event(signal: NarrativeSignal, cluster: NarrativeCluster) -> NarrativeEvent:
    return NarrativeEvent(
        id=f"narrative-event-{cluster.category}-{signal.signal_type}",
        narrative_id=signal.narrative_id,
        event_type=signal.signal_type,
        timestamp=signal.timestamp,
        strength=signal.strength,
        confidence=signal.confidence,
        evidence_ids=tuple(item.id for item in cluster.evidence),
    )


def _relationships(
    clusters: tuple[NarrativeCluster, ...],
    lifecycles: tuple[NarrativeLifecycle, ...],
) -> tuple[NarrativeRelationship, ...]:
    relationships: list[NarrativeRelationship] = []
    by_category = {lifecycle.category: lifecycle for lifecycle in lifecycles}
    complements = {
        "ai": ("depin", "data_availability", "infrastructure"),
        "bitcoinfi": ("layer_2", "defi"),
        "restaking": ("modular_chains", "data_availability"),
        "tokenization": ("rwa", "stablecoins"),
        "cross_chain": ("interoperability",),
    }
    for cluster in clusters:
        narrative_id = cluster.id.replace("narrative-cluster", "narrative", 1)
        if cluster.parent:
            relationships.append(NarrativeRelationship(cluster.parent, narrative_id, "parent", 0.8))
        for child in cluster.children:
            relationships.append(NarrativeRelationship(narrative_id, child, "child", 0.8))
        for target in complements.get(cluster.category, ()):
            if target in by_category:
                relationships.append(NarrativeRelationship(f"narrative-{cluster.category}", f"narrative-{target}", "complementary", 0.7))
    if "layer_1" in by_category and "layer_2" in by_category:
        relationships.append(NarrativeRelationship("narrative-layer_1", "narrative-layer_2", "competing", 0.6))
    if "rollups" in by_category and "modular_chains" in by_category:
        relationships.append(NarrativeRelationship("narrative-rollups", "narrative-modular_chains", "successor", 0.55))
    return tuple(sorted(relationships, key=lambda item: (item.source_narrative_id, item.target_narrative_id, item.relationship_type)))


def _now_from_trend(trend: NarrativeTrend):
    from datetime import UTC, datetime

    return datetime.now(UTC)
