from __future__ import annotations

from hunter.execution.identity import identity
from hunter.intelligence.fusion.models import (
    CanonicalEvidence,
    FusionInput,
    FusionTarget,
    IntelligenceGraphEdge,
    IntelligenceGraphNode,
    UnifiedInsight,
    UnifiedObservation,
    UnifiedSignal,
)


def build_intelligence_graph(
    fused_id: str,
    target: FusionTarget,
    inputs: tuple[FusionInput, ...],
    canonical_evidence_groups: tuple[CanonicalEvidence, ...],
    signals: tuple[UnifiedSignal, ...],
    observations: tuple[UnifiedObservation, ...],
    insights: tuple[UnifiedInsight, ...],
) -> tuple[tuple[IntelligenceGraphNode, ...], tuple[IntelligenceGraphEdge, ...]]:
    nodes: dict[str, IntelligenceGraphNode] = {
        target.target_id: IntelligenceGraphNode(id=target.target_id, node_type="target", label=target.label or target.target_id),
        fused_id: IntelligenceGraphNode(id=fused_id, node_type="fused_intelligence", label="Fused Intelligence"),
    }
    edges: dict[str, IntelligenceGraphEdge] = {
        _edge_id(fused_id, target.target_id, "fuses_target"): IntelligenceGraphEdge(
            id=_edge_id(fused_id, target.target_id, "fuses_target"),
            source_id=fused_id,
            target_id=target.target_id,
            edge_type="fuses_target",
            weight=1.0,
        )
    }
    for item in inputs:
        nodes[item.intelligence_id] = IntelligenceGraphNode(
            id=item.intelligence_id,
            node_type="intelligence",
            label=item.engine_id,
            metadata={"engine_id": item.engine_id, "engine_version": item.engine_version},
        )
        nodes[item.engine_id] = IntelligenceGraphNode(id=item.engine_id, node_type="engine", label=item.engine_id)
        _add_edge(edges, item.engine_id, item.intelligence_id, "emitted", 1.0)
        _add_edge(edges, item.intelligence_id, fused_id, "contributes_to", item.confidence_score)
        for evidence_id in item.evidence_ids:
            nodes[evidence_id] = IntelligenceGraphNode(id=evidence_id, node_type="evidence", label=evidence_id)
            _add_edge(edges, evidence_id, item.intelligence_id, "supports", 1.0)
    for group in canonical_evidence_groups:
        nodes[group.canonical_key] = IntelligenceGraphNode(
            id=group.canonical_key,
            node_type="canonical_evidence",
            label=group.dependency_classification,
            metadata={
                "dependency_classification": group.dependency_classification,
                "evidence_count": len(group.evidence_ids),
                "source_intelligence_count": len(group.source_intelligence_ids),
            },
        )
        _add_edge(edges, group.canonical_key, fused_id, "canonicalizes_evidence", 1.0)
        for evidence_id in group.evidence_ids:
            _add_edge(edges, evidence_id, group.canonical_key, "member_of_canonical_evidence", 1.0)
        for intelligence_id in group.source_intelligence_ids:
            _add_edge(edges, intelligence_id, group.canonical_key, "contributes_evidence", 1.0)
    for signal in signals:
        nodes[signal.id] = IntelligenceGraphNode(id=signal.id, node_type="unified_signal", label=signal.category)
        _add_edge(edges, signal.id, fused_id, "summarizes_signal", signal.confidence)
    for observation in observations:
        nodes[observation.id] = IntelligenceGraphNode(id=observation.id, node_type="unified_observation", label=observation.description)
        _add_edge(edges, observation.id, fused_id, "summarizes_observation", observation.importance)
    for insight in insights:
        nodes[insight.id] = IntelligenceGraphNode(id=insight.id, node_type="unified_insight", label=insight.title)
        _add_edge(edges, insight.id, fused_id, "summarizes_insight", insight.confidence)
    return tuple(nodes[key] for key in sorted(nodes)), tuple(edges[key] for key in sorted(edges))


def _add_edge(edges: dict[str, IntelligenceGraphEdge], source_id: str, target_id: str, edge_type: str, weight: float) -> None:
    edge_id = _edge_id(source_id, target_id, edge_type)
    edges[edge_id] = IntelligenceGraphEdge(
        id=edge_id,
        source_id=source_id,
        target_id=target_id,
        edge_type=edge_type,
        weight=weight,
    )


def _edge_id(source_id: str, target_id: str, edge_type: str) -> str:
    return identity("fusion-graph-edge", {"source": source_id, "target": target_id, "type": edge_type})
