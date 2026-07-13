from __future__ import annotations

from hunter.explainability.models import DecisionAudit, RankComparison


class DecisionAuditRenderer:
    def render_project(self, audit: DecisionAudit) -> str:
        lines = [
            f"# Decision Audit: {audit.project_id}",
            "",
            f"Final Score: {audit.final_score:.4f}",
            f"Rank: {audit.rank}",
            f"Committee: {audit.committee_decision} ({audit.committee_confidence:.4f})",
            "",
            "## Decision Breakdown",
            "",
            "| Engine | Raw Score | Normalized Score | Base Weight | Adjusted Weight | Contribution | Confidence | Freshness | Coverage | Version |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
        ]
        for item in audit.contributions:
            lines.append(
                f"| {item.engine} | {item.raw_score:.4f} | {item.normalized_score:.4f} | "
                f"{item.base_weight:.6f} | {item.adjusted_weight:.6f} | "
                f"{item.final_score_contribution:.4f} | {item.confidence:.4f} | "
                f"{item.freshness:.4f} | {item.evidence_coverage:.4f} | {item.scoring_version or '-'} |"
            )
        lines.extend(["", "## Evidence Trace", ""])
        for trace in audit.evidence_trace:
            lines.append(f"### {trace.engine}")
            lines.append(f"- Evidence IDs: {', '.join(trace.evidence_ids)}")
            lines.append(f"- Repository IDs: {', '.join(trace.repository_ids)}")
            lines.append(f"- Timestamp: {trace.timestamp.isoformat()}")
            lines.append(f"- Confidence: {trace.confidence:.4f}")
            lines.append(f"- Freshness: {trace.freshness:.4f}")
            if trace.engine == "Opportunity Timing":
                lines.append(f"- Timing freshness: {trace.freshness:.4f}")
            lines.append(f"- Missing evidence: {', '.join(trace.missing_evidence) or 'none'}")
            lines.append(f"- Stale evidence: {', '.join(trace.stale_evidence) or 'none'}")
            lines.append(f"- Validation warnings: {', '.join(trace.validation_warnings) or 'none'}")
        lines.extend(["", "## Decision Tree", ""])
        lines.extend(f"- {item}" for item in audit.decision_tree)
        lines.extend(["", "## Top Positive Drivers", ""])
        lines.extend(
            f"- {item.engine}: {item.final_score_contribution:.4f}" for item in audit.top_positive_contributors
        )
        lines.extend(["", "## Top Negative Drivers", ""])
        lines.extend(
            f"- {item.engine}: {item.final_score_contribution:.4f}" for item in audit.top_negative_contributors
        )
        lines.extend(["", "## Invalidation Conditions", ""])
        lines.extend(f"- {item}" for item in audit.invalidation_conditions)
        lines.extend(["", "## Sensitivity Analysis", ""])
        lines.extend(
            f"- Without {item.engine}: final score decreases by {item.final_score_decrease_if_removed:.4f}"
            for item in audit.sensitivity
        )
        return "\n".join(lines) + "\n"

    def render_comparison(self, comparison: RankComparison) -> str:
        lines = [
            f"# Rank Comparison: {comparison.left_project_id} vs {comparison.right_project_id}",
            "",
            f"Final ranking difference: {comparison.final_ranking_difference}",
            f"Largest confidence difference: {comparison.largest_confidence_difference:.4f}",
            f"Largest risk difference: {comparison.largest_risk_difference:.4f}",
            f"Largest valuation difference: {comparison.largest_valuation_difference:.4f}",
            f"Largest macro difference: {comparison.largest_macro_difference:.4f}",
            f"Largest future-demand difference: {comparison.largest_future_demand_difference:.4f}",
            f"Largest committee difference: {comparison.largest_committee_difference:.4f}",
            "",
            "| Engine | Left | Right | Difference | Preferred |",
            "| --- | ---: | ---: | ---: | --- |",
        ]
        for item in comparison.largest_score_differences:
            lines.append(
                f"| {item.engine} | {item.left_score:.4f} | {item.right_score:.4f} | "
                f"{item.difference:.4f} | {item.preferred_project_id} |"
            )
        return "\n".join(lines) + "\n"

    def render_ranking(self, audits: tuple[DecisionAudit, ...]) -> str:
        lines = [
            "# Decision Audit Ranking",
            "",
            "| Rank | Project | Score | Committee | Top Contributor |",
            "| ---: | --- | ---: | --- | --- |",
        ]
        for audit in audits:
            top = audit.top_positive_contributors[0].engine if audit.top_positive_contributors else "none"
            lines.append(
                f"| {audit.rank} | {audit.project_id} | {audit.final_score:.4f} | {audit.committee_decision} | {top} |"
            )
        return "\n".join(lines) + "\n"
