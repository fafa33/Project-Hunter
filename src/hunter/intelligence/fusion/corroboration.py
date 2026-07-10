from __future__ import annotations

from collections import defaultdict

from hunter.intelligence.fusion.models import CorroborationAssessment, DependencyAssessment, FusionInput


def assess_corroboration(
    inputs: tuple[FusionInput, ...],
    dependencies: DependencyAssessment,
) -> CorroborationAssessment:
    category_engines: dict[str, set[str]] = defaultdict(set)
    dependent_pairs = {tuple(sorted(edge[:2])) for edge in dependencies.dependency_edges}
    for item in inputs:
        for category in item.signal_categories:
            category_engines[category].add(item.engine_id)
    corroborated: set[str] = set()
    engines: set[str] = set()
    for category, category_engine_ids in category_engines.items():
        independent = _independent_engines(category_engine_ids, dependent_pairs)
        if len(independent) > 1:
            corroborated.add(category)
            engines.update(independent)
    total = len(category_engines)
    score = len(corroborated) / total if total else 0.0
    explanation = "No independently corroborated categories" if not corroborated else f"{len(corroborated)} corroborated category(s)"
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
