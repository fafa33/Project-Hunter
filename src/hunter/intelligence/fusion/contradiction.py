from __future__ import annotations

from collections import defaultdict

from hunter.intelligence.fusion.models import ContradictionAssessment, FusionInput


def assess_contradictions(inputs: tuple[FusionInput, ...]) -> ContradictionAssessment:
    category_items: dict[str, list[tuple[float, float, float]]] = defaultdict(list)
    for item in inputs:
        quality = _evidence_quality(item)
        time_weight = _time_weight(item, inputs)
        for index, category in enumerate(item.signal_categories):
            if index < len(item.signal_strengths):
                confidence = item.signal_confidences[index] if index < len(item.signal_confidences) else item.confidence_score
                category_items[category].append((item.signal_strengths[index], confidence, quality * time_weight))
    contradicted: set[str] = set()
    severities: list[float] = []
    for category, items in category_items.items():
        if len(items) < 2:
            continue
        strengths = [item[0] for item in items]
        spread = max(strengths) - min(strengths)
        has_opposing_direction = min(strengths) <= 0.45 and max(strengths) >= 0.55
        if spread >= 0.5 and has_opposing_direction:
            contradicted.add(category)
            confidence_quality = sum(item[1] * item[2] for item in items) / len(items)
            severities.append(spread * confidence_quality)
    severity = sum(severities) / len(severities) if severities else 0.0
    explanation = "No material contradictions detected" if not contradicted else f"{len(contradicted)} contradicted category(s)"
    return ContradictionAssessment(
        contradicted_categories=tuple(contradicted),
        severity=severity,
        explanation=explanation,
    )


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
