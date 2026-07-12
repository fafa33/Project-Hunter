from __future__ import annotations

import json
import math
import re
from collections import defaultdict, deque
from datetime import UTC, datetime

from hunter.acquisition.models import NormalizedEvidence
from hunter.acquisition.repositories import FileAcquisitionRepository, InMemoryAcquisitionRepository
from hunter.economic.models import (
    EconomicEdge,
    EconomicGraph,
    EconomicGraphMetrics,
    EconomicGraphRun,
    EconomicNode,
    EconomicRelationshipType,
)
from hunter.economic.repository import EconomicGraphRepository
from hunter.execution.identity import identity
from hunter.market_validation.configuration import load_market_validation_config
from hunter.market_validation.models import ProjectValidationTarget

ECONOMIC_EVIDENCE_PROVIDERS = {"coingecko", "defillama", "narrative"}
ECONOMIC_METRICS = {
    "coingecko_market_profile",
    "coingecko_detail_metadata",
    "defillama_protocol_profile",
    "narrative_item",
}
ECONOMIC_KEYWORDS = {
    "revenue",
    "fee",
    "fees",
    "tvl",
    "liquidity",
    "capital",
    "treasury",
    "staking",
    "restaking",
    "validator",
    "gas",
    "settlement",
    "oracle",
    "bridge",
    "burn",
    "supply",
    "emission",
    "demand",
    "value",
    "market_cap",
    "volume",
}


class EconomicDependencyGraphEngine:
    def __init__(
        self,
        *,
        acquisition_repository: InMemoryAcquisitionRepository | None = None,
        graph_repository: EconomicGraphRepository | None = None,
    ) -> None:
        self.acquisition_repository = acquisition_repository or FileAcquisitionRepository()
        self.graph_repository = graph_repository or EconomicGraphRepository()

    def build(self, *, as_of: datetime | None = None) -> tuple[EconomicGraph, EconomicGraphRun]:
        timestamp = (as_of or datetime.now(tz=UTC)).astimezone(UTC)
        targets = tuple(load_market_validation_config().project_universe)
        latest = _latest_valid(self.acquisition_repository)
        nodes = _nodes(targets, latest)
        raw_edges = tuple(edge for target in targets for edge in _edges_for_project(target, targets, latest, timestamp))
        edges, rejected = _validate_edges(raw_edges)
        metrics = _metrics(nodes, edges)
        graph = EconomicGraph(
            graph_id=identity(
                "economic-dependency-graph",
                {"nodes": tuple(node.project_id for node in nodes), "edges": tuple(_edge_key(edge) for edge in edges)},
            ),
            generated_at=timestamp,
            nodes=nodes,
            edges=edges,
            metrics=metrics,
        )
        project_count = max(len(targets), 1)
        run = EconomicGraphRun(
            run_id=identity("economic-graph-run", {"graph": graph.graph_id, "timestamp": timestamp}),
            generated_at=timestamp,
            projects_analyzed=len(targets),
            nodes=len(nodes),
            edges=len(edges),
            validated_relationships=len(edges),
            rejected_relationships=rejected,
            graph_coverage=round((len({edge.source_project for edge in edges}) / project_count) * 100.0, 2),
            economic_coverage=round((len(nodes) / project_count) * 100.0, 2),
        )
        self.graph_repository.save(graph, run)
        return graph, run


def economic_path(graph: EconomicGraph, source: str, target: str) -> tuple[str, ...]:
    outgoing: dict[str, list[str]] = defaultdict(list)
    for edge in graph.edges:
        outgoing[edge.source_project].append(edge.target_project)
    queue: deque[tuple[str, tuple[str, ...]]] = deque([(source, (source,))])
    seen = {source}
    while queue:
        node, path = queue.popleft()
        if node == target:
            return path
        for next_node in sorted(outgoing[node]):
            if next_node in seen:
                continue
            seen.add(next_node)
            queue.append((next_node, (*path, next_node)))
    return ()


def _latest_valid(repository: InMemoryAcquisitionRepository) -> dict[str, tuple[NormalizedEvidence, ...]]:
    by_project: dict[str, dict[tuple[str, str], NormalizedEvidence]] = defaultdict(dict)
    for evidence in repository.normalized.values():
        validation = repository.validations.get(evidence.evidence_id)
        if validation is None or validation.status != "valid" or evidence.provider not in ECONOMIC_EVIDENCE_PROVIDERS:
            continue
        if evidence.metric not in ECONOMIC_METRICS:
            continue
        if not _has_economic_signal(evidence):
            continue
        key = (evidence.provider, evidence.metric)
        current = by_project[evidence.target_id].get(key)
        if current is None or evidence.retrieved_at > current.retrieved_at:
            by_project[evidence.target_id][key] = evidence
    return {project_id: tuple(items.values()) for project_id, items in by_project.items()}


def _nodes(
    targets: tuple[ProjectValidationTarget, ...],
    latest: dict[str, tuple[NormalizedEvidence, ...]],
) -> tuple[EconomicNode, ...]:
    rows = []
    for target in targets:
        evidence = latest.get(target.project_id, ())
        if not evidence:
            continue
        rows.append(
            EconomicNode(
                project_id=target.project_id,
                name=target.name,
                sector=target.sector,
                evidence_ids=tuple(sorted(item.evidence_id for item in evidence)),
                repository_ids=tuple(sorted(item.repository_id for item in evidence)),
                confidence=_mean(tuple(item.confidence for item in evidence)),
                freshness=_mean(tuple(item.freshness for item in evidence)),
            )
        )
    return tuple(sorted(rows, key=lambda item: item.project_id))


def _edges_for_project(
    source: ProjectValidationTarget,
    targets: tuple[ProjectValidationTarget, ...],
    latest: dict[str, tuple[NormalizedEvidence, ...]],
    timestamp: datetime,
) -> tuple[EconomicEdge, ...]:
    evidence = latest.get(source.project_id, ())
    edges = []
    for item in evidence:
        text = _evidence_text(item)
        if not _has_economic_text_signal(text, item):
            continue
        for target in targets:
            if target.project_id == source.project_id:
                continue
            if not _mentions_target(text, target):
                continue
            relationship = _relationship_type(item, text)
            economic_strength = _economic_strength(item)
            edges.append(
                EconomicEdge(
                    source_project=source.project_id,
                    target_project=target.project_id,
                    relationship_type=relationship,
                    economic_strength=economic_strength,
                    criticality=_criticality(relationship, target.sector),
                    revenue_impact=_revenue_impact(item, relationship),
                    capital_impact=_capital_impact(item, relationship),
                    dependency_confidence=item.confidence,
                    evidence_ids=(item.evidence_id,),
                    repository_ids=(item.repository_id,),
                    discovery_timestamp=timestamp,
                    validation_timestamp=timestamp,
                    freshness=item.freshness,
                    validation_status="VALID",
                    reason=f"persisted {item.provider}:{item.metric} economic evidence references {target.project_id}",
                )
            )
    return tuple(edges)


def _validate_edges(edges: tuple[EconomicEdge, ...]) -> tuple[tuple[EconomicEdge, ...], int]:
    by_pair: dict[tuple[str, str], EconomicEdge] = {}
    rejected = 0
    for edge in sorted(edges, key=lambda item: (_edge_key(item), -item.dependency_confidence)):
        if edge.source_project == edge.target_project:
            rejected += 1
            continue
        key = (edge.source_project, edge.target_project)
        if key in by_pair:
            rejected += 1
            continue
        if _creates_cycle(tuple(by_pair.values()), edge):
            rejected += 1
            continue
        by_pair[key] = edge
    return tuple(sorted(by_pair.values(), key=_edge_key)), rejected


def _metrics(nodes: tuple[EconomicNode, ...], edges: tuple[EconomicEdge, ...]) -> tuple[EconomicGraphMetrics, ...]:
    incoming: dict[str, list[EconomicEdge]] = defaultdict(list)
    outgoing: dict[str, list[EconomicEdge]] = defaultdict(list)
    for edge in edges:
        incoming[edge.target_project].append(edge)
        outgoing[edge.source_project].append(edge)
    total_nodes = max(len(nodes), 1)
    total_edges = max(len(edges), 1)
    rows = []
    for node in nodes:
        incoming_edges = tuple(incoming[node.project_id])
        outgoing_edges = tuple(outgoing[node.project_id])
        fan_in = len(incoming_edges)
        fan_out = len(outgoing_edges)
        revenue_in = _mean(tuple(edge.revenue_impact for edge in incoming_edges))
        capital_in = _mean(tuple(edge.capital_impact for edge in incoming_edges))
        revenue_out = _mean(tuple(edge.revenue_impact for edge in outgoing_edges))
        capital_out = _mean(tuple(edge.capital_impact for edge in outgoing_edges))
        capital_centrality = round((sum(edge.capital_impact for edge in incoming_edges) + capital_out) / total_nodes, 4)
        revenue_centrality = round((sum(edge.revenue_impact for edge in incoming_edges) + revenue_out) / total_nodes, 4)
        value_capture = _mean((revenue_in, capital_in, node.confidence))
        dependency_concentration = round(fan_out / total_edges, 4)
        revenue_concentration = round(sum(edge.revenue_impact for edge in incoming_edges) / total_edges, 4)
        capital_concentration = round(sum(edge.capital_impact for edge in incoming_edges) / total_edges, 4)
        second_order = _reachable_count(node.project_id, outgoing, max_depth=2)
        third_order = _reachable_count(node.project_id, outgoing, max_depth=3)
        resilience = round(min(1.0, (fan_in + 1) / (fan_out + fan_in + 1)), 4)
        fragility = round(max(0.0, 1.0 - resilience), 4)
        switching_cost = (
            _mean(tuple(edge.criticality for edge in incoming_edges + outgoing_edges))
            if incoming_edges or outgoing_edges
            else 0.0
        )
        rows.append(
            EconomicGraphMetrics(
                project_id=node.project_id,
                capital_centrality=capital_centrality,
                revenue_centrality=revenue_centrality,
                value_capture=value_capture,
                economic_moat=_mean((value_capture, capital_centrality, revenue_centrality, switching_cost)),
                switching_cost=switching_cost,
                revenue_concentration=revenue_concentration,
                capital_concentration=capital_concentration,
                dependency_concentration=dependency_concentration,
                economic_resilience=resilience,
                economic_fragility=fragility,
                second_order_dependency=second_order,
                third_order_dependency=third_order,
                critical_counterparties=tuple(
                    sorted(
                        {
                            edge.source_project if edge.target_project == node.project_id else edge.target_project
                            for edge in incoming_edges + outgoing_edges
                            if edge.criticality >= 0.75
                        }
                    )
                ),
            )
        )
    return tuple(sorted(rows, key=lambda item: item.project_id))


def _creates_cycle(edges: tuple[EconomicEdge, ...], candidate: EconomicEdge) -> bool:
    graph = EconomicGraph("candidate", candidate.discovery_timestamp, (), (*edges, candidate), ())
    return bool(economic_path(graph, candidate.target_project, candidate.source_project))


def _reachable_count(project_id: str, outgoing: dict[str, list[EconomicEdge]], *, max_depth: int) -> int:
    queue: deque[tuple[str, int]] = deque([(project_id, 0)])
    seen = {project_id}
    while queue:
        node, depth = queue.popleft()
        if depth >= max_depth:
            continue
        for edge in outgoing[node]:
            if edge.target_project in seen:
                continue
            seen.add(edge.target_project)
            queue.append((edge.target_project, depth + 1))
    return len(seen) - 1


def _has_economic_signal(evidence: NormalizedEvidence) -> bool:
    if evidence.provider in {"coingecko", "defillama"}:
        return True
    return _has_economic_text_signal(_evidence_text(evidence), evidence)


def _has_economic_text_signal(text: str, evidence: NormalizedEvidence) -> bool:
    if evidence.provider in {"coingecko", "defillama"}:
        return True
    return any(keyword in text for keyword in ECONOMIC_KEYWORDS)


def _evidence_text(evidence: NormalizedEvidence) -> str:
    return json.dumps(
        {
            "raw_metrics": dict(evidence.raw_metrics),
            "normalized_metrics": dict(evidence.normalized_metrics),
            "source_url": evidence.source_url,
        },
        sort_keys=True,
        default=str,
    ).lower()


def _mentions_target(text: str, target: ProjectValidationTarget) -> bool:
    tokens = {target.project_id, target.name.lower(), target.sector.lower().replace("_", " ")}
    tokens |= {token.strip() for token in target.name.lower().replace("-", " ").split() if len(token.strip()) > 3}
    for token in tokens:
        if token and re.search(rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])", text):
            return True
    return False


def _relationship_type(evidence: NormalizedEvidence, text: str) -> EconomicRelationshipType:
    if evidence.provider == "defillama":
        if "revenue" in text:
            return "revenue_dependency"
        if "fee" in text:
            return "fee_dependency"
        if "tvl" in text or "liquidity" in text:
            return "liquidity_dependency"
    if "security" in text or "validator" in text or "staking" in text or "restaking" in text:
        return "security_dependency"
    if "treasury" in text:
        return "treasury_dependency"
    if "burn" in text or "emission" in text or "supply" in text:
        return "emission_dependency"
    if "demand" in text or "network effect" in text:
        return "demand_dependency"
    if "value" in text or "capture" in text:
        return "value_capture_dependency"
    if "market_cap" in text or "volume" in text or evidence.provider == "coingecko":
        return "capital_dependency"
    return "infrastructure_dependency"


def _economic_strength(evidence: NormalizedEvidence) -> float:
    if evidence.normalized_metrics:
        return _mean(tuple(evidence.normalized_metrics.values()))
    candidates = tuple(
        _normalize_amount(value)
        for key, value in evidence.raw_metrics.items()
        if key in {"tvl", "revenue", "fees", "daily_fees", "daily_revenue", "market_cap", "volume", "fdv"}
        and isinstance(value, int | float)
    )
    if candidates:
        return _mean(candidates)
    return evidence.confidence


def _normalize_amount(value: int | float) -> float:
    if value <= 0:
        return 0.0
    return round(min(1.0, math.log10(float(value) + 1.0) / 12.0), 4)


def _criticality(relationship: EconomicRelationshipType, sector: str) -> float:
    base = {
        "revenue_dependency": 0.9,
        "liquidity_dependency": 0.86,
        "security_dependency": 0.88,
        "demand_dependency": 0.8,
        "value_capture_dependency": 0.82,
        "treasury_dependency": 0.74,
        "fee_dependency": 0.84,
        "infrastructure_dependency": 0.78,
        "emission_dependency": 0.72,
        "capital_dependency": 0.76,
    }[relationship]
    sector_bonus = 0.06 if sector in {"layer_1", "oracle", "interoperability", "staking", "restaking"} else 0.0
    return round(min(1.0, base + sector_bonus), 4)


def _revenue_impact(evidence: NormalizedEvidence, relationship: EconomicRelationshipType) -> float:
    raw = evidence.raw_metrics
    values = tuple(
        _normalize_amount(value)
        for key, value in raw.items()
        if key in {"revenue", "fees", "daily_fees", "daily_revenue", "monthly_fees", "monthly_revenue"}
        and isinstance(value, int | float)
    )
    if values:
        return _mean(values)
    if relationship in {"revenue_dependency", "fee_dependency", "value_capture_dependency"}:
        return _economic_strength(evidence)
    return round(_economic_strength(evidence) * 0.5, 4)


def _capital_impact(evidence: NormalizedEvidence, relationship: EconomicRelationshipType) -> float:
    raw = evidence.raw_metrics
    values = tuple(
        _normalize_amount(value)
        for key, value in raw.items()
        if key in {"tvl", "market_cap", "fdv", "volume", "fully_diluted_valuation"} and isinstance(value, int | float)
    )
    if values:
        return _mean(values)
    if relationship in {"capital_dependency", "liquidity_dependency", "treasury_dependency"}:
        return _economic_strength(evidence)
    return round(_economic_strength(evidence) * 0.5, 4)


def _edge_key(edge: EconomicEdge) -> tuple[str, str, str]:
    return (edge.source_project, edge.target_project, edge.relationship_type)


def _mean(values: tuple[float, ...]) -> float:
    if not values:
        return 0.0
    return round(sum(values) / len(values), 4)
