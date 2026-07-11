from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta

import pytest

from hunter.intelligence import Confidence, Evidence, Insight, Intelligence, Observation, Signal
from hunter.intelligence.fusion import CrossEngineFusionEngine
from hunter.intelligence.fusion.models import FusionTarget
from hunter.opportunity import (
    OpportunityTimingConfig,
    OpportunityTimingEngine,
    opportunity_assessment_to_record,
    opportunity_snapshot_from_assessment,
)
from hunter.opportunity.acceleration import assess_acceleration
from hunter.opportunity.divergence import assess_divergence
from hunter.opportunity.exceptions import InsufficientFusionInputError
from hunter.opportunity.phases import classify_phase
from hunter.opportunity.temporal import analyze_temporal
from hunter.opportunity.windows import classify_window
from hunter.persistence import record_from_json, record_to_json
from hunter.persistence.integration.adapter import PipelinePersistenceAdapter
from hunter.persistence.integration.policies import PipelinePersistenceSettings
from hunter.persistence.sql import UnitOfWork, create_schema, create_sqlite_engine
from hunter.persistence.sql.session import SessionFactory
from hunter.pipeline import PipelineOrchestrator
from hunter.plugins.contracts import PipelineContext

NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
TARGET = FusionTarget(target_type="project", target_id="project-a")


def test_opportunity_models_are_canonical_and_immutable() -> None:
    assessment = OpportunityTimingEngine().assess((_fused(0.7, 0), _fused(0.8, 1)), TARGET)

    assert assessment.source_run_ids == ("run-0", "run-1")
    assert assessment.source_fused_intelligence_ids == ("fused-0", "fused-1")
    with pytest.raises(FrozenInstanceError):
        assessment.assessment_id = "changed"  # type: ignore[misc]
    with pytest.raises(TypeError):
        assessment.metadata["x"] = "y"  # type: ignore[index]


def test_phase_and_window_classification() -> None:
    assessment = OpportunityTimingEngine().assess((_fused(0.55, 0), _fused(0.75, 1), _fused(0.85, 2)), TARGET)

    assert assessment.opportunity_phase in {"early_entry", "confirmed_entry", "expansion", "mature"}
    assert assessment.opportunity_window in {"opening", "open", "strengthening"}
    assert classify_phase(10, assessment.confirmation_state, assessment.acceleration_state, assessment.risk_state, analyze_temporal((_fused(0.1, 0),), required_depth=3)) == "too_early"
    assert classify_window(10, "too_early", assessment.risk_state, analyze_temporal((_fused(0.1, 0),), required_depth=3)) == "closed"


def test_temporal_comparison_acceleration_deterioration_and_reversal() -> None:
    records = (_fused(0.7, 0), _fused(0.5, 1), _fused(0.65, 2))
    temporal = analyze_temporal(records, required_depth=3)
    acceleration = assess_acceleration(records)

    assert temporal.reversal is True
    assert acceleration.state in {"reversal", "positive_acceleration"}
    deteriorating = analyze_temporal((_fused(0.8, 0), _fused(0.5, 1)), required_depth=2)
    assert deteriorating.deterioration is True


def test_confirmation_divergence_risk_confidence_score_horizon_and_invalidation() -> None:
    records = (
        _fused(0.72, 0, categories=("social",), strengths=(0.9,)),
        _fused(0.74, 1, categories=("protocol", "developer"), strengths=(0.3, 0.3)),
        _fused(0.82, 2, categories=("narrative",), strengths=(0.8,)),
    )
    assessment = OpportunityTimingEngine().assess(records, TARGET)
    divergence = assess_divergence(records)

    assert divergence.divergences
    assert 0 <= assessment.timing_score <= 100
    assert "score" in assessment.confidence
    assert assessment.risk_state.risks
    assert assessment.canonical_evidence_refs
    assert assessment.expected_horizon in {"weeks", "1-3 months", "3-6 months", "6-12 months", "12-24 months", "indeterminate"}
    assert assessment.invalidation_conditions


def test_false_start_and_historical_phase_transitions() -> None:
    previous = opportunity_assessment_to_record(
        OpportunityTimingEngine().assess((_fused(0.45, 0), _fused(0.65, 1)), TARGET),
        pipeline_run_id="run-pipeline",
        created_at=NOW,
    )
    current = OpportunityTimingEngine().assess((_fused(0.45, 0), _fused(0.65, 1), _fused(0.3, 2)), TARGET, historical_snapshots=(previous,))

    assert current.historical_comparisons[0].prior_phases
    assert current.historical_comparisons[0].similarity_summary


def test_deterministic_and_configuration_sensitive_identity() -> None:
    records = (_fused(0.6, 0), _fused(0.7, 1), _fused(0.8, 2))
    first = OpportunityTimingEngine().assess(records, TARGET)
    second = OpportunityTimingEngine().assess(tuple(reversed(records)), TARGET)
    changed = OpportunityTimingEngine(OpportunityTimingConfig(required_historical_depth=5)).assess(records, TARGET)

    assert first.assessment_id == second.assessment_id
    assert first.assessment_id != changed.assessment_id
    assert first.assessment_id == replace(first, metadata={"x": "y"}).assessment_id


def test_persistence_round_trip_and_repeated_run_idempotence() -> None:
    engine = create_sqlite_engine(":memory:")
    create_schema(engine)
    session_factory = SessionFactory(engine)
    assessment = OpportunityTimingEngine().assess((_fused(0.6, 0), _fused(0.75, 1), _fused(0.85, 2)), TARGET)
    record = opportunity_assessment_to_record(assessment, pipeline_run_id="run-pipeline", created_at=NOW)
    snapshot = opportunity_snapshot_from_assessment(assessment, created_at=NOW)

    assert record_from_json(record_to_json(record)) == record
    assert record_from_json(record_to_json(snapshot)) == snapshot
    with UnitOfWork(session_factory) as uow:
        repositories = uow.repositories
        assert repositories is not None
        first = repositories.opportunity_timing_assessments().save(record)
        second = repositories.opportunity_timing_assessments().save(record)
        third = repositories.opportunity_timing_assessments().save(replace(record, created_at=NOW + timedelta(days=1)))
    assert first == second == third


def test_pipeline_ordering_missing_fusion_inputs_and_optional_stage_behavior() -> None:
    context = PipelineContext()
    PipelineOrchestrator().run(context=context, opportunity_timing_engine=OpportunityTimingEngine(), fusion_target=TARGET)
    assert context.opportunity_timing == []

    context.set("persisted_fused_intelligence", (_fused(0.6, 0), _fused(0.7, 1), _fused(0.8, 2)))
    PipelineOrchestrator().run(context=context, opportunity_timing_engine=OpportunityTimingEngine(), fusion_target=TARGET)
    assert len(context.opportunity_timing) == 1

    with pytest.raises(InsufficientFusionInputError):
        OpportunityTimingEngine().assess((), TARGET)


def test_persistence_enabled_pipeline_runs_timing_after_fusion_records_are_available() -> None:
    engine = create_sqlite_engine(":memory:")
    create_schema(engine)
    session_factory = SessionFactory(engine)
    adapter = PipelinePersistenceAdapter(
        lambda: UnitOfWork(session_factory),
        settings=PipelinePersistenceSettings(enabled=True, enforce_engine_manifest=False),
    )
    context = PipelineContext()
    context.emit_intelligence(_intelligence("macro-engine", "macro", 0.75))
    context.emit_intelligence(_intelligence("developer-engine", "developer", 0.8))

    result = PipelineOrchestrator().run(
        context=context,
        persistence_adapter=adapter,
        fusion_engine=CrossEngineFusionEngine(),
        fusion_target=TARGET,
        opportunity_timing_engine=OpportunityTimingEngine(),
    )

    assert len(result.fused_intelligence) == 1
    assert len(result.opportunity_timing) == 1
    with UnitOfWork(session_factory) as uow:
        repositories = uow.repositories
        assert repositories is not None
        assert repositories.opportunity_timing_assessments().load(result.opportunity_timing[0].assessment_id) is not None


def test_end_to_end_historical_states_without_recommendation_language() -> None:
    engine = OpportunityTimingEngine()
    scenarios = {
        "too_early": (_fused(0.1, 0), _fused(0.15, 1)),
        "forming": (_fused(0.3, 0), _fused(0.45, 1)),
        "opening": (_fused(0.45, 0), _fused(0.55, 1), _fused(0.68, 2)),
        "confirmed": (_fused(0.55, 0), _fused(0.75, 1), _fused(0.9, 2)),
        "deterioration": (_fused(0.85, 0), _fused(0.6, 1), _fused(0.4, 2)),
        "invalidation": (_fused(0.9, 0, contradiction=1.0), _fused(0.2, 1, contradiction=1.0)),
    }
    outputs = [engine.assess(records, TARGET) for records in scenarios.values()]
    emitted = " ".join(str(item) for item in outputs).lower()

    assert {item.opportunity_phase for item in outputs}
    for banned in ("buy", "sell", "price target", "expected return", "portfolio allocation", "order execution"):
        assert banned not in emitted


def _fused(
    score: float,
    index: int,
    *,
    categories: tuple[str, ...] = ("macro", "developer"),
    strengths: tuple[float, ...] = (0.7, 0.75),
    contradiction: float = 0.0,
):
    from hunter.persistence.records import FusedIntelligenceRecord

    effective_at = NOW + timedelta(days=index)
    signals = tuple(
        {
            "id": f"signal-{index}-{category}",
            "category": category,
            "strength": strengths[pos] if pos < len(strengths) else score,
            "confidence": score,
            "severity": 0.2,
            "engine_ids": (f"engine-{category}",),
            "evidence_ids": (f"evidence-{index}-{category}",),
        }
        for pos, category in enumerate(categories)
    )
    groups = tuple(
        {
            "canonical_key": f"canonical-{index}-{category}",
            "evidence_ids": (f"evidence-{index}-{category}",),
            "references": (f"ref-{index}-{category}",),
            "lineage_keys": (f"lineage-{index}-{category}",),
            "source_intelligence_ids": (f"intelligence-{index}-{category}",),
            "engine_ids": (f"engine-{category}", f"peer-{category}"),
            "plugin_ids": (f"plugin-{category}",),
            "source_run_ids": (f"run-{index}",),
            "dependency_classification": "shared-evidence-lineage",
            "metadata": {"evidence_count": 1},
        }
        for category in categories
    )
    return FusedIntelligenceRecord(
        id=f"fused-{index}",
        created_at=effective_at,
        effective_at=effective_at,
        pipeline_run_id=f"pipeline-run-{index}",
        target_id="project-a",
        target_type="project",
        fusion_strategy="weighted-corroboration-v1",
        source_intelligence_ids=tuple(f"intelligence-{index}-{category}" for category in categories),
        confidence={"score": score},
        configuration_fingerprint="fusion-config",
        contribution_model_fingerprint="fusion-model",
        source_run_ids=(f"run-{index}",),
        effective_window=(effective_at.isoformat(), effective_at.isoformat()),
        canonical_evidence_groups=groups,
        contributions=tuple({"engine_id": f"engine-{category}", "weight": 1.0} for category in categories),
        corroboration={"corroborated_categories": categories, "score": score, "explanation": "deterministic corroboration"},
        contradictions={"contradicted_categories": ("macro",) if contradiction else (), "severity": contradiction, "explanation": "deterministic contradiction"},
        dependencies={"dependent_engine_ids": (), "dependency_edges": (), "penalty": 0.0, "explanation": "independent"},
        missing_evidence={"missing_categories": (), "severity": 0.0, "explanation": "complete"},
        unified_signals=signals,
        unified_observations=({"id": f"observation-{index}", "importance": score},),
        unified_insights=({"id": f"insight-{index}", "confidence": score},),
        unified_narrative={"summary": "deterministic assessment", "key_points": ("evidence-backed state",)},
        graph_nodes=({"id": f"node-{index}", "node_type": "fused_intelligence"},),
        graph_edges=({"id": f"edge-{index}", "edge_type": "contributes_to"},),
    )


def _intelligence(engine_id: str, category: str, strength: float) -> Intelligence:
    evidence = Evidence(
        id=f"evidence-{engine_id}",
        source=engine_id,
        collected_at=NOW,
        reliability=0.9,
        freshness=0.9,
        reference=f"ref-{engine_id}",
        raw_data={"value": engine_id},
        metadata={"lineage_key": f"lineage-{engine_id}"},
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
            "pipeline_run_id": "run-live",
            "engine_version": "1.0.0",
            "plugin_id": f"plugin-{category}",
            "plugin_version": "1.0.0",
        },
    )
