from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hunter.economic.models import (
    EconomicEdge,
    EconomicGraph,
    EconomicGraphMetrics,
    EconomicGraphRun,
    EconomicNode,
)


class EconomicGraphRepository:
    def __init__(self, root: str | Path = "data/economic_graph") -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, graph: EconomicGraph, run: EconomicGraphRun) -> None:
        _write_jsonl(self.root / "nodes.jsonl", (_node_payload(item) for item in graph.nodes))
        _write_jsonl(self.root / "edges.jsonl", (_edge_payload(item) for item in graph.edges))
        _write_jsonl(self.root / "metrics.jsonl", (_metric_payload(item) for item in graph.metrics))
        _write_jsonl(self.root / "runs.jsonl", (_run_payload(run),), append=True)

    def graph(self) -> EconomicGraph:
        nodes = tuple(_node_from_payload(item) for item in _read_jsonl(self.root / "nodes.jsonl"))
        edges = tuple(_edge_from_payload(item) for item in _read_jsonl(self.root / "edges.jsonl"))
        metrics = tuple(_metric_from_payload(item) for item in _read_jsonl(self.root / "metrics.jsonl"))
        generated = max((edge.discovery_timestamp for edge in edges), default=datetime.now(tz=UTC))
        return EconomicGraph("persisted-economic-graph", generated, nodes, edges, metrics)

    def runs(self) -> tuple[EconomicGraphRun, ...]:
        return tuple(_run_from_payload(item) for item in _read_jsonl(self.root / "runs.jsonl"))


def _node_payload(item: EconomicNode) -> dict[str, Any]:
    return asdict(item)


def _edge_payload(item: EconomicEdge) -> dict[str, Any]:
    payload = asdict(item)
    payload["discovery_timestamp"] = item.discovery_timestamp.isoformat()
    payload["validation_timestamp"] = item.validation_timestamp.isoformat()
    return payload


def _metric_payload(item: EconomicGraphMetrics) -> dict[str, Any]:
    return asdict(item)


def _run_payload(item: EconomicGraphRun) -> dict[str, Any]:
    payload = asdict(item)
    payload["generated_at"] = item.generated_at.isoformat()
    return payload


def _node_from_payload(payload: dict[str, Any]) -> EconomicNode:
    return EconomicNode(
        project_id=str(payload["project_id"]),
        name=str(payload["name"]),
        sector=str(payload["sector"]),
        evidence_ids=tuple(payload.get("evidence_ids", ())),
        repository_ids=tuple(payload.get("repository_ids", ())),
        confidence=float(payload["confidence"]),
        freshness=float(payload["freshness"]),
    )


def _edge_from_payload(payload: dict[str, Any]) -> EconomicEdge:
    return EconomicEdge(
        source_project=str(payload["source_project"]),
        target_project=str(payload["target_project"]),
        relationship_type=str(payload["relationship_type"]),  # type: ignore[arg-type]
        economic_strength=float(payload["economic_strength"]),
        criticality=float(payload["criticality"]),
        revenue_impact=float(payload["revenue_impact"]),
        capital_impact=float(payload["capital_impact"]),
        dependency_confidence=float(payload["dependency_confidence"]),
        evidence_ids=tuple(payload.get("evidence_ids", ())),
        repository_ids=tuple(payload.get("repository_ids", ())),
        discovery_timestamp=datetime.fromisoformat(str(payload["discovery_timestamp"])).astimezone(UTC),
        validation_timestamp=datetime.fromisoformat(str(payload["validation_timestamp"])).astimezone(UTC),
        freshness=float(payload["freshness"]),
        validation_status=str(payload["validation_status"]),
        reason=str(payload.get("reason", "")),
    )


def _metric_from_payload(payload: dict[str, Any]) -> EconomicGraphMetrics:
    return EconomicGraphMetrics(
        project_id=str(payload["project_id"]),
        capital_centrality=float(payload["capital_centrality"]),
        revenue_centrality=float(payload["revenue_centrality"]),
        value_capture=float(payload["value_capture"]),
        economic_moat=float(payload["economic_moat"]),
        switching_cost=float(payload["switching_cost"]),
        revenue_concentration=float(payload["revenue_concentration"]),
        capital_concentration=float(payload["capital_concentration"]),
        dependency_concentration=float(payload["dependency_concentration"]),
        economic_resilience=float(payload["economic_resilience"]),
        economic_fragility=float(payload["economic_fragility"]),
        second_order_dependency=int(payload["second_order_dependency"]),
        third_order_dependency=int(payload["third_order_dependency"]),
        critical_counterparties=tuple(payload.get("critical_counterparties", ())),
    )


def _run_from_payload(payload: dict[str, Any]) -> EconomicGraphRun:
    return EconomicGraphRun(
        run_id=str(payload["run_id"]),
        generated_at=datetime.fromisoformat(str(payload["generated_at"])).astimezone(UTC),
        projects_analyzed=int(payload["projects_analyzed"]),
        nodes=int(payload["nodes"]),
        edges=int(payload["edges"]),
        validated_relationships=int(payload["validated_relationships"]),
        rejected_relationships=int(payload["rejected_relationships"]),
        graph_coverage=float(payload["graph_coverage"]),
        economic_coverage=float(payload["economic_coverage"]),
    )


def _write_jsonl(path: Path, rows: Any, *, append: bool = False) -> None:
    mode = "a" if append else "w"
    with path.open(mode, encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True))
            handle.write("\n")


def _read_jsonl(path: Path) -> tuple[dict[str, Any], ...]:
    if not path.exists():
        return ()
    return tuple(json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
