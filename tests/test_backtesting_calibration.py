from __future__ import annotations

import json
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta

import pytest

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
    assert len(repository.runs()) == 1
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


def test_backtest_snapshots_preserve_history_retry_conflict_and_legacy(tmp_path) -> None:
    acquisition = FileAcquisitionRepository(tmp_path / "acquisition")
    acquisition.save_normalized((_evidence("cg-1", "bitcoin", "coingecko", "coingecko_market_profile"),))
    acquisition.save_validations((_validation("cg-1"),))
    repository = BacktestRepository(tmp_path / "backtest")
    engine = BacktestingCalibrationEngine(acquisition_repository=acquisition, backtest_repository=repository)

    run_a = engine.run(as_of=NOW)
    assert run_a.snapshot_ref is not None
    snapshot_a = repository.root / run_a.snapshot_ref
    bytes_a = {path.name: path.read_bytes() for path in snapshot_a.iterdir()}

    run_b = engine.run(as_of=NOW + timedelta(days=1))

    assert run_b.run_id != run_a.run_id
    assert repository.run(run_a.run_id) == run_a
    assert repository.run(run_b.run_id) == run_b
    assert {path.name: path.read_bytes() for path in snapshot_a.iterdir()} == bytes_a

    repository.save(run_a)
    assert len(repository.runs()) == 2
    conflicting = replace(run_a, engine_metrics=run_a.engine_metrics[:-1])
    with pytest.raises(ValueError, match="snapshot conflict"):
        repository.save(conflicting)

    (repository.root / "engine_metrics.jsonl").write_text("{}\n", encoding="utf-8")
    (repository.root / "project_metrics.jsonl").write_text("{}\n", encoding="utf-8")
    assert repository.run(run_a.run_id) == run_a
    assert repository.current_metrics_status()["replay_limitation"] is not None

    legacy_root = tmp_path / "legacy-backtest"
    legacy_root.mkdir()
    legacy_run = {
        "run_id": run_a.run_id,
        "generated_at": run_a.generated_at.isoformat(),
        "historical_runs": run_a.historical_runs,
        "projects_evaluated": run_a.projects_evaluated,
        "engines_evaluated": run_a.engines_evaluated,
        "coverage": run_a.coverage,
        "historical_consistency": run_a.historical_consistency,
        "calibration_completeness": run_a.calibration_completeness,
        "calibration_id": run_a.calibration.calibration_id,
    }
    (legacy_root / "runs.jsonl").write_text(json.dumps(legacy_run) + "\n", encoding="utf-8")
    (legacy_root / "engine_metrics.jsonl").write_bytes((snapshot_a / "engine_metrics.jsonl").read_bytes())
    (legacy_root / "project_metrics.jsonl").write_bytes((snapshot_a / "project_metrics.jsonl").read_bytes())
    calibration = asdict(run_a.calibration)
    calibration["generated_at"] = run_a.calibration.generated_at.isoformat()
    (legacy_root / "calibration_reports.jsonl").write_text(json.dumps(calibration) + "\n", encoding="utf-8")

    legacy_run_loaded = BacktestRepository(legacy_root).runs()[0]
    assert legacy_run_loaded.engine_metrics == run_a.engine_metrics
    assert legacy_run_loaded.project_metrics == run_a.project_metrics
    assert legacy_run_loaded.replay_limitation is not None


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
