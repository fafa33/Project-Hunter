from __future__ import annotations

from datetime import UTC, datetime

from hunter.cli import main
from hunter.explainability import DecisionAuditRenderer, DecisionExplainabilityEngine
from hunter.market_validation import MarketValidationRunner
from hunter.market_validation.configuration import load_market_validation_config
from hunter.market_validation.models import EngineValidationSource
from hunter.market_validation.runner import REQUIRED_ENGINES, SourceBackedV1ProjectExecutor


def test_decision_audit_explains_project_without_changing_scores() -> None:
    run = MarketValidationRunner(load_market_validation_config()).run()
    result = next(item for item in run.project_results if item.project_id == "chainlink")

    audit = DecisionExplainabilityEngine().explain_project(run, "chainlink")

    assert audit.final_score == result.hunter_score
    assert audit.committee_decision == result.committee_decision
    assert {item.engine for item in audit.contributions}.issuperset(
        {
            "Valuation",
            "Comparative Valuation",
            "Future Demand",
            "Macro",
            "Technology Necessity",
            "Capital Rotation",
            "Developer",
            "Whale Intelligence",
            "Protocol",
            "News",
            "Social",
            "Pattern Matching",
            "Probability",
            "Opportunity Timing",
            "Committee",
            "Risk Penalty",
        }
    )
    assert all(item.applied_weight >= 0.0 for item in audit.contributions)
    assert all(trace.missing_evidence for trace in audit.evidence_trace)
    assert "Macro deterioration" in audit.invalidation_conditions
    assert audit.sensitivity == ()


def test_rank_comparison_is_factual_and_deterministic() -> None:
    run = MarketValidationRunner(load_market_validation_config()).run()
    engine = DecisionExplainabilityEngine()

    first = engine.compare_projects(run, "chainlink", "api3")
    second = engine.compare_projects(run, "chainlink", "api3")

    assert first == second
    assert first.left_project_id == "chainlink"
    assert first.right_project_id == "api3"
    assert first.largest_score_differences
    assert {item.preferred_project_id for item in first.engine_preferences}.issubset({"chainlink", "api3", "tie"})


def test_decision_audit_renderer_includes_required_sections() -> None:
    run = MarketValidationRunner(load_market_validation_config()).run()
    audit = DecisionExplainabilityEngine().explain_project(run, "safe")
    rendered = DecisionAuditRenderer().render_project(audit)

    assert "Decision Breakdown" in rendered
    assert "Evidence Trace" in rendered
    assert "Decision Tree" in rendered
    assert "Invalidation Conditions" in rendered
    assert "Sensitivity Analysis" in rendered


def test_explain_cli_commands_execute() -> None:
    assert main(["explain", "chainlink"]) == 0
    assert main(["explain", "compare", "chainlink", "api3"]) == 0
    assert main(["explain", "ranking"]) == 0


def test_evidence_trace_resolves_to_original_source_records_when_available() -> None:
    config = load_market_validation_config()
    executor = SourceBackedV1ProjectExecutor(
        config.effective_at,
        {"safe": _sources(prefix="safe-real", score=0.9, confidence=0.8, freshness=0.7)},
    )
    run = MarketValidationRunner(config, executor=executor).run()

    audit = DecisionExplainabilityEngine().explain_project(run, "safe")

    assert all(trace.repository_ids for trace in audit.evidence_trace if trace.confidence > 0.0)
    assert all(trace.evidence_ids for trace in audit.evidence_trace if trace.confidence > 0.0)
    assert len({trace.confidence for trace in audit.evidence_trace if trace.confidence > 0.0}) == 1
    assert audit.sensitivity


def _sources(*, prefix: str, score: float, confidence: float, freshness: float) -> tuple[EngineValidationSource, ...]:
    weight = round(1.0 / len(REQUIRED_ENGINES), 6)
    return tuple(
        EngineValidationSource(
            engine=engine,
            score=score,
            confidence=confidence,
            timestamp=datetime(2026, 7, 11, tzinfo=UTC),
            freshness=freshness,
            source_record_ids=(f"record:{prefix}:{engine}",),
            evidence_ids=(f"evidence:{prefix}:{engine}",),
            raw_input_metrics={"source_metric": score},
            normalized_inputs={"normalized": score},
            applied_weight=weight,
            weighted_contribution=round(score * weight, 4),
        )
        for engine in REQUIRED_ENGINES
    )
