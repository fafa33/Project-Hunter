from __future__ import annotations

import csv
import json
from io import StringIO
from pathlib import Path
from typing import Any

from hunter.market_validation.evidence import EvidenceCoverageAnalyzer, EvidenceReportRenderer
from hunter.market_validation.models import MarketValidationComparison, MarketValidationRun, ProjectValidationResult


class MarketValidationRenderer:
    def render_json(self, run: MarketValidationRun) -> str:
        return json.dumps(_run_payload(run), sort_keys=True, indent=2)

    def render_csv(self, run: MarketValidationRun) -> str:
        output = StringIO()
        writer = csv.DictWriter(
            output, fieldnames=list(_row(run.project_results[0]).keys()) if run.project_results else []
        )
        writer.writeheader()
        for result in run.project_results:
            writer.writerow(_row(result))
        return output.getvalue()

    def render_markdown(self, run: MarketValidationRun) -> str:
        evidence_report = EvidenceCoverageAnalyzer().analyze(run)
        evidence_renderer = EvidenceReportRenderer()
        leader = run.champion_project_id or "No Qualified Candidate"
        runner_up = run.runner_up_project_id or "none"
        lines = [
            "# Project Hunter V1 Real Market Validation",
            "",
            f"Run ID: `{run.run_id}`",
            f"Projects: {len(run.project_results)}",
            f"Top candidate: {leader}",
            f"Runner-up: {runner_up}",
            f"No-qualified-candidate state: {run.no_qualified_candidate}",
            "",
            "## Full Ranking",
            "",
            "| Rank | Project | Sector | Score | Confidence | Committee | Missing | Stale |",
            "| --- | --- | --- | ---: | ---: | --- | --- | --- |",
        ]
        for item in run.project_results:
            lines.append(
                f"| {item.rank} | {item.project_id} | {item.sector} | {item.hunter_score:.4f} | "
                f"{item.confidence:.4f} | {item.committee_decision} | "
                f"{', '.join(item.missing_evidence) or 'none'} | {', '.join(item.stale_evidence) or 'none'} |"
            )
        lines.extend(["", "## Sector Ranking", ""])
        for sector in sorted({item.sector for item in run.project_results}):
            lines.append(f"### {sector}")
            for item in [row for row in run.project_results if row.sector == sector]:
                lines.append(f"- {item.sector_rank}. {item.project_id}: {item.hunter_score:.4f}")
        lines.extend(["", "## Score Breakdown", ""])
        for item in run.project_results[:10]:
            lines.append(f"### {item.project_id}")
            lines.append(f"- Reasons: {', '.join(item.reasons_for_ranking)}")
            lines.append(f"- Positive drivers: {', '.join(item.strongest_positive_drivers) or 'none'}")
            lines.append(f"- Negative drivers: {', '.join(item.strongest_negative_drivers) or 'none'}")
            lines.append(f"- Validation warnings: {', '.join(item.validation_warnings) or 'none'}")
        lines.extend(
            [
                "",
                "## Decision Breakdown",
                "",
                "Deterministic decision audits are available through `hunter explain PROJECT`.",
                "",
                "## Evidence Trace",
                "",
                "Evidence trace details are rendered by the Decision Explainability & Audit Engine.",
                "",
                "## Real Evidence Coverage",
                "",
                evidence_renderer.render_status(evidence_report),
                "",
                "## Evidence Completeness",
                "",
                evidence_renderer.render_coverage(evidence_report),
                "",
                "## Engine Availability",
                "",
                evidence_renderer.render_validate(evidence_report),
                "",
                "## Collector Status",
                "",
                evidence_renderer.render_sources(evidence_report),
                "",
                "## Missing Sources",
                "",
                evidence_renderer.render_missing(evidence_report),
                "",
                "## Evidence Freshness",
                "",
                evidence_renderer.render_freshness(evidence_report),
                "",
                "## Repository Trace",
                "",
                evidence_renderer.render_sources(evidence_report),
                "",
                "## Contribution Table",
                "",
                "Contribution tables preserve raw score, normalized score, applied weight, and final contribution.",
                "",
                "## Decision Tree",
                "",
                "Decision trees are generated deterministically from persisted validation outputs.",
                "",
                "## Why This Project",
                "",
                "Project-specific reasons are derived from persisted score and committee fields.",
                "",
                "## Why Not Competitor",
                "",
                "Pairwise comparisons are available through `hunter explain compare PROJECT_A PROJECT_B`.",
                "",
                "## Top Positive Drivers",
                "",
                "Top positive drivers are preserved per project result.",
                "",
                "## Top Negative Drivers",
                "",
                "Top negative drivers are preserved per project result.",
                "",
                "## Invalidation Conditions",
                "",
                "Invalidation conditions are rendered by the audit engine without changing scores.",
                "",
                "## Sensitivity Analysis",
                "",
                "Sensitivity analysis reports contribution removal effects without recalculating scores.",
            ]
        )
        return "\n".join(lines) + "\n"

    def render_comparison_markdown(self, comparison: MarketValidationComparison) -> str:
        lines = [
            "# Market Validation Run Comparison",
            "",
            f"Left run: `{comparison.left_run_id}`",
            f"Right run: `{comparison.right_run_id}`",
            f"Champion change: {comparison.champion_change}",
            "",
            "| Project | Rank Change | Score Change | Confidence Change | Committee Change | Evidence Change |",
            "| --- | ---: | ---: | ---: | --- | ---: |",
        ]
        for delta in comparison.project_deltas:
            lines.append(
                f"| {delta.project_id} | {delta.rank_change} | {delta.score_change:.4f} | "
                f"{delta.confidence_change:.4f} | {delta.committee_change} | {delta.evidence_change} |"
            )
        return "\n".join(lines) + "\n"

    def write_reports(self, run: MarketValidationRun, output_directory: str | Path) -> tuple[Path, ...]:
        output = Path(output_directory)
        output.mkdir(parents=True, exist_ok=True)
        files = (
            output / f"{run.run_id}.csv",
            output / f"{run.run_id}.md",
            output / f"{run.run_id}.json",
        )
        files[0].write_text(self.render_csv(run), encoding="utf-8")
        files[1].write_text(self.render_markdown(run), encoding="utf-8")
        files[2].write_text(self.render_json(run), encoding="utf-8")
        return files


def _run_payload(run: MarketValidationRun) -> dict[str, Any]:
    return {
        "run_id": run.run_id,
        "effective_at": run.effective_at.isoformat(),
        "created_at": run.created_at.isoformat(),
        "champion_project_id": run.champion_project_id,
        "runner_up_project_id": run.runner_up_project_id,
        "no_qualified_candidate": run.no_qualified_candidate,
        "results": [_row(item) for item in run.project_results],
    }


def _row(result: ProjectValidationResult) -> dict[str, Any]:
    return {
        "rank": result.rank,
        "sector_rank": result.sector_rank,
        "project_id": result.project_id,
        "project_name": result.project_name,
        "sector": result.sector,
        "hunter_score": result.hunter_score,
        "risk": result.risk,
        "confidence": result.confidence,
        "valuation": result.valuation,
        "comparative_valuation": result.comparative_valuation,
        "mispricing": result.mispricing,
        "asymmetry": result.asymmetry,
        "whale_intelligence": result.whale_intelligence,
        "macro_intelligence": result.macro_intelligence,
        "future_demand": result.future_demand,
        "opportunity_timing": result.opportunity_timing,
        "probability": result.probability,
        "pattern_matching": result.pattern_matching,
        "technology_necessity": result.technology_necessity,
        "capital_rotation": result.capital_rotation,
        "necessity_gap": result.necessity_gap,
        "committee_decision": result.committee_decision,
        "committee_confidence": result.committee_confidence,
        "final_rank": result.rank,
        "missing_evidence": ";".join(result.missing_evidence),
        "stale_evidence": ";".join(result.stale_evidence),
        "data_freshness": result.data_freshness,
        "validation_health": result.validation_health,
        "strongest_positive_drivers": ";".join(result.strongest_positive_drivers),
        "strongest_negative_drivers": ";".join(result.strongest_negative_drivers),
        "reasons_for_ranking": ";".join(result.reasons_for_ranking),
        "validation_warnings": ";".join(result.validation_warnings),
        "engine_availability": ";".join(f"{source.engine}:{source.status}" for source in result.engine_sources),
        "collector_status": ";".join(f"{source.engine}:{source.collector}" for source in result.engine_sources),
        "evidence_ids": ";".join(
            evidence_id for source in result.engine_sources for evidence_id in source.evidence_ids
        ),
        "repository_ids": ";".join(
            repository_id for source in result.engine_sources for repository_id in source.repository_ids
        ),
    }
