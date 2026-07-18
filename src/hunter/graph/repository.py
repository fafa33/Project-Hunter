from __future__ import annotations

import hashlib
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
        snapshot_ref = _snapshot_ref(run.run_id)
        snapshot = self.root / snapshot_ref
        _write_immutable_jsonl(snapshot / "nodes.jsonl", (_node_payload(item) for item in graph.nodes))
        _write_immutable_jsonl(snapshot / "edges.jsonl", (_edge_payload(item) for item in graph.edges))
        _write_immutable_jsonl(snapshot / "metrics.jsonl", (_metric_payload(item) for item in graph.metrics))
        _write_immutable_json(
            snapshot / "manifest.json",
            {"graph_id": graph.graph_id, "generated_at": graph.generated_at.isoformat(), "run_id": run.run_id},
        )
        _append_unique(self.root / "runs.jsonl", _run_payload(run, snapshot_ref=snapshot_ref), identity="run_id")

    def graph(self, run_id: str | None = None) -> TechnologyGraph:
        if run_id is not None:
            run = next((item for item in self.runs() if item.run_id == run_id), None)
            if run is None:
                raise LookupError(f"Unknown technology graph run: {run_id}")
            if run.snapshot_ref is None:
                raise LookupError(f"Technology graph run has no historical snapshot: {run_id}")
            return self._snapshot_graph(run.snapshot_ref)
        runs = self.runs()
        if runs and runs[-1].snapshot_ref is not None:
            return self._snapshot_graph(runs[-1].snapshot_ref)
        return self._legacy_graph()

    def snapshot_status(self, run_id: str | None = None) -> dict[str, str | None]:
        if run_id is not None:
            run = next((item for item in self.runs() if item.run_id == run_id), None)
            if run is None:
                raise LookupError(f"Unknown technology graph run: {run_id}")
            return {"snapshot_ref": run.snapshot_ref, "replay_limitation": run.replay_limitation}
        return {
            "snapshot_ref": None,
            "replay_limitation": "legacy current-state files have no trustworthy run linkage",
        }

    def _snapshot_graph(self, snapshot_ref: str) -> TechnologyGraph:
        snapshot = self.root / snapshot_ref
        manifest = _read_json(snapshot / "manifest.json")
        nodes = tuple(_node_from_payload(item) for item in _read_jsonl(snapshot / "nodes.jsonl"))
        edges = tuple(_edge_from_payload(item) for item in _read_jsonl(snapshot / "edges.jsonl"))
        metrics = tuple(_metric_from_payload(item) for item in _read_jsonl(snapshot / "metrics.jsonl"))
        return TechnologyGraph(
            str(manifest["graph_id"]),
            datetime.fromisoformat(str(manifest["generated_at"])).astimezone(UTC),
            nodes,
            edges,
            metrics,
        )

    def _legacy_graph(self) -> TechnologyGraph:
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


def _run_payload(item: TechnologyGraphRun, *, snapshot_ref: str | None = None) -> dict[str, Any]:
    payload = asdict(item)
    payload["generated_at"] = item.generated_at.isoformat()
    if snapshot_ref is not None:
        payload["snapshot_ref"] = snapshot_ref
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
        snapshot_ref=str(payload["snapshot_ref"]) if payload.get("snapshot_ref") else None,
        replay_limitation=(
            None if payload.get("snapshot_ref") else "legacy run summary has no trustworthy snapshot linkage"
        ),
    )


def _append_jsonl(path: Path, rows: Any) -> None:
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True))
            handle.write("\n")


def _read_jsonl(path: Path) -> tuple[dict[str, Any], ...]:
    if not path.exists():
        return ()
    return tuple(json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def _snapshot_ref(identity: str) -> str:
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return f"snapshots/{digest}"


def _canonical_line(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"


def _write_immutable_jsonl(path: Path, rows: Any) -> None:
    content = "".join(_canonical_line(row) for row in rows)
    _write_immutable(path, content)


def _write_immutable_json(path: Path, payload: dict[str, Any]) -> None:
    _write_immutable(path, _canonical_line(payload))


def _write_immutable(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_text(encoding="utf-8") != content:
            raise ValueError(f"Immutable technology graph snapshot conflict: {path}")
        return
    with path.open("x", encoding="utf-8") as handle:
        handle.write(content)


def _append_unique(path: Path, payload: dict[str, Any], *, identity: str) -> None:
    existing = _read_jsonl(path)
    matches = tuple(item for item in existing if item.get(identity) == payload.get(identity))
    if matches:
        if any(_canonical_line(item) != _canonical_line(payload) for item in matches):
            raise ValueError(f"Technology graph run identity conflict: {payload[identity]}")
        return
    _append_jsonl(path, (payload,))


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return payload
