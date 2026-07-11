from __future__ import annotations

from hunter.patterns.models import PatternMatch, PatternMatchingAssessment


class PatternReportRenderer:
    def render_sections(self, assessment: PatternMatchingAssessment) -> tuple[tuple[str, str], ...]:
        return (
            ("Pattern Matching", f"{assessment.overall_similarity:.4f} similarity"),
            ("Historical Matches", _matches(assessment.top_matches)),
            ("Historical Similarity", f"{assessment.historical_similarity:.4f}"),
            ("Context Similarity", f"{assessment.context_similarity:.4f}"),
            ("Similarity Breakdown", _breakdown(assessment.top_matches)),
            ("Positive Patterns", _matches(assessment.positive_matches)),
            ("Negative Patterns", _matches(assessment.negative_matches)),
            ("Matching Factors", _lines(tuple(_collect(assessment.top_matches, "matching_factors")))),
            ("Differing Factors", _lines(tuple(_collect(assessment.top_matches, "differing_factors")))),
            ("Historical Confidence", f"{assessment.historical_confidence:.4f}"),
        )

    def render_markdown(self, assessment: PatternMatchingAssessment) -> str:
        return "\n\n".join(f"## {title}\n\n{body}" for title, body in self.render_sections(assessment))


def _matches(matches: tuple[PatternMatch, ...]) -> str:
    if not matches:
        return "- none"
    return "\n".join(
        f"- {match.project_name}: {match.similarity_percent:.2f}% "
        f"({match.label}; historical={match.historical_similarity:.4f}; context={match.context_similarity:.4f})"
        for match in matches
    )


def _breakdown(matches: tuple[PatternMatch, ...]) -> str:
    if not matches:
        return "- none"
    top = matches[0]
    historical = [f"- historical.{key}: {value:.4f}" for key, value in top.breakdown.dimensions.as_dict().items()]
    context = [f"- context.{key}: {value:.4f}" for key, value in top.breakdown.context_dimensions.as_dict().items()]
    return "\n".join((*historical, *context))


def _collect(matches: tuple[PatternMatch, ...], attribute: str) -> set[str]:
    values: set[str] = set()
    for match in matches:
        values.update(getattr(match, attribute))
    return values


def _lines(values: tuple[str, ...]) -> str:
    return "\n".join(f"- {value}" for value in sorted(values)) if values else "- none"
