from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

DependencyType = Literal[
    "direct",
    "indirect",
    "optional",
    "mandatory",
    "runtime",
    "development",
    "security",
    "economic",
    "governance",
    "infrastructure",
]


@dataclass(frozen=True)
class TechnologyNode:
    project_id: str
    name: str
    sector: str
    evidence_ids: tuple[str, ...]
    repository_ids: tuple[str, ...]
    confidence: float
    freshness: float


@dataclass(frozen=True)
class TechnologyEdge:
    source_project: str
    target_project: str
    dependency_type: DependencyType
    strength: float
    criticality: float
    replacement_difficulty: float
    switching_cost: float
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
class TechnologyGraphMetrics:
    project_id: str
    dependency_depth: int
    dependency_centrality: float
    infrastructure_centrality: float
    critical_path: tuple[str, ...]
    fan_in: int
    fan_out: int
    redundancy: float
    single_point_of_failure_risk: float
    replacement_availability: float
    technology_uniqueness: float
    dependency_concentration: float


@dataclass(frozen=True)
class TechnologyGraph:
    graph_id: str
    generated_at: datetime
    nodes: tuple[TechnologyNode, ...]
    edges: tuple[TechnologyEdge, ...]
    metrics: tuple[TechnologyGraphMetrics, ...]


@dataclass(frozen=True)
class TechnologyGraphRun:
    run_id: str
    generated_at: datetime
    projects_analyzed: int
    nodes: int
    edges: int
    validated_dependencies: int
    rejected_dependencies: int
    graph_coverage: float
    technology_coverage: float
