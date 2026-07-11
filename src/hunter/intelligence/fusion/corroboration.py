from __future__ import annotations

from collections import defaultdict

from hunter.intelligence.fusion.models import CorroborationAssessment, DependencyAssessment, FusionInput


def assess_corroboration(
    inputs: tuple[FusionInput, ...],
    dependencies: DependencyAssessment,
) -> CorroborationAssessment:
    category_directions: dict[tuple[str, str], set[str]] = defaultdict(set)
    category_scores: dict[tuple[str, str], list[float]] = defaultdict(list)
    dependent_pairs = {tuple(sorted(edge[:2])) for edge in dependencies.dependency_edges}
    for item in inputs:
        quality = _evidence_quality(item)
        for index, category in enumerate(item.signal_categories):
            direction = _direction(item.signal_strengths[index]) if index < len(item.signal_strengths) else "neutral"
            confidence = (
                item.signal_confidences[index] if index < len(item.signal_confidences) else item.confidence_score
            )
            key = (category, direction)
            category_directions[key].add(item.engine_id)
            category_scores[key].append(confidence * quality * _time_weight(item, inputs))
    corroborated: set[str] = set()
    engines: set[str] = set()
    scores: list[float] = []
    for (category, _direction_name), category_engine_ids in category_directions.items():
        independent = _independent_engines(category_engine_ids, dependent_pairs)
        if len(independent) > 1:
            corroborated.add(category)
            engines.update(independent)
            scores.extend(category_scores[(category, _direction_name)])
    total = len({category for category, _ in category_directions})
    category_score = len(corroborated) / total if total else 0.0
    quality_score = sum(scores) / len(scores) if scores else 0.0
    score = (0.6 * category_score) + (0.4 * quality_score) if corroborated else 0.0
    explanation = (
        "No independently corroborated categories"
        if not corroborated
        else f"{len(corroborated)} corroborated category(s)"
    )
    return CorroborationAssessment(
        corroborated_categories=tuple(corroborated),
        corroborating_engine_ids=tuple(engines),
        score=score,
        explanation=explanation,
    )


def _independent_engines(engine_ids: set[str], dependent_pairs: set[tuple[str, str]]) -> set[str]:
    independent: set[str] = set()
    for engine_id in engine_ids:
        if any(engine_id in pair and pair[0] in engine_ids and pair[1] in engine_ids for pair in dependent_pairs):
            continue
        independent.add(engine_id)
    return independent


def _direction(strength: float) -> str:
    if strength >= 0.55:
        return "positive"
    if strength <= 0.45:
        return "negative"
    return "neutral"


def _evidence_quality(item: FusionInput) -> float:
    values = tuple(item.evidence_reliabilities) + tuple(item.evidence_freshness)
    return sum(values) / len(values) if values else 0.5


def _time_weight(item: FusionInput, inputs: tuple[FusionInput, ...]) -> float:
    latest = max(input_item.effective_at for input_item in inputs)
    age_days = abs((latest - item.effective_at).total_seconds()) / 86400
    if age_days <= 7:
        return 1.0
    if age_days <= 30:
        return 0.75
    return 0.5
