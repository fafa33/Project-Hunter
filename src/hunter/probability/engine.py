from __future__ import annotations

from dataclasses import asdict

from hunter.execution.identity import fingerprint, identity
from hunter.probability.configuration import ProbabilityConfig
from hunter.probability.metrics import (
    average,
    conflict_categories,
    evidence_freshness,
    evidence_quality,
    historical_reliability,
    missing_evidence,
    record_confidence,
)
from hunter.probability.models import ProbabilityAssessment, ProbabilityComponent, ProbabilityInputSet

IDENTITY_SCHEMA_VERSION = "probability-engine-identity-v1"


class ProbabilityEngine:
    def __init__(self, config: ProbabilityConfig | None = None) -> None:
        self.config = config or ProbabilityConfig()

    def assess(self, inputs: ProbabilityInputSet, *, config: ProbabilityConfig | None = None) -> ProbabilityAssessment:
        active_config = config or self.config
        component_values = _component_values(inputs, active_config)
        components = _components(component_values, inputs, active_config)
        raw_score = sum(component.contribution for component in components)
        conflicts = conflict_categories(inputs.fused_intelligence, inputs.opportunity_timing)
        missing = missing_evidence(inputs.fused_intelligence, inputs.opportunity_timing)
        conflict_score = _conflict_score(inputs, conflicts)
        consensus_score = _consensus_score(component_values, conflicts)
        robustness = _robustness(inputs, component_values, conflicts, missing, active_config)
        historical = historical_reliability(inputs.snapshots) or component_values.get("backtesting_reliability", 0.0)
        decision_confidence = average(
            (robustness, historical, consensus_score, component_values.get("confidence", 0.0))
        )
        penalty = (
            len(missing) * active_config.missing_evidence_penalty / max(1, len(dict(active_config.component_weights)))
        )
        probability_score = _clamp01(raw_score - penalty - (conflict_score * active_config.conflict_penalty))
        if len(inputs.evidence) < active_config.minimum_evidence_records and not inputs.fused_intelligence:
            probability_score = 0.0
        source_ids = _source_ids(inputs)
        configuration_fingerprint = fingerprint("probability-configuration", asdict(active_config))
        assessment_id = identity(
            "probability-assessment",
            {
                "target_id": inputs.target_id,
                "effective_at": inputs.effective_at,
                "source_record_ids": source_ids,
                "configuration_fingerprint": configuration_fingerprint,
                "identity_schema_version": IDENTITY_SCHEMA_VERSION,
            },
        )
        positives = tuple(sorted(components, key=lambda item: (-item.contribution, item.name))[:5])
        negatives = tuple(sorted(components, key=lambda item: (item.contribution, item.name))[:5])
        supporting_engines = _supporting_engines(inputs)
        return ProbabilityAssessment(
            assessment_id=assessment_id,
            target_id=inputs.target_id,
            effective_at=inputs.effective_at,
            source_record_ids=source_ids,
            probability_score=probability_score,
            success_probability=probability_score,
            failure_probability=_clamp01(1.0 - probability_score),
            probability_label=_label(probability_score, robustness, active_config),
            evidence_robustness=robustness,
            historical_reliability=historical,
            decision_confidence=decision_confidence,
            consensus_score=consensus_score,
            conflict_score=conflict_score,
            components=components,
            largest_positive_contributors=positives,
            largest_negative_contributors=negatives,
            supporting_engines=supporting_engines,
            conflicting_engines=_conflicting_engines(inputs, conflicts),
            supporting_evidence=tuple(sorted(record.id for record in inputs.evidence)),
            weak_evidence=tuple(sorted(record.id for record in inputs.evidence if record.reliability < 0.5)),
            missing_evidence=missing,
            explanation=_explanation(positives, negatives, conflicts, missing, decision_confidence),
            metadata={
                "configuration_fingerprint": configuration_fingerprint,
                "identity_schema_version": IDENTITY_SCHEMA_VERSION,
                "source_record_count": len(source_ids),
            },
        )


def _component_values(inputs: ProbabilityInputSet, config: ProbabilityConfig) -> dict[str, float]:
    values: dict[str, list[float]] = {name: [] for name, _ in config.component_weights}
    engine_map = dict(config.engine_component_map)
    for record in inputs.intelligence:
        key = _component_for_engine(record.engine_id, engine_map)
        if key in values:
            values[key].append(_confidence_score(record.confidence))
    for record in inputs.fused_intelligence:
        confidence = _confidence_score(record.confidence)
        for contribution in record.contributions:
            engine_id = str(contribution.get("engine_id", ""))
            key = _component_for_engine(engine_id, engine_map)
            if key in values:
                values[key].append(_numeric(contribution.get("confidence"), confidence))
        values["confidence"].append(confidence)
    for record in inputs.opportunity_timing:
        values["opportunity_timing"].append(record.timing_score / 100.0)
        values["confidence"].append(_confidence_score(record.confidence))
        values["risk_balance"].append(_clamp01(1.0 - float(record.risk_state.get("score", 0.0))))
    values["evidence_quality"].append(evidence_quality(inputs.evidence, inputs.fused_intelligence))
    values["evidence_freshness"].append(evidence_freshness(inputs.evidence, inputs.fused_intelligence))
    values["historical_consistency"].append(_snapshot_value(inputs.snapshots, "historical_consistency"))
    values["backtesting_reliability"].append(
        _snapshot_value(inputs.snapshots, "backtesting_reliability")
        or _snapshot_value(inputs.snapshots, "backtesting_quality")
    )
    values["confidence"].append(
        record_confidence(inputs.intelligence, inputs.fused_intelligence, inputs.opportunity_timing)
    )
    return {name: average(score for score in scores if score > 0.0) for name, scores in values.items()}


def _components(
    values: dict[str, float], inputs: ProbabilityInputSet, config: ProbabilityConfig
) -> tuple[ProbabilityComponent, ...]:
    source_ids = _source_ids(inputs)
    components = []
    for name, weight in config.component_weights:
        value = values.get(name, 0.0)
        components.append(
            ProbabilityComponent(
                name=name,
                value=value,
                weight=weight,
                contribution=round(value * weight, 4),
                source_record_ids=source_ids,
                explanation=f"{name} contributed {round(value * weight, 4)} from persisted evidence",
            )
        )
    return tuple(sorted(components, key=lambda item: item.name))


def _component_for_engine(engine_id: str, engine_map: dict[str, str]) -> str:
    normalized = engine_id.lower()
    for token, component in engine_map.items():
        if token in normalized:
            return component
    return "fundamental_strength"


def _confidence_score(confidence: dict[str, object]) -> float:
    for key in ("score", "overall", "fused_confidence", "confidence"):
        if key in confidence:
            return _numeric(confidence[key], 0.0)
    values = [_numeric(value, 0.0) for value in confidence.values()]
    return average(values)


def _snapshot_value(snapshots: tuple[object, ...], key: str) -> float:
    return average(
        _numeric(getattr(snapshot, "payload", {}).get(key), 0.0)
        for snapshot in snapshots
        if key in getattr(snapshot, "payload", {})
    )


def _consensus_score(values: dict[str, float], conflicts: tuple[str, ...]) -> float:
    present = tuple(value for value in values.values() if value > 0.0)
    if not present:
        return 0.0
    spread = max(present) - min(present)
    return _clamp01(1.0 - spread - (len(conflicts) * 0.05))


def _conflict_score(inputs: ProbabilityInputSet, conflicts: tuple[str, ...]) -> float:
    engine_count = max(1, len(_supporting_engines(inputs)))
    return _clamp01(len(conflicts) / engine_count)


def _robustness(
    inputs: ProbabilityInputSet,
    values: dict[str, float],
    conflicts: tuple[str, ...],
    missing: tuple[str, ...],
    config: ProbabilityConfig,
) -> float:
    coverage = sum(1 for value in values.values() if value > 0.0) / max(1, len(dict(config.component_weights)))
    evidence = evidence_quality(inputs.evidence, inputs.fused_intelligence)
    consensus = _consensus_score(values, conflicts)
    missing_penalty = len(missing) / max(1, len(dict(config.component_weights)))
    return _clamp01(average((coverage, evidence, consensus)) - missing_penalty)


def _label(score: float, robustness: float, config: ProbabilityConfig) -> str:
    if score <= 0.0 or robustness <= 0.05:
        return "Insufficient Evidence"
    label = "Insufficient Evidence"
    for candidate, threshold in config.label_thresholds:
        if score >= threshold:
            label = candidate
    return label


def _source_ids(inputs: ProbabilityInputSet) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                *(record.id for record in inputs.fused_intelligence),
                *(record.id for record in inputs.opportunity_timing),
                *(record.id for record in inputs.intelligence),
                *(record.id for record in inputs.evidence),
                *(record.id for record in inputs.snapshots),
            }
        )
    )


def _supporting_engines(inputs: ProbabilityInputSet) -> tuple[str, ...]:
    engines = {record.engine_id for record in inputs.intelligence}
    engines.update(
        str(contribution.get("engine_id"))
        for record in inputs.fused_intelligence
        for contribution in record.contributions
        if contribution.get("engine_id")
    )
    return tuple(sorted(engines))


def _conflicting_engines(inputs: ProbabilityInputSet, conflicts: tuple[str, ...]) -> tuple[str, ...]:
    if not conflicts:
        return ()
    return _supporting_engines(inputs)


def _explanation(
    positives: tuple[ProbabilityComponent, ...],
    negatives: tuple[ProbabilityComponent, ...],
    conflicts: tuple[str, ...],
    missing: tuple[str, ...],
    confidence: float,
) -> tuple[str, ...]:
    return (
        "Largest positive contributors: " + ", ".join(item.name for item in positives),
        "Largest weak contributors: " + ", ".join(item.name for item in negatives),
        "Conflicting evidence: " + (", ".join(conflicts) if conflicts else "none"),
        "Missing evidence: " + (", ".join(missing) if missing else "none"),
        f"Decision confidence: {confidence:.4f}",
    )


def _numeric(value: object, default: float) -> float:
    if isinstance(value, int | float):
        return _clamp01(float(value))
    return default


def _clamp01(value: float) -> float:
    return round(max(0.0, min(1.0, float(value))), 4)
