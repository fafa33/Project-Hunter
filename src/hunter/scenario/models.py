from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

ScenarioType = Literal[
    "technology_disappears",
    "technology_replacement",
    "protocol_migration",
    "chain_migration",
    "bridge_failure",
    "oracle_failure",
    "revenue_decline",
    "revenue_growth",
    "tvl_collapse",
    "tvl_growth",
    "developer_decline",
    "developer_growth",
    "capital_rotation",
    "narrative_shift",
    "infrastructure_replacement",
    "governance_change",
    "major_protocol_upgrade",
    "dependency_removal",
    "dependency_addition",
    "security_incident",
    "market_wide_infrastructure_shock",
]


@dataclass(frozen=True)
class ScenarioDefinition:
    scenario_id: str
    scenario_type: ScenarioType
    target_project: str
    description: str
    evidence_ids: tuple[str, ...]
    repository_ids: tuple[str, ...]
    created_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "created_at", self.created_at.astimezone(UTC))


@dataclass(frozen=True)
class ScenarioImpact:
    scenario_id: str
    project_id: str
    direct_impact: float
    indirect_impact: float
    second_order_impact: float
    third_order_impact: float
    dependency_propagation: float
    economic_propagation: float
    recovery_difficulty: float
    replacement_availability: float
    infrastructure_resilience: float
    economic_resilience: float
    system_fragility: float
    affected_nodes: tuple[str, ...]
    affected_edges: tuple[str, ...]
    dependency_paths: tuple[tuple[str, ...], ...]
    economic_paths: tuple[tuple[str, ...], ...]
    evidence_ids: tuple[str, ...]
    repository_ids: tuple[str, ...]
    confidence: float
    freshness: float
    validation_status: str


@dataclass(frozen=True)
class ScenarioResult:
    scenario: ScenarioDefinition
    impacts: tuple[ScenarioImpact, ...]
    affected_projects: tuple[str, ...]
    affected_edges: tuple[str, ...]
    affected_nodes: tuple[str, ...]
    confidence: float
    freshness: float


@dataclass(frozen=True)
class ScenarioRun:
    run_id: str
    generated_at: datetime
    projects_analyzed: int
    scenarios: int
    projects_simulated: int
    affected_nodes: int
    affected_edges: int
    propagation_depth: int
    scenario_coverage: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "generated_at", self.generated_at.astimezone(UTC))
