from __future__ import annotations

from datetime import UTC, datetime

from hunter.cli import main
from hunter.opportunity import (
    OpportunityEngine,
    OpportunityMetricSnapshot,
    OpportunityReportRenderer,
    rank_opportunities,
)

NOW = datetime(2026, 1, 5, tzinfo=UTC)


def test_higher_mispricing_improves_opportunity_score() -> None:
    low = OpportunityEngine().evaluate(_snapshot(valuation_discount=0.2, relative_valuation=0.2, historical_discount=0.2))
    high = OpportunityEngine().evaluate(_snapshot(valuation_discount=0.9, relative_valuation=0.8, historical_discount=0.8))

    assert high.opportunity_score > low.opportunity_score


def test_macro_future_demand_and_whale_accumulation_improve_score() -> None:
    base = OpportunityEngine().evaluate(_snapshot(macro_tailwinds=0.2, future_demand=0.2, whale_accumulation=0.2))
    improved = OpportunityEngine().evaluate(_snapshot(macro_tailwinds=0.8, future_demand=0.9, whale_accumulation=0.85))

    assert improved.opportunity_score > base.opportunity_score


def test_missing_evidence_lowers_confidence_and_weak_validation_reduces_score() -> None:
    complete = OpportunityEngine().evaluate(_snapshot(validation_health=1.0, missing_evidence=0.0, missing=()))
    missing = OpportunityEngine().evaluate(_snapshot(validation_health=1.0, missing_evidence=0.8, missing=("valuation", "macro")))
    weak_validation = OpportunityEngine().evaluate(_snapshot(validation_health=0.2, missing_evidence=0.0, missing=()))

    assert missing.confidence["evidence_completeness"] < complete.confidence["evidence_completeness"]
    assert weak_validation.opportunity_score < complete.opportunity_score


def test_ranking_by_opportunity_and_conviction_is_deterministic() -> None:
    low = OpportunityEngine().evaluate(_snapshot(project_id="b", valuation_discount=0.2, confidence=0.9))
    high = OpportunityEngine().evaluate(_snapshot(project_id="a", valuation_discount=0.9, confidence=0.9))

    assert rank_opportunities((low, high), sort="opportunity")[0].project_id == "a"
    assert rank_opportunities((low, high), sort="conviction")[0].project_id == "a"
    assert main(["rank", "--sort", "opportunity"]) == 0
    assert main(["rank", "--sort", "conviction"]) == 0


def test_report_contains_opportunity_sections_and_no_fabricated_evidence() -> None:
    assessment = OpportunityEngine().evaluate(_snapshot(evidence=("evidence-a",), missing=("future_demand",)))
    report = OpportunityReportRenderer().render_markdown(assessment)

    for section in (
        "Opportunity Score",
        "Conviction",
        "Opportunity Window",
        "Risk/Reward",
        "Positive Drivers",
        "Negative Drivers",
        "Supporting Evidence",
        "Missing Evidence",
        "Confidence",
    ):
        assert section in report
    assert "evidence-a" in report
    assert "fabricated" not in report.lower()


def _snapshot(
    *,
    project_id: str = "project-a",
    valuation_discount: float = 0.6,
    relative_valuation: float = 0.6,
    historical_discount: float = 0.6,
    whale_accumulation: float = 0.6,
    smart_money_positioning: float = 0.6,
    developer_momentum: float = 0.6,
    macro_tailwinds: float = 0.6,
    future_demand: float = 0.6,
    sector_strength: float = 0.6,
    capital_formation: float = 0.6,
    validation_health: float = 1.0,
    evidence_freshness: float = 0.9,
    confidence: float = 0.8,
    risk: float = 0.2,
    missing_evidence: float = 0.0,
    backtesting_quality: float = 0.8,
    historical_opportunity_similarity: float = 0.6,
    evidence: tuple[str, ...] = ("evidence-1",),
    missing: tuple[str, ...] = (),
) -> OpportunityMetricSnapshot:
    return OpportunityMetricSnapshot(
        project_id=project_id,
        effective_at=NOW,
        evidence_ids=evidence,
        missing_evidence=missing,
        values={
            "valuation_discount": valuation_discount,
            "relative_valuation": relative_valuation,
            "historical_discount": historical_discount,
            "whale_accumulation": whale_accumulation,
            "smart_money_positioning": smart_money_positioning,
            "developer_momentum": developer_momentum,
            "macro_tailwinds": macro_tailwinds,
            "future_demand": future_demand,
            "sector_strength": sector_strength,
            "capital_formation": capital_formation,
            "validation_health": validation_health,
            "evidence_freshness": evidence_freshness,
            "confidence": confidence,
            "risk": risk,
            "missing_evidence": missing_evidence,
            "backtesting_quality": backtesting_quality,
            "historical_opportunity_similarity": historical_opportunity_similarity,
        },
    )
