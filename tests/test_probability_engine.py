from __future__ import annotations

from datetime import UTC, datetime

from hunter.cli import main
from hunter.persistence.records import (
    EvidenceRecord,
    FusedIntelligenceRecord,
    IntelligenceRecord,
    OpportunityTimingAssessmentRecord,
    SnapshotRecord,
)
from hunter.probability import ProbabilityEngine, ProbabilityReportRenderer
from hunter.probability.configuration import load_probability_config
from hunter.probability.models import ProbabilityInputSet
from hunter.probability.ranking import rank_probability_assessments

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def test_higher_opportunity_score_improves_probability() -> None:
    low = ProbabilityEngine().assess(input_set(timing_score=40.0))
    high = ProbabilityEngine().assess(input_set(timing_score=80.0))

    assert high.probability_score > low.probability_score


def test_higher_macro_alignment_improves_probability() -> None:
    low = ProbabilityEngine().assess(input_set(macro=0.2))
    high = ProbabilityEngine().assess(input_set(macro=0.9))

    assert high.probability_score > low.probability_score


def test_higher_future_demand_improves_probability() -> None:
    low = ProbabilityEngine().assess(input_set(future_demand=0.2))
    high = ProbabilityEngine().assess(input_set(future_demand=0.9))

    assert high.probability_score > low.probability_score


def test_higher_mispricing_improves_probability() -> None:
    low = ProbabilityEngine().assess(input_set(mispricing=0.2))
    high = ProbabilityEngine().assess(input_set(mispricing=0.9))

    assert high.probability_score > low.probability_score


def test_higher_validation_health_improves_probability() -> None:
    low = ProbabilityEngine().assess(input_set(validation=0.2))
    high = ProbabilityEngine().assess(input_set(validation=0.9))

    assert high.probability_score > low.probability_score


def test_conflicting_engines_reduce_consensus() -> None:
    clean = ProbabilityEngine().assess(input_set(conflicts=()))
    conflicted = ProbabilityEngine().assess(input_set(conflicts=("macro_alignment", "whale_alignment")))

    assert conflicted.consensus_score < clean.consensus_score
    assert conflicted.conflict_score > clean.conflict_score


def test_missing_evidence_lowers_robustness() -> None:
    complete = ProbabilityEngine().assess(input_set(missing=()))
    missing = ProbabilityEngine().assess(input_set(missing=("developer", "backtesting")))

    assert missing.evidence_robustness < complete.evidence_robustness


def test_weak_backtesting_lowers_historical_reliability() -> None:
    weak = ProbabilityEngine().assess(input_set(backtesting=0.2))
    strong = ProbabilityEngine().assess(input_set(backtesting=0.9))

    assert weak.historical_reliability < strong.historical_reliability


def test_probability_ranking_is_deterministic() -> None:
    low = ProbabilityEngine().assess(input_set(target_id="b", timing_score=40.0))
    high = ProbabilityEngine().assess(input_set(target_id="a", timing_score=80.0))

    assert rank_probability_assessments((low, high), sort="probability")[0].target_id == "a"
    assert rank_probability_assessments((low, high), sort="robustness")[0].target_id in {"a", "b"}
    assert rank_probability_assessments((low, high), sort="consensus")[0].target_id in {"a", "b"}
    assert main(["rank", "--sort", "probability"]) == 0
    assert main(["rank", "--sort", "robustness"]) == 0
    assert main(["rank", "--sort", "consensus"]) == 0


def test_reports_contain_probability_sections() -> None:
    report = ProbabilityReportRenderer().render_markdown(ProbabilityEngine().assess(input_set()))

    assert "Probability Score" in report
    assert "Success Probability" in report
    assert "Failure Probability" in report
    assert "Consensus" in report
    assert "Evidence Robustness" in report
    assert "Historical Reliability" in report


def test_probability_does_not_fabricate_evidence() -> None:
    assessment = ProbabilityEngine().assess(input_set())

    assert assessment.supporting_evidence == ("evidence-1", "evidence-2")
    assert "fabricated" not in " ".join(assessment.supporting_evidence).lower()


def test_probability_configuration_loads_from_yaml() -> None:
    config = load_probability_config("configs/probability.yaml")

    assert config.enabled is True
    assert dict(config.component_weights)["opportunity_timing"] > 0.0


def input_set(
    *,
    target_id: str = "project-a",
    timing_score: float = 70.0,
    macro: float = 0.7,
    future_demand: float = 0.7,
    mispricing: float = 0.7,
    validation: float = 0.7,
    backtesting: float = 0.7,
    missing: tuple[str, ...] = (),
    conflicts: tuple[str, ...] = (),
) -> ProbabilityInputSet:
    return ProbabilityInputSet(
        target_id=target_id,
        effective_at=NOW,
        fused_intelligence=(fused_record(target_id, macro, future_demand, mispricing, validation, missing, conflicts),),
        opportunity_timing=(timing_record(target_id, timing_score, missing, conflicts),),
        intelligence=(
            intelligence_record("macro-intelligence", macro),
            intelligence_record("future-demand-intelligence", future_demand),
            intelligence_record("mispricing-engine", mispricing),
            intelligence_record("validation-engine", validation),
        ),
        evidence=(evidence_record("evidence-1", 0.8, 0.9), evidence_record("evidence-2", 0.7, 0.8)),
        snapshots=(snapshot_record(target_id, backtesting),),
    )


def fused_record(
    target_id: str,
    macro: float,
    future_demand: float,
    mispricing: float,
    validation: float,
    missing: tuple[str, ...],
    conflicts: tuple[str, ...],
) -> FusedIntelligenceRecord:
    return FusedIntelligenceRecord(
        id=f"fused-{target_id}",
        created_at=NOW,
        effective_at=NOW,
        pipeline_run_id="run-1",
        target_id=target_id,
        target_type="project",
        fusion_strategy="deterministic",
        source_intelligence_ids=("intel-macro", "intel-future"),
        source_run_ids=("run-1",),
        confidence={"fused_confidence": 0.8, "evidence_quality": 0.75},
        contributions=(
            {"engine_id": "macro-intelligence", "confidence": macro},
            {"engine_id": "future-demand-intelligence", "confidence": future_demand},
            {"engine_id": "mispricing-engine", "confidence": mispricing},
            {"engine_id": "validation-engine", "confidence": validation},
        ),
        contradictions={"contradicted_categories": conflicts},
        missing_evidence={"missing_categories": missing},
        canonical_evidence_groups=(
            {"canonical_key": "evidence-1", "freshness": 0.9},
            {"canonical_key": "evidence-2", "freshness": 0.8},
        ),
    )


def timing_record(
    target_id: str, timing_score: float, missing: tuple[str, ...], conflicts: tuple[str, ...]
) -> OpportunityTimingAssessmentRecord:
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
        timing_score=timing_score,
        confidence={"overall": 0.8},
        evidence_quality=0.8,
        confirmation_state={"score": 0.8},
        acceleration_state={"state": "positive", "value": 0.8},
        divergence_state={"divergences": conflicts, "severity": 0.2 if conflicts else 0.0},
        risk_state={"risks": (), "score": 0.2},
        expected_horizon="1-3 months",
        supporting_factors=("confirmation",),
        opposing_factors=(),
        contradictions=conflicts,
        missing_evidence=missing,
        invalidation_conditions=("loss of confirmation",),
        historical_comparisons=(),
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


def evidence_record(record_id: str, reliability: float, freshness: float) -> EvidenceRecord:
    return EvidenceRecord(
        id=record_id,
        created_at=NOW,
        effective_at=NOW,
        pipeline_run_id="run-1",
        source="persisted-source",
        reference=record_id,
        collected_at=NOW,
        reliability=reliability,
        freshness=freshness,
        raw_data={"id": record_id},
    )


def snapshot_record(target_id: str, backtesting: float) -> SnapshotRecord:
    return SnapshotRecord(
        id=f"snapshot-{target_id}",
        created_at=NOW,
        effective_at=NOW,
        snapshot_type="backtesting",
        target_id=target_id,
        record_ids=("timing-project-a",),
        payload={
            "backtesting_reliability": backtesting,
            "historical_consistency": backtesting,
        },
    )
