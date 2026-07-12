from __future__ import annotations

from collections import defaultdict, deque
from datetime import UTC, datetime

from hunter.economic.models import EconomicEdge, EconomicGraph
from hunter.economic.repository import EconomicGraphRepository
from hunter.execution.identity import identity
from hunter.graph.models import TechnologyEdge, TechnologyGraph
from hunter.graph.repository import TechnologyGraphRepository
from hunter.market_validation.configuration import load_market_validation_config
from hunter.scenario.models import ScenarioDefinition, ScenarioImpact, ScenarioResult, ScenarioRun, ScenarioType
from hunter.scenario.repository import ScenarioRepository

SCENARIO_ENGINES: tuple[str, ...] = (
    "technology_necessity",
    "future_demand",
    "probability",
    "capital_rotation",
    "opportunity_timing",
    "committee",
)


class ScenarioSimulationEngine:
    def __init__(
        self,
        *,
        technology_repository: TechnologyGraphRepository | None = None,
        economic_repository: EconomicGraphRepository | None = None,
        scenario_repository: ScenarioRepository | None = None,
    ) -> None:
        self.technology_repository = technology_repository or TechnologyGraphRepository()
        self.economic_repository = economic_repository or EconomicGraphRepository()
        self.scenario_repository = scenario_repository or ScenarioRepository()

    def run(self, *, as_of: datetime | None = None) -> tuple[tuple[ScenarioResult, ...], ScenarioRun]:
        timestamp = (as_of or datetime.now(tz=UTC)).astimezone(UTC)
        technology_graph = self.technology_repository.graph()
        economic_graph = self.economic_repository.graph()
        project_count = len(load_market_validation_config().project_universe)
        scenarios = _scenario_definitions(technology_graph, economic_graph, timestamp)
        results = tuple(_simulate_scenario(scenario, technology_graph, economic_graph) for scenario in scenarios)
        affected_projects = {project for result in results for project in result.affected_projects}
        affected_edges = {edge for result in results for edge in result.affected_edges}
        affected_nodes = {node for result in results for node in result.affected_nodes}
        propagation_depth = max(
            (
                len(path) - 1
                for result in results
                for impact in result.impacts
                for path in (*impact.dependency_paths, *impact.economic_paths)
            ),
            default=0,
        )
        run = ScenarioRun(
            run_id=identity(
                "scenario-run",
                {"scenarios": tuple(item.scenario.scenario_id for item in results), "timestamp": timestamp},
            ),
            generated_at=timestamp,
            projects_analyzed=project_count,
            scenarios=len(results),
            projects_simulated=len(affected_projects),
            affected_nodes=len(affected_nodes),
            affected_edges=len(affected_edges),
            propagation_depth=propagation_depth,
            scenario_coverage=round((len(affected_projects) / max(project_count, 1)) * 100.0, 2),
        )
        self.scenario_repository.save(results, run)
        return results, run


def _scenario_definitions(
    technology_graph: TechnologyGraph,
    economic_graph: EconomicGraph,
    timestamp: datetime,
) -> tuple[ScenarioDefinition, ...]:
    technology_by_target: dict[str, list[TechnologyEdge]] = defaultdict(list)
    economic_by_target: dict[str, list[EconomicEdge]] = defaultdict(list)
    for edge in technology_graph.edges:
        technology_by_target[edge.target_project].append(edge)
    for edge in economic_graph.edges:
        economic_by_target[edge.target_project].append(edge)
    technology_targets = sorted(
        technology_by_target,
        key=lambda project: (-len(technology_by_target[project]), project),
    )[:4]
    economic_targets = sorted(
        economic_by_target,
        key=lambda project: (-len(economic_by_target[project]), project),
    )[:3]
    rows: list[ScenarioDefinition] = []
    for project in technology_targets:
        rows.append(_definition("technology_disappears", project, technology_by_target[project], (), timestamp))
    for project in economic_targets:
        rows.append(_definition("revenue_decline", project, (), economic_by_target[project], timestamp))
    if technology_targets:
        rows.append(
            _definition(
                "infrastructure_replacement",
                technology_targets[0],
                technology_by_target[technology_targets[0]],
                (),
                timestamp,
            )
        )
    if economic_targets:
        rows.append(
            _definition("capital_rotation", economic_targets[0], (), economic_by_target[economic_targets[0]], timestamp)
        )
    unique: dict[tuple[ScenarioType, str], ScenarioDefinition] = {}
    for row in rows:
        unique[(row.scenario_type, row.target_project)] = row
    return tuple(sorted(unique.values(), key=lambda item: (item.scenario_type, item.target_project)))


def _definition(
    scenario_type: ScenarioType,
    target_project: str,
    technology_edges: tuple[TechnologyEdge, ...] | list[TechnologyEdge],
    economic_edges: tuple[EconomicEdge, ...] | list[EconomicEdge],
    timestamp: datetime,
) -> ScenarioDefinition:
    evidence_ids = tuple(
        sorted(
            {
                evidence_id
                for edge in (*tuple(technology_edges), *tuple(economic_edges))
                for evidence_id in edge.evidence_ids
            }
        )
    )
    repository_ids = tuple(
        sorted(
            {
                repository_id
                for edge in (*tuple(technology_edges), *tuple(economic_edges))
                for repository_id in edge.repository_ids
            }
        )
    )
    return ScenarioDefinition(
        scenario_id=identity(
            "scenario-definition",
            {
                "scenario_type": scenario_type,
                "target_project": target_project,
                "evidence_ids": evidence_ids,
                "repository_ids": repository_ids,
            },
        ),
        scenario_type=scenario_type,
        target_project=target_project,
        description=f"{scenario_type}:{target_project}",
        evidence_ids=evidence_ids,
        repository_ids=repository_ids,
        created_at=timestamp,
    )


def _simulate_scenario(
    scenario: ScenarioDefinition,
    technology_graph: TechnologyGraph,
    economic_graph: EconomicGraph,
) -> ScenarioResult:
    dependency_paths = _paths_from(technology_graph.edges, scenario.target_project, reverse=True, max_depth=3)
    economic_paths = _paths_from(economic_graph.edges, scenario.target_project, reverse=True, max_depth=3)
    projects = tuple(sorted({node for path in (*dependency_paths, *economic_paths) for node in path}))
    technology_edges = _edges_on_paths(technology_graph.edges, dependency_paths)
    economic_edges = _edges_on_paths(economic_graph.edges, economic_paths)
    affected_edges = tuple(sorted({*_edge_ids(technology_edges), *_economic_edge_ids(economic_edges)}))
    technology_metrics = {item.project_id: item for item in technology_graph.metrics}
    economic_metrics = {item.project_id: item for item in economic_graph.metrics}
    impacts = []
    for project in projects:
        dependency_project_paths = tuple(path for path in dependency_paths if project in path)
        economic_project_paths = tuple(path for path in economic_paths if project in path)
        depth = min((path.index(project) for path in (*dependency_project_paths, *economic_project_paths)), default=0)
        technology_metric = technology_metrics.get(project)
        economic_metric = economic_metrics.get(project)
        direct = 1.0 if depth <= 1 else 0.0
        indirect = 0.0 if depth <= 1 else round(1.0 / depth, 4)
        dependency_propagation = _mean(
            (
                technology_metric.dependency_centrality if technology_metric else 0.0,
                technology_metric.infrastructure_centrality if technology_metric else 0.0,
            )
        )
        economic_propagation = _mean(
            (
                economic_metric.capital_centrality if economic_metric else 0.0,
                economic_metric.revenue_centrality if economic_metric else 0.0,
            )
        )
        replacement = technology_metric.replacement_availability if technology_metric else 0.0
        infra_resilience = replacement
        economic_resilience = economic_metric.economic_resilience if economic_metric else 0.0
        fragility = _mean(
            (
                technology_metric.single_point_of_failure_risk if technology_metric else 0.0,
                economic_metric.economic_fragility if economic_metric else 0.0,
                max(0.0, 1.0 - replacement),
            )
        )
        impact_edges = tuple(
            edge
            for edge in (*technology_edges, *economic_edges)
            if edge.source_project == project or edge.target_project == project
        )
        evidence_ids = tuple(sorted({evidence_id for edge in impact_edges for evidence_id in edge.evidence_ids}))
        repository_ids = tuple(
            sorted({repository_id for edge in impact_edges for repository_id in edge.repository_ids})
        )
        impacts.append(
            ScenarioImpact(
                scenario_id=scenario.scenario_id,
                project_id=project,
                direct_impact=direct,
                indirect_impact=indirect,
                second_order_impact=1.0 if depth == 2 else 0.0,
                third_order_impact=1.0 if depth == 3 else 0.0,
                dependency_propagation=dependency_propagation,
                economic_propagation=economic_propagation,
                recovery_difficulty=round(max(0.0, 1.0 - replacement), 4),
                replacement_availability=replacement,
                infrastructure_resilience=infra_resilience,
                economic_resilience=economic_resilience,
                system_fragility=fragility,
                affected_nodes=tuple(
                    sorted(set().union(*(set(path) for path in (*dependency_project_paths, *economic_project_paths))))
                ),
                affected_edges=tuple(sorted({*_edge_ids(impact_edges)})),
                dependency_paths=dependency_project_paths,
                economic_paths=economic_project_paths,
                evidence_ids=evidence_ids,
                repository_ids=repository_ids,
                confidence=_mean(tuple(edge.dependency_confidence for edge in impact_edges)),
                freshness=_mean(tuple(edge.freshness for edge in impact_edges)),
                validation_status="VALID",
            )
        )
    return ScenarioResult(
        scenario=scenario,
        impacts=tuple(sorted(impacts, key=lambda item: item.project_id)),
        affected_projects=projects,
        affected_edges=affected_edges,
        affected_nodes=projects,
        confidence=_mean(tuple(impact.confidence for impact in impacts)),
        freshness=_mean(tuple(impact.freshness for impact in impacts)),
    )


def compare_scenarios(left: ScenarioResult, right: ScenarioResult) -> dict[str, object]:
    left_projects = set(left.affected_projects)
    right_projects = set(right.affected_projects)
    return {
        "left": left.scenario.scenario_id,
        "right": right.scenario.scenario_id,
        "left_only": tuple(sorted(left_projects - right_projects)),
        "right_only": tuple(sorted(right_projects - left_projects)),
        "shared": tuple(sorted(left_projects & right_projects)),
        "affected_delta": len(right_projects) - len(left_projects),
    }


def _paths_from(
    edges: tuple[TechnologyEdge, ...] | tuple[EconomicEdge, ...],
    project_id: str,
    *,
    reverse: bool,
    max_depth: int,
) -> tuple[tuple[str, ...], ...]:
    adjacency: dict[str, list[str]] = defaultdict(list)
    for edge in edges:
        source = edge.target_project if reverse else edge.source_project
        target = edge.source_project if reverse else edge.target_project
        adjacency[source].append(target)
    rows: list[tuple[str, ...]] = [(project_id,)]
    queue: deque[tuple[str, tuple[str, ...]]] = deque([(project_id, (project_id,))])
    while queue:
        node, path = queue.popleft()
        if len(path) - 1 >= max_depth:
            continue
        for next_node in sorted(adjacency[node]):
            if next_node in path:
                continue
            next_path = (*path, next_node)
            rows.append(next_path)
            queue.append((next_node, next_path))
    return tuple(sorted(set(rows), key=lambda item: (len(item), item)))


def _edges_on_paths(
    edges: tuple[TechnologyEdge, ...] | tuple[EconomicEdge, ...],
    paths: tuple[tuple[str, ...], ...],
) -> tuple[TechnologyEdge | EconomicEdge, ...]:
    pairs = {(path[index + 1], path[index]) for path in paths for index in range(len(path) - 1)}
    return tuple(edge for edge in edges if (edge.source_project, edge.target_project) in pairs)


def _edge_ids(edges: tuple[TechnologyEdge | EconomicEdge, ...]) -> tuple[str, ...]:
    return tuple(sorted(f"{edge.source_project}->{edge.target_project}" for edge in edges))


def _economic_edge_ids(edges: tuple[EconomicEdge, ...]) -> tuple[str, ...]:
    return tuple(sorted(f"{edge.source_project}->{edge.target_project}" for edge in edges))


def _mean(values: tuple[float, ...]) -> float:
    if not values:
        return 0.0
    return round(sum(values) / len(values), 4)
