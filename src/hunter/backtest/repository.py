from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hunter.backtest.models import BacktestRun, CalibrationReport, EngineBacktestMetric, ProjectBacktestMetric


class BacktestRepository:
    def __init__(self, root: str | Path = "data/backtesting") -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, run: BacktestRun) -> BacktestRun:
        _write_jsonl(self.root / "runs.jsonl", (_run_payload(run),), append=True)
        _write_jsonl(
            self.root / "engine_metrics.jsonl", (_engine_payload(run.run_id, item) for item in run.engine_metrics)
        )
        _write_jsonl(
            self.root / "project_metrics.jsonl",
            (_project_payload(run.run_id, item) for item in run.project_metrics),
        )
        _write_jsonl(self.root / "calibration_reports.jsonl", (_calibration_payload(run.calibration),), append=True)
        return run

    def runs(self) -> tuple[BacktestRun, ...]:
        engine_metrics = tuple(_engine_from_payload(item) for item in _read_jsonl(self.root / "engine_metrics.jsonl"))
        project_metrics = tuple(
            _project_from_payload(item) for item in _read_jsonl(self.root / "project_metrics.jsonl")
        )
        calibrations = {item.calibration_id: item for item in self.calibrations()}
        rows = []
        for payload in _read_jsonl(self.root / "runs.jsonl"):
            calibration = calibrations.get(str(payload["calibration_id"]))
            if calibration is None:
                continue
            rows.append(
                BacktestRun(
                    run_id=str(payload["run_id"]),
                    generated_at=datetime.fromisoformat(str(payload["generated_at"])).astimezone(UTC),
                    historical_runs=int(payload["historical_runs"]),
                    projects_evaluated=int(payload["projects_evaluated"]),
                    engines_evaluated=int(payload["engines_evaluated"]),
                    coverage=float(payload["coverage"]),
                    historical_consistency=float(payload["historical_consistency"]),
                    calibration_completeness=float(payload["calibration_completeness"]),
                    engine_metrics=engine_metrics,
                    project_metrics=project_metrics,
                    calibration=calibration,
                )
            )
        return tuple(rows)

    def calibrations(self) -> tuple[CalibrationReport, ...]:
        return tuple(_calibration_from_payload(item) for item in _read_jsonl(self.root / "calibration_reports.jsonl"))


def _run_payload(item: BacktestRun) -> dict[str, Any]:
    return {
        "run_id": item.run_id,
        "generated_at": item.generated_at.isoformat(),
        "historical_runs": item.historical_runs,
        "projects_evaluated": item.projects_evaluated,
        "engines_evaluated": item.engines_evaluated,
        "coverage": item.coverage,
        "historical_consistency": item.historical_consistency,
        "calibration_completeness": item.calibration_completeness,
        "calibration_id": item.calibration.calibration_id,
    }


def _engine_payload(run_id: str, item: EngineBacktestMetric) -> dict[str, Any]:
    payload = asdict(item)
    payload["run_id"] = run_id
    return payload


def _project_payload(run_id: str, item: ProjectBacktestMetric) -> dict[str, Any]:
    payload = asdict(item)
    payload["run_id"] = run_id
    return payload


def _calibration_payload(item: CalibrationReport) -> dict[str, Any]:
    payload = asdict(item)
    payload["generated_at"] = item.generated_at.isoformat()
    return payload


def _engine_from_payload(payload: dict[str, Any]) -> EngineBacktestMetric:
    return EngineBacktestMetric(
        engine=str(payload["engine"]),
        historical_coverage=float(payload["historical_coverage"]),
        hit_rate=float(payload["hit_rate"]),
        precision=float(payload["precision"]),
        recall=float(payload["recall"]),
        false_positives=int(payload["false_positives"]),
        false_negatives=int(payload["false_negatives"]),
        top_n_accuracy=float(payload["top_n_accuracy"]),
        ranking_correlation=float(payload["ranking_correlation"]),
        decision_stability=float(payload["decision_stability"]),
        confidence_calibration=float(payload["confidence_calibration"]),
        evidence_completeness=float(payload["evidence_completeness"]),
        historical_consistency=float(payload["historical_consistency"]),
        prediction_reliability=float(payload["prediction_reliability"]),
        scenario_reliability=float(payload["scenario_reliability"]),
        evidence_ids=tuple(payload.get("evidence_ids", ())),
        repository_ids=tuple(payload.get("repository_ids", ())),
    )


def _project_from_payload(payload: dict[str, Any]) -> ProjectBacktestMetric:
    return ProjectBacktestMetric(
        project_id=str(payload["project_id"]),
        historical_coverage=float(payload["historical_coverage"]),
        confidence=float(payload["confidence"]),
        evidence_completeness=float(payload["evidence_completeness"]),
        historical_consistency=float(payload["historical_consistency"]),
        engines_available=int(payload["engines_available"]),
        engines_missing=int(payload["engines_missing"]),
        evidence_ids=tuple(payload.get("evidence_ids", ())),
        repository_ids=tuple(payload.get("repository_ids", ())),
    )


def _calibration_from_payload(payload: dict[str, Any]) -> CalibrationReport:
    return CalibrationReport(
        calibration_id=str(payload["calibration_id"]),
        generated_at=datetime.fromisoformat(str(payload["generated_at"])).astimezone(UTC),
        confidence_calibration=float(payload["confidence_calibration"]),
        evidence_quality=float(payload["evidence_quality"]),
        coverage_gaps=tuple(payload.get("coverage_gaps", ())),
        weak_engines=tuple(payload.get("weak_engines", ())),
        strong_engines=tuple(payload.get("strong_engines", ())),
        historical_drift=float(payload["historical_drift"]),
        recommended_weight_adjustments={
            str(k): float(v) for k, v in dict(payload["recommended_weight_adjustments"]).items()
        },
    )


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
