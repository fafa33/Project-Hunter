from __future__ import annotations

from datetime import UTC, datetime

import pytest

from hunter.cli import main
from hunter.market_validation import MarketValidationRunner
from hunter.market_validation.configuration import load_market_validation_config
from hunter.market_validation.models import EngineValidationSource
from hunter.market_validation.runner import REQUIRED_ENGINES, SourceBackedV1ProjectExecutor
from hunter.weights import WeightEngine, load_weight_config, recommend_weight_adjustments
from hunter.weights.configuration import weight_config_from_mapping


def test_weight_config_is_versioned_and_sums_to_one() -> None:
    config = load_weight_config()

    assert config.version == "hunter-score-v3.0.0-baseline"
    assert round(sum(config.weights.values()), 6) == 1.0
    assert config.calibration_policy == "recommend_only"


def test_invalid_weight_config_is_rejected() -> None:
    with pytest.raises(ValueError, match="sum to 1.0"):
        weight_config_from_mapping({"version": "bad", "weights": {"valuation": 0.5}})


def test_weight_engine_calculates_real_contributions_without_source_placeholders() -> None:
    source = _source("valuation", score=0.8, confidence=0.9, freshness=0.75)

    weighted = WeightEngine().apply((source,))[0]

    assert weighted.base_weight == load_weight_config().weights["valuation"]
    assert weighted.adjusted_weight > 0.0
    assert weighted.weighted_contribution > 0.0
    assert weighted.scoring_version == "hunter-score-v3.0.0-baseline"
    assert weighted.evidence_coverage == 1.0


def test_missing_evidence_keeps_zero_contribution_with_explainable_weight() -> None:
    unavailable = EngineValidationSource(
        engine="valuation",
        score=0.8,
        confidence=0.0,
        timestamp=datetime(2026, 7, 11, tzinfo=UTC),
        freshness=0.0,
        source_record_ids=(),
        evidence_ids=(),
        status="UNAVAILABLE",
        missing_fields=("valuation",),
    )

    weighted = WeightEngine().apply((unavailable,))[0]

    assert weighted.base_weight > 0.0
    assert weighted.adjusted_weight == 0.0
    assert weighted.weighted_contribution == 0.0
    assert weighted.evidence_coverage == 0.0


def test_market_validation_activates_hunter_and_final_scores() -> None:
    config = load_market_validation_config()
    executor = SourceBackedV1ProjectExecutor(
        config.effective_at,
        {"bitcoin": tuple(_source(engine, score=0.8, confidence=0.9, freshness=0.85) for engine in REQUIRED_ENGINES)},
    )

    result = next(
        item
        for item in MarketValidationRunner(config, executor=executor).run().project_results
        if item.project_id == "bitcoin"
    )

    assert result.hunter_score > 0.0
    assert result.final_score > 0.0
    assert result.scoring_version == "hunter-score-v3.0.0-baseline"
    assert all(source.base_weight >= 0.0 for source in result.engine_sources)
    assert all(source.scoring_version == result.scoring_version for source in result.engine_sources)


def test_weight_recommendations_do_not_activate_when_sample_size_is_insufficient() -> None:
    recommendation = recommend_weight_adjustments(load_weight_config(), None)

    assert recommendation.status == "INSUFFICIENT_SAMPLE_SIZE"
    assert recommendation.recommended_adjustments == {}


def test_weight_cli_commands_execute(capsys) -> None:
    assert main(["weights", "status"]) == 0
    assert "hunter-score-v3.0.0-baseline" in capsys.readouterr().out
    assert main(["weights", "validate"]) == 0
    assert main(["weights", "report"]) == 0
    assert main(["weights", "recommend"]) == 0
    assert main(["weights", "activate"]) == 0
    assert "recommended_weights_activated=false" in capsys.readouterr().out


def _source(engine: str, *, score: float, confidence: float, freshness: float) -> EngineValidationSource:
    return EngineValidationSource(
        engine=engine,
        score=score,
        confidence=confidence,
        timestamp=datetime(2026, 7, 11, tzinfo=UTC),
        freshness=freshness,
        source_record_ids=(f"record:{engine}",),
        evidence_ids=(f"evidence:{engine}",),
        repository_ids=(f"repository:{engine}",),
        raw_input_metrics={"raw": score},
        normalized_inputs={"normalized": score},
    )
