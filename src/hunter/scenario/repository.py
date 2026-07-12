from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hunter.scenario.models import ScenarioDefinition, ScenarioImpact, ScenarioResult, ScenarioRun


class ScenarioRepository:
    def __init__(self, root: str | Path = "data/scenarios") -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, results: tuple[ScenarioResult, ...], run: ScenarioRun) -> None:
        _write_jsonl(self.root / "scenarios.jsonl", (_scenario_payload(item.scenario) for item in results))
        _write_jsonl(
            self.root / "impacts.jsonl",
            (_impact_payload(impact) for result in results for impact in result.impacts),
        )
        _write_jsonl(self.root / "results.jsonl", (_result_payload(item) for item in results))
        _write_jsonl(self.root / "runs.jsonl", (_run_payload(run),), append=True)

    def results(self) -> tuple[ScenarioResult, ...]:
        scenarios = {item.scenario_id: item for item in self.scenarios()}
        impacts_by_scenario: dict[str, list[ScenarioImpact]] = {}
        for impact in self.impacts():
            impacts_by_scenario.setdefault(impact.scenario_id, []).append(impact)
        rows = []
        for payload in _read_jsonl(self.root / "results.jsonl"):
            scenario_id = str(payload["scenario_id"])
            scenario = scenarios.get(scenario_id)
            if scenario is None:
                continue
            rows.append(
                ScenarioResult(
                    scenario=scenario,
                    impacts=tuple(sorted(impacts_by_scenario.get(scenario_id, ()), key=lambda item: item.project_id)),
                    affected_projects=tuple(payload.get("affected_projects", ())),
                    affected_edges=tuple(payload.get("affected_edges", ())),
                    affected_nodes=tuple(payload.get("affected_nodes", ())),
                    confidence=float(payload["confidence"]),
                    freshness=float(payload["freshness"]),
                )
            )
        return tuple(rows)

    def scenarios(self) -> tuple[ScenarioDefinition, ...]:
        return tuple(_scenario_from_payload(item) for item in _read_jsonl(self.root / "scenarios.jsonl"))

    def impacts(self) -> tuple[ScenarioImpact, ...]:
        return tuple(_impact_from_payload(item) for item in _read_jsonl(self.root / "impacts.jsonl"))

    def runs(self) -> tuple[ScenarioRun, ...]:
        return tuple(_run_from_payload(item) for item in _read_jsonl(self.root / "runs.jsonl"))


def _scenario_payload(item: ScenarioDefinition) -> dict[str, Any]:
    payload = asdict(item)
    payload["created_at"] = item.created_at.isoformat()
    return payload


def _impact_payload(item: ScenarioImpact) -> dict[str, Any]:
    return asdict(item)


def _result_payload(item: ScenarioResult) -> dict[str, Any]:
    return {
        "scenario_id": item.scenario.scenario_id,
        "affected_projects": item.affected_projects,
        "affected_edges": item.affected_edges,
        "affected_nodes": item.affected_nodes,
        "confidence": item.confidence,
        "freshness": item.freshness,
    }


def _run_payload(item: ScenarioRun) -> dict[str, Any]:
    payload = asdict(item)
    payload["generated_at"] = item.generated_at.isoformat()
    return payload


def _scenario_from_payload(payload: dict[str, Any]) -> ScenarioDefinition:
    return ScenarioDefinition(
        scenario_id=str(payload["scenario_id"]),
        scenario_type=str(payload["scenario_type"]),  # type: ignore[arg-type]
        target_project=str(payload["target_project"]),
        description=str(payload["description"]),
        evidence_ids=tuple(payload.get("evidence_ids", ())),
        repository_ids=tuple(payload.get("repository_ids", ())),
        created_at=datetime.fromisoformat(str(payload["created_at"])).astimezone(UTC),
    )


def _impact_from_payload(payload: dict[str, Any]) -> ScenarioImpact:
    return ScenarioImpact(
        scenario_id=str(payload["scenario_id"]),
        project_id=str(payload["project_id"]),
        direct_impact=float(payload["direct_impact"]),
        indirect_impact=float(payload["indirect_impact"]),
        second_order_impact=float(payload["second_order_impact"]),
        third_order_impact=float(payload["third_order_impact"]),
        dependency_propagation=float(payload["dependency_propagation"]),
        economic_propagation=float(payload["economic_propagation"]),
        recovery_difficulty=float(payload["recovery_difficulty"]),
        replacement_availability=float(payload["replacement_availability"]),
        infrastructure_resilience=float(payload["infrastructure_resilience"]),
        economic_resilience=float(payload["economic_resilience"]),
        system_fragility=float(payload["system_fragility"]),
        affected_nodes=tuple(payload.get("affected_nodes", ())),
        affected_edges=tuple(payload.get("affected_edges", ())),
        dependency_paths=tuple(tuple(path) for path in payload.get("dependency_paths", ())),
        economic_paths=tuple(tuple(path) for path in payload.get("economic_paths", ())),
        evidence_ids=tuple(payload.get("evidence_ids", ())),
        repository_ids=tuple(payload.get("repository_ids", ())),
        confidence=float(payload["confidence"]),
        freshness=float(payload["freshness"]),
        validation_status=str(payload["validation_status"]),
    )


def _run_from_payload(payload: dict[str, Any]) -> ScenarioRun:
    return ScenarioRun(
        run_id=str(payload["run_id"]),
        generated_at=datetime.fromisoformat(str(payload["generated_at"])).astimezone(UTC),
        projects_analyzed=int(payload["projects_analyzed"]),
        scenarios=int(payload["scenarios"]),
        projects_simulated=int(payload["projects_simulated"]),
        affected_nodes=int(payload["affected_nodes"]),
        affected_edges=int(payload["affected_edges"]),
        propagation_depth=int(payload["propagation_depth"]),
        scenario_coverage=float(payload["scenario_coverage"]),
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
