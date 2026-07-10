from __future__ import annotations

from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime

import pytest

from hunter.intelligence import Confidence, Evidence, Insight, Intelligence, Observation, Signal
from hunter.intelligence.fusion import (
    CrossEngineFusionEngine,
    FrozenScalarMap,
    FusionConfig,
    FusionTarget,
    FusionWeightingConfig,
)
from hunter.intelligence.fusion.deduplication import canonicalize_evidence, deduplicate_evidence
from hunter.intelligence.fusion.engine import fused_intelligence_to_record
from hunter.intelligence.fusion.normalization import normalize_fusion_inputs
from hunter.persistence.integration.adapter import PipelinePersistenceAdapter
from hunter.persistence.integration.policies import PipelinePersistenceSettings
from hunter.persistence.records import IntelligenceRecord
from hunter.persistence.sql import SessionFactory, UnitOfWork, create_schema, create_sqlite_engine
from hunter.pipeline import PipelineOrchestrator
from hunter.plugins.contracts import PipelineContext

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def test_normalization_preserves_provenance_and_does_not_mutate_sources() -> None:
    intelligence = _intelligence("macro-engine", "1.0.0", "plugin-macro", "1.2.0", category="macro", strength=0.8)
    before = deepcopy(intelligence)

    inputs = normalize_fusion_inputs((intelligence,))

    assert inputs[0].intelligence_id == intelligence.id
    assert inputs[0].engine_id == "macro-engine"
    assert inputs[0].engine_version == "1.0.0"
    assert inputs[0].plugin_id == "plugin-macro"
    assert inputs[0].plugin_version == "1.2.0"
    assert inputs[0].run_id == "run-1"
    assert inputs[0].evidence_references == ("shared-ref",)
    assert intelligence == before


def test_deduplication_dependency_corroboration_and_contradiction_are_deterministic() -> None:
    left = _intelligence("macro-engine", "1.0.0", "plugin-macro", "1.0.0", category="macro", strength=0.9)
    right = _intelligence("news-engine", "2.0.0", "plugin-news", "2.0.0", category="macro", strength=0.2)
    target = FusionTarget(target_type="project", target_id="project-a")
    fused = CrossEngineFusionEngine().fuse((right, left), target)
    fused_again = CrossEngineFusionEngine().fuse((left, right), target)

    assert fused.id == fused_again.id
    assert len(deduplicate_evidence(normalize_fusion_inputs((left, right)))) == 1
    assert fused.dependencies.dependency_edges == (("macro-engine", "news-engine", "shared-evidence-reference"),)
    assert fused.dependencies.penalty == 0.2
    assert fused.corroboration.score == 0.0
    assert fused.contradictions.contradicted_categories == ("macro",)
    assert fused.contradictions.severity == pytest.approx(0.504)


def test_independent_corroboration_weighting_missing_evidence_and_confidence() -> None:
    left = _intelligence("macro-engine", "1.0.0", "plugin-macro", "1.0.0", category="macro", strength=0.75, reference="ref-a")
    right = _intelligence("news-engine", "2.0.0", "plugin-news", "2.0.0", category="macro", strength=0.7, reference="ref-b")
    config = FusionConfig(
        required_categories=("macro", "developer"),
        weighting=FusionWeightingConfig(engine_weights={"news-engine": 0.5}),
    )

    fused = CrossEngineFusionEngine(config).fuse((left, right), FusionTarget(target_type="project", target_id="project-a"))

    assert fused.corroboration.corroborated_categories == ("macro",)
    assert fused.missing_evidence.missing_categories == ("developer",)
    assert [item.engine_id for item in fused.contributions] == ["macro-engine", "news-engine"]
    assert fused.contributions[1].weight < fused.contributions[0].weight
    assert 0.0 < fused.confidence["score"] <= 1.0
    assert fused.confidence["missing_evidence_penalty"] > 0.0


def test_unified_narrative_and_graph_preserve_links() -> None:
    intelligence = _intelligence("protocol-engine", "1.0.0", "plugin-protocol", "1.0.0", category="protocol", strength=0.8)

    fused = CrossEngineFusionEngine().fuse((intelligence,), FusionTarget(target_type="project", target_id="project-a"))

    assert fused.narrative.summary.startswith("Fusion for project:project-a")
    assert fused.narrative.key_points[0].startswith("Strongest corroboration:")
    assert fused.narrative.key_points[1].startswith("Strongest contradiction:")
    assert fused.signals[0].source_signal_ids == ("signal-protocol-engine",)
    assert fused.observations[0].source_observation_ids == ("observation-protocol-engine",)
    assert fused.insights[0].source_insight_ids == ("insight-protocol-engine",)
    node_types = {node.node_type for node in fused.graph_nodes}
    edge_types = {edge.edge_type for edge in fused.graph_edges}
    assert {"target", "engine", "intelligence", "evidence", "fused_intelligence"}.issubset(node_types)
    assert {"emitted", "supports", "contributes_to", "fuses_target"}.issubset(edge_types)


def test_fusion_supports_persisted_intelligence_records_and_future_engines() -> None:
    record = IntelligenceRecord(
        id="intelligence-future",
        created_at=NOW,
        effective_at=NOW,
        pipeline_run_id="run-future",
        project="project-a",
        engine_id="future-engine",
        generated_at=NOW,
        signal_ids=("signal-future",),
        evidence_ids=("evidence-future",),
        observation_ids=("observation-future",),
        insight_ids=("insight-future",),
        confidence={"score": 0.8},
        metadata={"engine_version": "9.0.0", "plugin_id": "plugin-future", "plugin_version": "9.1.0"},
        target_refs=(("project", "project-a"),),
        evidence_references=("future://evidence",),
        evidence_reliabilities=(0.8,),
        evidence_freshness=(0.8,),
        signal_categories=("future",),
        signal_strengths=(0.8,),
        signal_confidences=(0.8,),
        signal_severities=(0.8,),
        observation_descriptions=("future observation",),
        insight_titles=("future insight",),
        insight_explanations=("future explanation",),
    )

    fused = CrossEngineFusionEngine().fuse((record,), FusionTarget(target_type="project", target_id="project-a"))

    assert fused.source_intelligence_ids == ("intelligence-future",)
    assert fused.contributions[0].engine_id == "future-engine"
    assert fused.contributions[0].engine_version == "9.0.0"
    assert fused.signals[0].category == "future"


def test_fused_record_persistence_is_idempotent() -> None:
    engine = create_sqlite_engine(":memory:")
    create_schema(engine)
    session_factory = SessionFactory(engine)
    fused = CrossEngineFusionEngine().fuse(
        (_intelligence("macro-engine", "1.0.0", "plugin-macro", "1.0.0", category="macro", strength=0.8),),
        FusionTarget(target_type="project", target_id="project-a"),
    )
    record = fused_intelligence_to_record(fused, pipeline_run_id="run-1", created_at=NOW)

    with UnitOfWork(session_factory) as uow:
        repositories = uow.repositories
        assert repositories is not None
        first = repositories.fused_intelligence().save(record)
        second = repositories.fused_intelligence().save(record)
        later = replace(record, created_at=datetime(2026, 1, 2, tzinfo=UTC))
        third = repositories.fused_intelligence().save(later)

    assert first == second
    assert third == first
    with UnitOfWork(session_factory) as uow:
        repositories = uow.repositories
        assert repositories is not None
        assert repositories.fused_intelligence().load(fused.id) == record


def test_pipeline_integration_is_optional_and_persists_fused_outputs() -> None:
    engine = create_sqlite_engine(":memory:")
    create_schema(engine)
    session_factory = SessionFactory(engine)
    adapter = PipelinePersistenceAdapter(
        lambda: UnitOfWork(session_factory),
        settings=PipelinePersistenceSettings(enabled=True, enforce_engine_manifest=False),
    )
    context = PipelineContext()
    context.emit_intelligence(
        _intelligence("macro-engine", "1.0.0", "plugin-macro", "1.0.0", category="macro", strength=0.8)
    )

    result = PipelineOrchestrator().run(
        context,
        persistence_adapter=adapter,
        fusion_engine=CrossEngineFusionEngine(),
        fusion_target=FusionTarget(target_type="project", target_id="project-a"),
    )

    assert len(result.fused_intelligence) == 1
    with UnitOfWork(session_factory) as uow:
        repositories = uow.repositories
        assert repositories is not None
        records = repositories.fused_intelligence().load_many((result.fused_intelligence[0].id,))
    assert len(records) == 1


def test_pipeline_without_fusion_remains_unchanged() -> None:
    context = PipelineContext()
    PipelineOrchestrator().run(context)

    assert context.fused_intelligence == []


def test_operational_timestamps_do_not_change_fusion_identity() -> None:
    intelligence = _intelligence("macro-engine", "1.0.0", "plugin-macro", "1.0.0", category="macro", strength=0.8)
    target = FusionTarget(target_type="project", target_id="project-a")
    first = CrossEngineFusionEngine().fuse((intelligence,), target)
    second = replace(first, created_at=datetime(2026, 1, 2, tzinfo=UTC))

    assert first.id == second.id


def test_rich_fused_record_round_trip_preserves_explainability() -> None:
    fused = CrossEngineFusionEngine(
        FusionConfig(required_categories=("macro", "developer"))
    ).fuse(
        (
            _intelligence("macro-engine", "1.0.0", "plugin-macro", "1.0.0", category="macro", strength=0.8),
            _intelligence("news-engine", "1.0.0", "plugin-news", "1.0.0", category="macro", strength=0.7, reference="other-ref"),
        ),
        FusionTarget(target_type="project", target_id="project-a"),
    )
    record = fused_intelligence_to_record(fused, pipeline_run_id="run-1", created_at=NOW)

    from hunter.persistence import record_from_json, record_to_json

    restored = record_from_json(record_to_json(record))

    assert restored == record
    assert record.contributions
    assert record.corroboration["corroborated_categories"] == ("macro",)
    assert record.missing_evidence["missing_categories"] == ("developer",)
    assert record.unified_signals
    assert record.unified_observations
    assert record.unified_insights
    assert record.unified_narrative["key_points"]
    assert record.graph_nodes
    assert record.graph_edges
    assert record.configuration_fingerprint
    assert record.contribution_model_fingerprint


def test_live_object_and_persisted_record_fusion_parity() -> None:
    live = _intelligence("macro-engine", "1.0.0", "plugin-macro", "1.0.0", category="macro", strength=0.8, reference="ref-a")
    record = IntelligenceRecord(
        id=live.id,
        created_at=NOW,
        effective_at=NOW,
        pipeline_run_id="run-1",
        project=live.project,
        engine_id=live.engine,
        generated_at=live.generated_at,
        signal_ids=tuple(signal.id for signal in live.signals),
        evidence_ids=tuple(evidence.id for evidence in live.evidence),
        observation_ids=tuple(observation.id for observation in live.observations),
        insight_ids=tuple(insight.id for insight in live.insights),
        confidence={"score": live.confidence.score},
        engine_version="1.0.0",
        plugin_id="plugin-macro",
        plugin_version="1.0.0",
        target_refs=(("project", "project-a"),),
        evidence_references=("ref-a",),
        evidence_lineage_keys=("lineage-macro-engine",),
        evidence_reliabilities=(0.9,),
        evidence_freshness=(0.9,),
        signal_categories=("macro",),
        signal_strengths=(0.8,),
        signal_confidences=(0.8,),
        signal_severities=(0.8,),
        observation_descriptions=("macro-engine observation",),
        insight_titles=("macro-engine insight",),
        insight_explanations=("macro-engine explanation",),
    )
    target = FusionTarget(target_type="project", target_id="project-a")

    live_fused = CrossEngineFusionEngine().fuse((live,), target)
    record_fused = CrossEngineFusionEngine().fuse((record,), target)

    assert live_fused.id == record_fused.id
    assert live_fused.signals == record_fused.signals
    assert live_fused.narrative == record_fused.narrative


def test_configuration_and_contribution_model_affect_fused_identity() -> None:
    intelligence = _intelligence("macro-engine", "1.0.0", "plugin-macro", "1.0.0", category="macro", strength=0.8)
    target = FusionTarget(target_type="project", target_id="project-a")

    first = CrossEngineFusionEngine(FusionConfig(required_categories=("macro",))).fuse((intelligence,), target)
    second = CrossEngineFusionEngine(FusionConfig(required_categories=("developer",))).fuse((intelligence,), target)
    third = CrossEngineFusionEngine(
        FusionConfig(weighting=FusionWeightingConfig(engine_weights={"macro-engine": 0.5}))
    ).fuse((intelligence,), target)

    assert first.id != second.id
    assert first.id != third.id


def test_target_alignment_for_all_supported_target_types() -> None:
    for target_type in ("project", "asset", "protocol", "chain", "sector", "narrative", "ecosystem"):
        target_id = "project-a" if target_type == "project" else f"{target_type}-a"
        intelligence = _intelligence(
            "macro-engine",
            "1.0.0",
            "plugin-macro",
            "1.0.0",
            category="macro",
            strength=0.8,
            target_refs={target_type: target_id},
        )

        fused = CrossEngineFusionEngine().fuse((intelligence,), FusionTarget(target_type=target_type, target_id=target_id))
        filtered = CrossEngineFusionEngine().fuse((intelligence,), FusionTarget(target_type=target_type, target_id="other"))

        assert fused.source_intelligence_ids == (intelligence.id,)
        assert filtered.source_intelligence_ids == ()


def test_canonical_evidence_deduplication_preserves_lineage_provenance() -> None:
    left = _intelligence("macro-engine", "1.0.0", "plugin-macro", "1.0.0", category="macro", strength=0.8, reference="ref-a")
    right = _intelligence("news-engine", "1.0.0", "plugin-news", "1.0.0", category="macro", strength=0.7, reference="ref-b", lineage_key="shared-lineage")
    left = _with_lineage(left, "shared-lineage")

    canonical = canonicalize_evidence(normalize_fusion_inputs((left, right)))

    assert len(canonical) == 1
    assert canonical[0].lineage_keys == ("shared-lineage",)
    assert canonical[0].source_intelligence_ids == ("intelligence-macro-engine", "intelligence-news-engine")


def test_fusion_models_are_deeply_immutable() -> None:
    fused = CrossEngineFusionEngine().fuse(
        (_intelligence("macro-engine", "1.0.0", "plugin-macro", "1.0.0", category="macro", strength=0.8),),
        FusionTarget(target_type="project", target_id="project-a"),
    )

    with pytest.raises(FrozenInstanceError):
        fused.id = "changed"  # type: ignore[misc]
    assert isinstance(fused.metadata, FrozenScalarMap)
    with pytest.raises(TypeError):
        fused.metadata["x"] = "y"  # type: ignore[index]
    with pytest.raises(TypeError):
        fused.confidence["score"] = 0.1  # type: ignore[index]


def test_improved_corroboration_and_contradiction_semantics() -> None:
    positive = _intelligence("macro-engine", "1.0.0", "plugin-macro", "1.0.0", category="macro", strength=0.85, reference="ref-a")
    positive_peer = _intelligence("news-engine", "1.0.0", "plugin-news", "1.0.0", category="macro", strength=0.8, reference="ref-b")
    negative = _intelligence("social-engine", "1.0.0", "plugin-social", "1.0.0", category="macro", strength=0.1, reference="ref-c")

    fused = CrossEngineFusionEngine().fuse(
        (positive, positive_peer, negative),
        FusionTarget(target_type="project", target_id="project-a"),
    )

    assert fused.corroboration.corroborated_categories == ("macro",)
    assert fused.corroboration.score > 0.0
    assert fused.contradictions.contradicted_categories == ("macro",)
    assert fused.contradictions.severity > 0.0


def _intelligence(
    engine_id: str,
    engine_version: str,
    plugin_id: str,
    plugin_version: str,
    *,
    category: str,
    strength: float,
    reference: str = "shared-ref",
    lineage_key: str | None = None,
    target_refs: dict[str, str] | None = None,
) -> Intelligence:
    evidence = Evidence(
        id=f"evidence-{engine_id}",
        source=engine_id,
        collected_at=NOW,
        reliability=0.9,
        freshness=0.9,
        reference=reference,
        raw_data={"value": engine_id},
        metadata={"lineage_key": lineage_key or f"lineage-{engine_id}"},
    )
    signal = Signal(
        id=f"signal-{engine_id}",
        source=engine_id,
        timestamp=NOW,
        category=category,
        strength=strength,
        confidence=0.8,
        severity=strength,
    )
    observation = Observation(
        id=f"observation-{engine_id}",
        engine=engine_id,
        project="project-a",
        description=f"{engine_id} observation",
        evidence=(evidence,),
        importance=0.8,
    )
    insight = Insight(
        id=f"insight-{engine_id}",
        title=f"{engine_id} insight",
        explanation=f"{engine_id} explanation",
        supporting_observations=(observation,),
        confidence=0.8,
        priority=0.8,
    )
    return Intelligence(
        id=f"intelligence-{engine_id}",
        project="project-a",
        engine=engine_id,
        signals=(signal,),
        evidence=(evidence,),
        observations=(observation,),
        insights=(insight,),
        confidence=Confidence(score=0.8, completeness=0.8, evidence_quality=0.8, freshness=0.8, uncertainty=0.2),
        generated_at=NOW,
        metadata={
            "pipeline_run_id": "run-1",
            "engine_version": engine_version,
            "plugin_id": plugin_id,
            "plugin_version": plugin_version,
            **{f"{key}_id": value for key, value in (target_refs or {}).items() if key != "project"},
            **({"target_type": "project", "target_id": target_refs["project"]} if target_refs and "project" in target_refs else {}),
        },
    )


def _with_lineage(intelligence: Intelligence, lineage_key: str) -> Intelligence:
    evidence = tuple(replace(evidence, metadata={"lineage_key": lineage_key}) for evidence in intelligence.evidence)
    observations = tuple(replace(observation, evidence=evidence) for observation in intelligence.observations)
    insights = tuple(replace(insight, supporting_observations=observations) for insight in intelligence.insights)
    return replace(intelligence, evidence=evidence, observations=observations, insights=insights)
