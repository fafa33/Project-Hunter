from __future__ import annotations

from datetime import UTC, datetime

from hunter.economic.models import EconomicEdge, EconomicGraph, EconomicGraphMetrics, EconomicGraphRun, EconomicNode
from hunter.economic.repository import EconomicGraphRepository
from hunter.graph.models import (
    TechnologyEdge,
    TechnologyGraph,
    TechnologyGraphMetrics,
    TechnologyGraphRun,
    TechnologyNode,
)
from hunter.graph.repository import TechnologyGraphRepository
from hunter.scenario import ScenarioRepository, ScenarioSimulationEngine, compare_scenarios

NOW = datetime(2026, 7, 11, tzinfo=UTC)


def test_scenario_execution_propagates_dependency_and_economic_paths(tmp_path) -> None:
    technology_repo, economic_repo, scenario_repo = _repositories(tmp_path)

    results, run = ScenarioSimulationEngine(
        technology_repository=technology_repo,
        economic_repository=economic_repo,
        scenario_repository=scenario_repo,
    ).run(as_of=NOW)

    ethereum = next(result for result in results if result.scenario.target_project == "ethereum")
    aave = next(impact for impact in ethereum.impacts if impact.project_id == "aave")

    assert run.scenarios > 0
    assert run.projects_simulated >= 3
    assert run.propagation_depth >= 1
    assert aave.direct_impact == 1.0
    assert aave.dependency_paths or aave.economic_paths
    assert aave.evidence_ids
    assert aave.repository_ids


def test_scenario_persistence_history_comparison_and_deterministic_replay(tmp_path) -> None:
    technology_repo, economic_repo, scenario_repo = _repositories(tmp_path)
    engine = ScenarioSimulationEngine(
        technology_repository=technology_repo,
        economic_repository=economic_repo,
        scenario_repository=scenario_repo,
    )

    first, first_run = engine.run(as_of=NOW)
    second, second_run = engine.run(as_of=NOW)
    loaded = scenario_repo.results()
    comparison = compare_scenarios(first[0], first[1])

    assert first_run.run_id == second_run.run_id
    assert first[0].scenario.scenario_id == second[0].scenario.scenario_id
    assert len(loaded) == len(second)
    assert len(scenario_repo.runs()) == 2
    assert "shared" in comparison


def test_scenario_coverage_and_repository_integrity(tmp_path) -> None:
    technology_repo, economic_repo, scenario_repo = _repositories(tmp_path)

    results, run = ScenarioSimulationEngine(
        technology_repository=technology_repo,
        economic_repository=economic_repo,
        scenario_repository=scenario_repo,
    ).run(as_of=NOW)

    impacts = scenario_repo.impacts()

    assert run.scenario_coverage > 0
    assert len(impacts) == sum(len(result.impacts) for result in results)
    assert all(impact.validation_status == "VALID" for impact in impacts)
    assert all(impact.evidence_ids for impact in impacts)


def _repositories(tmp_path):
    technology_repo = TechnologyGraphRepository(tmp_path / "technology")
    economic_repo = EconomicGraphRepository(tmp_path / "economic")
    scenario_repo = ScenarioRepository(tmp_path / "scenario")
    technology_repo.save(_technology_graph(), _technology_run())
    economic_repo.save(_economic_graph(), _economic_run())
    return technology_repo, economic_repo, scenario_repo


def _technology_graph() -> TechnologyGraph:
    nodes = (
        TechnologyNode("ethereum", "Ethereum", "layer_1", ("ev-eth",), ("repo-eth",), 1.0, 1.0),
        TechnologyNode("aave", "Aave", "defi", ("ev-aave",), ("repo-aave",), 1.0, 1.0),
        TechnologyNode("compound", "Compound", "defi", ("ev-compound",), ("repo-compound",), 1.0, 1.0),
    )
    edges = (
        TechnologyEdge(
            "aave",
            "ethereum",
            "runtime",
            1.0,
            0.9,
            0.8,
            0.85,
            1.0,
            ("ev-aave",),
            ("repo-aave",),
            NOW,
            NOW,
            1.0,
            "VALID",
        ),
        TechnologyEdge(
            "compound",
            "ethereum",
            "runtime",
            1.0,
            0.9,
            0.8,
            0.85,
            1.0,
            ("ev-compound",),
            ("repo-compound",),
            NOW,
            NOW,
            1.0,
            "VALID",
        ),
    )
    metrics = (
        TechnologyGraphMetrics("ethereum", 0, 0.5, 0.6, ("ethereum",), 2, 0, 0.8, 0.1, 0.8, 0.2, 0.0),
        TechnologyGraphMetrics("aave", 1, 0.3, 0.1, ("aave", "ethereum"), 0, 1, 0.2, 0.2, 0.2, 0.8, 0.5),
        TechnologyGraphMetrics("compound", 1, 0.3, 0.1, ("compound", "ethereum"), 0, 1, 0.2, 0.2, 0.2, 0.8, 0.5),
    )
    return TechnologyGraph("technology", NOW, nodes, edges, metrics)


def _economic_graph() -> EconomicGraph:
    nodes = (
        EconomicNode("ethereum", "Ethereum", "layer_1", ("ev-eth",), ("repo-eth",), 1.0, 1.0),
        EconomicNode("aave", "Aave", "defi", ("ev-aave",), ("repo-aave",), 1.0, 1.0),
        EconomicNode("compound", "Compound", "defi", ("ev-compound",), ("repo-compound",), 1.0, 1.0),
    )
    edges = (
        EconomicEdge(
            "aave",
            "ethereum",
            "revenue_dependency",
            0.9,
            0.9,
            0.8,
            0.7,
            1.0,
            ("ev-aave",),
            ("repo-aave",),
            NOW,
            NOW,
            1.0,
            "VALID",
        ),
        EconomicEdge(
            "compound",
            "ethereum",
            "liquidity_dependency",
            0.8,
            0.8,
            0.6,
            0.8,
            1.0,
            ("ev-compound",),
            ("repo-compound",),
            NOW,
            NOW,
            1.0,
            "VALID",
        ),
    )
    metrics = (
        EconomicGraphMetrics("ethereum", 0.5, 0.5, 0.8, 0.8, 0.9, 0.4, 0.4, 0.0, 0.8, 0.2, 0, 0, ("aave",)),
        EconomicGraphMetrics("aave", 0.2, 0.2, 0.5, 0.5, 0.7, 0.1, 0.1, 0.5, 0.5, 0.5, 1, 1, ("ethereum",)),
        EconomicGraphMetrics("compound", 0.2, 0.2, 0.5, 0.5, 0.7, 0.1, 0.1, 0.5, 0.5, 0.5, 1, 1, ("ethereum",)),
    )
    return EconomicGraph("economic", NOW, nodes, edges, metrics)


def _technology_run() -> TechnologyGraphRun:
    return TechnologyGraphRun("tech-run", NOW, 50, 3, 2, 2, 0, 4.0, 6.0)


def _economic_run() -> EconomicGraphRun:
    return EconomicGraphRun("econ-run", NOW, 50, 3, 2, 2, 0, 4.0, 6.0)
