from __future__ import annotations

from dataclasses import asdict

from hunter.execution.identity import fingerprint, identity
from hunter.necessity.configuration import CapitalRotationConfig, TechnologyGraphConfig, TechnologyNecessityConfig
from hunter.necessity.metrics import average, confidence_score, evidence_quality, missing_evidence, numeric
from hunter.necessity.models import (
    TechnologyNecessityAssessment,
    TechnologyNecessityComponent,
    TechnologyNecessityInputSet,
)

IDENTITY_SCHEMA_VERSION = "technology-necessity-identity-v1"


class TechnologyNecessityEngine:
    def __init__(
        self,
        config: TechnologyNecessityConfig | None = None,
        rotation_config: CapitalRotationConfig | None = None,
        graph_config: TechnologyGraphConfig | None = None,
    ) -> None:
        self.config = config or TechnologyNecessityConfig()
        self.rotation_config = rotation_config or CapitalRotationConfig()
        self.graph_config = graph_config or TechnologyGraphConfig(categories=(), dependencies=())

    def assess(
        self,
        inputs: TechnologyNecessityInputSet,
        *,
        config: TechnologyNecessityConfig | None = None,
        rotation_config: CapitalRotationConfig | None = None,
        graph_config: TechnologyGraphConfig | None = None,
    ) -> TechnologyNecessityAssessment:
        active_config = config or self.config
        active_rotation = rotation_config or self.rotation_config
        active_graph = graph_config or self.graph_config
        values = _component_values(inputs, active_config, active_graph)
        rotation = _capital_rotation(inputs, active_rotation)
        values["capital_attraction"] = max(values.get("capital_attraction", 0.0), rotation)
        components = _components(values, inputs, active_config)
        missing = missing_evidence(inputs.fused_intelligence, inputs.opportunity_timing)
        confidence = _confidence(inputs, values, missing, active_config)
        necessity_score = _weighted_score(components)
        recognition = _market_recognition(inputs, values)
        gap = _clamp01(max(0.0, necessity_score - recognition))
        infrastructure = values.get("infrastructure_criticality", 0.0)
        dependency = values.get("dependency_strength", 0.0)
        replacement = values.get("replacement_difficulty", 0.0)
        overall = _clamp01(average((necessity_score, rotation, infrastructure, dependency, replacement, gap)))
        if len(_source_ids(inputs)) < active_config.minimum_source_records:
            overall = 0.0
            confidence = 0.0
        configuration_fingerprint = fingerprint("technology-necessity-configuration", asdict(active_config))
        rotation_fingerprint = fingerprint("capital-rotation-configuration", asdict(active_rotation))
        graph_fingerprint = fingerprint("technology-graph-configuration", asdict(active_graph))
        assessment_id = identity(
            "technology-necessity-assessment",
            {
                "technology_id": inputs.technology_id,
                "effective_at": inputs.effective_at,
                "source_record_ids": _source_ids(inputs),
                "configuration_fingerprint": configuration_fingerprint,
                "rotation_fingerprint": rotation_fingerprint,
                "graph_fingerprint": graph_fingerprint,
                "identity_schema_version": IDENTITY_SCHEMA_VERSION,
            },
        )
        return TechnologyNecessityAssessment(
            assessment_id=assessment_id,
            technology_id=inputs.technology_id,
            effective_at=inputs.effective_at,
            source_record_ids=_source_ids(inputs),
            technology_necessity_score=necessity_score,
            capital_rotation_score=rotation,
            infrastructure_criticality=infrastructure,
            dependency_strength=dependency,
            replacement_difficulty=replacement,
            necessity_gap=gap,
            overall_necessity=overall,
            label=_label(overall, confidence, active_config),
            components=components,
            technology_position=_technology_position(inputs.technology_id, values, active_graph),
            supporting_evidence=tuple(record.id for record in inputs.evidence),
            missing_evidence=missing,
            confidence=confidence,
            metadata={
                "configuration_fingerprint": configuration_fingerprint,
                "rotation_fingerprint": rotation_fingerprint,
                "graph_fingerprint": graph_fingerprint,
                "identity_schema_version": IDENTITY_SCHEMA_VERSION,
            },
        )


def _component_values(
    inputs: TechnologyNecessityInputSet,
    config: TechnologyNecessityConfig,
    graph: TechnologyGraphConfig,
) -> dict[str, float]:
    values: dict[str, list[float]] = {name: [] for name, _ in config.component_weights}
    engine_map = dict(config.engine_component_map)
    for record in inputs.intelligence:
        component = _component_for_engine(record.engine_id, engine_map)
        if component in values:
            values[component].append(confidence_score(record.confidence))
    for record in inputs.fused_intelligence:
        values["evidence_confidence"].append(confidence_score(record.confidence))
        for contribution in record.contributions:
            component = _component_for_engine(str(contribution.get("engine_id", "")), engine_map)
            if component in values:
                values[component].append(numeric(contribution.get("confidence")))
    for record in inputs.opportunity_timing:
        values["adoption_momentum"].append(record.timing_score / 100.0)
        values["technology_maturity"].append(confidence_score(record.confidence))
    for snapshot in inputs.snapshots:
        for key in values:
            if key in snapshot.payload:
                values[key].append(numeric(snapshot.payload[key]))
        for alias, key in (
            ("future_demand", "future_relevance"),
            ("macro_alignment", "technology_necessity"),
            ("probability_score", "technology_necessity"),
            ("pattern_similarity", "market_awareness"),
        ):
            if alias in snapshot.payload and key in values:
                values[key].append(numeric(snapshot.payload[alias]))
    values["dependency_strength"].append(graph.dependency_strength(inputs.technology_id))
    values["evidence_confidence"].append(evidence_quality(inputs.evidence, inputs.fused_intelligence))
    return {name: average(tuple(score for score in scores if score > 0.0)) for name, scores in values.items()}


def _capital_rotation(inputs: TechnologyNecessityInputSet, config: CapitalRotationConfig) -> float:
    values: dict[str, list[float]] = {name: [] for name, _ in config.weights}
    for snapshot in inputs.snapshots:
        for key in values:
            if key in snapshot.payload:
                values[key].append(numeric(snapshot.payload[key]))
        if "capital_leaving" in snapshot.payload:
            values["capital_leaving_inverse"].append(1.0 - numeric(snapshot.payload["capital_leaving"]))
    averaged = {name: average(tuple(scores)) for name, scores in values.items()}
    return _clamp01(sum(averaged[name] * weight for name, weight in config.weights))


def _components(
    values: dict[str, float],
    inputs: TechnologyNecessityInputSet,
    config: TechnologyNecessityConfig,
) -> tuple[TechnologyNecessityComponent, ...]:
    evidence_ids = tuple(record.id for record in inputs.evidence)
    return tuple(
        sorted(
            (
                TechnologyNecessityComponent(
                    name=name,
                    value=values.get(name, 0.0),
                    weight=weight,
                    contribution=round(values.get(name, 0.0) * weight, 4),
                    evidence_ids=evidence_ids,
                    explanation=f"{name} contributed {round(values.get(name, 0.0) * weight, 4)} from persisted inputs",
                )
                for name, weight in config.component_weights
            ),
            key=lambda item: item.name,
        )
    )


def _weighted_score(components: tuple[TechnologyNecessityComponent, ...]) -> float:
    return _clamp01(sum(component.contribution for component in components))


def _market_recognition(inputs: TechnologyNecessityInputSet, values: dict[str, float]) -> float:
    recognition = [values.get("market_awareness", 0.0), values.get("capital_attraction", 0.0)]
    for snapshot in inputs.snapshots:
        if "market_recognition" in snapshot.payload:
            recognition.append(numeric(snapshot.payload["market_recognition"]))
    return average(tuple(recognition))


def _confidence(
    inputs: TechnologyNecessityInputSet,
    values: dict[str, float],
    missing: tuple[str, ...],
    config: TechnologyNecessityConfig,
) -> float:
    coverage = sum(1 for value in values.values() if value > 0.0) / max(1, len(dict(config.component_weights)))
    quality = evidence_quality(inputs.evidence, inputs.fused_intelligence)
    missing_penalty = len(missing) * config.missing_evidence_penalty / max(1, len(dict(config.component_weights)))
    return _clamp01(average((coverage, quality)) - missing_penalty)


def _label(score: float, confidence: float, config: TechnologyNecessityConfig) -> str:
    if confidence <= 0.05:
        return "Insufficient Evidence"
    label = "Insufficient Evidence"
    for candidate, threshold in config.label_thresholds:
        if score >= threshold:
            label = candidate
    return label


def _technology_position(
    technology_id: str,
    values: dict[str, float],
    graph: TechnologyGraphConfig,
) -> tuple[str, ...]:
    positions = []
    if technology_id in graph.categories:
        positions.append("configured_category")
    if values.get("dependency_strength", 0.0) >= 0.5:
        positions.append("dependency_hub")
    if values.get("market_awareness", 0.0) < values.get("technology_necessity", 0.0):
        positions.append("under_recognized")
    if values.get("infrastructure_criticality", 0.0) >= 0.7:
        positions.append("critical_infrastructure_candidate")
    return tuple(sorted(positions)) or ("unclassified",)


def _component_for_engine(engine_id: str, engine_map: dict[str, str]) -> str:
    normalized = engine_id.lower()
    for token, component in engine_map.items():
        if token in normalized:
            return component
    return "technology_necessity"


def _source_ids(inputs: TechnologyNecessityInputSet) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                *(record.id for record in inputs.intelligence),
                *(record.id for record in inputs.fused_intelligence),
                *(record.id for record in inputs.opportunity_timing),
                *(record.id for record in inputs.evidence),
                *(record.id for record in inputs.snapshots),
            }
        )
    )


def _clamp01(value: float) -> float:
    return round(max(0.0, min(1.0, float(value))), 4)
