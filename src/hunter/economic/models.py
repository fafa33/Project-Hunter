from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

EconomicRelationshipType = Literal[
    "revenue_dependency",
    "liquidity_dependency",
    "security_dependency",
    "demand_dependency",
    "value_capture_dependency",
    "treasury_dependency",
    "fee_dependency",
    "infrastructure_dependency",
    "emission_dependency",
    "capital_dependency",
]


@dataclass(frozen=True)
class EconomicNode:
    project_id: str
    name: str
    sector: str
    evidence_ids: tuple[str, ...]
    repository_ids: tuple[str, ...]
    confidence: float
    freshness: float


@dataclass(frozen=True)
class EconomicEdge:
    source_project: str
    target_project: str
    relationship_type: EconomicRelationshipType
    economic_strength: float
    criticality: float
    revenue_impact: float
    capital_impact: float
    dependency_confidence: float
    evidence_ids: tuple[str, ...]
    repository_ids: tuple[str, ...]
    discovery_timestamp: datetime
    validation_timestamp: datetime
    freshness: float
    validation_status: str
    reason: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "discovery_timestamp", self.discovery_timestamp.astimezone(UTC))
        object.__setattr__(self, "validation_timestamp", self.validation_timestamp.astimezone(UTC))


@dataclass(frozen=True)
class EconomicGraphMetrics:
    project_id: str
    capital_centrality: float
    revenue_centrality: float
    value_capture: float
    economic_moat: float
    switching_cost: float
    revenue_concentration: float
    capital_concentration: float
    dependency_concentration: float
    economic_resilience: float
    economic_fragility: float
    second_order_dependency: int
    third_order_dependency: int
    critical_counterparties: tuple[str, ...]


@dataclass(frozen=True)
class EconomicGraph:
    graph_id: str
    generated_at: datetime
    nodes: tuple[EconomicNode, ...]
    edges: tuple[EconomicEdge, ...]
    metrics: tuple[EconomicGraphMetrics, ...]


@dataclass(frozen=True)
class EconomicGraphRun:
    run_id: str
    generated_at: datetime
    projects_analyzed: int
    nodes: int
    edges: int
    validated_relationships: int
    rejected_relationships: int
    graph_coverage: float
    economic_coverage: float
    snapshot_ref: str | None = None
    replay_limitation: str | None = None
