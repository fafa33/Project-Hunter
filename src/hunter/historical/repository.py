from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from hunter.historical.models import (
    HistoricalBacktestRun,
    HistoricalBiasValidation,
    HistoricalCalibrationMetric,
    HistoricalDecisionOutcomeRecord,
    HistoricalEvidenceSnapshot,
    HistoricalPerformanceMetrics,
    HistoricalReplayExplanation,
    HistoricalValidationCase,
    SuccessLabel,
)


class HistoricalValidationRepository:
    def __init__(self, root: str | Path = "data/historical_validation") -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, run: HistoricalBacktestRun, *, append_snapshots: bool = True) -> HistoricalBacktestRun:
        existing = {item["snapshot_id"] for item in _read_jsonl(self.root / "snapshots.jsonl")}
        duplicate_finalized = [
            snapshot.snapshot_id
            for snapshot in run.snapshots
            if append_snapshots and snapshot.finalized and snapshot.snapshot_id in existing
        ]
        if duplicate_finalized:
            msg = f"immutable historical snapshots already exist: {','.join(sorted(duplicate_finalized))}"
            raise ValueError(msg)
        _write_jsonl(self.root / "cases.jsonl", (_payload(item) for item in run.cases))
        if append_snapshots:
            _write_jsonl(self.root / "snapshots.jsonl", (_payload(item) for item in run.snapshots), append=True)
        _write_jsonl(self.root / "engine_outputs.jsonl", (_payload(item) for item in run.engine_outputs))
        _write_jsonl(self.root / "committee_assessments.jsonl", (_payload(item) for item in run.committee_assessments))
        _write_jsonl(self.root / "ranking_snapshots.jsonl", (_payload(item) for item in run.ranking_snapshots))
        _write_jsonl(self.root / "outcomes.jsonl", (_payload(item) for item in run.outcomes))
        _write_jsonl(self.root / "benchmark_outcomes.jsonl", (_payload(item) for item in run.benchmark_outcomes))
        _write_jsonl(self.root / "calibration_metrics.jsonl", (_payload(item) for item in run.calibration_metrics))
        _write_jsonl(self.root / "engine_metrics.jsonl", (_payload(item) for item in run.engine_metrics))
        _write_jsonl(self.root / "challenge_results.jsonl", (_payload(item) for item in run.challenge_results))
        _write_jsonl(self.root / "bias_validations.jsonl", (_payload(item) for item in run.bias_validations))
        _write_jsonl(self.root / "decision_outcomes.jsonl", (_payload(item) for item in run.decision_outcomes))
        if run.performance_metrics is not None:
            _write_jsonl(self.root / "performance_metrics.jsonl", (_payload(run.performance_metrics),))
        _write_jsonl(self.root / "replay_explanations.jsonl", (_payload(item) for item in run.explanations))
        _write_jsonl(self.root / "runs.jsonl", (_run_payload(run),), append=True)
        return run

    def runs(self) -> tuple[dict[str, Any], ...]:
        return _read_jsonl(self.root / "runs.jsonl")

    def cases(self) -> tuple[HistoricalValidationCase, ...]:
        return tuple(_case(item) for item in _read_jsonl(self.root / "cases.jsonl"))

    def snapshots(self) -> tuple[HistoricalEvidenceSnapshot, ...]:
        return tuple(_snapshot(item) for item in _read_jsonl(self.root / "snapshots.jsonl"))

    def bias_validations(self) -> tuple[HistoricalBiasValidation, ...]:
        return tuple(_bias(item) for item in _read_jsonl(self.root / "bias_validations.jsonl"))

    def calibration_metrics(self) -> tuple[HistoricalCalibrationMetric, ...]:
        return tuple(_calibration(item) for item in _read_jsonl(self.root / "calibration_metrics.jsonl"))

    def performance_metrics(self) -> tuple[HistoricalPerformanceMetrics, ...]:
        return tuple(_performance(item) for item in _read_jsonl(self.root / "performance_metrics.jsonl"))

    def decision_outcomes(self) -> tuple[HistoricalDecisionOutcomeRecord, ...]:
        return tuple(_decision_outcome(item) for item in _read_jsonl(self.root / "decision_outcomes.jsonl"))

    def replay_explanations(self) -> tuple[HistoricalReplayExplanation, ...]:
        return tuple(_explanation(item) for item in _read_jsonl(self.root / "replay_explanations.jsonl"))


def _payload(item: Any) -> dict[str, Any]:
    payload = _jsonable(asdict(item))
    if not isinstance(payload, dict):
        msg = "historical payload must be a mapping"
        raise TypeError(msg)
    return payload


def _run_payload(run: HistoricalBacktestRun) -> dict[str, Any]:
    return {
        "run_id": run.run_id,
        "generated_at": run.generated_at.isoformat(),
        "case_count": len(run.cases),
        "snapshot_count": len(run.snapshots),
        "engine_output_count": len(run.engine_outputs),
        "outcome_count": len(run.outcomes),
        "historical_coverage": run.historical_coverage,
        "leakage_passed": run.leakage_passed,
        "survivorship_passed": run.survivorship_passed,
        "sample_size_status": run.sample_size_status,
        "decision_outcome_count": len(run.decision_outcomes),
        "explanation_count": len(run.explanations),
    }


def _jsonable(value: object) -> object:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, tuple | list):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    return value


def _case(payload: dict[str, Any]) -> HistoricalValidationCase:
    from hunter.historical.configuration import _case as parse_case

    return parse_case(payload)


def _snapshot(payload: dict[str, Any]) -> HistoricalEvidenceSnapshot:
    from hunter.historical.models import HistoricalEvidenceRecord

    evidence = tuple(
        HistoricalEvidenceRecord(
            source_provider=str(item["source_provider"]),
            source_record_ids=tuple(item.get("source_record_ids", ())),
            evidence_ids=tuple(item.get("evidence_ids", ())),
            repository_ids=tuple(item.get("repository_ids", ())),
            event_timestamp=_dt(item["event_timestamp"]),
            publication_timestamp=_dt(item["publication_timestamp"]),
            ingestion_timestamp=_dt(item["ingestion_timestamp"]),
            evaluation_cutoff_timestamp=_dt(item["evaluation_cutoff_timestamp"]),
            confidence=float(item["confidence"]),
            freshness=float(item["freshness"]),
            validation_status=str(item["validation_status"]),
            engine=str(item["engine"]),
            raw_metrics=dict(item.get("raw_metrics", {})),
            normalized_metrics={str(k): float(v) for k, v in dict(item.get("normalized_metrics", {})).items()},
            data_availability_timestamp=(
                _dt(item["data_availability_timestamp"]) if item.get("data_availability_timestamp") else None
            ),
        )
        for item in payload.get("evidence", ())
    )
    return HistoricalEvidenceSnapshot(
        snapshot_id=str(payload["snapshot_id"]),
        case_id=str(payload["case_id"]),
        version=int(payload["version"]),
        finalized=bool(payload["finalized"]),
        created_at=_dt(payload["created_at"]),
        previous_snapshot_id=payload.get("previous_snapshot_id"),
        correction_reason=payload.get("correction_reason"),
        correction_timestamp=_dt(payload["correction_timestamp"]) if payload.get("correction_timestamp") else None,
        changed_fields=tuple(payload.get("changed_fields", ())),
        evidence=evidence,
        missing_evidence=tuple(payload.get("missing_evidence", ())),
        unavailable_engines=tuple(payload.get("unavailable_engines", ())),
        stale_engines=tuple(payload.get("stale_engines", ())),
        validation_warnings=tuple(payload.get("validation_warnings", ())),
    )


def _bias(payload: dict[str, Any]) -> HistoricalBiasValidation:
    return HistoricalBiasValidation(
        case_id=str(payload["case_id"]),
        leakage_passed=bool(payload["leakage_passed"]),
        survivorship_passed=bool(payload["survivorship_passed"]),
        violations=tuple(payload.get("violations", ())),
    )


def _calibration(payload: dict[str, Any]) -> HistoricalCalibrationMetric:
    return HistoricalCalibrationMetric(
        metric_id=str(payload["metric_id"]),
        brier_score=payload["brier_score"],
        calibration_error=payload["calibration_error"],
        reliability_buckets=tuple((str(row[0]), int(row[1]), row[2]) for row in payload.get("reliability_buckets", ())),
        sample_size_status=str(payload["sample_size_status"]),
        expected_probability=payload.get("expected_probability", "INSUFFICIENT_SAMPLE_SIZE"),
        observed_probability=payload.get("observed_probability", "INSUFFICIENT_SAMPLE_SIZE"),
        reliability_curve=tuple(
            (str(row[0]), row[1], row[2], int(row[3])) for row in payload.get("reliability_curve", ())
        ),
        confidence_distribution=tuple((str(row[0]), int(row[1])) for row in payload.get("confidence_distribution", ())),
    )


def _performance(payload: dict[str, Any]) -> HistoricalPerformanceMetrics:
    return HistoricalPerformanceMetrics(
        metric_id=str(payload["metric_id"]),
        accuracy=payload["accuracy"],
        precision=payload["precision"],
        recall=payload["recall"],
        f1=payload["f1"],
        roc_auc=payload["roc_auc"],
        maximum_drawdown=payload["maximum_drawdown"],
        annualized_return=payload["annualized_return"],
        sharpe_ratio=payload["sharpe_ratio"],
        sortino_ratio=payload["sortino_ratio"],
        win_rate=payload["win_rate"],
        average_return=payload["average_return"],
        median_return=payload["median_return"],
        best_trade=payload["best_trade"],
        worst_trade=payload["worst_trade"],
        hit_rate=payload["hit_rate"],
        time_to_target=payload["time_to_target"],
        false_positive_rate=payload["false_positive_rate"],
        false_negative_rate=payload["false_negative_rate"],
        sample_count=int(payload["sample_count"]),
    )


def _decision_outcome(payload: dict[str, Any]) -> HistoricalDecisionOutcomeRecord:
    return HistoricalDecisionOutcomeRecord(
        case_id=str(payload["case_id"]),
        project_id=str(payload["project_id"]),
        decision_date=_dt(payload["decision_date"]),
        hunter_score=float(payload["hunter_score"]),
        timing=float(payload["timing"]),
        committee_decision=str(payload["committee_decision"]),
        confidence=float(payload["confidence"]),
        freshness=float(payload["freshness"]),
        price=_optional_float(payload.get("price")),
        market_cap=_optional_float(payload.get("market_cap")),
        fdv=_optional_float(payload.get("fdv")),
        tvl=_optional_float(payload.get("tvl")),
        developer_activity=_optional_float(payload.get("developer_activity")),
        narrative_state=str(payload["narrative_state"]),
        macro_state=str(payload["macro_state"]),
        whale_state=str(payload["whale_state"]),
        technology_graph=str(payload["technology_graph"]),
        economic_graph=str(payload["economic_graph"]),
        scenario_state=str(payload["scenario_state"]),
        final_outcome=cast(SuccessLabel, payload["final_outcome"]),
        source_evidence_ids=tuple(payload.get("source_evidence_ids", ())),
        source_repository_ids=tuple(payload.get("source_repository_ids", ())),
        leakage_status=str(payload["leakage_status"]),
    )


def _explanation(payload: dict[str, Any]) -> HistoricalReplayExplanation:
    return HistoricalReplayExplanation(
        case_id=str(payload["case_id"]),
        project_id=str(payload["project_id"]),
        decision=str(payload["decision"]),
        decision_reason=str(payload["decision_reason"]),
        positive_drivers=tuple(payload.get("positive_drivers", ())),
        negative_drivers=tuple(payload.get("negative_drivers", ())),
        existing_evidence_ids=tuple(payload.get("existing_evidence_ids", ())),
        reconstructed_evidence_ids=tuple(payload.get("reconstructed_evidence_ids", ())),
        historical_evidence_ids=tuple(payload.get("historical_evidence_ids", ())),
        historical_repository_ids=tuple(payload.get("historical_repository_ids", ())),
        unavailable_evidence=tuple(payload.get("unavailable_evidence", ())),
        unavailable_reason=str(payload.get("unavailable_reason", "")),
        leakage_status=str(payload["leakage_status"]),
    )


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    return float(value)


def _dt(value: object) -> datetime:
    parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _write_jsonl(path: Path, rows: Any, *, append: bool = False) -> None:
    mode = "a" if append else "w"
    with path.open(mode, encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True))
            handle.write("\n")


def _read_jsonl(path: Path) -> tuple[dict[str, Any], ...]:
    if not path.exists():
        return ()
    return tuple(json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
