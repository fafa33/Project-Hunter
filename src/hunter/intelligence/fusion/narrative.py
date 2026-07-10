from __future__ import annotations

from hunter.intelligence.fusion.models import (
    ContradictionAssessment,
    CorroborationAssessment,
    DependencyAssessment,
    FusionInput,
    FusionTarget,
    MissingEvidenceAssessment,
    UnifiedInsight,
    UnifiedNarrative,
)


def build_unified_narrative(
    target: FusionTarget,
    inputs: tuple[FusionInput, ...],
    insights: tuple[UnifiedInsight, ...],
    confidence: dict[str, float],
    corroboration: CorroborationAssessment | None = None,
    contradictions: ContradictionAssessment | None = None,
    dependencies: DependencyAssessment | None = None,
    missing_evidence: MissingEvidenceAssessment | None = None,
) -> UnifiedNarrative:
    engine_count = len({item.engine_id for item in inputs})
    positive = [signal for signal in _signals_from_inputs(inputs) if signal[1] >= 0.55]
    negative = [signal for signal in _signals_from_inputs(inputs) if signal[1] <= 0.45]
    dominant_positive = max(positive, key=lambda item: (item[1], item[2], item[0]), default=("none", 0.0, 0.0))
    dominant_negative = min(negative, key=lambda item: (item[1], -item[2], item[0]), default=("none", 0.0, 0.0))
    summary = (
        f"Fusion for {target.target_type}:{target.target_id} combines "
        f"{len(inputs)} intelligence input(s) from {engine_count} engine(s) with confidence {confidence.get('score', 0.0):.4f}."
    )
    key_points = (
        f"Strongest corroboration: {_corroboration_text(corroboration)}.",
        f"Strongest contradiction: {_contradiction_text(contradictions)}.",
        f"Major dependency: {_dependency_text(dependencies)}.",
        f"Missing evidence: {_missing_text(missing_evidence)}.",
        f"Dominant positive case: {dominant_positive[0]} ({dominant_positive[1]:.4f}).",
        f"Dominant negative case: {dominant_negative[0]} ({dominant_negative[1]:.4f}).",
        f"Unresolved uncertainty: {confidence.get('uncertainty', 1.0):.4f}.",
    )
    uncertainty = "High uncertainty" if confidence.get("uncertainty", 1.0) >= 0.5 else "Moderate uncertainty"
    return UnifiedNarrative(
        summary=summary,
        key_points=key_points,
        uncertainty=uncertainty,
        source_insight_ids=tuple(source_id for insight in insights for source_id in insight.source_insight_ids),
    )


def _signals_from_inputs(inputs: tuple[FusionInput, ...]) -> tuple[tuple[str, float, float], ...]:
    rows: list[tuple[str, float, float]] = []
    for item in inputs:
        for index, category in enumerate(item.signal_categories):
            strength = item.signal_strengths[index] if index < len(item.signal_strengths) else 0.5
            confidence = item.signal_confidences[index] if index < len(item.signal_confidences) else item.confidence_score
            rows.append((category, strength, confidence))
    return tuple(rows)


def _strongest_insight(insights: tuple[UnifiedInsight, ...]) -> str:
    if not insights:
        return "none"
    return max(insights, key=lambda item: (item.priority, item.confidence, item.id)).title


def _corroboration_text(assessment: CorroborationAssessment | None) -> str:
    if assessment is None or not assessment.corroborated_categories:
        return "none"
    return f"{assessment.corroborated_categories[0]} ({assessment.score:.4f})"


def _contradiction_text(assessment: ContradictionAssessment | None) -> str:
    if assessment is None or not assessment.contradicted_categories:
        return "none"
    return f"{assessment.contradicted_categories[0]} ({assessment.severity:.4f})"


def _dependency_text(assessment: DependencyAssessment | None) -> str:
    if assessment is None or not assessment.dependency_edges:
        return "none"
    source, target, reason = assessment.dependency_edges[0]
    return f"{source}->{target} {reason}"


def _missing_text(assessment: MissingEvidenceAssessment | None) -> str:
    if assessment is None or not assessment.missing_categories:
        return "none"
    return ", ".join(assessment.missing_categories)
