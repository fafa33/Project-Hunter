from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path
from typing import Any

from hunter.acquisition.models import EvidenceValidation, NormalizedEvidence
from hunter.acquisition.repositories import InMemoryAcquisitionRepository
from hunter.economic.models import EconomicEdge, EconomicGraphMetrics
from hunter.economic.repository import EconomicGraphRepository
from hunter.graph.models import TechnologyEdge, TechnologyGraphMetrics
from hunter.graph.repository import TechnologyGraphRepository
from hunter.historical_acquisition.repository import HistoricalEvidenceRepository
from hunter.historical_acquisition.usd_valuation import HistoricalUsdValuator, UsdValuation
from hunter.jsonl_contract import JsonlRecord
from hunter.macro import MacroRepository, MacroSnapshot
from hunter.market_validation.models import EngineValidationSource, Scalar
from hunter.narrative.provider import NARRATIVE_ENGINES, NARRATIVE_METRIC
from hunter.onchain.configuration import OnChainConfig, load_onchain_config
from hunter.onchain.evidence import EvidenceTrust, verified_capital_flow_rows, verified_swap_rows
from hunter.onchain.models import RawOnChainObservation
from hunter.onchain.repository import OnChainRepository
from hunter.onchain.wallet_performance import FIFO_POLICY, WalletPerformance, wallet_performance_from_swaps
from hunter.scenario import SCENARIO_ENGINES, ScenarioRepository
from hunter.scenario.models import ScenarioImpact
from hunter.timing.models import TimingAssessment
from hunter.timing.repository import TIMING_JSONL_SCHEMA, TimingRepository, timing_assessment_from_payload
from hunter.whale import WhaleRepository, WhaleSnapshot

GRAPH_ENGINES: tuple[str, ...] = (
    "technology_necessity",
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
    "technology_necessity",
    "committee",
)
MACRO_ENGINE_TARGETS: tuple[str, ...] = (
    "macro_intelligence",
    "probability",
    "future_demand",
    "capital_rotation",
    "pattern_matching",
    "risk",
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
    usd_valuator: Callable[[RawOnChainObservation], UsdValuation] | None = None,
) -> dict[str, tuple[EngineValidationSource, ...]]:
    resolved_usd_valuator = usd_valuator or _default_usd_valuator()
    evidence_by_project = _latest_valid_evidence(repository)
    sources: dict[str, list[EngineValidationSource]] = defaultdict(list)
    for project_id, evidence_by_key in evidence_by_project.items():
        coingecko = evidence_by_key.get(("coingecko", "coingecko_market_profile"))
        defillama = evidence_by_key.get(("defillama", "defillama_protocol_profile"))
        github = evidence_by_key.get(("github", "github_repository_profile"))
        narrative = evidence_by_key.get(("narrative", NARRATIVE_METRIC))
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
        timing_repository = TimingRepository()
        macro_sources = _macro_engine_sources(as_of=as_of)
        capital_rows = OnChainRepository().raw()
        for project_id in evidence_by_project:
            sources[project_id].extend(macro_sources)
            sources[project_id].extend(_whale_engine_sources(as_of=as_of, project_id=project_id))
            contextual = _capital_whale_context(
                project_id, capital_rows, as_of=as_of, usd_valuator=resolved_usd_valuator
            )
            if contextual is not None:
                sources[project_id].append(contextual)
            swap_context = _swap_wallet_context(
                project_id, capital_rows, as_of=as_of, usd_valuator=resolved_usd_valuator
            )
            if swap_context is not None:
                sources[project_id].append(swap_context)
        for project_id, graph_sources in _graph_engine_sources(as_of=as_of).items():
            sources[project_id].extend(graph_sources)
        for project_id, economic_sources in _economic_graph_engine_sources(as_of=as_of).items():
            sources[project_id].extend(economic_sources)
        for project_id, scenario_sources in _scenario_engine_sources(as_of=as_of).items():
            sources[project_id].extend(scenario_sources)
        for project_id, timing_sources in _timing_engine_sources(
            timing_repository.records(timing_repository.assessment_path), as_of=as_of
        ).items():
            sources[project_id].extend(timing_sources)
    return {project_id: tuple(sorted(items, key=lambda item: item.engine)) for project_id, items in sources.items()}


def _macro_engine_sources(*, as_of: datetime | None) -> tuple[EngineValidationSource, ...]:
    snapshot = MacroRepository().latest_snapshot()
    if snapshot is None or not snapshot.evidence:
        return ()
    return tuple(_macro_source(engine, snapshot, snapshot.generated_at) for engine in MACRO_ENGINE_TARGETS)


def _macro_source(engine: str, snapshot: MacroSnapshot, timestamp: datetime) -> EngineValidationSource:
    source_record_ids = tuple(f"{item.metric.provider}:{item.metric.name}" for item in snapshot.evidence)
    evidence_ids = tuple(item.evidence_id for item in snapshot.evidence)
    repository_ids = tuple(item.repository_id for item in snapshot.evidence)
    return EngineValidationSource(
        engine=engine,
        score=_macro_score(engine, snapshot),
        confidence=snapshot.macro_confidence,
        timestamp=timestamp,
        freshness=snapshot.freshness,
        source_record_ids=source_record_ids,
        evidence_ids=evidence_ids,
        source="macro-intelligence",
        collector="macro-repository",
        repository_ids=repository_ids,
        validation_status="VALID",
        status="AVAILABLE",
        raw_input_metrics=dict(snapshot.raw_metrics),
        normalized_inputs=dict(snapshot.normalized_metrics),
    )


def _macro_score(engine: str, snapshot: MacroSnapshot) -> float:
    if engine == "risk":
        return snapshot.risk_off_score
    if engine == "probability":
        return _mean((snapshot.risk_on_score, snapshot.monetary_policy_score, snapshot.crypto_liquidity_score))
    if engine == "future_demand":
        return _mean((snapshot.liquidity_score, snapshot.crypto_liquidity_score, 1.0 - snapshot.recession_probability))
    if engine == "capital_rotation":
        return _mean((snapshot.crypto_liquidity_score, snapshot.risk_on_score))
    if engine == "pattern_matching":
        return _mean((snapshot.risk_on_score, snapshot.risk_off_score, snapshot.evidence_quality))
    if engine == "committee":
        return _mean((snapshot.risk_on_score, snapshot.crypto_liquidity_score, snapshot.macro_confidence))
    return _mean(
        (
            snapshot.liquidity_score,
            snapshot.inflation_score,
            snapshot.monetary_policy_score,
            1.0 - snapshot.recession_probability,
            snapshot.risk_on_score,
            snapshot.crypto_liquidity_score,
        )
    )


def _whale_engine_sources(*, as_of: datetime | None, project_id: str | None = None) -> Sequence[EngineValidationSource]:
    # Aggregate derivatives evidence is market context, not project wallet attribution.
    # Until an authoritative project-wallet mapping exists, do not score it as whale evidence.
    snapshot = WhaleRepository().latest_snapshot()
    cutoff = as_of or datetime.now(UTC)
    if snapshot is None or not snapshot.evidence or not cutoff - timedelta(hours=24) <= snapshot.generated_at <= cutoff:
        return []
    if any(
        item.validation_status != "VALID"
        or not cutoff - timedelta(hours=24) <= item.metric.timestamp <= cutoff
        or item.metric.retrieval_time > cutoff
        for item in snapshot.evidence
    ):
        return []
    # Preserve market context for explainability without contributing a project score.
    return [
        EngineValidationSource(
            engine="whale_intelligence",
            score=0.0,
            confidence=0.0,
            timestamp=snapshot.generated_at,
            freshness=snapshot.freshness,
            source_record_ids=tuple(
                f"{item.metric.provider}:{item.metric.asset}:{item.metric.name}" for item in snapshot.evidence
            ),
            evidence_ids=tuple(item.evidence_id for item in snapshot.evidence),
            repository_ids=tuple(item.repository_id for item in snapshot.evidence),
            source="whale-market-context",
            collector="whale-repository",
            validation_status="PARTIAL",
            status="UNAVAILABLE",
            missing_fields=("project_wallet_attribution",),
            warnings=("market_context_not_project_whale_signal",),
            raw_input_metrics=dict(snapshot.raw_metrics),
        ),
    ]


def _capital_whale_context(
    project_id: str,
    rows: tuple[dict[str, object], ...],
    *,
    as_of: datetime | None,
    config: OnChainConfig | None = None,
    repository: Any | None = None,
    trust: EvidenceTrust | None = None,
    usd_valuator: Callable[[RawOnChainObservation], UsdValuation] | None = None,
) -> EngineValidationSource | None:
    cutoff = as_of or datetime.now(UTC)
    verified = verified_capital_flow_rows(
        project_id,
        rows,
        config=config,
        repository=repository,
        trust=trust,
        as_of=cutoff,
    )
    accepted = {row.evidence_id: row.block_timestamp for row in verified}
    if not accepted:
        return None
    ids = tuple(sorted(accepted))
    valuations = tuple(usd_valuator(row) for row in verified) if usd_valuator is not None else ()
    usd_metrics = capital_flow_usd_metrics(valuations)
    missing_fields = ["wallet_attribution", "trade_direction"]
    if not usd_metrics:
        missing_fields.append("economic_value")
    warnings = ["unattributed_transfer_not_whale_signal"]
    if usd_metrics:
        warnings.append("usd_valuation_context_not_whale_signal")
    return EngineValidationSource(
        engine="whale_intelligence",
        score=0.0,
        confidence=0.0,
        timestamp=max(accepted.values()),
        freshness=0.0,
        source_record_ids=ids,
        evidence_ids=ids,
        repository_ids=ids,
        source="capital-flow-context",
        collector="onchain-repository",
        validation_status="PARTIAL",
        status="UNAVAILABLE",
        missing_fields=tuple(missing_fields),
        warnings=tuple(warnings),
        raw_input_metrics={"context_transfer_count": len(ids), **usd_metrics},
    )


def normalized_freshness(
    timestamp: datetime,
    cutoff: datetime,
    horizon: timedelta = timedelta(hours=24),
) -> float:
    horizon_seconds = horizon.total_seconds()
    if horizon_seconds <= 0:
        return 0.0
    age_seconds = (cutoff - timestamp).total_seconds()
    if age_seconds <= 0:
        return 1.0
    return max(0.0, 1.0 - age_seconds / horizon_seconds)


def _performance_metrics(performance: tuple[WalletPerformance, ...]) -> dict[str, Scalar]:
    fields: dict[str, Scalar] = {
        "coverage": None,
        "sample_size": 0,
        "win_rate": None,
        "cost_basis_usd": None,
        "realized_pnl_usd": None,
        "realized_return": None,
    }
    if not performance or any(item.status != "AVAILABLE" for item in performance):
        return fields
    with localcontext() as context:
        context.prec = 28
        sample_size = sum(item.sample_size for item in performance)
        total_cost = sum((item.cost_basis_usd for item in performance if item.cost_basis_usd is not None), Decimal(0))
        total_realized = sum(
            (item.realized_pnl_usd for item in performance if item.realized_pnl_usd is not None), Decimal(0)
        )
        weighted_win_rate = sum(
            (
                item.win_rate * item.sample_size
                for item in performance
                if item.win_rate is not None and item.sample_size
            ),
            Decimal(0),
        )
        coverages = tuple(item.coverage for item in performance if item.coverage is not None)
        fields.update(
            {
                "coverage": _scalar_decimal(min(coverages)) if len(coverages) == len(performance) else None,
                "sample_size": sample_size,
                "win_rate": _scalar_decimal(weighted_win_rate / sample_size) if sample_size else None,
                "cost_basis_usd": _scalar_decimal(total_cost),
                "realized_pnl_usd": _scalar_decimal(total_realized),
                "realized_return": _scalar_decimal(total_realized / total_cost) if total_cost else None,
                "target_asset_contracts": ",".join(
                    sorted(item.target_asset_contract for item in performance if item.target_asset_contract)
                ),
            }
        )
    return fields


def _swap_wallet_context(
    project_id: str,
    rows: Sequence[Mapping[str, object] | RawOnChainObservation],
    *,
    as_of: datetime | None,
    config: OnChainConfig | None = None,
    repository: Any | None = None,
    trust: EvidenceTrust | None = None,
    usd_valuator: Callable[[RawOnChainObservation], UsdValuation] | None = None,
) -> EngineValidationSource | None:
    cutoff = as_of or datetime.now(UTC)
    verified = verified_swap_rows(
        project_id,
        rows,
        config=config,
        repository=repository,
        trust=trust,
        as_of=cutoff,
    )
    if not verified:
        return None
    valuations = tuple(usd_valuator(row) for row in verified) if usd_valuator is not None else ()
    consideration_by_id = (
        {
            row.evidence_id: _consideration_valuation(row, usd_valuator, config or load_onchain_config())
            for row in verified
        }
        if usd_valuator is not None
        else {}
    )
    performance = wallet_performance_from_swaps(verified, consideration_by_id, as_of=cutoff)
    usd_metrics = capital_flow_usd_metrics(valuations)
    ids = tuple(sorted(row.evidence_id for row in verified))
    evidence_timestamps = tuple(row.block_timestamp for row in verified)
    available = bool(performance) and all(item.status == "AVAILABLE" for item in performance) and bool(usd_metrics)
    metrics: dict[str, Scalar] = {
        "verified_swap_count": len(ids),
        "fifo_policy": FIFO_POLICY,
        "net_pnl_usd": "UNAVAILABLE",
        **_performance_metrics(performance),
        **usd_metrics,
    }
    missing_fields = ("fees", "unrealized_pnl", "net_pnl_usd")
    if not usd_metrics:
        missing_fields = (*missing_fields, "economic_value")
    warnings = ("net_pnl_unavailable_fees_not_evidenced", "gross_realized_performance_only")
    return EngineValidationSource(
        engine="whale_intelligence",
        score=0.0,
        confidence=0.5 if available else 0.0,
        timestamp=max(evidence_timestamps),
        freshness=normalized_freshness(max(evidence_timestamps), cutoff),
        source_record_ids=ids,
        evidence_ids=ids,
        repository_ids=ids,
        source="swap-wallet-context",
        collector="onchain-repository",
        validation_status="PARTIAL",
        status="AVAILABLE" if available else "UNAVAILABLE",
        missing_fields=missing_fields,
        warnings=warnings,
        raw_input_metrics=metrics,
    )


def _consideration_valuation(
    row: RawOnChainObservation,
    usd_valuator: Callable[[RawOnChainObservation], UsdValuation],
    config: OnChainConfig,
) -> UsdValuation:
    unavailable = UsdValuation(
        status="UNAVAILABLE",
        reason="signed consideration USD valuation unavailable",
        chain_id=row.chain_id,
        target_at=row.block_timestamp,
    )
    if row.trade_direction == "buy":
        contract = row.token_in_contract
        amount = row.token_in_amount
        raw_amount = row.token_in_raw_amount
    elif row.trade_direction == "sell":
        contract = row.token_out_contract
        amount = row.token_out_amount
        raw_amount = row.token_out_raw_amount
    else:
        return unavailable
    if not contract or amount is None or raw_amount is None:
        return unavailable
    try:
        if not amount.is_finite() or amount <= 0:
            return unavailable
    except InvalidOperation:
        return unavailable
    asset = next(
        (
            item
            for item in config.tracked_assets
            if item.chain_id == row.chain_id
            and item.contract_address
            and item.contract_address.lower() == contract.lower()
            and item.coingecko_id
        ),
        None,
    )
    if asset is None or not asset.coingecko_id:
        return unavailable
    synthetic = replace(
        row,
        asset_contract=contract.lower(),
        asset_symbol=asset.symbol,
        decimals=asset.decimals,
        raw_amount=str(raw_amount),
        normalized_amount=amount,
    )
    return usd_valuator(synthetic)


def _scalar_decimal(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def capital_flow_usd_metrics(valuations: tuple[UsdValuation, ...]) -> dict[str, Scalar]:
    available = tuple(
        item
        for item in valuations
        if item.status == "AVAILABLE" and item.value_usd is not None and item.price_usd is not None
    )
    if not available:
        return {}
    with localcontext() as context:
        context.prec = max(
            28,
            *(len(item.value_usd.as_tuple().digits) for item in available),
            *(len(item.price_usd.as_tuple().digits) for item in available),
        )
        total = sum((item.value_usd for item in available), Decimal(0))
    effective_values = tuple(item.effective_at for item in available if item.effective_at is not None)
    retrieval_values = tuple(item.retrieval_at for item in available if item.retrieval_at is not None)
    source_ids = tuple(sorted({item.source_record_id for item in available if item.source_record_id}))
    return {
        "economic_value_usd": str(total),
        "usd_price": str(available[-1].price_usd),
        "usd_effective_at": max(effective_values).isoformat() if effective_values else "",
        "usd_retrieval_at": max(retrieval_values).isoformat() if retrieval_values else "",
        "usd_source_record_ids": ",".join(source_ids),
        "usd_freshness_seconds": max(
            (item.freshness_seconds or 0 for item in available),
            default=0,
        ),
        "usd_provenance": "coingecko-historical-market-chart",
    }


def _default_usd_valuator() -> Callable[[RawOnChainObservation], UsdValuation] | None:
    path = Path("data/historical_acquisition/raw.jsonl")
    if not path.exists():
        return None
    return HistoricalUsdValuator(load_onchain_config(), HistoricalEvidenceRepository(path.parent)).value


def _whale_source(engine: str, snapshot: WhaleSnapshot, timestamp: datetime) -> EngineValidationSource:
    source_record_ids = tuple(
        f"{item.metric.provider}:{item.metric.asset}:{item.metric.name}" for item in snapshot.evidence
    )
    evidence_ids = tuple(item.evidence_id for item in snapshot.evidence)
    repository_ids = tuple(item.repository_id for item in snapshot.evidence)
    return EngineValidationSource(
        engine=engine,
        score=_whale_score(engine, snapshot),
        confidence=snapshot.confidence,
        timestamp=timestamp,
        freshness=snapshot.freshness,
        source_record_ids=source_record_ids,
        evidence_ids=evidence_ids,
        source="whale-intelligence",
        collector="whale-repository",
        repository_ids=repository_ids,
        validation_status="VALID",
        status="AVAILABLE",
        raw_input_metrics=dict(snapshot.raw_metrics),
        normalized_inputs=dict(snapshot.normalized_metrics),
    )


def _whale_score(engine: str, snapshot: WhaleSnapshot) -> float:
    if engine == "risk":
        return _mean((snapshot.distribution_score, snapshot.exchange_pressure))
    if engine == "probability":
        return _mean((snapshot.whale_score, snapshot.accumulation_score, snapshot.institutional_score))
    if engine == "future_demand":
        return _mean((snapshot.accumulation_score, snapshot.market_participation, snapshot.institutional_score))
    if engine == "capital_rotation":
        return _mean((snapshot.stablecoin_pressure, snapshot.market_participation, snapshot.whale_score))
    if engine == "pattern_matching":
        return _mean((snapshot.whale_score, snapshot.evidence_quality, 1.0 - snapshot.distribution_score))
    if engine == "committee":
        return _mean((snapshot.whale_score, snapshot.confidence, snapshot.institutional_score))
    return snapshot.whale_score


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
    timestamp = newest
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
        timestamp = graph.generated_at
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
    if engine == "technology_necessity":
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
        timestamp = graph.generated_at
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
    if engine == "technology_necessity":
        return _mean((metrics.switching_cost, metrics.economic_moat))
    return _mean((metrics.economic_moat, metrics.value_capture, metrics.economic_resilience))


def _scenario_engine_sources(*, as_of: datetime | None) -> dict[str, tuple[EngineValidationSource, ...]]:
    repository = ScenarioRepository()
    scenario_times = {item.scenario_id: item.created_at for item in repository.scenarios()}
    impacts_by_project: dict[str, list[ScenarioImpact]] = defaultdict(list)
    for impact in repository.impacts():
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
        timestamps = tuple(
            scenario_times[item.scenario_id] for item in impact_tuple if item.scenario_id in scenario_times
        )
        if len(timestamps) != len(impact_tuple):
            continue
        timestamp = max(timestamps)
        confidence = _mean(tuple(impact.confidence for impact in impact_tuple))
        freshness = _mean(tuple(impact.freshness for impact in impact_tuple))
        rows[project_id] = tuple(
            _scenario_source(engine, impact_tuple, timestamp, confidence, freshness, evidence_ids, repository_ids)
            for engine in SCENARIO_ENGINES
            if engine != "opportunity_timing"
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
    return _mean(tuple(impact.infrastructure_resilience + impact.economic_resilience for impact in impacts)) / 2


def _timing_engine_sources(
    records: tuple[JsonlRecord, ...], *, as_of: datetime | None
) -> dict[str, tuple[EngineValidationSource, ...]]:
    """Select canonical Timing without repository latest/current fallbacks.

    The caller is the service-owned assembly boundary. This adapter is pure: it
    receives persisted record envelopes and never performs I/O or invents time.
    """

    if as_of is None:
        return {}
    cutoff = as_of.astimezone(UTC)
    rows: dict[str, tuple[EngineValidationSource, ...]] = {}
    eligible: dict[str, tuple[JsonlRecord, TimingAssessment]] = {}
    for record in records:
        if not _strict_known_timing_record(record, cutoff=cutoff):
            continue
        assessment = timing_assessment_from_payload(record.payload)
        current = eligible.get(assessment.project_id)
        selection_key = (record.effective_at, record.recorded_at, assessment.assessment_id)
        if current is None:
            eligible[assessment.project_id] = (record, assessment)
            continue
        current_record, current_assessment = current
        current_key = (
            current_record.effective_at,
            current_record.recorded_at,
            current_assessment.assessment_id,
        )
        if selection_key > current_key:
            eligible[assessment.project_id] = (record, assessment)
    for project_id, (record, assessment) in eligible.items():
        rows[project_id] = (_timing_source(assessment, record=record),)
    return rows


def _strict_known_timing_record(record: JsonlRecord, *, cutoff: datetime) -> bool:
    if not record.strict_known_eligible:
        return False
    if record.schema_version != TIMING_JSONL_SCHEMA:
        return False
    times = (record.effective_at, record.recorded_at, record.known_at)
    if any(value is None or value > cutoff for value in times):
        return False
    payload = record.payload
    if str(payload.get("lifecycle_status", "ACTIVE")).upper() != "ACTIVE":
        return False
    if payload.get("superseded_by_id") not in (None, ""):
        return False
    if str(payload.get("validation_status", "VALID")).upper() != "VALID":
        return False
    try:
        assessment = timing_assessment_from_payload(payload)
    except (KeyError, TypeError, ValueError):
        return False
    if assessment.generated_at > cutoff or assessment.generated_at != record.effective_at:
        return False
    if assessment.classification == "INSUFFICIENT_EVIDENCE":
        return False
    if assessment.freshness < 0.5 or assessment.stale_evidence:
        return False
    if not assessment.evidence_ids or not assessment.repository_ids:
        return False
    return "opportunity_timing" not in assessment.source_engines


def _timing_source(assessment: TimingAssessment, *, record: JsonlRecord | None = None) -> EngineValidationSource:
    time_diagnostics: dict[str, Scalar] = {}
    if record is not None:
        time_diagnostics = {
            "effective_at": record.effective_at.isoformat() if record.effective_at else None,
            "recorded_at": record.recorded_at.isoformat() if record.recorded_at else None,
            "known_at": record.known_at.isoformat() if record.known_at else None,
            "schema_version": record.schema_version,
        }
    return EngineValidationSource(
        engine="opportunity_timing",
        score=assessment.entry_score,
        confidence=assessment.timing_confidence,
        timestamp=assessment.generated_at,
        freshness=assessment.freshness,
        source_record_ids=(assessment.assessment_id,),
        evidence_ids=assessment.evidence_ids,
        source="opportunity-timing",
        collector="timing-repository",
        repository_ids=assessment.repository_ids,
        validation_status="VALID",
        status="AVAILABLE",
        raw_input_metrics={**dict(assessment.raw_inputs), **time_diagnostics},
        normalized_inputs=dict(assessment.normalized_factors),
        applied_weight=0.0,
        weighted_contribution=0.0,
    )


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
