from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hunter.backtest.models import BacktestRun, CalibrationReport, EngineBacktestMetric, ProjectBacktestMetric


class BacktestRepository:
    def __init__(self, root: str | Path = "data/backtesting") -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, run: BacktestRun) -> BacktestRun:
        snapshot_ref = _snapshot_ref(run.run_id)
        snapshot = self.root / snapshot_ref
        _write_immutable_jsonl(
            snapshot / "engine_metrics.jsonl", (_engine_payload(run.run_id, item) for item in run.engine_metrics)
        )
        _write_immutable_jsonl(
            snapshot / "project_metrics.jsonl",
            (_project_payload(run.run_id, item) for item in run.project_metrics),
        )
        _write_immutable_json(
            snapshot / "manifest.json",
            {
                "calibration_id": run.calibration.calibration_id,
                "generated_at": run.generated_at.isoformat(),
                "run_id": run.run_id,
            },
        )
        persisted = replace(run, snapshot_ref=snapshot_ref, replay_limitation=None)
        _append_unique(self.root / "runs.jsonl", _run_payload(persisted), identity="run_id")
        _append_unique(
            self.root / "calibration_reports.jsonl",
            _calibration_payload(run.calibration),
            identity="calibration_id",
        )
        return persisted

    def runs(self) -> tuple[BacktestRun, ...]:
        calibrations = {item.calibration_id: item for item in self.calibrations()}
        rows = []
        for payload in _read_jsonl(self.root / "runs.jsonl"):
            calibration = calibrations.get(str(payload["calibration_id"]))
            if calibration is None:
                continue
            snapshot_ref = str(payload["snapshot_ref"]) if payload.get("snapshot_ref") else None
            if snapshot_ref is None:
                engine_path = self.root / "engine_metrics.jsonl"
                project_path = self.root / "project_metrics.jsonl"
                replay_limitation = "legacy run uses unversioned current metrics without trustworthy run linkage"
            else:
                engine_path = self.root / snapshot_ref / "engine_metrics.jsonl"
                project_path = self.root / snapshot_ref / "project_metrics.jsonl"
                replay_limitation = None
            engine_metrics = tuple(_engine_from_payload(item) for item in _read_jsonl(engine_path))
            project_metrics = tuple(_project_from_payload(item) for item in _read_jsonl(project_path))
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
                    snapshot_ref=snapshot_ref,
                    replay_limitation=replay_limitation,
                )
            )
        return tuple(rows)

    def calibrations(self) -> tuple[CalibrationReport, ...]:
        return tuple(_calibration_from_payload(item) for item in _read_jsonl(self.root / "calibration_reports.jsonl"))

    def run(self, run_id: str) -> BacktestRun:
        run = next((item for item in self.runs() if item.run_id == run_id), None)
        if run is None:
            raise LookupError(f"Unknown backtest run: {run_id}")
        return run

    def current_metrics_status(self) -> dict[str, str | None]:
        return {
            "snapshot_ref": None,
            "replay_limitation": "legacy current metrics have no trustworthy run linkage",
        }


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
        "snapshot_ref": item.snapshot_ref,
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


def _append_jsonl(path: Path, rows: Any) -> None:
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True))
            handle.write("\n")


def _read_jsonl(path: Path) -> tuple[dict[str, Any], ...]:
    if not path.exists():
        return ()
    return tuple(json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def _snapshot_ref(identity: str) -> str:
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return f"snapshots/{digest}"


def _canonical_line(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"


def _write_immutable_jsonl(path: Path, rows: Any) -> None:
    _write_immutable(path, "".join(_canonical_line(row) for row in rows))


def _write_immutable_json(path: Path, payload: dict[str, Any]) -> None:
    _write_immutable(path, _canonical_line(payload))


def _write_immutable(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_text(encoding="utf-8") != content:
            raise ValueError(f"Immutable backtest snapshot conflict: {path}")
        return
    with path.open("x", encoding="utf-8") as handle:
        handle.write(content)


def _append_unique(path: Path, payload: dict[str, Any], *, identity: str) -> None:
    matches = tuple(item for item in _read_jsonl(path) if item.get(identity) == payload.get(identity))
    if matches:
        if any(_canonical_line(item) != _canonical_line(payload) for item in matches):
            raise ValueError(f"Backtest identity conflict: {payload[identity]}")
        return
    _append_jsonl(path, (payload,))
