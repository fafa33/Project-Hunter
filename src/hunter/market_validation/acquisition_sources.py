from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from hunter.acquisition.models import EvidenceValidation, NormalizedEvidence
from hunter.acquisition.repositories import InMemoryAcquisitionRepository
from hunter.economic.models import EconomicEdge, EconomicGraphMetrics
from hunter.economic.repository import EconomicGraphRepository
from hunter.graph.models import TechnologyEdge, TechnologyGraphMetrics
from hunter.graph.repository import TechnologyGraphRepository
from hunter.market_validation.models import EngineValidationSource, Scalar
from hunter.narrative.provider import NARRATIVE_ENGINES, NARRATIVE_METRIC
from hunter.scenario import SCENARIO_ENGINES, ScenarioRepository
from hunter.scenario.models import ScenarioImpact

MARKET_ENGINES: tuple[str, ...] = ("valuation", "comparative_valuation", "mispricing", "asymmetry")
GRAPH_ENGINES: tuple[str, ...] = (
    "technology_necessity",
    "necessity_gap",
    "future_demand",
    "probability",
    "pattern_matching",
    "capital_rotation",
    "committee",
)
ECONOMIC_GRAPH_ENGINES: tuple[str, ...] = (
    "capital_rotation",
    "future_demand",
    "probability",
    "opportunity_timing",
    "technology_necessity",
    "committee",
)


@dataclass(frozen=True)
class EngineEvidenceCoverage:
    engine: str
    configured_projects: int
    available_projects: int

    @property
    def coverage_percent(self) -> float:
        if self.configured_projects <= 0:
            return 0.0
        return round((self.available_projects / self.configured_projects) * 100.0, 2)


def acquisition_engine_sources(
    repository: InMemoryAcquisitionRepository,
    *,
    as_of: datetime | None = None,
) -> dict[str, tuple[EngineValidationSource, ...]]:
    evidence_by_project = _latest_valid_evidence(repository)
    sources: dict[str, list[EngineValidationSource]] = defaultdict(list)
    for project_id, evidence_by_key in evidence_by_project.items():
        coingecko = evidence_by_key.get(("coingecko", "coingecko_market_profile"))
        defillama = evidence_by_key.get(("defillama", "defillama_protocol_profile"))
        github = evidence_by_key.get(("github", "github_repository_profile"))
        narrative = evidence_by_key.get(("narrative", NARRATIVE_METRIC))
        if coingecko is not None:
            for engine in MARKET_ENGINES:
                sources[project_id].append(_source_from_evidence(engine, coingecko.evidence, coingecko.validation))
        if github is not None:
            sources[project_id].append(_source_from_evidence("developer", github.evidence, github.validation))
        if defillama is not None:
            sources[project_id].append(_source_from_evidence("protocol", defillama.evidence, defillama.validation))
        if narrative is not None:
            for engine in NARRATIVE_ENGINES:
                sources[project_id].append(_source_from_evidence(engine, narrative.evidence, narrative.validation))
        risk = _combined_source("risk", tuple(item for item in (coingecko, defillama) if item is not None))
        if risk is not None:
            sources[project_id].append(risk)
        health = _validation_health_source(project_id, tuple(evidence_by_key.values()), as_of=as_of)
        if health is not None:
            sources[project_id].append(health)
    if hasattr(repository, "root"):
        for project_id, graph_sources in _graph_engine_sources(as_of=as_of).items():
            sources[project_id].extend(graph_sources)
        for project_id, economic_sources in _economic_graph_engine_sources(as_of=as_of).items():
            sources[project_id].extend(economic_sources)
        for project_id, scenario_sources in _scenario_engine_sources(as_of=as_of).items():
            sources[project_id].extend(scenario_sources)
    return {project_id: tuple(sorted(items, key=lambda item: item.engine)) for project_id, items in sources.items()}


def engine_coverage(
    sources_by_project: Mapping[str, tuple[EngineValidationSource, ...]],
    *,
    project_ids: tuple[str, ...],
    engines: tuple[str, ...],
) -> tuple[EngineEvidenceCoverage, ...]:
    configured = len(project_ids)
    rows = []
    for engine in engines:
        available = sum(
            1
            for project_id in project_ids
            if any(
                source.engine == engine and source.status == "AVAILABLE"
                for source in sources_by_project.get(project_id, ())
            )
        )
        rows.append(EngineEvidenceCoverage(engine, configured, available))
    return tuple(rows)


@dataclass(frozen=True)
class _EvidenceWithValidation:
    evidence: NormalizedEvidence
    validation: EvidenceValidation


def _latest_valid_evidence(
    repository: InMemoryAcquisitionRepository,
) -> dict[str, dict[tuple[str, str], _EvidenceWithValidation]]:
    rows: dict[str, dict[tuple[str, str], _EvidenceWithValidation]] = defaultdict(dict)
    for evidence in repository.normalized.values():
        validation = repository.validations.get(evidence.evidence_id)
        if validation is None or validation.status != "valid":
            continue
        key = (evidence.provider, evidence.metric)
        current = rows[evidence.target_id].get(key)
        if current is None or evidence.retrieved_at > current.evidence.retrieved_at:
            rows[evidence.target_id][key] = _EvidenceWithValidation(evidence, validation)
    return {project_id: dict(items) for project_id, items in rows.items()}


def _source_from_evidence(
    engine: str,
    evidence: NormalizedEvidence,
    validation: EvidenceValidation,
) -> EngineValidationSource:
    score = _evidence_score(evidence)
    confidence = min(evidence.confidence, validation.confidence)
    freshness = min(evidence.freshness, validation.freshness)
    return EngineValidationSource(
        engine=engine,
        score=score,
        confidence=confidence,
        timestamp=evidence.retrieved_at,
        freshness=freshness,
        source_record_ids=(evidence.raw_evidence_id or evidence.raw_source_id,),
        evidence_ids=(evidence.evidence_id,),
        source=evidence.provider,
        collector=evidence.collector,
        repository_ids=(evidence.repository_id,),
        validation_status=validation.status,
        status="AVAILABLE",
        raw_input_metrics=_scalar_metrics(evidence.raw_metrics),
        normalized_inputs=dict(evidence.normalized_metrics),
        applied_weight=0.0,
        weighted_contribution=0.0,
    )


def _combined_source(
    engine: str,
    evidence: tuple[_EvidenceWithValidation, ...],
) -> EngineValidationSource | None:
    if not evidence:
        return None
    scores = tuple(_evidence_score(item.evidence) for item in evidence)
    confidences = tuple(min(item.evidence.confidence, item.validation.confidence) for item in evidence)
    freshness = tuple(min(item.evidence.freshness, item.validation.freshness) for item in evidence)
    newest = max(item.evidence.retrieved_at for item in evidence)
    normalized: dict[str, float] = {}
    raw: dict[str, Scalar] = {}
    for item in evidence:
        prefix = item.evidence.provider
        normalized.update({f"{prefix}.{key}": value for key, value in item.evidence.normalized_metrics.items()})
        raw.update({f"{prefix}.{key}": value for key, value in _scalar_metrics(item.evidence.raw_metrics).items()})
    return EngineValidationSource(
        engine=engine,
        score=_mean(scores),
        confidence=_mean(confidences),
        timestamp=newest,
        freshness=_mean(freshness),
        source_record_ids=tuple(item.evidence.raw_evidence_id or item.evidence.raw_source_id for item in evidence),
        evidence_ids=tuple(item.evidence.evidence_id for item in evidence),
        source="coingecko+defillama" if len(evidence) > 1 else evidence[0].evidence.provider,
        collector="repository",
        repository_ids=tuple(item.evidence.repository_id for item in evidence),
        validation_status="VALID",
        status="AVAILABLE",
        raw_input_metrics=raw,
        normalized_inputs=normalized,
        applied_weight=0.0,
        weighted_contribution=0.0,
    )


def _validation_health_source(
    project_id: str,
    evidence: tuple[_EvidenceWithValidation, ...],
    *,
    as_of: datetime | None,
) -> EngineValidationSource | None:
    if not evidence:
        return None
    newest = max(item.evidence.retrieved_at for item in evidence)
    score = _mean(tuple(1.0 if item.validation.status == "valid" else 0.0 for item in evidence))
    confidence = _mean(tuple(item.validation.confidence for item in evidence))
    freshness = _mean(tuple(item.validation.freshness for item in evidence))
    timestamp = as_of.astimezone(UTC) if as_of is not None else newest
    return EngineValidationSource(
        engine="validation_health",
        score=score,
        confidence=confidence,
        timestamp=timestamp,
        freshness=freshness,
        source_record_ids=tuple(item.evidence.raw_evidence_id or item.evidence.raw_source_id for item in evidence),
        evidence_ids=tuple(item.evidence.evidence_id for item in evidence),
        source="acquisition-validation",
        collector="repository",
        repository_ids=tuple(item.evidence.repository_id for item in evidence),
        validation_status="VALID",
        status="AVAILABLE",
        raw_input_metrics={"project_id": project_id, "validated_records": len(evidence)},
        normalized_inputs={"valid_ratio": score},
        applied_weight=0.0,
        weighted_contribution=0.0,
    )


def _graph_engine_sources(*, as_of: datetime | None) -> dict[str, tuple[EngineValidationSource, ...]]:
    graph = TechnologyGraphRepository().graph()
    if graph is None:
        return {}
    metrics_by_project = {item.project_id: item for item in graph.metrics}
    edges_by_project: dict[str, list[TechnologyEdge]] = defaultdict(list)
    for edge in graph.edges:
        edges_by_project[edge.source_project].append(edge)
        edges_by_project[edge.target_project].append(edge)
    rows: dict[str, tuple[EngineValidationSource, ...]] = {}
    for project_id, metrics in metrics_by_project.items():
        edges = tuple(
            sorted(edges_by_project.get(project_id, ()), key=lambda item: (item.source_project, item.target_project))
        )
        evidence_ids = tuple(sorted({evidence_id for edge in edges for evidence_id in edge.evidence_ids}))
        repository_ids = tuple(sorted({repository_id for edge in edges for repository_id in edge.repository_ids}))
        if not edges or not evidence_ids:
            continue
        timestamp = as_of.astimezone(UTC) if as_of is not None else graph.generated_at
        confidence = _mean(tuple(edge.dependency_confidence for edge in edges))
        freshness = _mean(tuple(edge.freshness for edge in edges))
        rows[project_id] = tuple(
            _graph_source(engine, metrics, edges, timestamp, confidence, freshness, evidence_ids, repository_ids)
            for engine in GRAPH_ENGINES
        )
    return rows


def _graph_source(
    engine: str,
    metrics: TechnologyGraphMetrics,
    edges: tuple[TechnologyEdge, ...],
    timestamp: datetime,
    confidence: float,
    freshness: float,
    evidence_ids: tuple[str, ...],
    repository_ids: tuple[str, ...],
) -> EngineValidationSource:
    return EngineValidationSource(
        engine=engine,
        score=_graph_score(engine, metrics),
        confidence=confidence,
        timestamp=timestamp,
        freshness=freshness,
        source_record_ids=tuple(f"{edge.source_project}->{edge.target_project}" for edge in edges),
        evidence_ids=evidence_ids,
        source="technology-graph",
        collector="dependency-repository",
        repository_ids=repository_ids,
        validation_status="VALID",
        status="AVAILABLE",
        raw_input_metrics={
            "project_id": metrics.project_id,
            "fan_in": metrics.fan_in,
            "fan_out": metrics.fan_out,
            "dependency_depth": metrics.dependency_depth,
            "supporting_edges": len(edges),
        },
        normalized_inputs={
            "dependency_centrality": metrics.dependency_centrality,
            "infrastructure_centrality": metrics.infrastructure_centrality,
            "single_point_of_failure_risk": metrics.single_point_of_failure_risk,
            "replacement_availability": metrics.replacement_availability,
            "technology_uniqueness": metrics.technology_uniqueness,
            "dependency_concentration": metrics.dependency_concentration,
        },
        applied_weight=0.0,
        weighted_contribution=0.0,
    )


def _graph_score(engine: str, metrics: TechnologyGraphMetrics) -> float:
    if engine in {"technology_necessity", "necessity_gap"}:
        return _mean((metrics.infrastructure_centrality, metrics.dependency_centrality, metrics.technology_uniqueness))
    if engine == "capital_rotation":
        return _mean((metrics.dependency_centrality, metrics.dependency_concentration))
    if engine == "committee":
        return _mean(
            (
                metrics.dependency_centrality,
                metrics.infrastructure_centrality,
                metrics.technology_uniqueness,
                metrics.single_point_of_failure_risk,
            )
        )
    return _mean((metrics.dependency_centrality, metrics.infrastructure_centrality))


def _economic_graph_engine_sources(*, as_of: datetime | None) -> dict[str, tuple[EngineValidationSource, ...]]:
    graph = EconomicGraphRepository().graph()
    metrics_by_project = {item.project_id: item for item in graph.metrics}
    edges_by_project: dict[str, list[EconomicEdge]] = defaultdict(list)
    for edge in graph.edges:
        edges_by_project[edge.source_project].append(edge)
        edges_by_project[edge.target_project].append(edge)
    rows: dict[str, tuple[EngineValidationSource, ...]] = {}
    for project_id, metrics in metrics_by_project.items():
        edges = tuple(
            sorted(edges_by_project.get(project_id, ()), key=lambda item: (item.source_project, item.target_project))
        )
        evidence_ids = tuple(sorted({evidence_id for edge in edges for evidence_id in edge.evidence_ids}))
        repository_ids = tuple(sorted({repository_id for edge in edges for repository_id in edge.repository_ids}))
        if not edges or not evidence_ids:
            continue
        timestamp = as_of.astimezone(UTC) if as_of is not None else graph.generated_at
        confidence = _mean(tuple(edge.dependency_confidence for edge in edges))
        freshness = _mean(tuple(edge.freshness for edge in edges))
        rows[project_id] = tuple(
            _economic_graph_source(
                engine, metrics, edges, timestamp, confidence, freshness, evidence_ids, repository_ids
            )
            for engine in ECONOMIC_GRAPH_ENGINES
        )
    return rows


def _economic_graph_source(
    engine: str,
    metrics: EconomicGraphMetrics,
    edges: tuple[EconomicEdge, ...],
    timestamp: datetime,
    confidence: float,
    freshness: float,
    evidence_ids: tuple[str, ...],
    repository_ids: tuple[str, ...],
) -> EngineValidationSource:
    return EngineValidationSource(
        engine=engine,
        score=_economic_graph_score(engine, metrics),
        confidence=confidence,
        timestamp=timestamp,
        freshness=freshness,
        source_record_ids=tuple(f"{edge.source_project}->{edge.target_project}" for edge in edges),
        evidence_ids=evidence_ids,
        source="economic-graph",
        collector="economic-repository",
        repository_ids=repository_ids,
        validation_status="VALID",
        status="AVAILABLE",
        raw_input_metrics={
            "project_id": metrics.project_id,
            "supporting_edges": len(edges),
            "second_order_dependency": metrics.second_order_dependency,
            "third_order_dependency": metrics.third_order_dependency,
        },
        normalized_inputs={
            "capital_centrality": metrics.capital_centrality,
            "revenue_centrality": metrics.revenue_centrality,
            "value_capture": metrics.value_capture,
            "economic_moat": metrics.economic_moat,
            "switching_cost": metrics.switching_cost,
            "revenue_concentration": metrics.revenue_concentration,
            "capital_concentration": metrics.capital_concentration,
            "dependency_concentration": metrics.dependency_concentration,
            "economic_resilience": metrics.economic_resilience,
            "economic_fragility": metrics.economic_fragility,
        },
        applied_weight=0.0,
        weighted_contribution=0.0,
    )


def _economic_graph_score(engine: str, metrics: EconomicGraphMetrics) -> float:
    if engine == "capital_rotation":
        return _mean((metrics.capital_centrality, metrics.capital_concentration, metrics.dependency_concentration))
    if engine == "future_demand":
        return _mean((metrics.value_capture, metrics.economic_moat, metrics.economic_resilience))
    if engine == "probability":
        return _mean((metrics.economic_resilience, metrics.economic_moat, 1.0 - metrics.economic_fragility))
    if engine == "opportunity_timing":
        return _mean((metrics.capital_centrality, metrics.revenue_centrality))
    if engine == "technology_necessity":
        return _mean((metrics.switching_cost, metrics.economic_moat))
    return _mean((metrics.economic_moat, metrics.value_capture, metrics.economic_resilience))


def _scenario_engine_sources(*, as_of: datetime | None) -> dict[str, tuple[EngineValidationSource, ...]]:
    impacts_by_project: dict[str, list[ScenarioImpact]] = defaultdict(list)
    for impact in ScenarioRepository().impacts():
        if impact.validation_status == "VALID" and impact.evidence_ids:
            impacts_by_project[impact.project_id].append(impact)
    rows: dict[str, tuple[EngineValidationSource, ...]] = {}
    for project_id, impacts in impacts_by_project.items():
        impact_tuple = tuple(sorted(impacts, key=lambda item: (item.scenario_id, item.project_id)))
        evidence_ids = tuple(sorted({evidence_id for impact in impact_tuple for evidence_id in impact.evidence_ids}))
        repository_ids = tuple(
            sorted({repository_id for impact in impact_tuple for repository_id in impact.repository_ids})
        )
        if not evidence_ids:
            continue
        timestamp = as_of.astimezone(UTC) if as_of is not None else datetime.now(tz=UTC)
        confidence = _mean(tuple(impact.confidence for impact in impact_tuple))
        freshness = _mean(tuple(impact.freshness for impact in impact_tuple))
        rows[project_id] = tuple(
            _scenario_source(engine, impact_tuple, timestamp, confidence, freshness, evidence_ids, repository_ids)
            for engine in SCENARIO_ENGINES
        )
    return rows


def _scenario_source(
    engine: str,
    impacts: tuple[ScenarioImpact, ...],
    timestamp: datetime,
    confidence: float,
    freshness: float,
    evidence_ids: tuple[str, ...],
    repository_ids: tuple[str, ...],
) -> EngineValidationSource:
    return EngineValidationSource(
        engine=engine,
        score=_scenario_score(engine, impacts),
        confidence=confidence,
        timestamp=timestamp,
        freshness=freshness,
        source_record_ids=tuple(sorted({impact.scenario_id for impact in impacts})),
        evidence_ids=evidence_ids,
        source="scenario-simulation",
        collector="scenario-repository",
        repository_ids=repository_ids,
        validation_status="VALID",
        status="AVAILABLE",
        raw_input_metrics={
            "scenario_count": len(impacts),
            "affected_edges": sum(len(impact.affected_edges) for impact in impacts),
            "affected_nodes": len({node for impact in impacts for node in impact.affected_nodes}),
        },
        normalized_inputs={
            "direct_impact": _mean(tuple(impact.direct_impact for impact in impacts)),
            "indirect_impact": _mean(tuple(impact.indirect_impact for impact in impacts)),
            "dependency_propagation": _mean(tuple(impact.dependency_propagation for impact in impacts)),
            "economic_propagation": _mean(tuple(impact.economic_propagation for impact in impacts)),
            "recovery_difficulty": _mean(tuple(impact.recovery_difficulty for impact in impacts)),
            "replacement_availability": _mean(tuple(impact.replacement_availability for impact in impacts)),
            "infrastructure_resilience": _mean(tuple(impact.infrastructure_resilience for impact in impacts)),
            "economic_resilience": _mean(tuple(impact.economic_resilience for impact in impacts)),
            "system_fragility": _mean(tuple(impact.system_fragility for impact in impacts)),
        },
        applied_weight=0.0,
        weighted_contribution=0.0,
    )


def _scenario_score(engine: str, impacts: tuple[ScenarioImpact, ...]) -> float:
    if engine == "technology_necessity":
        return _mean(tuple(impact.dependency_propagation for impact in impacts))
    if engine == "future_demand":
        return _mean(tuple(impact.economic_resilience for impact in impacts))
    if engine == "probability":
        return _mean(tuple(1.0 - impact.system_fragility for impact in impacts))
    if engine == "capital_rotation":
        return _mean(tuple(impact.economic_propagation for impact in impacts))
    if engine == "opportunity_timing":
        return _mean(tuple(max(impact.direct_impact, impact.indirect_impact) for impact in impacts))
    return _mean(tuple(impact.infrastructure_resilience + impact.economic_resilience for impact in impacts)) / 2


def _evidence_score(evidence: NormalizedEvidence) -> float:
    if evidence.normalized_metrics:
        return _mean(tuple(evidence.normalized_metrics.values()))
    return evidence.confidence


def _scalar_metrics(metrics: Mapping[str, object]) -> dict[str, Scalar]:
    return {
        str(key): value
        for key, value in metrics.items()
        if isinstance(value, str | int | float | bool) or value is None
    }


def _mean(values: tuple[float, ...]) -> float:
    if not values:
        return 0.0
    return round(sum(values) / len(values), 4)
