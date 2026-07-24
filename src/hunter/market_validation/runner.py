from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from hunter.execution import FixedClock
from hunter.execution.identity import identity
from hunter.market_validation.configuration import MarketValidationConfig
from hunter.market_validation.contracts import ProjectValidationExecutor
from hunter.market_validation.models import (
    EngineValidationSource,
    MarketValidationComparison,
    MarketValidationRun,
    ProjectValidationDelta,
    ProjectValidationResult,
    ProjectValidationTarget,
)
from hunter.pipeline import PipelineOrchestrator
from hunter.plugins.contracts import PipelineContext
from hunter.weights import WeightEngine


class MarketValidationRunner:
    def __init__(
        self,
        config: MarketValidationConfig,
        executor: ProjectValidationExecutor | None = None,
    ) -> None:
        self.config = config
        self.executor = executor or DeterministicProjectExecutor(config.effective_at)

    def run(self) -> MarketValidationRun:
        raw = tuple(
            self.executor.execute_project(project, run_id=self.config.run_id)
            for project in self.config.project_universe
        )
        ranked = _rank(raw)
        champion = ranked[0] if ranked and ranked[0].committee_decision == "QUALIFIED_CANDIDATE" else None
        runner_up = ranked[1] if champion is not None and len(ranked) > 1 else None
        scoring_versions = tuple(sorted({result.scoring_version for result in ranked if result.scoring_version}))
        return MarketValidationRun(
            run_id=self.config.run_id,
            effective_at=self.config.effective_at,
            project_results=ranked,
            champion_project_id=champion.project_id if champion else None,
            runner_up_project_id=runner_up.project_id if runner_up else None,
            no_qualified_candidate=champion is None,
            created_at=self.config.effective_at,
            metadata={
                "project_count": len(ranked),
                "schema": "market-validation-v1",
                "scoring_version": scoring_versions[0] if len(scoring_versions) == 1 else ",".join(scoring_versions),
            },
        )


class DeterministicProjectExecutor:
    def __init__(self, effective_at: datetime) -> None:
        self.effective_at = effective_at.astimezone(UTC)

    def execute_project(self, target: ProjectValidationTarget, *, run_id: str) -> ProjectValidationResult:
        return EvidenceBackedProjectExecutor(self.effective_at, {}).execute_project(target, run_id=run_id)


class EvidenceBackedProjectExecutor:
    def __init__(
        self,
        effective_at: datetime,
        sources_by_project: dict[str, tuple[EngineValidationSource, ...]],
    ) -> None:
        self.effective_at = effective_at.astimezone(UTC)
        self.sources_by_project = {
            str(project_id): tuple(
                sorted(
                    _enforce_canonical_input_authority(sources, effective_at=self.effective_at),
                    key=lambda item: item.engine,
                )
            )
            for project_id, sources in sources_by_project.items()
        }

    def execute_project(self, target: ProjectValidationTarget, *, run_id: str) -> ProjectValidationResult:
        context = PipelineContext(
            clock=FixedClock(self.effective_at),
            values={"project_id": target.project_id, "sector": target.sector},
        )
        PipelineOrchestrator().run(context=context)
        sources = _collapse_sources_by_engine(self.sources_by_project.get(target.project_id, ()))
        present_available = tuple(source for source in sources if _source_available(source, as_of=self.effective_at))
        source_by_engine = {source.engine: source for source in present_available}
        missing_required = tuple(engine for engine in REQUIRED_ENGINES if engine not in source_by_engine)
        invalid = tuple(source.engine for source in sources if not _source_available(source, as_of=self.effective_at))
        unweighted_sources = tuple(
            sorted(
                (
                    *sources,
                    *(
                        _unavailable_source(engine, self.effective_at)
                        for engine in missing_required
                        if engine not in {source.engine for source in sources}
                    ),
                ),
                key=lambda item: item.engine,
            )
        )
        all_sources = WeightEngine().apply(unweighted_sources)
        available_sources = tuple(
            source for source in all_sources if _source_available(source, as_of=self.effective_at)
        )
        source_by_engine = {source.engine: source for source in available_sources}
        missing = tuple(sorted((*missing_required, *(field for source in sources for field in source.missing_fields))))
        stale = tuple(sorted(source.engine for source in sources if source.freshness < 0.5))
        warnings = tuple(
            sorted(
                (
                    *(f"missing:{item}" for item in missing),
                    *(warning for source in sources for warning in source.warnings),
                )
            )
        )
        confidence_values = tuple(source.confidence for source in available_sources)
        freshness_values = tuple(source.freshness for source in available_sources)
        confidence = _clamp(sum(confidence_values) / len(confidence_values)) if confidence_values else 0.0
        freshness = _clamp(sum(freshness_values) / len(freshness_values)) if freshness_values else 0.0
        weighted_score = WeightEngine().score(all_sources)
        hunter = weighted_score.hunter_score
        final_score = weighted_score.final_score
        validation_health = _clamp(len(source_by_engine) / len(REQUIRED_ENGINES)) if REQUIRED_ENGINES else 0.0
        decision = "INSUFFICIENT_EVIDENCE" if missing_required or invalid else "WATCH_CLOSELY"
        if decision != "INSUFFICIENT_EVIDENCE" and hunter >= 0.62 and confidence >= 0.58:
            decision = "QUALIFIED_CANDIDATE"
        elif decision != "INSUFFICIENT_EVIDENCE" and hunter < 0.48:
            decision = "WAIT"
        return ProjectValidationResult(
            result_id=identity(
                "market-validation-project-result",
                {
                    "run_id": run_id,
                    "project_id": target.project_id,
                    "effective_at": self.effective_at,
                    "schema": "market-validation-v1",
                },
            ),
            run_id=run_id,
            project_id=target.project_id,
            project_name=target.name,
            sector=target.sector,
            rank=0,
            sector_rank=0,
            hunter_score=hunter,
            final_score=final_score,
            risk=_score(source_by_engine, "risk"),
            confidence=confidence,
            valuation=_score(source_by_engine, "valuation"),
            comparative_valuation=_score(source_by_engine, "comparative_valuation"),
            mispricing=_score(source_by_engine, "mispricing"),
            asymmetry=_score(source_by_engine, "asymmetry"),
            whale_intelligence=_score(source_by_engine, "whale_intelligence"),
            macro_intelligence=_score(source_by_engine, "macro_intelligence"),
            future_demand=_score(source_by_engine, "future_demand"),
            opportunity_timing=_score(source_by_engine, "opportunity_timing"),
            probability=_score(source_by_engine, "probability"),
            pattern_matching=_score(source_by_engine, "pattern_matching"),
            technology_necessity=_score(source_by_engine, "technology_necessity"),
            capital_rotation=_score(source_by_engine, "capital_rotation"),
            necessity_gap=_score(source_by_engine, "necessity_gap"),
            committee_decision=decision,
            committee_confidence=confidence,
            missing_evidence=missing,
            stale_evidence=stale,
            data_freshness=freshness,
            validation_health=validation_health,
            strongest_positive_drivers=tuple(
                source.engine
                for source in sorted(sources, key=lambda item: (-item.weighted_contribution, item.engine))[:3]
            ),
            strongest_negative_drivers=tuple(sorted(("risk", *stale, *missing))),
            reasons_for_ranking=(
                ("persisted upstream V1 validation output",) if sources else ("insufficient upstream evidence",)
            ),
            validation_warnings=warnings,
            engine_sources=all_sources,
            scoring_version=weighted_score.scoring_version,
        )


def compare_runs(left: MarketValidationRun, right: MarketValidationRun) -> MarketValidationComparison:
    left_by_project = {item.project_id: item for item in left.project_results}
    right_by_project = {item.project_id: item for item in right.project_results}
    deltas = []
    for project_id in sorted(left_by_project.keys() & right_by_project.keys()):
        left_item = left_by_project[project_id]
        right_item = right_by_project[project_id]
        deltas.append(
            ProjectValidationDelta(
                project_id=project_id,
                rank_change=right_item.rank - left_item.rank,
                score_change=round(right_item.hunter_score - left_item.hunter_score, 4),
                confidence_change=round(right_item.confidence - left_item.confidence, 4),
                committee_change=(
                    "unchanged"
                    if right_item.committee_decision == left_item.committee_decision
                    else f"{left_item.committee_decision}->{right_item.committee_decision}"
                ),
                evidence_change=len(right_item.missing_evidence) - len(left_item.missing_evidence),
            )
        )
    champion_change = (
        "unchanged"
        if left.champion_project_id == right.champion_project_id
        else f"{left.champion_project_id or 'none'}->{right.champion_project_id or 'none'}"
    )
    return MarketValidationComparison(
        left_run_id=left.run_id,
        right_run_id=right.run_id,
        champion_change=champion_change,
        project_deltas=tuple(deltas),
    )


def _rank(results: tuple[ProjectValidationResult, ...]) -> tuple[ProjectValidationResult, ...]:
    ranked = sorted(
        results, key=lambda item: (-item.final_score, -item.hunter_score, -item.committee_confidence, item.project_id)
    )
    sector_counts: dict[str, int] = {}
    updated = []
    for index, item in enumerate(ranked, start=1):
        sector_counts[item.sector] = sector_counts.get(item.sector, 0) + 1
        updated.append(replace(item, rank=index, sector_rank=sector_counts[item.sector]))
    return tuple(updated)


def _clamp(value: float) -> float:
    return round(max(0.0, min(1.0, float(value))), 4)


REQUIRED_ENGINES: tuple[str, ...] = (
    "valuation",
    "comparative_valuation",
    "mispricing",
    "asymmetry",
    "whale_intelligence",
    "macro_intelligence",
    "future_demand",
    "opportunity_timing",
    "probability",
    "pattern_matching",
    "technology_necessity",
    "capital_rotation",
    "necessity_gap",
    "risk",
    "committee",
)


def _score(sources: dict[str, EngineValidationSource], engine: str) -> float:
    source = sources.get(engine)
    if source is None or not _source_available(source):
        return 0.0
    return source.score


def _source_available(source: EngineValidationSource, *, as_of: datetime | None = None) -> bool:
    return (
        source.status == "AVAILABLE"
        and source.confidence > 0.0
        and not source.missing_fields
        and (as_of is None or source.timestamp <= as_of)
    )


def _unavailable_source(engine: str, timestamp: datetime) -> EngineValidationSource:
    reason = _missing_reason(engine)
    return EngineValidationSource(
        engine=engine,
        score=0.0,
        confidence=0.0,
        timestamp=timestamp,
        freshness=0.0,
        source_record_ids=(),
        evidence_ids=(),
        source="persisted-upstream",
        collector="repository",
        validation_status="MISSING",
        status="UNAVAILABLE",
        raw_input_metrics={},
        normalized_inputs={},
        applied_weight=0.0,
        weighted_contribution=0.0,
        missing_fields=(engine,),
        warnings=(reason,),
    )


DEFERRED_CANONICAL_INPUTS: tuple[str, ...] = (
    "valuation",
    "comparative_valuation",
    "mispricing",
    "asymmetry",
    "necessity_gap",
)


def _enforce_canonical_input_authority(
    sources: tuple[EngineValidationSource, ...], *, effective_at: datetime
) -> tuple[EngineValidationSource, ...]:
    accepted = []
    rejected = set()
    for source in sources:
        if source.engine in DEFERRED_CANONICAL_INPUTS and _known_invalid_deferred_alias(source):
            rejected.add(source.engine)
            continue
        if source.engine == "opportunity_timing" and not _canonical_timing_source(source):
            rejected.add(source.engine)
            continue
        accepted.append(source)
    accepted.extend(_unavailable_source(engine, effective_at) for engine in sorted(rejected))
    return tuple(accepted)


def _canonical_timing_source(source: EngineValidationSource) -> bool:
    return (
        source.source == "opportunity-timing"
        and source.collector == "timing-repository"
        and source.validation_status == "VALID"
        and source.status == "AVAILABLE"
        and bool(source.source_record_ids)
        and bool(source.evidence_ids)
        and bool(source.repository_ids)
    )


def _known_invalid_deferred_alias(source: EngineValidationSource) -> bool:
    if source.engine in {"valuation", "comparative_valuation", "mispricing", "asymmetry"}:
        return True
    if source.engine == "necessity_gap":
        return source.source in {"technology-graph", "scenario-simulation"}
    return False


def _missing_reason(engine: str) -> str:
    if engine in DEFERRED_CANONICAL_INPUTS:
        return f"contract_unavailable:{engine}"
    if engine == "opportunity_timing":
        return "strict_known_canonical_timing_unavailable"
    return f"missing:{engine}"


def _collapse_sources_by_engine(sources: tuple[EngineValidationSource, ...]) -> tuple[EngineValidationSource, ...]:
    grouped: dict[str, list[EngineValidationSource]] = {}
    for source in sources:
        grouped.setdefault(source.engine, []).append(source)
    collapsed = []
    for engine, items in grouped.items():
        if len(items) == 1:
            collapsed.append(items[0])
            continue
        available = tuple(item for item in items if _source_available(item))
        candidates = available or tuple(items)
        newest = max(item.timestamp for item in candidates)
        collapsed.append(
            EngineValidationSource(
                engine=engine,
                score=_mean(tuple(item.score for item in candidates)),
                confidence=_mean(tuple(item.confidence for item in candidates)),
                timestamp=newest,
                freshness=_mean(tuple(item.freshness for item in candidates)),
                source_record_ids=tuple(
                    sorted({source_id for item in candidates for source_id in item.source_record_ids})
                ),
                evidence_ids=tuple(sorted({evidence_id for item in candidates for evidence_id in item.evidence_ids})),
                source="+".join(sorted({item.source for item in candidates})),
                collector="weighted-source-aggregator",
                repository_ids=tuple(
                    sorted({repository_id for item in candidates for repository_id in item.repository_ids})
                ),
                validation_status=(
                    "VALID" if all(item.validation_status == "VALID" for item in candidates) else "PARTIAL"
                ),
                status="AVAILABLE" if available else "UNAVAILABLE",
                raw_input_metrics={
                    f"{index}.{key}": value
                    for index, item in enumerate(candidates)
                    for key, value in item.raw_input_metrics.items()
                },
                normalized_inputs={
                    f"{index}.{key}": value
                    for index, item in enumerate(candidates)
                    for key, value in item.normalized_inputs.items()
                },
                missing_fields=tuple(sorted({field for item in items for field in item.missing_fields})),
                warnings=tuple(sorted({warning for item in items for warning in item.warnings})),
            )
        )
    return tuple(sorted(collapsed, key=lambda item: item.engine))


def _mean(values: tuple[float, ...]) -> float:
    if not values:
        return 0.0
    return _clamp(sum(values) / len(values))


# Backward-compatible aliases for pre-consolidation imports.
DeterministicV1ProjectExecutor = DeterministicProjectExecutor
SourceBackedV1ProjectExecutor = EvidenceBackedProjectExecutor
