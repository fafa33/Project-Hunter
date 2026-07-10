from __future__ import annotations

from collections import defaultdict

from hunter.intelligence.fusion.configuration import FusionConfig
from hunter.intelligence.fusion.models import DependencyAssessment, EngineContribution, FusionInput


def build_engine_contributions(
    inputs: tuple[FusionInput, ...],
    config: FusionConfig,
    dependencies: DependencyAssessment,
) -> tuple[EngineContribution, ...]:
    grouped: dict[tuple[str, str | None, str | None, str | None], list[FusionInput]] = defaultdict(list)
    for item in inputs:
        grouped[(item.engine_id, item.engine_version, item.plugin_id, item.plugin_version)].append(item)
    contributions: list[EngineContribution] = []
    dependent_engines = set(dependencies.dependent_engine_ids)
    for key in sorted(grouped):
        engine_id, engine_version, plugin_id, plugin_version = key
        items = grouped[key]
        base_weight = config.weighting.engine_weights.get(engine_id, config.weighting.default_engine_weight)
        dependency_multiplier = 1.0 - config.weighting.dependency_penalty if engine_id in dependent_engines else 1.0
        confidence = sum(item.confidence_score for item in items) / len(items)
        contributions.append(
            EngineContribution(
                engine_id=engine_id,
                engine_version=engine_version,
                plugin_id=plugin_id,
                plugin_version=plugin_version,
                intelligence_ids=tuple(item.intelligence_id for item in items),
                evidence_count=sum(len(item.evidence_ids) for item in items),
                signal_count=sum(len(item.signal_ids) for item in items),
                observation_count=sum(len(item.observation_ids) for item in items),
                insight_count=sum(len(item.insight_ids) for item in items),
                weight=base_weight * confidence * dependency_multiplier,
                confidence=confidence,
            )
        )
    return tuple(contributions)
