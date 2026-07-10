from __future__ import annotations

from hunter.intelligence.fusion.models import FusionInput, FusionTarget, UnifiedInsight, UnifiedNarrative


def build_unified_narrative(
    target: FusionTarget,
    inputs: tuple[FusionInput, ...],
    insights: tuple[UnifiedInsight, ...],
    confidence: dict[str, float],
) -> UnifiedNarrative:
    engine_count = len({item.engine_id for item in inputs})
    summary = (
        f"Fusion for {target.target_type}:{target.target_id} combines "
        f"{len(inputs)} intelligence input(s) from {engine_count} engine(s) with confidence {confidence.get('score', 0.0):.4f}."
    )
    key_points = tuple(insight.title for insight in sorted(insights, key=lambda item: (-item.priority, item.id))[:5])
    if not key_points:
        key_points = ("No unified insight exceeded the fusion threshold.",)
    uncertainty = "High uncertainty" if confidence.get("uncertainty", 1.0) >= 0.5 else "Moderate uncertainty"
    return UnifiedNarrative(
        summary=summary,
        key_points=key_points,
        uncertainty=uncertainty,
        source_insight_ids=tuple(source_id for insight in insights for source_id in insight.source_insight_ids),
    )
