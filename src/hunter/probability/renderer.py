from __future__ import annotations

from hunter.probability.models import ProbabilityAssessment


class ProbabilityReportRenderer:
    def render_sections(self, assessment: ProbabilityAssessment) -> tuple[tuple[str, str], ...]:
        return (
            ("Probability Score", f"{assessment.probability_score:.4f} - {assessment.probability_label}"),
            ("Success Probability", f"{assessment.success_probability:.4f}"),
            ("Failure Probability", f"{assessment.failure_probability:.4f}"),
            ("Consensus", f"{assessment.consensus_score:.4f}"),
            ("Conflict", f"{assessment.conflict_score:.4f}"),
            ("Evidence Robustness", f"{assessment.evidence_robustness:.4f}"),
            ("Historical Reliability", f"{assessment.historical_reliability:.4f}"),
            ("Supporting Engines", _lines(assessment.supporting_engines)),
            ("Conflicting Engines", _lines(assessment.conflicting_engines)),
            ("Confidence", f"{assessment.decision_confidence:.4f}"),
            ("Explanation", _lines(assessment.explanation)),
        )

    def render_markdown(self, assessment: ProbabilityAssessment) -> str:
        return "\n\n".join(f"## {title}\n\n{body}" for title, body in self.render_sections(assessment))


def _lines(values: tuple[str, ...]) -> str:
    return "\n".join(f"- {value}" for value in values) if values else "- none"
