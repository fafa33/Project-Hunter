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
        ]
        if run.performance_metrics is not None:
            metrics = run.performance_metrics
            lines.extend(
                [
                    "",
                    "## Performance Metrics",
                    f"Accuracy: {metrics.accuracy}",
                    f"Precision: {metrics.precision}",
                    f"Recall: {metrics.recall}",
                    f"F1: {metrics.f1}",
                    f"Maximum drawdown: {metrics.maximum_drawdown}",
                    f"Win rate: {metrics.win_rate}",
                ]
            )
        if run.calibration_metrics:
            calibration = run.calibration_metrics[0]
            lines.extend(
                [
                    "",
                    "## Calibration",
                    f"Expected probability: {calibration.expected_probability}",
                    f"Observed probability: {calibration.observed_probability}",
                    f"Calibration error: {calibration.calibration_error}",
                ]
            )
        lines.extend(
            [
                "",
                "| Case | Project | Decision | Outcome | Correct | Leakage | Survivorship |",
                "| --- | --- | --- | --- | --- | --- | --- |",
            ]
        )
        for result in run.challenge_results:
            lines.append(
                f"| {result.case_id} | {result.project_id} | {result.committee_decision} | "
                f"{result.realized_outcome} | {result.was_hunter_correct} | "
                f"{result.leakage_validation} | {result.survivorship_validation} |"
            )
        if run.explanations:
            lines.extend(["", "## Historical Explainability"])
            for explanation in run.explanations:
                lines.append(
                    f"- {explanation.case_id}: {explanation.decision} - {explanation.decision_reason}; "
                    f"existing={','.join(explanation.existing_evidence_ids) or '-'}; "
                    f"reconstructed={','.join(explanation.reconstructed_evidence_ids) or '-'}; "
                    f"unavailable={','.join(explanation.unavailable_evidence) or '-'}; "
                    f"reason={explanation.unavailable_reason}"
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
                    "performance_metrics": (
                        None
                        if run.performance_metrics is None
                        else {
                            "accuracy": run.performance_metrics.accuracy,
                            "precision": run.performance_metrics.precision,
                            "recall": run.performance_metrics.recall,
                            "f1": run.performance_metrics.f1,
                            "maximum_drawdown": run.performance_metrics.maximum_drawdown,
                            "win_rate": run.performance_metrics.win_rate,
                        }
                    ),
                },
                sort_keys=True,
                indent=2,
            ),
            encoding="utf-8",
        )
        _write_csv(root / "project_outcomes.csv", run.challenge_results)
        _write_engine_csv(root / "engine_metrics.csv", run)
        _write_calibration_csv(root / "calibration_buckets.csv", run)
        _write_decision_outcomes_csv(root / "decision_outcomes.csv", run)

    def render_benchmarks(self, run: HistoricalBacktestRun) -> str:
        lines = [
            "case_id\tbenchmark\twindow_days\tabsolute_return\texcess_return\tcoverage\tmissing"
            "\treconstruction_confidence\tcompleteness\tfreshness"
        ]
        for row in run.benchmark_outcomes:
            lines.append(
                f"{row.case_id}\t{row.benchmark_id}\t{row.window_days}\t{row.absolute_return}\t{row.excess_return}"
                f"\t{row.coverage_percentage:.2f}\t{','.join(row.missing_evidence_categories) or '-'}"
                f"\t{row.reconstruction_confidence:.4f}\t{row.historical_completeness:.4f}"
                f"\t{row.evidence_freshness:.4f}"
            )
        return "\n".join(lines)

    def render_calibration(self, run: HistoricalBacktestRun) -> str:
        lines = ["metric_id\texpected_probability\tobserved_probability\tcalibration_error\tstatus"]
        for row in run.calibration_metrics:
            lines.append(
                f"{row.metric_id}\t{row.expected_probability}\t{row.observed_probability}"
                f"\t{row.calibration_error}\t{row.sample_size_status}"
            )
        return "\n".join(lines)

    def render_explanation(self, run: HistoricalBacktestRun, case_id: str | None = None) -> str:
        lines = ["case_id\tproject\tdecision\treason\texisting\treconstructed\tunavailable\tunavailable_reason"]
        for row in run.explanations:
            if case_id and row.case_id != case_id and row.project_id != case_id:
                continue
            lines.append(
                f"{row.case_id}\t{row.project_id}\t{row.decision}\t{row.decision_reason}"
                f"\t{','.join(row.existing_evidence_ids) or '-'}"
                f"\t{','.join(row.reconstructed_evidence_ids) or '-'}"
                f"\t{','.join(row.unavailable_evidence) or '-'}"
                f"\t{row.unavailable_reason}"
            )
        return "\n".join(lines)

    def render_comparison(self, left: HistoricalBacktestRun, right: HistoricalBacktestRun) -> str:
        return (
            f"left={left.run_id}\tright={right.run_id}"
            f"\tleft_cases={len(left.cases)}\tright_cases={len(right.cases)}"
            f"\tleft_coverage={left.historical_coverage:.2f}\tright_coverage={right.historical_coverage:.2f}"
            f"\tleft_leakage={'PASS' if left.leakage_passed else 'FAIL'}"
            f"\tright_leakage={'PASS' if right.leakage_passed else 'FAIL'}"
        )


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


def _write_decision_outcomes_csv(path: Path, run: HistoricalBacktestRun) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "case_id",
                "project_id",
                "decision_date",
                "hunter_score",
                "timing",
                "committee_decision",
                "confidence",
                "freshness",
                "final_outcome",
                "leakage_status",
            ]
        )
        for row in run.decision_outcomes:
            writer.writerow(
                [
                    row.case_id,
                    row.project_id,
                    row.decision_date.isoformat(),
                    row.hunter_score,
                    row.timing,
                    row.committee_decision,
                    row.confidence,
                    row.freshness,
                    row.final_outcome,
                    row.leakage_status,
                ]
            )
