from __future__ import annotations

import json
import re
from collections import defaultdict, deque
from datetime import UTC, datetime

from hunter.acquisition.models import NormalizedEvidence
from hunter.acquisition.repositories import FileAcquisitionRepository, InMemoryAcquisitionRepository
from hunter.execution.identity import identity
from hunter.graph.models import (
    DependencyType,
    TechnologyEdge,
    TechnologyGraph,
    TechnologyGraphMetrics,
    TechnologyGraphRun,
    TechnologyNode,
)
from hunter.graph.repository import TechnologyGraphRepository
from hunter.market_validation.configuration import load_market_validation_config
from hunter.market_validation.models import ProjectValidationTarget

EDGE_EVIDENCE_PROVIDERS = {"coingecko", "defillama", "github", "narrative"}
EDGE_METRICS = {
    "coingecko_market_profile",
    "coingecko_detail_metadata",
    "defillama_protocol_profile",
    "github_repository_profile",
    "narrative_item",
}


class TechnologyDependencyGraphEngine:
    def __init__(
        self,
        *,
        acquisition_repository: InMemoryAcquisitionRepository | None = None,
        graph_repository: TechnologyGraphRepository | None = None,
    ) -> None:
        self.acquisition_repository = acquisition_repository or FileAcquisitionRepository()
        self.graph_repository = graph_repository or TechnologyGraphRepository()

    def build(self, *, as_of: datetime | None = None) -> tuple[TechnologyGraph, TechnologyGraphRun]:
        timestamp = (as_of or datetime.now(tz=UTC)).astimezone(UTC)
        targets = tuple(load_market_validation_config().project_universe)
        latest = _latest_valid(self.acquisition_repository)
        nodes = _nodes(targets, latest)
        raw_edges = tuple(
            _edge for target in targets for _edge in _edges_for_project(target, targets, latest, timestamp)
        )
        edges, rejected = _validate_edges(raw_edges)
        metrics = _metrics(nodes, edges)
        graph = TechnologyGraph(
            graph_id=identity(
                "technology-dependency-graph",
                {"nodes": tuple(node.project_id for node in nodes), "edges": tuple(_edge_key(edge) for edge in edges)},
            ),
            generated_at=timestamp,
            nodes=nodes,
            edges=edges,
            metrics=metrics,
        )
        project_count = max(len(targets), 1)
        run = TechnologyGraphRun(
            run_id=identity("technology-graph-run", {"graph": graph.graph_id, "timestamp": timestamp}),
            generated_at=timestamp,
            projects_analyzed=len(targets),
            nodes=len(nodes),
            edges=len(edges),
            validated_dependencies=len(edges),
            rejected_dependencies=rejected,
            graph_coverage=round((len({edge.source_project for edge in edges}) / project_count) * 100.0, 2),
            technology_coverage=round((len(nodes) / project_count) * 100.0, 2),
        )
        self.graph_repository.save(graph, run)
        return graph, run


def _latest_valid(repository: InMemoryAcquisitionRepository) -> dict[str, tuple[NormalizedEvidence, ...]]:
    by_project: dict[str, dict[tuple[str, str], NormalizedEvidence]] = defaultdict(dict)
    for evidence in repository.normalized.values():
        validation = repository.validations.get(evidence.evidence_id)
        if validation is None or validation.status != "valid" or evidence.provider not in EDGE_EVIDENCE_PROVIDERS:
            continue
        if evidence.metric not in EDGE_METRICS:
            continue
        key = (evidence.provider, evidence.metric)
        current = by_project[evidence.target_id].get(key)
        if current is None or evidence.retrieved_at > current.retrieved_at:
            by_project[evidence.target_id][key] = evidence
    return {project_id: tuple(items.values()) for project_id, items in by_project.items()}


def _nodes(
    targets: tuple[ProjectValidationTarget, ...],
    latest: dict[str, tuple[NormalizedEvidence, ...]],
) -> tuple[TechnologyNode, ...]:
    rows = []
    for target in targets:
        evidence = latest.get(target.project_id, ())
        if not evidence:
            continue
        rows.append(
            TechnologyNode(
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
) -> tuple[TechnologyEdge, ...]:
    evidence = latest.get(source.project_id, ())
    edges = []
    for item in evidence:
        text = _evidence_text(item)
        for target in targets:
            if target.project_id == source.project_id:
                continue
            if not _mentions_target(text, target):
                continue
            edges.append(
                TechnologyEdge(
                    source_project=source.project_id,
                    target_project=target.project_id,
                    dependency_type=_dependency_type(item, text),
                    strength=item.confidence,
                    criticality=_criticality(target.sector),
                    replacement_difficulty=_replacement_difficulty(target.sector),
                    switching_cost=_switching_cost(target.sector),
                    dependency_confidence=item.confidence,
                    evidence_ids=(item.evidence_id,),
                    repository_ids=(item.repository_id,),
                    discovery_timestamp=timestamp,
                    validation_timestamp=timestamp,
                    freshness=item.freshness,
                    validation_status="VALID",
                    reason=f"persisted {item.provider}:{item.metric} references {target.project_id}",
                )
            )
    return tuple(edges)


def _validate_edges(edges: tuple[TechnologyEdge, ...]) -> tuple[tuple[TechnologyEdge, ...], int]:
    by_pair: dict[tuple[str, str], TechnologyEdge] = {}
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


def _metrics(
    nodes: tuple[TechnologyNode, ...], edges: tuple[TechnologyEdge, ...]
) -> tuple[TechnologyGraphMetrics, ...]:
    node_ids = {node.project_id for node in nodes}
    incoming: dict[str, list[TechnologyEdge]] = defaultdict(list)
    outgoing: dict[str, list[TechnologyEdge]] = defaultdict(list)
    for edge in edges:
        incoming[edge.target_project].append(edge)
        outgoing[edge.source_project].append(edge)
    total_nodes = max(len(node_ids), 1)
    rows = []
    for node in nodes:
        fan_in = len(incoming[node.project_id])
        fan_out = len(outgoing[node.project_id])
        depth, path = _depth_and_path(node.project_id, outgoing)
        infrastructure_centrality = round(fan_in / total_nodes, 4)
        dependency_centrality = round((fan_in + fan_out) / max(total_nodes * 2, 1), 4)
        redundancy = round(min(1.0, fan_in / 3), 4)
        replacement = round(max(0.0, 1.0 - redundancy), 4)
        rows.append(
            TechnologyGraphMetrics(
                project_id=node.project_id,
                dependency_depth=depth,
                dependency_centrality=dependency_centrality,
                infrastructure_centrality=infrastructure_centrality,
                critical_path=path,
                fan_in=fan_in,
                fan_out=fan_out,
                redundancy=redundancy,
                single_point_of_failure_risk=round(infrastructure_centrality * replacement, 4),
                replacement_availability=redundancy,
                technology_uniqueness=replacement,
                dependency_concentration=round(fan_out / max(len(edges), 1), 4),
            )
        )
    return tuple(sorted(rows, key=lambda item: item.project_id))


def dependency_path(graph: TechnologyGraph, source: str, target: str) -> tuple[str, ...]:
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


def _creates_cycle(edges: tuple[TechnologyEdge, ...], candidate: TechnologyEdge) -> bool:
    graph = TechnologyGraph("candidate", candidate.discovery_timestamp, (), (*edges, candidate), ())
    return bool(dependency_path(graph, candidate.target_project, candidate.source_project))


def _depth_and_path(project_id: str, outgoing: dict[str, list[TechnologyEdge]]) -> tuple[int, tuple[str, ...]]:
    best: tuple[str, ...] = (project_id,)
    queue: deque[tuple[str, tuple[str, ...]]] = deque([(project_id, (project_id,))])
    while queue:
        node, path = queue.popleft()
        if len(path) > len(best):
            best = path
        for edge in sorted(outgoing[node], key=lambda item: item.target_project):
            if edge.target_project in path:
                continue
            queue.append((edge.target_project, (*path, edge.target_project)))
    return max(len(best) - 1, 0), best


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


def _dependency_type(evidence: NormalizedEvidence, text: str) -> DependencyType:
    if evidence.provider == "defillama" and "chain_list" in evidence.raw_metrics:
        return "runtime"
    if "security" in text:
        return "security"
    if "governance" in text:
        return "governance"
    if evidence.provider == "github":
        return "development"
    return "infrastructure"


def _criticality(sector: str) -> float:
    return {
        "layer_1": 0.92,
        "settlement": 0.92,
        "oracle": 0.86,
        "data_availability": 0.86,
        "interoperability": 0.82,
        "indexing": 0.78,
        "infrastructure": 0.8,
    }.get(sector, 0.62)


def _replacement_difficulty(sector: str) -> float:
    return {
        "layer_1": 0.9,
        "oracle": 0.84,
        "data_availability": 0.82,
        "interoperability": 0.78,
        "decentralized_storage": 0.76,
        "decentralized_compute": 0.74,
    }.get(sector, 0.6)


def _switching_cost(sector: str) -> float:
    return round((_criticality(sector) + _replacement_difficulty(sector)) / 2, 4)


def _edge_key(edge: TechnologyEdge) -> tuple[str, str, str]:
    return (edge.source_project, edge.target_project, edge.dependency_type)


def _mean(values: tuple[float, ...]) -> float:
    if not values:
        return 0.0
    return round(sum(values) / len(values), 4)
