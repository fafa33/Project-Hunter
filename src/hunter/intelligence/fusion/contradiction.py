from __future__ import annotations

from collections import defaultdict

from hunter.intelligence.fusion.models import ContradictionAssessment, FusionInput


def assess_contradictions(inputs: tuple[FusionInput, ...]) -> ContradictionAssessment:
    category_strengths: dict[str, list[float]] = defaultdict(list)
    for item in inputs:
        for index, category in enumerate(item.signal_categories):
            if index < len(item.signal_strengths):
                category_strengths[category].append(item.signal_strengths[index])
    contradicted: set[str] = set()
    spreads: list[float] = []
    for category, strengths in category_strengths.items():
        if len(strengths) < 2:
            continue
        spread = max(strengths) - min(strengths)
        if spread >= 0.5:
            contradicted.add(category)
            spreads.append(spread)
    severity = sum(spreads) / len(spreads) if spreads else 0.0
    explanation = "No material contradictions detected" if not contradicted else f"{len(contradicted)} contradicted category(s)"
    return ContradictionAssessment(
        contradicted_categories=tuple(contradicted),
        severity=severity,
        explanation=explanation,
    )
