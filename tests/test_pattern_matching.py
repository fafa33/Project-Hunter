from __future__ import annotations

from datetime import UTC, datetime

from hunter.cli import main
from hunter.patterns import (
    HistoricalPatternLibrary,
    HistoricalProjectPattern,
    PatternInputSet,
    PatternMatchingEngine,
    PatternReportRenderer,
    load_historical_library,
    load_pattern_config,
)
from hunter.patterns.ranking import rank_pattern_assessments
from hunter.persistence.records import (
    EvidenceRecord,
    FusedIntelligenceRecord,
    IntelligenceRecord,
    OpportunityTimingAssessmentRecord,
    SnapshotRecord,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def test_identical_intelligence_produces_identical_similarity() -> None:
    library = library_for({"macro_alignment": 0.7, "probability": 0.7})
    first = PatternMatchingEngine(library=library).assess(input_set(macro=0.7, probability=0.7))
    second = PatternMatchingEngine(library=library).assess(input_set(macro=0.7, probability=0.7))

    assert first.top_matches[0].similarity == second.top_matches[0].similarity
    assert first.assessment_id == second.assessment_id


def test_higher_macro_similarity_improves_overall_similarity() -> None:
    library = library_for({"macro_alignment": 0.9, "probability": 0.7}, {"current_macro_conditions": 0.9})
    low = PatternMatchingEngine(library=library).assess(input_set(macro=0.2, probability=0.7))
    high = PatternMatchingEngine(library=library).assess(input_set(macro=0.9, probability=0.7))

    assert high.overall_similarity > low.overall_similarity
    assert high.top_matches[0].context_similarity > low.top_matches[0].context_similarity


def test_higher_probability_similarity_improves_ranking() -> None:
    library = library_for({"macro_alignment": 0.7, "probability": 0.9})
    low = PatternMatchingEngine(library=library).assess(input_set(target_id="b", macro=0.7, probability=0.2))
    high = PatternMatchingEngine(library=library).assess(input_set(target_id="a", macro=0.7, probability=0.9))

    assert rank_pattern_assessments((low, high), sort="similarity")[0].target_id == "a"
    assert rank_pattern_assessments((low, high), sort="historical")[0].target_id in {"a", "b"}
    assert rank_pattern_assessments((low, high), sort="pattern")[0].target_id in {"a", "b"}
    assert main(["rank", "--sort", "similarity"]) == 0
    assert main(["rank", "--sort", "historical"]) == 0
    assert main(["rank", "--sort", "pattern"]) == 0


def test_missing_evidence_lowers_confidence() -> None:
    library = library_for({"macro_alignment": 0.7, "probability": 0.7})
    complete = PatternMatchingEngine(library=library).assess(input_set(missing=()))
    missing = PatternMatchingEngine(library=library).assess(input_set(missing=("developer", "tokenomics")))

    assert missing.historical_confidence < complete.historical_confidence


def test_negative_historical_matches_are_reported_correctly() -> None:
    library = HistoricalPatternLibrary(
        (
            HistoricalProjectPattern(
                project_id="weak",
                name="Weak Historical Pattern",
                outcome="unsuccessful",
                dimensions={
                    "macro_alignment": 0.2,
                    "probability": 0.2,
                    "evidence_quality": 0.3,
                    "confidence": 0.3,
                },
                context_dimensions={"current_macro_conditions": 0.2, "current_future_demand": 0.2},
                warning_patterns=("weak_tokenomics", "poor_adoption", "declining_whale_support"),
            ),
        )
    )

    assessment = PatternMatchingEngine(library=library).assess(input_set(macro=0.2, probability=0.2))

    assert assessment.negative_matches
    assert assessment.negative_matches[0].warning_patterns == (
        "declining_whale_support",
        "poor_adoption",
        "weak_tokenomics",
    )


def test_reports_contain_pattern_matching_sections() -> None:
    assessment = PatternMatchingEngine(library=library_for({"macro_alignment": 0.7, "probability": 0.7})).assess(
        input_set()
    )

    report = PatternReportRenderer().render_markdown(assessment)

    assert "Pattern Matching" in report
    assert "Historical Matches" in report
    assert "Historical Similarity" in report
    assert "Context Similarity" in report
    assert "Similarity Breakdown" in report
    assert "Positive Patterns" in report
    assert "Negative Patterns" in report
    assert "Historical Confidence" in report


def test_no_fabricated_evidence() -> None:
    assessment = PatternMatchingEngine(library=library_for({"macro_alignment": 0.7, "probability": 0.7})).assess(
        input_set()
    )

    assert assessment.source_record_ids
    assert all("fabricated" not in item.lower() for item in assessment.source_record_ids)


def test_pattern_configs_load() -> None:
    config = load_pattern_config("configs/patterns.yaml")
    library = load_historical_library("configs/historical_projects.yaml")

    assert config.enabled is True
    assert len(library.projects) >= 24


def library_for(
    dimensions: dict[str, float], context_dimensions: dict[str, float] | None = None
) -> HistoricalPatternLibrary:
    merged = {
        "fundamentals": 0.7,
        "valuation": 0.7,
        "revenue": 0.7,
        "developer_activity": 0.7,
        "tokenomics": 0.7,
        "whale_behaviour": 0.7,
        "macro_alignment": 0.7,
        "future_demand": 0.7,
        "opportunity_timing": 0.7,
        "probability": 0.7,
        "validation_health": 0.7,
        "backtesting_reliability": 0.7,
        "evidence_quality": 0.75,
        "risk": 0.7,
        "confidence": 0.7,
        **dimensions,
    }
    return HistoricalPatternLibrary(
        (
            HistoricalProjectPattern(
                project_id="reference",
                name="Reference Project",
                outcome="successful",
                dimensions=merged,
                context_dimensions={
                    "current_macro_conditions": 0.7,
                    "current_technology_trends": 0.7,
                    "current_capital_rotation": 0.7,
                    "current_institutional_adoption": 0.7,
                    "current_regulatory_environment": 0.7,
                    "current_future_demand": 0.7,
                    "current_sector_strength": 0.7,
                    **(context_dimensions or {}),
                },
            ),
        )
    )


def input_set(
    *,
    target_id: str = "project-a",
    macro: float = 0.7,
    probability: float = 0.7,
    missing: tuple[str, ...] = (),
) -> PatternInputSet:
    return PatternInputSet(
        target_id=target_id,
        effective_at=NOW,
        intelligence=(
            intelligence_record("macro-intelligence", macro),
            intelligence_record("probability-engine", probability),
        ),
        fused_intelligence=(fused_record(target_id, macro, probability, missing),),
        opportunity_timing=(timing_record(target_id, missing),),
        evidence=(evidence_record("evidence-1", 0.8), evidence_record("evidence-2", 0.7)),
        snapshots=(
            snapshot_record(
                target_id,
                {
                    "probability_score": probability,
                    "backtesting_reliability": 0.7,
                    "fundamentals": 0.7,
                    "valuation": 0.7,
                    "revenue": 0.7,
                    "tokenomics": 0.7,
                    "current_macro_conditions": macro,
                    "current_future_demand": 0.7,
                    "current_technology_trends": 0.7,
                    "current_capital_rotation": 0.7,
                    "current_institutional_adoption": 0.7,
                    "current_regulatory_environment": 0.7,
                    "current_sector_strength": 0.7,
                },
            ),
        ),
    )


def intelligence_record(engine_id: str, score: float) -> IntelligenceRecord:
    return IntelligenceRecord(
        id=f"intel-{engine_id}",
        created_at=NOW,
        effective_at=NOW,
        pipeline_run_id="run-1",
        project="project-a",
        engine_id=engine_id,
        generated_at=NOW,
        signal_ids=("signal-1",),
        evidence_ids=("evidence-1",),
        observation_ids=("observation-1",),
        insight_ids=("insight-1",),
        confidence={"score": score},
    )


def fused_record(target_id: str, macro: float, probability: float, missing: tuple[str, ...]) -> FusedIntelligenceRecord:
    return FusedIntelligenceRecord(
        id=f"fused-{target_id}",
        created_at=NOW,
        effective_at=NOW,
        pipeline_run_id="run-1",
        target_id=target_id,
        target_type="project",
        fusion_strategy="deterministic",
        source_intelligence_ids=("intel-macro", "intel-probability"),
        source_run_ids=("run-1",),
        confidence={"fused_confidence": 0.8},
        contributions=(
            {"engine_id": "macro-intelligence", "confidence": macro},
            {"engine_id": "probability-engine", "confidence": probability},
        ),
        missing_evidence={"missing_categories": missing},
    )


def timing_record(target_id: str, missing: tuple[str, ...]) -> OpportunityTimingAssessmentRecord:
    return OpportunityTimingAssessmentRecord(
        id=f"timing-{target_id}",
        created_at=NOW,
        effective_at=NOW,
        pipeline_run_id="run-1",
        target_id=target_id,
        target_type="project",
        source_fused_intelligence_ids=(f"fused-{target_id}",),
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


def snapshot_record(target_id: str, payload: dict[str, float]) -> SnapshotRecord:
    return SnapshotRecord(
        id=f"snapshot-{target_id}",
        created_at=NOW,
        effective_at=NOW,
        snapshot_type="historical-pattern-input",
        target_id=target_id,
        record_ids=("timing-project-a",),
        payload=payload,
    )
