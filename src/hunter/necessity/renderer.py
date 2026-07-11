from __future__ import annotations

from hunter.necessity.models import TechnologyNecessityAssessment


class TechnologyNecessityReportRenderer:
    def render_sections(self, assessment: TechnologyNecessityAssessment) -> tuple[tuple[str, str], ...]:
        return (
            ("Technology Necessity", f"{assessment.technology_necessity_score:.4f} - {assessment.label}"),
            ("Capital Rotation", f"{assessment.capital_rotation_score:.4f}"),
            ("Necessity Gap", f"{assessment.necessity_gap:.4f}"),
            ("Infrastructure Criticality", f"{assessment.infrastructure_criticality:.4f}"),
            ("Dependency Strength", f"{assessment.dependency_strength:.4f}"),
            ("Replacement Difficulty", f"{assessment.replacement_difficulty:.4f}"),
            ("Technology Position", _lines(assessment.technology_position)),
            ("Supporting Evidence", _lines(assessment.supporting_evidence)),
            ("Missing Evidence", _lines(assessment.missing_evidence)),
            ("Confidence", f"{assessment.confidence:.4f}"),
        )

    def render_markdown(self, assessment: TechnologyNecessityAssessment) -> str:
        return "\n\n".join(f"## {title}\n\n{body}" for title, body in self.render_sections(assessment))


def _lines(values: tuple[str, ...]) -> str:
    return "\n".join(f"- {value}" for value in values) if values else "- none"
