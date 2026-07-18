from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from hunter.acquisition.models import EvidenceValidation, NormalizedEvidence
from hunter.acquisition.repositories import InMemoryAcquisitionRepository
from hunter.graph import TechnologyDependencyGraphEngine, TechnologyGraphRepository
from hunter.graph.engine import dependency_path

NOW = datetime(2026, 7, 11, tzinfo=UTC)


def test_graph_builds_only_from_persisted_valid_evidence_and_preserves_trace(tmp_path) -> None:
    repository = InMemoryAcquisitionRepository()
    repository.save_normalized(
        (
            _evidence("aave-edge", "aave", raw_metrics={"chain_list": ("Ethereum",)}),
            _evidence("ethereum-node", "ethereum", raw_metrics={"description": "Ethereum settlement"}),
        )
    )
    repository.save_validations((_validation("aave-edge"), _validation("ethereum-node")))

    graph, run = TechnologyDependencyGraphEngine(
        acquisition_repository=repository,
        graph_repository=TechnologyGraphRepository(tmp_path),
    ).build(as_of=NOW)

    assert run.validated_dependencies == 1
    assert run.rejected_dependencies == 0
    assert graph.edges[0].source_project == "aave"
    assert graph.edges[0].target_project == "ethereum"
    assert graph.edges[0].evidence_ids == ("aave-edge",)
    assert graph.edges[0].repository_ids == ("repo:aave-edge",)
    assert graph.edges[0].validation_status == "VALID"


def test_graph_rejects_duplicates_cycles_and_invalid_evidence(tmp_path) -> None:
    repository = InMemoryAcquisitionRepository()
    repository.save_normalized(
        (
            _evidence("aave-edge-1", "aave", raw_metrics={"description": "depends on Ethereum"}),
            _evidence(
                "aave-edge-2",
                "aave",
                provider="github",
                metric="github_repository_profile",
                raw_metrics={"description": "uses Ethereum runtime"},
            ),
            _evidence("ethereum-cycle", "ethereum", raw_metrics={"description": "mentions Aave"}),
            _evidence("invalid", "chainlink", raw_metrics={"description": "Ethereum oracle"}),
        )
    )
    repository.save_validations(
        (
            _validation("aave-edge-1"),
            _validation("aave-edge-2"),
            _validation("ethereum-cycle"),
            _validation("invalid", status="invalid"),
        )
    )

    graph, run = TechnologyDependencyGraphEngine(
        acquisition_repository=repository,
        graph_repository=TechnologyGraphRepository(tmp_path),
    ).build(as_of=NOW)

    assert ((graph.edges[0].source_project, graph.edges[0].target_project),) == (("aave", "ethereum"),)
    assert run.rejected_dependencies == 2
    assert all(edge.source_project != "chainlink" for edge in graph.edges)


def test_graph_metrics_paths_and_persistence_are_deterministic(tmp_path) -> None:
    repository = InMemoryAcquisitionRepository()
    repository.save_normalized(
        (
            _evidence("aave-edge", "aave", raw_metrics={"description": "Ethereum"}),
            _evidence("compound-edge", "compound", raw_metrics={"description": "Ethereum"}),
            _evidence("uniswap-edge", "uniswap", raw_metrics={"description": "Ethereum"}),
            _evidence("ethereum-node", "ethereum", raw_metrics={"description": "base node"}),
        )
    )
    repository.save_validations(
        (
            _validation("aave-edge"),
            _validation("compound-edge"),
            _validation("uniswap-edge"),
            _validation("ethereum-node"),
        )
    )
    graph_repository = TechnologyGraphRepository(tmp_path)
    engine = TechnologyDependencyGraphEngine(acquisition_repository=repository, graph_repository=graph_repository)

    first, first_run = engine.build(as_of=NOW)
    second, second_run = engine.build(as_of=NOW)
    loaded = graph_repository.graph()
    ethereum = next(metric for metric in first.metrics if metric.project_id == "ethereum")

    assert first.graph_id == second.graph_id
    assert first_run.run_id == second_run.run_id
    assert dependency_path(first, "aave", "ethereum") == ("aave", "ethereum")
    assert ethereum.fan_in == 3
    assert ethereum.infrastructure_centrality > 0
    assert len(loaded.edges) == len(first.edges)


def test_technology_graph_snapshots_preserve_history_retry_conflict_and_legacy(tmp_path) -> None:
    acquisition = InMemoryAcquisitionRepository()
    acquisition.save_normalized(
        (
            _evidence("aave-edge", "aave", raw_metrics={"description": "Ethereum"}),
            _evidence("ethereum-node", "ethereum", raw_metrics={"description": "base node"}),
        )
    )
    acquisition.save_validations((_validation("aave-edge"), _validation("ethereum-node")))
    repository = TechnologyGraphRepository(tmp_path)
    engine = TechnologyDependencyGraphEngine(acquisition_repository=acquisition, graph_repository=repository)

    graph_a, run_a = engine.build(as_of=NOW)
    persisted_a = repository.runs()[0]
    assert persisted_a.snapshot_ref is not None
    snapshot_a = tmp_path / persisted_a.snapshot_ref
    bytes_a = {path.name: path.read_bytes() for path in snapshot_a.iterdir()}

    graph_b, run_b = engine.build(as_of=NOW + timedelta(days=1))

    assert run_b.run_id != run_a.run_id
    assert repository.graph(run_a.run_id) == graph_a
    assert repository.graph(run_b.run_id) == graph_b
    assert {path.name: path.read_bytes() for path in snapshot_a.iterdir()} == bytes_a

    repository.save(graph_a, run_a)
    assert len(repository.runs()) == 2
    with pytest.raises(ValueError, match="snapshot conflict"):
        repository.save(replace(graph_a, nodes=()), run_a)

    (tmp_path / "nodes.jsonl").write_text("{}\n", encoding="utf-8")
    assert repository.graph(run_a.run_id) == graph_a
    assert repository.snapshot_status()["replay_limitation"] is not None

    legacy_root = tmp_path / "legacy"
    legacy_root.mkdir()
    for name in ("nodes.jsonl", "edges.jsonl", "metrics.jsonl"):
        (legacy_root / name).write_bytes((snapshot_a / name).read_bytes())
    legacy_repository = TechnologyGraphRepository(legacy_root)
    legacy_graph = legacy_repository.graph()
    assert (legacy_graph.nodes, legacy_graph.edges, legacy_graph.metrics) == (
        graph_a.nodes,
        graph_a.edges,
        graph_a.metrics,
    )
    assert legacy_repository.snapshot_status()["replay_limitation"] is not None


def _evidence(
    evidence_id: str,
    target_id: str,
    *,
    raw_metrics: dict[str, object],
    provider: str = "defillama",
    metric: str = "defillama_protocol_profile",
) -> NormalizedEvidence:
    return NormalizedEvidence(
        evidence_id=evidence_id,
        repository_id=f"repo:{evidence_id}",
        provider=provider,
        collector=f"{provider}-collector",
        raw_source_id=f"raw:{evidence_id}",
        domain="protocol",
        metric=metric,
        target_id=target_id,
        value=target_id,
        raw_metrics=raw_metrics,
        normalized_metrics={"schema_completeness": 1.0},
        source_url="https://example.test",
        retrieved_at=NOW,
        normalized_at=NOW,
        confidence=1.0,
        freshness=1.0,
        raw_evidence_id=f"raw-evidence:{evidence_id}",
    )


def _validation(evidence_id: str, *, status: str = "valid") -> EvidenceValidation:
    return EvidenceValidation(
        evidence_id=evidence_id,
        status=status,  # type: ignore[arg-type]
        validated_at=NOW,
        confidence=1.0,
        freshness=1.0,
    )
