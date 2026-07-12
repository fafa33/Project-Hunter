from __future__ import annotations

import csv
import json
from pathlib import Path

from hunter.historical.models import HistoricalBacktestRun, HistoricalChallengeResult


class HistoricalValidationRenderer:
    def render_markdown(self, run: HistoricalBacktestRun) -> str:
        lines = [
            "# Historical Validation",
            f"Run: {run.run_id}",
            f"Cases: {len(run.cases)}",
            f"Coverage: {run.historical_coverage:.2f}%",
            f"Leakage: {'PASS' if run.leakage_passed else 'FAIL'}",
            f"Survivorship: {'PASS' if run.survivorship_passed else 'FAIL'}",
            f"Sample size: {run.sample_size_status}",
            "",
            "| Case | Project | Decision | Outcome | Correct | Leakage | Survivorship |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
        for result in run.challenge_results:
            lines.append(
                f"| {result.case_id} | {result.project_id} | {result.committee_decision} | "
                f"{result.realized_outcome} | {result.was_hunter_correct} | "
                f"{result.leakage_validation} | {result.survivorship_validation} |"
            )
        return "\n".join(lines)

    def write_reports(
        self, run: HistoricalBacktestRun, output_dir: str | Path = "reports/historical_validation"
    ) -> None:
        root = Path(output_dir)
        root.mkdir(parents=True, exist_ok=True)
        (root / "summary.md").write_text(self.render_markdown(run), encoding="utf-8")
        (root / "summary.json").write_text(
            json.dumps(
                {
                    "run_id": run.run_id,
                    "case_count": len(run.cases),
                    "historical_coverage": run.historical_coverage,
                    "leakage_passed": run.leakage_passed,
                    "survivorship_passed": run.survivorship_passed,
                    "sample_size_status": run.sample_size_status,
                },
                sort_keys=True,
                indent=2,
            ),
            encoding="utf-8",
        )
        _write_csv(root / "project_outcomes.csv", run.challenge_results)
        _write_engine_csv(root / "engine_metrics.csv", run)
        _write_calibration_csv(root / "calibration_buckets.csv", run)


def _write_csv(path: Path, rows: tuple[HistoricalChallengeResult, ...]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["case_id", "project_id", "outcome", "excess_return", "max_drawdown", "correct"])
        for row in rows:
            writer.writerow(
                [
                    row.case_id,
                    row.project_id,
                    row.realized_outcome,
                    row.excess_return,
                    row.maximum_drawdown,
                    row.was_hunter_correct,
                ]
            )


def _write_engine_csv(path: Path, run: HistoricalBacktestRun) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["engine", "availability", "sample_count", "evidence_quality", "freshness_quality"])
        for row in run.engine_metrics:
            writer.writerow(
                [row.engine, row.historical_availability, row.sample_count, row.evidence_quality, row.freshness_quality]
            )


def _write_calibration_csv(path: Path, run: HistoricalBacktestRun) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["metric_id", "bucket", "sample_count", "value", "status"])
        for metric in run.calibration_metrics:
            for bucket, sample_count, value in metric.reliability_buckets:
                writer.writerow([metric.metric_id, bucket, sample_count, value, metric.sample_size_status])
