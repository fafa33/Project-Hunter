from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

from hunter.acquisition import FileAcquisitionRepository
from hunter.backtest import BacktestRepository
from hunter.economic.repository import EconomicGraphRepository
from hunter.execution.identity import identity
from hunter.graph.repository import TechnologyGraphRepository
from hunter.historical import HistoricalValidationRepository
from hunter.jsonl_contract import JsonlWritePlan
from hunter.macro import MacroRepository
from hunter.market_validation import MarketValidationRunner, load_market_validation_config
from hunter.market_validation.acquisition_sources import acquisition_engine_sources
from hunter.market_validation.models import EngineValidationSource, ProjectValidationResult
from hunter.market_validation.runner import EvidenceBackedProjectExecutor
from hunter.scenario import ScenarioRepository
from hunter.timing.models import TimingAssessment, TimingDependencySnapshot
from hunter.timing.repository import TIMING_JSONL_SCHEMA, TimingRepository
from hunter.whale import WhaleRepository

REQUIRED_TIMING_ENGINES: tuple[str, ...] = (
    "valuation",
    "comparative_valuation",
    "mispricing",
    "asymmetry",
    "risk",
    "developer",
    "protocol",
    "narrative",
    "macro_intelligence",
    "whale_intelligence",
    "future_demand",
    "technology_necessity",
    "capital_rotation",
    "necessity_gap",
    "probability",
    "pattern_matching",
    "committee",
)


class OpportunityTimingEvidenceEngine:
    def __init__(self, repository: TimingRepository | None = None) -> None:
        self.repository = repository or TimingRepository()

    def sync(self, *, as_of: datetime | None = None) -> tuple[TimingAssessment, ...]:
        timestamp = (as_of or datetime.now(tz=UTC)).astimezone(UTC)
        market_config = load_market_validation_config()
        acquisition_repository = FileAcquisitionRepository()
        sources = acquisition_engine_sources(acquisition_repository, as_of=timestamp)
        dependencies = current_timing_dependencies(generation_timestamp=timestamp)
        runner = MarketValidationRunner(
            market_config,
            executor=EvidenceBackedProjectExecutor(timestamp, sources),
        )
        assessments = tuple(self.assess_project(result, as_of=timestamp) for result in runner.run().project_results)
        self.repository.save(
            assessments,
            dependencies=dependencies,
            write_plan=JsonlWritePlan(
                TIMING_JSONL_SCHEMA,
                timestamp,
                None,
                "timing dependencies do not yet expose a complete known-time boundary",
                timestamp,
            ),
        )
        return assessments

    def assess_project(self, result: ProjectValidationResult, *, as_of: datetime | None = None) -> TimingAssessment:
        timestamp = as_of or (result.engine_sources[0].timestamp if result.engine_sources else datetime.now(tz=UTC))
        timestamp = timestamp.astimezone(UTC)
        source_by_engine = _latest_sources(result.engine_sources, as_of=timestamp)
        missing = tuple(engine for engine in REQUIRED_TIMING_ENGINES if engine not in source_by_engine)
        stale = tuple(engine for engine, source in source_by_engine.items() if source.freshness < 0.5)
        raw_inputs = {engine: source.score for engine, source in source_by_engine.items()}
        evidence_ids = tuple(sorted({eid for source in source_by_engine.values() for eid in source.evidence_ids}))
        repository_ids = tuple(sorted({rid for source in source_by_engine.values() for rid in source.repository_ids}))
        source_engines = tuple(source_by_engine)
        if missing:
            return TimingAssessment(
                assessment_id=_assessment_id(result.project_id, timestamp, evidence_ids, missing),
                project_id=result.project_id,
                generated_at=timestamp,
                entry_score=0.0,
                exit_score=0.0,
                accumulation_score=0.0,
                distribution_score=0.0,
                risk_reward_score=0.0,
                cycle_position="unknown",
                market_regime="unknown",
                timing_confidence=0.0,
                evidence_quality=_quality(source_by_engine),
                freshness=_freshness(source_by_engine),
                classification="INSUFFICIENT_EVIDENCE",
                source_engines=source_engines,
                evidence_ids=evidence_ids,
                repository_ids=repository_ids,
                reasoning_chain=(f"missing_required:{','.join(missing)}",),
                missing_evidence=missing,
                stale_evidence=stale,
                raw_inputs=raw_inputs,
                normalized_factors={},
            )
        factors = _timing_factors(source_by_engine)
        historical_confidence = _historical_confidence(result.project_id, timestamp)
        confidence = _clamp(
            _mean(tuple(source.confidence for source in source_by_engine.values())) * factors["evidence_quality"]
        )
        confidence = _clamp(confidence * _freshness(source_by_engine) * historical_confidence)
        classification = _classification(factors["entry_score"], factors["exit_score"], confidence)
        return TimingAssessment(
            assessment_id=_assessment_id(result.project_id, timestamp, evidence_ids, missing),
            project_id=result.project_id,
            generated_at=timestamp,
            entry_score=factors["entry_score"],
            exit_score=factors["exit_score"],
            accumulation_score=factors["accumulation_score"],
            distribution_score=factors["distribution_score"],
            risk_reward_score=factors["risk_reward_score"],
            cycle_position=_cycle_position(source_by_engine),
            market_regime=_market_regime(source_by_engine),
            timing_confidence=confidence,
            evidence_quality=factors["evidence_quality"],
            freshness=_freshness(source_by_engine),
            classification=classification,
            source_engines=source_engines,
            evidence_ids=evidence_ids,
            repository_ids=repository_ids,
            reasoning_chain=_reasoning_chain(factors, classification, historical_confidence),
            missing_evidence=missing,
            stale_evidence=stale,
            raw_inputs=raw_inputs,
            normalized_factors=factors,
        )


def _latest_sources(
    sources: tuple[EngineValidationSource, ...], *, as_of: datetime
) -> dict[str, EngineValidationSource]:
    rows: dict[str, EngineValidationSource] = {}
    for source in sources:
        if (
            source.engine == "opportunity_timing"
            or source.timestamp > as_of
            or source.status != "AVAILABLE"
            or source.confidence <= 0.0
        ):
            continue
        current = rows.get(source.engine)
        if current is None or source.timestamp > current.timestamp:
            rows[source.engine] = source
    return rows


def current_timing_dependencies(*, generation_timestamp: datetime | None = None) -> TimingDependencySnapshot:
    timestamp = (generation_timestamp or datetime.now(tz=UTC)).astimezone(UTC)
    acquisition_repository = FileAcquisitionRepository()
    protocol_items = _latest_valid_acquisition_items(
        acquisition_repository, provider="defillama", metric="defillama_protocol_profile"
    )
    narrative_items = _latest_valid_acquisition_items(
        acquisition_repository, provider="narrative", metric="narrative_item"
    )
    developer_items = _latest_valid_acquisition_items(
        acquisition_repository, provider="github", metric="github_repository_profile"
    )
    graph_items, graph_timestamp = _graph_dependency_items()
    macro_items, macro_timestamp = _macro_dependency_items()
    whale_items, whale_timestamp = _whale_dependency_items()
    dependencies = {
        "protocol": protocol_items,
        "narrative": narrative_items,
        "developer": developer_items,
        "graph": graph_items,
        "macro": macro_items,
        "whale": whale_items,
    }
    dependency_timestamps = {
        key: value
        for key, value in {
            "protocol": _latest_item_timestamp(protocol_items),
            "narrative": _latest_item_timestamp(narrative_items),
            "developer": _latest_item_timestamp(developer_items),
            "graph": graph_timestamp,
            "macro": macro_timestamp,
            "whale": whale_timestamp,
        }.items()
        if value is not None
    }
    dependency_fingerprints = {key: _fingerprint(items) for key, items in dependencies.items() if items}
    return TimingDependencySnapshot(
        generation_timestamp=timestamp,
        dependency_timestamps=dependency_timestamps,
        dependency_fingerprints=dependency_fingerprints,
        protocol_evidence_timestamp=dependency_timestamps.get("protocol"),
        narrative_evidence_timestamp=dependency_timestamps.get("narrative"),
        developer_evidence_timestamp=dependency_timestamps.get("developer"),
        graph_timestamp=dependency_timestamps.get("graph"),
        macro_timestamp=dependency_timestamps.get("macro"),
        whale_timestamp=dependency_timestamps.get("whale"),
    )


def _latest_valid_acquisition_items(
    repository: FileAcquisitionRepository, *, provider: str, metric: str
) -> tuple[dict[str, str], ...]:
    rows: dict[str, dict[str, str]] = {}
    for evidence in repository.normalized.values():
        validation = repository.validations.get(evidence.evidence_id)
        if validation is None or validation.status != "valid":
            continue
        if evidence.provider != provider or evidence.metric != metric:
            continue
        current = rows.get(evidence.target_id)
        if current is None or evidence.retrieved_at.isoformat() > current["timestamp"]:
            rows[evidence.target_id] = {
                "project_id": evidence.target_id,
                "evidence_id": evidence.evidence_id,
                "repository_id": evidence.repository_id,
                "timestamp": evidence.retrieved_at.isoformat(),
            }
    return tuple(rows[key] for key in sorted(rows))


def _graph_dependency_items() -> tuple[tuple[dict[str, str], ...], datetime | None]:
    items = []
    timestamps = []
    technology_graph = TechnologyGraphRepository().graph()
    for edge in technology_graph.edges:
        timestamps.append(edge.discovery_timestamp)
        for evidence_id in edge.evidence_ids:
            items.append(
                {
                    "dependency": "technology_graph",
                    "project_id": f"{edge.source_project}->{edge.target_project}",
                    "evidence_id": evidence_id,
                    "timestamp": edge.discovery_timestamp.isoformat(),
                }
            )
    economic_graph = EconomicGraphRepository().graph()
    for edge in economic_graph.edges:
        timestamps.append(edge.discovery_timestamp)
        for evidence_id in edge.evidence_ids:
            items.append(
                {
                    "dependency": "economic_graph",
                    "project_id": f"{edge.source_project}->{edge.target_project}",
                    "evidence_id": evidence_id,
                    "timestamp": edge.discovery_timestamp.isoformat(),
                }
            )
    for impact in ScenarioRepository().impacts():
        if impact.validation_status != "VALID":
            continue
        for evidence_id in impact.evidence_ids:
            items.append(
                {
                    "dependency": "scenario",
                    "project_id": impact.project_id,
                    "evidence_id": evidence_id,
                    "timestamp": "scenario-persisted",
                }
            )
    return tuple(sorted(items, key=lambda item: tuple(item.items()))), max(timestamps, default=None)


def _macro_dependency_items() -> tuple[tuple[dict[str, str], ...], datetime | None]:
    snapshot = MacroRepository().latest_snapshot()
    if snapshot is None or not snapshot.evidence:
        return (), None
    return (
        tuple(
            {
                "metric": item.metric.name,
                "provider": item.metric.provider,
                "evidence_id": item.evidence_id,
                "repository_id": item.repository_id,
                "timestamp": snapshot.generated_at.isoformat(),
            }
            for item in snapshot.evidence
        ),
        snapshot.generated_at,
    )


def _whale_dependency_items() -> tuple[tuple[dict[str, str], ...], datetime | None]:
    snapshot = WhaleRepository().latest_snapshot()
    if snapshot is None or not snapshot.evidence:
        return (), None
    return (
        tuple(
            {
                "metric": item.metric.name,
                "provider": item.metric.provider,
                "asset": item.metric.asset,
                "evidence_id": item.evidence_id,
                "repository_id": item.repository_id,
                "timestamp": snapshot.generated_at.isoformat(),
            }
            for item in snapshot.evidence
        ),
        snapshot.generated_at,
    )


def _latest_item_timestamp(items: tuple[dict[str, str], ...]) -> datetime | None:
    timestamps = []
    for item in items:
        value = item.get("timestamp")
        if not value or value == "scenario-persisted":
            continue
        timestamps.append(_parse_timestamp(value))
    return max(timestamps, default=None)


def _fingerprint(items: tuple[dict[str, str], ...]) -> str:
    payload = json.dumps(items, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _timing_factors(sources: dict[str, EngineValidationSource]) -> dict[str, float]:
    score = {engine: source.score for engine, source in sources.items()}
    entry = _mean(
        (
            score["valuation"],
            score["comparative_valuation"],
            score["mispricing"],
            score["asymmetry"],
            score["probability"],
            score["macro_intelligence"],
            score["whale_intelligence"],
            score["future_demand"],
            score["technology_necessity"],
            score["capital_rotation"],
            score["necessity_gap"],
        )
    )
    exit_score = _mean((score["risk"], 1.0 - score["probability"], 1.0 - score["macro_intelligence"]))
    accumulation = _mean((score["mispricing"], score["asymmetry"], score["whale_intelligence"], score["probability"]))
    distribution = _mean((score["risk"], 1.0 - score["capital_rotation"], 1.0 - score["whale_intelligence"]))
    risk_reward = _mean((entry, 1.0 - score["risk"], score["probability"]))
    return {
        "entry_score": entry,
        "exit_score": exit_score,
        "accumulation_score": accumulation,
        "distribution_score": distribution,
        "risk_reward_score": risk_reward,
        "evidence_quality": _quality(sources),
    }


def _classification(entry: float, exit_score: float, confidence: float) -> str:
    if confidence <= 0.0:
        return "INSUFFICIENT_EVIDENCE"
    if exit_score >= 0.75:
        return "STRONG_REDUCE"
    if exit_score >= 0.6:
        return "REDUCE"
    if entry >= 0.75:
        return "STRONG_ACCUMULATION"
    if entry >= 0.58:
        return "ACCUMULATION"
    return "WAIT"


def _cycle_position(sources: dict[str, EngineValidationSource]) -> str:
    macro = sources["macro_intelligence"].score
    capital = sources["capital_rotation"].score
    risk = sources["risk"].score
    if risk >= 0.65:
        return "late_or_stressed"
    if macro >= 0.65 and capital >= 0.6:
        return "expansion"
    if macro >= 0.5:
        return "recovery"
    return "contraction"


def _market_regime(sources: dict[str, EngineValidationSource]) -> str:
    macro = sources["macro_intelligence"].score
    risk = sources["risk"].score
    whale = sources["whale_intelligence"].score
    if risk >= 0.65:
        return "risk_off"
    if macro >= 0.6 and whale >= 0.5:
        return "risk_on"
    return "neutral"


def _historical_confidence(project_id: str, as_of: datetime) -> float:
    values = []
    backtests = tuple(run for run in BacktestRepository().runs() if run.generated_at <= as_of)
    if backtests:
        latest = backtests[-1]
        for item in latest.project_metrics:
            if item.project_id == project_id:
                values.append(_mean((item.confidence, item.evidence_completeness, item.historical_consistency)))
                break
        values.append(_mean((latest.coverage, latest.historical_consistency, latest.calibration_completeness)))
    historical_runs = tuple(
        row for row in HistoricalValidationRepository().runs() if _parse_timestamp(str(row["generated_at"])) <= as_of
    )
    if historical_runs:
        values.append(float(historical_runs[-1].get("historical_coverage", 0.0)))
    return _mean(tuple(values)) if values else 0.9


def _reasoning_chain(factors: dict[str, float], classification: str, historical_confidence: float) -> tuple[str, ...]:
    return (
        f"classification:{classification}",
        f"entry_score:{factors['entry_score']:.4f}",
        f"exit_score:{factors['exit_score']:.4f}",
        f"risk_reward:{factors['risk_reward_score']:.4f}",
        f"historical_confidence:{historical_confidence:.4f}",
    )


def _assessment_id(
    project_id: str, timestamp: datetime, evidence_ids: tuple[str, ...], missing: tuple[str, ...]
) -> str:
    return identity(
        "opportunity-timing-evidence",
        {
            "project_id": project_id,
            "generated_at": timestamp,
            "evidence_ids": tuple(sorted(evidence_ids)),
            "missing": tuple(sorted(missing)),
        },
    )


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _quality(sources: dict[str, EngineValidationSource]) -> float:
    if not REQUIRED_TIMING_ENGINES:
        return 0.0
    coverage = len(sources) / len(REQUIRED_TIMING_ENGINES)
    evidence_coverage = _mean(tuple(source.evidence_coverage or 1.0 for source in sources.values()))
    return _clamp(coverage * evidence_coverage)


def _freshness(sources: dict[str, EngineValidationSource]) -> float:
    return _mean(tuple(source.freshness for source in sources.values()))


def _mean(values: tuple[float, ...]) -> float:
    if not values:
        return 0.0
    return _clamp(sum(values) / len(values))


def _clamp(value: float) -> float:
    return round(max(0.0, min(1.0, float(value))), 4)
