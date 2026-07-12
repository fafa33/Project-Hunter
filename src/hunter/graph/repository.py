from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hunter.graph.models import (
    TechnologyEdge,
    TechnologyGraph,
    TechnologyGraphMetrics,
    TechnologyGraphRun,
    TechnologyNode,
)


class TechnologyGraphRepository:
    def __init__(self, root: str | Path = "data/technology_graph") -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, graph: TechnologyGraph, run: TechnologyGraphRun) -> None:
        _write_jsonl(self.root / "nodes.jsonl", (_node_payload(item) for item in graph.nodes))
        _write_jsonl(self.root / "edges.jsonl", (_edge_payload(item) for item in graph.edges))
        _write_jsonl(self.root / "metrics.jsonl", (_metric_payload(item) for item in graph.metrics))
        _write_jsonl(self.root / "runs.jsonl", (_run_payload(run),), append=True)

    def graph(self) -> TechnologyGraph:
        nodes = tuple(_node_from_payload(item) for item in _read_jsonl(self.root / "nodes.jsonl"))
        edges = tuple(_edge_from_payload(item) for item in _read_jsonl(self.root / "edges.jsonl"))
        metrics = tuple(_metric_from_payload(item) for item in _read_jsonl(self.root / "metrics.jsonl"))
        generated = max((edge.discovery_timestamp for edge in edges), default=datetime.now(tz=UTC))
        return TechnologyGraph("persisted-technology-graph", generated, nodes, edges, metrics)

    def runs(self) -> tuple[TechnologyGraphRun, ...]:
        return tuple(_run_from_payload(item) for item in _read_jsonl(self.root / "runs.jsonl"))


def _node_payload(item: TechnologyNode) -> dict[str, Any]:
    return asdict(item)


def _edge_payload(item: TechnologyEdge) -> dict[str, Any]:
    payload = asdict(item)
    payload["discovery_timestamp"] = item.discovery_timestamp.isoformat()
    payload["validation_timestamp"] = item.validation_timestamp.isoformat()
    return payload


def _metric_payload(item: TechnologyGraphMetrics) -> dict[str, Any]:
    return asdict(item)


def _run_payload(item: TechnologyGraphRun) -> dict[str, Any]:
    payload = asdict(item)
    payload["generated_at"] = item.generated_at.isoformat()
    return payload


def _node_from_payload(payload: dict[str, Any]) -> TechnologyNode:
    return TechnologyNode(
        project_id=str(payload["project_id"]),
        name=str(payload["name"]),
        sector=str(payload["sector"]),
        evidence_ids=tuple(payload.get("evidence_ids", ())),
        repository_ids=tuple(payload.get("repository_ids", ())),
        confidence=float(payload["confidence"]),
        freshness=float(payload["freshness"]),
    )


def _edge_from_payload(payload: dict[str, Any]) -> TechnologyEdge:
    return TechnologyEdge(
        source_project=str(payload["source_project"]),
        target_project=str(payload["target_project"]),
        dependency_type=str(payload["dependency_type"]),  # type: ignore[arg-type]
        strength=float(payload["strength"]),
        criticality=float(payload["criticality"]),
        replacement_difficulty=float(payload["replacement_difficulty"]),
        switching_cost=float(payload["switching_cost"]),
        dependency_confidence=float(payload["dependency_confidence"]),
        evidence_ids=tuple(payload.get("evidence_ids", ())),
        repository_ids=tuple(payload.get("repository_ids", ())),
        discovery_timestamp=datetime.fromisoformat(str(payload["discovery_timestamp"])).astimezone(UTC),
        validation_timestamp=datetime.fromisoformat(str(payload["validation_timestamp"])).astimezone(UTC),
        freshness=float(payload["freshness"]),
        validation_status=str(payload["validation_status"]),
        reason=str(payload.get("reason", "")),
    )


def _metric_from_payload(payload: dict[str, Any]) -> TechnologyGraphMetrics:
    return TechnologyGraphMetrics(
        project_id=str(payload["project_id"]),
        dependency_depth=int(payload["dependency_depth"]),
        dependency_centrality=float(payload["dependency_centrality"]),
        infrastructure_centrality=float(payload["infrastructure_centrality"]),
        critical_path=tuple(payload.get("critical_path", ())),
        fan_in=int(payload["fan_in"]),
        fan_out=int(payload["fan_out"]),
        redundancy=float(payload["redundancy"]),
        single_point_of_failure_risk=float(payload["single_point_of_failure_risk"]),
        replacement_availability=float(payload["replacement_availability"]),
        technology_uniqueness=float(payload["technology_uniqueness"]),
        dependency_concentration=float(payload["dependency_concentration"]),
    )


def _run_from_payload(payload: dict[str, Any]) -> TechnologyGraphRun:
    return TechnologyGraphRun(
        run_id=str(payload["run_id"]),
        generated_at=datetime.fromisoformat(str(payload["generated_at"])).astimezone(UTC),
        projects_analyzed=int(payload["projects_analyzed"]),
        nodes=int(payload["nodes"]),
        edges=int(payload["edges"]),
        validated_dependencies=int(payload["validated_dependencies"]),
        rejected_dependencies=int(payload["rejected_dependencies"]),
        graph_coverage=float(payload["graph_coverage"]),
        technology_coverage=float(payload["technology_coverage"]),
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
