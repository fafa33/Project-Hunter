from __future__ import annotations

from hunter.opportunity.models import OpportunityAssessment


class OpportunityReportRenderer:
    def render_sections(self, assessment: OpportunityAssessment) -> tuple[tuple[str, str], ...]:
        return (
            ("Opportunity Score", f"{assessment.opportunity_score:.4f} - {assessment.opportunity_label}"),
            ("Conviction", f"{assessment.conviction_score:.4f} - {assessment.conviction_explanation}"),
            ("Opportunity Window", assessment.opportunity_window),
            ("Risk/Reward", assessment.risk_reward_balance),
            ("Positive Drivers", _lines(assessment.positive_factors)),
            ("Negative Drivers", _lines(assessment.negative_factors)),
            ("Supporting Evidence", _lines(assessment.supporting_evidence)),
            ("Missing Evidence", _lines(assessment.missing_evidence)),
            ("Confidence", _confidence(assessment)),
        )

    def render_markdown(self, assessment: OpportunityAssessment) -> str:
        return "\n\n".join(f"## {title}\n\n{body}" for title, body in self.render_sections(assessment))


def _lines(values: tuple[str, ...]) -> str:
    return "\n".join(f"- {value}" for value in values) if values else "- none"


def _confidence(assessment: OpportunityAssessment) -> str:
    return "\n".join(f"- {key}: {value:.4f}" for key, value in assessment.confidence.as_dict().items())
