from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import asdict
from datetime import UTC, datetime

from hunter.execution.identity import fingerprint, identity
from hunter.intelligence.fusion.alignment import align_to_target, effective_window
from hunter.intelligence.fusion.confidence import calculate_fused_confidence
from hunter.intelligence.fusion.configuration import FusionConfig
from hunter.intelligence.fusion.contradiction import assess_contradictions
from hunter.intelligence.fusion.corroboration import assess_corroboration
from hunter.intelligence.fusion.deduplication import canonicalize_evidence, deduplicate_evidence, deduplicate_sources
from hunter.intelligence.fusion.dependencies import assess_dependencies
from hunter.intelligence.fusion.graph import build_intelligence_graph
from hunter.intelligence.fusion.models import (
    FusedIntelligence,
    FusionInput,
    FusionTarget,
    MissingEvidenceAssessment,
    UnifiedInsight,
    UnifiedObservation,
    UnifiedSignal,
)
from hunter.intelligence.fusion.narrative import build_unified_narrative
from hunter.intelligence.fusion.normalization import normalize_fusion_inputs
from hunter.intelligence.fusion.weighting import build_engine_contributions
from hunter.intelligence.intelligence import Intelligence
from hunter.persistence.records import FusedIntelligenceRecord, IntelligenceRecord


class CrossEngineFusionEngine:
    def __init__(self, config: FusionConfig | None = None) -> None:
        self.config = config or FusionConfig()

    def fuse(
        self,
        intelligence: Iterable[Intelligence | IntelligenceRecord],
        target: FusionTarget,
        *,
        config: FusionConfig | None = None,
    ) -> FusedIntelligence:
        active_config = config or self.config
        configuration_fingerprint = fingerprint("fusion-configuration", asdict(active_config))
        contribution_model_fingerprint = fingerprint("fusion-contribution-model", asdict(active_config.weighting))
        inputs = deduplicate_sources(normalize_fusion_inputs(intelligence))
        aligned_inputs = align_to_target(inputs, target)
        dependencies = assess_dependencies(aligned_inputs, active_config)
        corroboration = assess_corroboration(aligned_inputs, dependencies)
        contradictions = assess_contradictions(aligned_inputs)
        missing = assess_missing_evidence(aligned_inputs, active_config)
        contributions = build_engine_contributions(aligned_inputs, active_config, dependencies)
        signals = build_unified_signals(aligned_inputs)
        observations = build_unified_observations(aligned_inputs)
        insights = build_unified_insights(aligned_inputs)
        confidence = calculate_fused_confidence(
            contributions,
            corroboration,
            contradictions,
            dependencies,
            missing,
            active_config,
        )
        effective_at = max((item.effective_at for item in aligned_inputs), default=datetime(1970, 1, 1, tzinfo=UTC))
        window = effective_window(aligned_inputs)
        source_ids = tuple(item.intelligence_id for item in aligned_inputs)
        fused_id = identity(
            "fused-intelligence",
            {
                "target": target,
                "source_intelligence_ids": source_ids,
                "configuration_fingerprint": configuration_fingerprint,
                "contribution_model_fingerprint": contribution_model_fingerprint,
                "strategy": active_config.strategy,
                "effective_window": window,
                "identity_schema_version": "fusion-identity-v1",
            },
        )
        graph_nodes, graph_edges = build_intelligence_graph(
            fused_id,
            target,
            aligned_inputs,
            signals,
            observations,
            insights,
        )
        narrative = build_unified_narrative(
            target,
            aligned_inputs,
            insights,
            confidence,
            corroboration=corroboration,
            contradictions=contradictions,
            dependencies=dependencies,
            missing_evidence=missing,
        )
        source_run_ids = tuple(sorted({item.run_id for item in aligned_inputs if item.run_id}))
        return FusedIntelligence(
            id=fused_id,
            target=target,
            source_intelligence_ids=source_ids,
            contributions=contributions,
            corroboration=corroboration,
            contradictions=contradictions,
            dependencies=dependencies,
            missing_evidence=missing,
            signals=signals,
            observations=observations,
            insights=insights,
            narrative=narrative,
            graph_nodes=graph_nodes,
            graph_edges=graph_edges,
            confidence=confidence,
            effective_at=effective_at,
            created_at=effective_at,
            metadata={
                "fusion_strategy": active_config.strategy,
                "configuration_fingerprint": configuration_fingerprint,
                "contribution_model_fingerprint": contribution_model_fingerprint,
                "identity_schema_version": "fusion-identity-v1",
                "input_count": len(aligned_inputs),
                "engine_count": len({item.engine_id for item in aligned_inputs}),
                "source_run_id": source_run_ids[0] if source_run_ids else None,
                "effective_window": "|".join(window),
                "deduplicated_evidence_count": len(deduplicate_evidence(aligned_inputs)),
            },
        )


def fused_intelligence_to_record(
    fused: FusedIntelligence,
    *,
    pipeline_run_id: str,
    created_at: datetime | None = None,
) -> FusedIntelligenceRecord:
    return FusedIntelligenceRecord(
        id=fused.id,
        created_at=created_at or fused.created_at,
        effective_at=fused.effective_at,
        pipeline_run_id=pipeline_run_id,
        target_id=fused.target.target_id,
        fusion_strategy=str(fused.metadata.get("fusion_strategy") or "weighted-corroboration-v1"),
        source_intelligence_ids=fused.source_intelligence_ids,
        confidence=fused.confidence.as_dict(),
        target_type=fused.target.target_type,
        configuration_fingerprint=str(fused.metadata.get("configuration_fingerprint") or ""),
        contribution_model_fingerprint=str(fused.metadata.get("contribution_model_fingerprint") or ""),
        source_run_ids=tuple(str(item) for item in _source_run_ids(fused)),
        effective_window=tuple(str(fused.metadata.get("effective_window") or "").split("|")) if fused.metadata.get("effective_window") else (),
        contributions=tuple(_contribution_payload(item) for item in fused.contributions),
        corroboration={
            "corroborated_categories": fused.corroboration.corroborated_categories,
            "corroborating_engine_ids": fused.corroboration.corroborating_engine_ids,
            "score": fused.corroboration.score,
            "explanation": fused.corroboration.explanation,
        },
        contradictions={
            "contradicted_categories": fused.contradictions.contradicted_categories,
            "severity": fused.contradictions.severity,
            "explanation": fused.contradictions.explanation,
        },
        dependencies={
            "dependent_engine_ids": fused.dependencies.dependent_engine_ids,
            "dependency_edges": fused.dependencies.dependency_edges,
            "penalty": fused.dependencies.penalty,
            "explanation": fused.dependencies.explanation,
        },
        missing_evidence={
            "missing_categories": fused.missing_evidence.missing_categories,
            "severity": fused.missing_evidence.severity,
            "explanation": fused.missing_evidence.explanation,
        },
        unified_signals=tuple(_signal_payload(item) for item in fused.signals),
        unified_observations=tuple(_observation_payload(item) for item in fused.observations),
        unified_insights=tuple(_insight_payload(item) for item in fused.insights),
        unified_narrative={
            "summary": fused.narrative.summary,
            "key_points": fused.narrative.key_points,
            "uncertainty": fused.narrative.uncertainty,
            "source_insight_ids": fused.narrative.source_insight_ids,
        },
        graph_nodes=tuple(_node_payload(item) for item in fused.graph_nodes),
        graph_edges=tuple(_edge_payload(item) for item in fused.graph_edges),
        metadata={
            "target_type": fused.target.target_type,
            "engine_count": fused.metadata.get("engine_count"),
            "input_count": fused.metadata.get("input_count"),
            "deduplicated_evidence_count": fused.metadata.get("deduplicated_evidence_count"),
        },
    )


def assess_missing_evidence(inputs: tuple[FusionInput, ...], config: FusionConfig) -> MissingEvidenceAssessment:
    observed = {category for item in inputs for category in item.signal_categories}
    required = set(config.required_categories)
    missing = required.difference(observed)
    severity = len(missing) / len(required) if required else 0.0
    explanation = "No required evidence categories configured" if not required else f"{len(missing)} required category gap(s)"
    return MissingEvidenceAssessment(
        missing_categories=tuple(missing),
        severity=severity,
        explanation=explanation,
    )


def build_unified_signals(inputs: tuple[FusionInput, ...]) -> tuple[UnifiedSignal, ...]:
    grouped: dict[str, list[tuple[FusionInput, int]]] = defaultdict(list)
    for item in inputs:
        for index, category in enumerate(item.signal_categories):
            grouped[category].append((item, index))
    signals: list[UnifiedSignal] = []
    for category in sorted(grouped):
        members = grouped[category]
        strengths = [item.signal_strengths[index] for item, index in members if index < len(item.signal_strengths)]
        confidences = [item.signal_confidences[index] for item, index in members if index < len(item.signal_confidences)]
        severities = [item.signal_severities[index] for item, index in members if index < len(item.signal_severities)]
        source_signal_ids = tuple(item.signal_ids[index] for item, index in members if index < len(item.signal_ids))
        engine_ids = tuple(item.engine_id for item, _ in members)
        evidence_ids = tuple(evidence.canonical_key for evidence in canonicalize_evidence(tuple(item for item, _ in members)))
        payload = {"category": category, "source_signal_ids": source_signal_ids, "engine_ids": engine_ids}
        signals.append(
            UnifiedSignal(
                id=identity("fusion-unified-signal", payload),
                category=category,
                strength=_average(strengths),
                confidence=_average(confidences),
                severity=_average(severities),
                source_signal_ids=source_signal_ids,
                engine_ids=engine_ids,
                evidence_ids=evidence_ids,
            )
        )
    return tuple(signals)


def build_unified_observations(inputs: tuple[FusionInput, ...]) -> tuple[UnifiedObservation, ...]:
    observations: list[UnifiedObservation] = []
    for item in inputs:
        for index, observation_id in enumerate(item.observation_ids):
            description = item.observation_descriptions[index] if index < len(item.observation_descriptions) else observation_id
            observations.append(
                UnifiedObservation(
                    id=identity("fusion-unified-observation", {"source": observation_id, "engine": item.engine_id}),
                    description=description,
                    importance=item.confidence_score,
                    source_observation_ids=(observation_id,),
                    evidence_ids=item.evidence_ids,
                    engine_ids=(item.engine_id,),
                )
            )
    return tuple(sorted(observations, key=lambda item: item.id))


def build_unified_insights(inputs: tuple[FusionInput, ...]) -> tuple[UnifiedInsight, ...]:
    insights: list[UnifiedInsight] = []
    for item in inputs:
        for index, insight_id in enumerate(item.insight_ids):
            title = item.insight_titles[index] if index < len(item.insight_titles) else insight_id
            explanation = item.insight_explanations[index] if index < len(item.insight_explanations) else title
            insights.append(
                UnifiedInsight(
                    id=identity("fusion-unified-insight", {"source": insight_id, "engine": item.engine_id}),
                    title=title,
                    explanation=explanation,
                    confidence=item.confidence_score,
                    priority=item.confidence_score,
                    source_insight_ids=(insight_id,),
                    observation_ids=item.observation_ids,
                    engine_ids=(item.engine_id,),
                )
            )
    return tuple(sorted(insights, key=lambda item: item.id))


def _average(values: list[float]) -> float:
    if not values:
        return 0.0
    return round(sum(values) / len(values), 4)


def _source_run_ids(fused: FusedIntelligence) -> tuple[str, ...]:
    source_run_id = fused.metadata.get("source_run_id")
    return (str(source_run_id),) if source_run_id else ()


def _contribution_payload(item: object) -> dict[str, object]:
    return {
        "engine_id": item.engine_id,
        "engine_version": item.engine_version,
        "plugin_id": item.plugin_id,
        "plugin_version": item.plugin_version,
        "intelligence_ids": item.intelligence_ids,
        "evidence_count": item.evidence_count,
        "signal_count": item.signal_count,
        "observation_count": item.observation_count,
        "insight_count": item.insight_count,
        "weight": item.weight,
        "confidence": item.confidence,
    }


def _signal_payload(item: UnifiedSignal) -> dict[str, object]:
    return {
        "id": item.id,
        "category": item.category,
        "strength": item.strength,
        "confidence": item.confidence,
        "severity": item.severity,
        "source_signal_ids": item.source_signal_ids,
        "engine_ids": item.engine_ids,
        "evidence_ids": item.evidence_ids,
    }


def _observation_payload(item: UnifiedObservation) -> dict[str, object]:
    return {
        "id": item.id,
        "description": item.description,
        "importance": item.importance,
        "source_observation_ids": item.source_observation_ids,
        "evidence_ids": item.evidence_ids,
        "engine_ids": item.engine_ids,
    }


def _insight_payload(item: UnifiedInsight) -> dict[str, object]:
    return {
        "id": item.id,
        "title": item.title,
        "explanation": item.explanation,
        "confidence": item.confidence,
        "priority": item.priority,
        "source_insight_ids": item.source_insight_ids,
        "observation_ids": item.observation_ids,
        "engine_ids": item.engine_ids,
    }


def _node_payload(item: object) -> dict[str, object]:
    return {
        "id": item.id,
        "node_type": item.node_type,
        "label": item.label,
        "metadata": item.metadata.as_dict(),
    }


def _edge_payload(item: object) -> dict[str, object]:
    return {
        "id": item.id,
        "source_id": item.source_id,
        "target_id": item.target_id,
        "edge_type": item.edge_type,
        "weight": item.weight,
        "metadata": item.metadata.as_dict(),
    }
