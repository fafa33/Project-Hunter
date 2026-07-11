from __future__ import annotations

from datetime import UTC, datetime

from hunter.cli import main
from hunter.necessity import (
    TechnologyGraphConfig,
    TechnologyNecessityEngine,
    TechnologyNecessityInputSet,
    TechnologyNecessityReportRenderer,
    load_capital_rotation_config,
    load_technology_graph_config,
    load_technology_necessity_config,
)
from hunter.necessity.ranking import rank_necessity_assessments
from hunter.persistence.records import (
    EvidenceRecord,
    FusedIntelligenceRecord,
    IntelligenceRecord,
    OpportunityTimingAssessmentRecord,
    SnapshotRecord,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def test_higher_future_demand_improves_technology_necessity() -> None:
    low = engine().assess(input_set(future_demand=0.2))
    high = engine().assess(input_set(future_demand=0.9))

    assert high.technology_necessity_score > low.technology_necessity_score


def test_higher_macro_alignment_improves_technology_necessity() -> None:
    low = engine().assess(input_set(macro=0.2))
    high = engine().assess(input_set(macro=0.9))

    assert high.technology_necessity_score > low.technology_necessity_score


def test_higher_dependency_strength_improves_necessity() -> None:
    weak = TechnologyNecessityEngine(
        graph_config=TechnologyGraphConfig(categories=("Oracle",), dependencies=())
    ).assess(input_set(technology_id="Oracle"))
    strong = engine().assess(input_set(technology_id="Oracle"))

    assert strong.dependency_strength > weak.dependency_strength
    assert strong.overall_necessity > weak.overall_necessity


def test_higher_capital_rotation_improves_ranking() -> None:
    low = engine().assess(input_set(technology_id="Storage", rotation=0.2))
    high = engine().assess(input_set(technology_id="Oracle", rotation=0.9))

    assert rank_necessity_assessments((low, high), sort="rotation")[0].technology_id == "Oracle"
    assert rank_necessity_assessments((low, high), sort="necessity")[0].technology_id in {"Oracle", "Storage"}
    assert rank_necessity_assessments((low, high), sort="gap")[0].technology_id in {"Oracle", "Storage"}
    assert rank_necessity_assessments((low, high), sort="dependency")[0].technology_id in {"Oracle", "Storage"}
    assert main(["rank", "--sort", "necessity"]) == 0
    assert main(["rank", "--sort", "gap"]) == 0
    assert main(["rank", "--sort", "rotation"]) == 0
    assert main(["rank", "--sort", "dependency"]) == 0


def test_missing_evidence_lowers_confidence() -> None:
    complete = engine().assess(input_set(missing=()))
    missing = engine().assess(input_set(missing=("future_demand", "capital_rotation")))

    assert missing.confidence < complete.confidence


def test_necessity_gap_is_deterministic() -> None:
    first = engine().assess(input_set(market_recognition=0.2))
    second = engine().assess(input_set(market_recognition=0.2))

    assert first.necessity_gap == second.necessity_gap
    assert first.assessment_id == second.assessment_id


def test_reports_contain_technology_necessity_sections() -> None:
    report = TechnologyNecessityReportRenderer().render_markdown(engine().assess(input_set()))

    assert "Technology Necessity" in report
    assert "Capital Rotation" in report
    assert "Necessity Gap" in report
    assert "Infrastructure Criticality" in report
    assert "Dependency Strength" in report
    assert "Replacement Difficulty" in report
    assert "Technology Position" in report
    assert "Supporting Evidence" in report
    assert "Missing Evidence" in report
    assert "Confidence" in report


def test_no_fabricated_evidence() -> None:
    assessment = engine().assess(input_set())

    assert assessment.supporting_evidence == ("evidence-1", "evidence-2")
    assert all("fabricated" not in item.lower() for item in assessment.supporting_evidence)


def test_necessity_configs_load() -> None:
    necessity = load_technology_necessity_config("configs/technology_necessity.yaml")
    rotation = load_capital_rotation_config("configs/capital_rotation.yaml")
    graph = load_technology_graph_config("configs/technology_graph.yaml")

    assert necessity.enabled is True
    assert dict(rotation.weights)["capital_entering"] > 0.0
    assert "Oracle" in graph.categories


def engine() -> TechnologyNecessityEngine:
    return TechnologyNecessityEngine(
        graph_config=TechnologyGraphConfig(
            categories=("Agentic AI", "Oracle", "Identity", "Payments", "Settlement"),
            dependencies=(
                ("Agentic AI", "Oracle"),
                ("Oracle", "Identity"),
                ("Identity", "Payments"),
                ("Payments", "Settlement"),
            ),
        )
    )


def input_set(
    *,
    technology_id: str = "Oracle",
    future_demand: float = 0.7,
    macro: float = 0.7,
    rotation: float = 0.7,
    market_recognition: float = 0.4,
    missing: tuple[str, ...] = (),
) -> TechnologyNecessityInputSet:
    return TechnologyNecessityInputSet(
        technology_id=technology_id,
        effective_at=NOW,
        intelligence=(
            intelligence_record("future-demand-intelligence", future_demand),
            intelligence_record("macro-intelligence", macro),
            intelligence_record("developer-intelligence", 0.7),
            intelligence_record("validation-engine", 0.7),
        ),
        fused_intelligence=(fused_record(technology_id, future_demand, macro, missing),),
        opportunity_timing=(timing_record(technology_id, missing),),
        evidence=(evidence_record("evidence-1", 0.8), evidence_record("evidence-2", 0.7)),
        snapshots=(snapshot_record(technology_id, future_demand, macro, rotation, market_recognition),),
    )


def intelligence_record(engine_id: str, score: float) -> IntelligenceRecord:
    return IntelligenceRecord(
        id=f"intel-{engine_id}",
        created_at=NOW,
        effective_at=NOW,
        pipeline_run_id="run-1",
        project="Oracle",
        engine_id=engine_id,
        generated_at=NOW,
        signal_ids=("signal-1",),
        evidence_ids=("evidence-1",),
        observation_ids=("observation-1",),
        insight_ids=("insight-1",),
        confidence={"score": score},
    )


def fused_record(
    technology_id: str, future_demand: float, macro: float, missing: tuple[str, ...]
) -> FusedIntelligenceRecord:
    return FusedIntelligenceRecord(
        id=f"fused-{technology_id}",
        created_at=NOW,
        effective_at=NOW,
        pipeline_run_id="run-1",
        target_id=technology_id,
        target_type="sector",
        fusion_strategy="deterministic",
        source_intelligence_ids=("intel-future", "intel-macro"),
        source_run_ids=("run-1",),
        confidence={"fused_confidence": 0.8},
        contributions=(
            {"engine_id": "future-demand-intelligence", "confidence": future_demand},
            {"engine_id": "macro-intelligence", "confidence": macro},
        ),
        missing_evidence={"missing_categories": missing},
    )


def timing_record(technology_id: str, missing: tuple[str, ...]) -> OpportunityTimingAssessmentRecord:
    return OpportunityTimingAssessmentRecord(
        id=f"timing-{technology_id}",
        created_at=NOW,
        effective_at=NOW,
        pipeline_run_id="run-1",
        target_id=technology_id,
        target_type="sector",
        source_fused_intelligence_ids=(f"fused-{technology_id}",),
        source_run_ids=("run-1",),
        configuration_fingerprint="config",
        model_fingerprint="model",
        historical_window=("2025-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"),
        opportunity_phase="early_entry",
        opportunity_window="opening",
        timing_score=70.0,
        confidence={"overall": 0.8},
        evidence_quality=0.8,
        confirmation_state={"score": 0.8},
        acceleration_state={"state": "positive", "value": 0.8},
        divergence_state={"divergences": (), "severity": 0.0},
        risk_state={"risks": (), "score": 0.3},
        expected_horizon="1-3 months",
        supporting_factors=("confirmation",),
        opposing_factors=(),
        contradictions=(),
        missing_evidence=missing,
        invalidation_conditions=("loss of confirmation",),
        historical_comparisons=(),
    )


def evidence_record(record_id: str, reliability: float) -> EvidenceRecord:
    return EvidenceRecord(
        id=record_id,
        created_at=NOW,
        effective_at=NOW,
        pipeline_run_id="run-1",
        source="persisted-source",
        reference=record_id,
        collected_at=NOW,
        reliability=reliability,
        freshness=0.8,
        raw_data={"id": record_id},
    )


def snapshot_record(
    technology_id: str, future_demand: float, macro: float, rotation: float, market_recognition: float
) -> SnapshotRecord:
    return SnapshotRecord(
        id=f"snapshot-{technology_id}",
        created_at=NOW,
        effective_at=NOW,
        snapshot_type="technology-necessity-input",
        target_id=technology_id,
        record_ids=("timing-oracle",),
        payload={
            "future_demand": future_demand,
            "macro_alignment": macro,
            "infrastructure_criticality": 0.8,
            "replacement_difficulty": 0.7,
            "enterprise_adoption": 0.7,
            "institutional_adoption": 0.7,
            "government_adoption": 0.5,
            "technology_maturity": 0.7,
            "market_recognition": market_recognition,
            "market_awareness": market_recognition,
            "capital_entering": rotation,
            "institutional_rotation": rotation,
            "developer_rotation": rotation,
            "narrative_rotation": rotation,
            "infrastructure_rotation": rotation,
            "sector_rotation": rotation,
            "capital_leaving": 1.0 - rotation,
        },
    )
