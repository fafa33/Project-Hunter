from __future__ import annotations

from datetime import UTC, datetime

from hunter.acquisition.models import EvidenceValidation, NormalizedEvidence
from hunter.acquisition.repositories import FileAcquisitionRepository
from hunter.backtest import BacktestingCalibrationEngine, BacktestRepository, compare_backtests

NOW = datetime(2026, 7, 11, tzinfo=UTC)


def test_backtest_historical_replay_persistence_and_explainability(tmp_path) -> None:
    acquisition = FileAcquisitionRepository(tmp_path / "acquisition")
    acquisition.save_normalized(
        (
            _evidence("cg-1", "bitcoin", "coingecko", "coingecko_market_profile"),
            _evidence("gh-1", "bitcoin", "github", "github_repository_profile"),
        )
    )
    acquisition.save_validations((_validation("cg-1"), _validation("gh-1")))
    repository = BacktestRepository(tmp_path / "backtest")

    run = BacktestingCalibrationEngine(
        acquisition_repository=acquisition,
        backtest_repository=repository,
    ).run(as_of=NOW)
    loaded = repository.runs()[0]

    assert run.projects_evaluated == 50
    assert run.engines_evaluated == 21
    assert run.coverage > 0
    assert loaded.run_id == run.run_id
    assert any(metric.evidence_ids for metric in loaded.engine_metrics)
    assert loaded.calibration.calibration_id


def test_backtest_deterministic_replay_comparison_and_history(tmp_path) -> None:
    acquisition = FileAcquisitionRepository(tmp_path / "acquisition")
    acquisition.save_normalized((_evidence("cg-1", "bitcoin", "coingecko", "coingecko_market_profile"),))
    acquisition.save_validations((_validation("cg-1"),))
    repository = BacktestRepository(tmp_path / "backtest")
    engine = BacktestingCalibrationEngine(acquisition_repository=acquisition, backtest_repository=repository)

    first = engine.run(as_of=NOW)
    second = engine.run(as_of=NOW)
    comparison = compare_backtests(first, second)

    assert first.run_id == second.run_id
    assert len(repository.runs()) == 2
    assert comparison["coverage_delta"] == 0.0
    assert comparison["consistency_delta"] == 0.0


def test_calibration_reports_weak_and_strong_engines_without_model_changes(tmp_path) -> None:
    acquisition = FileAcquisitionRepository(tmp_path / "acquisition")
    acquisition.save_normalized(
        (
            _evidence("cg-1", "bitcoin", "coingecko", "coingecko_market_profile"),
            _evidence("dl-1", "bitcoin", "defillama", "defillama_protocol_profile"),
        )
    )
    acquisition.save_validations((_validation("cg-1"), _validation("dl-1")))

    run = BacktestingCalibrationEngine(
        acquisition_repository=acquisition,
        backtest_repository=BacktestRepository(tmp_path / "backtest"),
    ).run(as_of=NOW)

    assert run.calibration.evidence_quality > 0
    assert run.calibration.coverage_gaps
    assert set(run.calibration.recommended_weight_adjustments) == {metric.engine for metric in run.engine_metrics}


def _evidence(evidence_id: str, target_id: str, provider: str, metric: str) -> NormalizedEvidence:
    return NormalizedEvidence(
        evidence_id=evidence_id,
        repository_id=f"repo:{evidence_id}",
        provider=provider,
        collector=f"{provider}-collector",
        raw_source_id=f"raw:{evidence_id}",
        domain="market",
        metric=metric,
        target_id=target_id,
        value=target_id,
        raw_metrics={"raw": evidence_id},
        normalized_metrics={"score": 0.8},
        source_url="https://example.test",
        retrieved_at=NOW,
        normalized_at=NOW,
        confidence=1.0,
        freshness=1.0,
        raw_evidence_id=f"raw-evidence:{evidence_id}",
    )


def _validation(evidence_id: str) -> EvidenceValidation:
    return EvidenceValidation(
        evidence_id=evidence_id,
        status="valid",
        validated_at=NOW,
        confidence=1.0,
        freshness=1.0,
    )
