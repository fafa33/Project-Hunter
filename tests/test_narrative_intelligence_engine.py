from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from hunter.intelligence import Intelligence
from hunter.intelligence.engines.narrative import NarrativeEvidence, NarrativeIntelligenceEngine, create_plugin
from hunter.intelligence.engines.narrative.analyzers import NarrativeAnalyzer
from hunter.intelligence.engines.narrative.clustering import NarrativeClusterer
from hunter.intelligence.engines.narrative.collectors import ContextNarrativeCollector
from hunter.intelligence.engines.narrative.confidence import NarrativeConfidenceModel
from hunter.intelligence.engines.narrative.configuration import NarrativeEngineConfiguration
from hunter.intelligence.engines.narrative.exceptions import NarrativeValidationError
from hunter.intelligence.engines.narrative.lifecycle import NarrativeLifecycleModel
from hunter.intelligence.engines.narrative.normalization import NarrativeNormalizer
from hunter.pipeline import PipelineOrchestrator
from hunter.plugins.contracts import PipelineContext
from hunter.plugins.manager import PluginManager

NOW = datetime(2026, 7, 10, tzinfo=UTC)


def evidence(
    evidence_id: str,
    category: str,
    days_ago: int,
    *,
    strength: float = 0.8,
    reliability: float = 0.9,
    engine: str = "news",
    source: str = "fixture",
    institutional: bool = False,
    retail: bool = False,
    promotional: bool = False,
    spam: bool = False,
    duplicate_key: str = "",
) -> NarrativeEvidence:
    return NarrativeEvidence(
        id=evidence_id,
        category=category,
        source=source,
        timestamp=NOW - timedelta(days=days_ago),
        reliability=reliability,
        strength=strength,
        text=f"{category} narrative evidence {evidence_id}",
        engine=engine,
        project="global-crypto",
        reference=f"https://example.test/{evidence_id}",
        institutional=institutional,
        retail=retail,
        promotional=promotional,
        spam=spam,
        duplicate_key=duplicate_key,
    )


def narrative_records() -> tuple[NarrativeEvidence, ...]:
    return (
        evidence("ai-old", "ai", 90, strength=0.35, engine="news", institutional=True),
        evidence("ai-1", "ai", 4, strength=0.9, engine="developer", institutional=True),
        evidence("ai-2", "ai", 3, strength=0.85, engine="protocol", retail=True),
        evidence("ai-3", "ai", 2, strength=0.8, engine="whale", retail=True),
        evidence("ai-4", "ai", 1, strength=0.75, engine="macro", institutional=True),
        evidence("depin-old", "depin", 120, strength=0.6, engine="news"),
        evidence("depin-new", "depin", 5, strength=0.7, engine="developer"),
        evidence("data-availability", "data_availability", 3, strength=0.7, engine="protocol"),
        evidence("layer1", "layer_1", 3, strength=0.65, engine="news"),
        evidence("layer2", "layer_2", 3, strength=0.65, engine="news"),
    )


def dataset():
    return NarrativeNormalizer().normalize(narrative_records())


def test_context_collector_reads_replaceable_narrative_inputs() -> None:
    context = PipelineContext(values={"narrative_records": list(narrative_records())})

    collected = ContextNarrativeCollector().collect(context)

    assert len(collected) == len(narrative_records())
    assert isinstance(collected[0], NarrativeEvidence)


def test_canonical_models_reject_invalid_evidence() -> None:
    with pytest.raises(NarrativeValidationError):
        NarrativeEvidence(
            id="",
            category="ai",
            source="fixture",
            timestamp=NOW,
            reliability=0.9,
            strength=0.8,
            text="",
        )


def test_normalization_filters_noise_and_suppresses_duplicates() -> None:
    duplicate = evidence("ai-duplicate", "ai", 1, duplicate_key="same")
    duplicate_lower_quality = evidence("ai-duplicate-low", "ai", 1, reliability=0.5, duplicate_key="same")
    spam = evidence("spam", "ai", 1, spam=True)
    unsupported = evidence("bad-category", "unsupported", 1)

    normalized = NarrativeNormalizer().normalize(
        (*narrative_records(), duplicate, duplicate_lower_quality, spam, unsupported)
    )

    assert "ai-duplicate-low" in normalized.duplicates
    assert "spam" in normalized.filtered
    assert "bad-category" in normalized.filtered
    assert any(item.id == "ai-duplicate" for item in normalized.evidence)


def test_clustering_groups_related_evidence_and_builds_hierarchy() -> None:
    clusters = NarrativeClusterer().cluster(dataset())

    cluster_ids = {cluster.id for cluster in clusters}
    assert "narrative-cluster-ai" in cluster_ids
    assert "narrative-cluster-ai-institutional" in cluster_ids
    assert "narrative-cluster-ai-retail" in cluster_ids


def test_lifecycle_transitions_detect_acceleration_and_saturation() -> None:
    analysis = NarrativeAnalyzer().analyze(dataset())
    phases = {lifecycle.category: lifecycle.phase for lifecycle in analysis.lifecycles}

    assert phases["ai"] in {"crowded", "saturation", "acceleration"}
    assert phases["depin"] in {"expansion", "early_expansion", "acceleration"}


def test_relationship_detection_finds_parent_competing_and_complementary_links() -> None:
    analysis = NarrativeAnalyzer().analyze(dataset())
    relationships = {
        (item.source_narrative_id, item.target_narrative_id, item.relationship_type) for item in analysis.relationships
    }

    assert ("narrative-ai", "narrative-depin", "complementary") in relationships
    assert ("narrative-layer_1", "narrative-layer_2", "competing") in relationships
    assert any(item[2] == "child" for item in relationships)


def test_confidence_uses_cross_engine_agreement_diversity_and_persistence() -> None:
    sparse = NarrativeNormalizer().normalize((evidence("single", "ai", 1, engine="news"),))
    rich = dataset()

    sparse_confidence = NarrativeConfidenceModel().calculate(sparse)
    rich_confidence = NarrativeConfidenceModel().calculate(rich)

    assert rich_confidence.score > sparse_confidence.score
    assert rich_confidence.uncertainty < sparse_confidence.uncertainty


def test_lifecycle_model_marks_ignored_low_evidence_narrative() -> None:
    config = NarrativeEngineConfiguration(emerging_threshold=0.3, minimum_evidence_quality=0.0)
    sparse = NarrativeNormalizer(config).normalize((evidence("ignored", "privacy", 2, strength=0.2),))
    analysis = NarrativeAnalyzer(lifecycle=NarrativeLifecycleModel(config)).analyze(sparse)

    assert analysis.lifecycles[0].phase == "emerging"


def test_narrative_engine_generates_standardized_intelligence() -> None:
    context = PipelineContext(values={"narrative_records": list(narrative_records())})
    engine = NarrativeIntelligenceEngine()

    collected = engine.collect(context)
    analysis = engine.analyze(context, collected)
    intelligence = engine.generate_intelligence(context, analysis)

    assert isinstance(intelligence, Intelligence)
    assert intelligence.engine == "narrative-intelligence"
    assert intelligence.signals
    assert intelligence.evidence
    assert intelligence.observations
    assert intelligence.insights
    assert "ai" in intelligence.metadata.get("lifecycle", "")


def test_narrative_plugin_registration_exposes_plugin_contract() -> None:
    plugin = create_plugin()

    assert plugin.metadata.id == "narrative-intelligence"
    assert "narrative-intelligence" in plugin.metadata.capabilities


def test_plugin_manager_loads_narrative_plugin_from_configuration(tmp_path) -> None:
    config = tmp_path / "plugins.yaml"
    config.write_text(
        """
enabled:
  narrative-intelligence: true
configuration: {}
load_order:
  - narrative-intelligence
priorities: {}
module_paths:
  - hunter.intelligence.engines.narrative:create_plugin
""",
        encoding="utf-8",
    )

    loaded = PluginManager().load(config_path=config)

    assert [plugin.metadata.id for plugin in loaded] == ["narrative-intelligence"]


def test_pipeline_executes_narrative_engine_plugin(tmp_path) -> None:
    config = tmp_path / "plugins.yaml"
    config.write_text(
        """
enabled:
  narrative-intelligence: true
configuration: {}
load_order:
  - narrative-intelligence
priorities: {}
module_paths:
  - hunter.intelligence.engines.narrative:create_plugin
""",
        encoding="utf-8",
    )
    context = PipelineContext(values={"narrative_records": list(narrative_records())})

    result = PipelineOrchestrator().run(context=context, config_path=config)

    assert len(result.intelligence) == 1
    assert result.intelligence[0].engine == "narrative-intelligence"
    assert "narrative:intelligence:execute" in result.events
