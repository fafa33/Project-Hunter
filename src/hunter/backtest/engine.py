from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime

from hunter.acquisition.repositories import FileAcquisitionRepository
from hunter.backtest.models import BacktestRun, CalibrationReport, EngineBacktestMetric, ProjectBacktestMetric
from hunter.backtest.repository import BacktestRepository
from hunter.economic.repository import EconomicGraphRepository
from hunter.execution.identity import identity
from hunter.graph.repository import TechnologyGraphRepository
from hunter.market_validation import MarketValidationRunner, load_market_validation_config
from hunter.market_validation.acquisition_sources import acquisition_engine_sources
from hunter.market_validation.evidence import (
    REQUIRED_EVIDENCE_ENGINES,
    EvidenceCoverageAnalyzer,
    ensure_complete_engine_sources,
)
from hunter.market_validation.models import EngineValidationSource, MarketValidationRun
from hunter.market_validation.runner import EvidenceBackedProjectExecutor
from hunter.scenario import ScenarioRepository


class BacktestingCalibrationEngine:
    def __init__(
        self,
        *,
        acquisition_repository: FileAcquisitionRepository | None = None,
        backtest_repository: BacktestRepository | None = None,
        technology_repository: TechnologyGraphRepository | None = None,
        economic_repository: EconomicGraphRepository | None = None,
        scenario_repository: ScenarioRepository | None = None,
    ) -> None:
        self.acquisition_repository = acquisition_repository or FileAcquisitionRepository()
        self.backtest_repository = backtest_repository or BacktestRepository()
        self.technology_repository = technology_repository or TechnologyGraphRepository()
        self.economic_repository = economic_repository or EconomicGraphRepository()
        self.scenario_repository = scenario_repository or ScenarioRepository()

    def run(self, *, as_of: datetime | None = None) -> BacktestRun:
        timestamp = (as_of or datetime.now(tz=UTC)).astimezone(UTC)
        config = load_market_validation_config()
        sources = acquisition_engine_sources(self.acquisition_repository, as_of=config.effective_at)
        validation_run = MarketValidationRunner(
            config,
            executor=EvidenceBackedProjectExecutor(config.effective_at, sources),
        ).run()
        historical_runs = _historical_run_count(
            self.acquisition_repository,
            self.technology_repository,
            self.economic_repository,
            self.scenario_repository,
        )
        engine_metrics = _engine_metrics(validation_run)
        project_metrics = _project_metrics(validation_run)
        prior_backtests = tuple(run for run in self.backtest_repository.runs() if run.generated_at < timestamp)
        calibration = _calibration(timestamp, engine_metrics, prior_backtests)
        coverage = EvidenceCoverageAnalyzer().analyze(validation_run).stats.coverage_percent
        run = BacktestRun(
            run_id=identity(
                "backtest-run",
                {
                    "timestamp": timestamp,
                    "engines": tuple(item.engine for item in engine_metrics),
                    "projects": tuple(item.project_id for item in project_metrics),
                },
            ),
            generated_at=timestamp,
            historical_runs=historical_runs,
            projects_evaluated=len(project_metrics),
            engines_evaluated=len(engine_metrics),
            coverage=coverage,
            historical_consistency=_mean(tuple(item.historical_consistency for item in engine_metrics)),
            calibration_completeness=_calibration_completeness(calibration, engine_metrics),
            engine_metrics=engine_metrics,
            project_metrics=project_metrics,
            calibration=calibration,
        )
        return self.backtest_repository.save(run)


def compare_backtests(left: BacktestRun, right: BacktestRun) -> dict[str, float | str]:
    return {
        "left": left.run_id,
        "right": right.run_id,
        "coverage_delta": round(right.coverage - left.coverage, 4),
        "consistency_delta": round(right.historical_consistency - left.historical_consistency, 4),
        "calibration_delta": round(right.calibration_completeness - left.calibration_completeness, 4),
    }


def _engine_metrics(validation_run: MarketValidationRun) -> tuple[EngineBacktestMetric, ...]:
    sources_by_engine: dict[str, list[EngineValidationSource]] = defaultdict(list)
    for result in validation_run.project_results:
        for source in ensure_complete_engine_sources(result, timestamp=validation_run.effective_at):
            sources_by_engine[source.engine].append(source)
    rows = []
    for engine in REQUIRED_EVIDENCE_ENGINES:
        sources = tuple(sources_by_engine.get(engine, ()))
        available = tuple(source for source in sources if _available(source))
        positives = tuple(source for source in sources if source.score >= 0.5)
        valid = tuple(source for source in sources if source.validation_status == "VALID" and _available(source))
        true_positive = tuple(source for source in positives if source in valid)
        false_positive = tuple(source for source in positives if source not in valid)
        false_negative = tuple(source for source in valid if source not in positives)
        top_sources = tuple(sorted(sources, key=lambda item: (-item.score, -item.confidence, item.engine))[:10])
        top_valid = sum(1 for source in top_sources if source in valid)
        hit_rate = _percent(len(valid), len(sources))
        precision = _percent(len(true_positive), len(positives))
        recall = _percent(len(true_positive), len(valid))
        rows.append(
            EngineBacktestMetric(
                engine=engine,
                historical_coverage=_percent(len(available), len(sources)),
                hit_rate=hit_rate,
                precision=precision,
                recall=recall,
                false_positives=len(false_positive),
                false_negatives=len(false_negative),
                top_n_accuracy=_percent(top_valid, len(top_sources)),
                ranking_correlation=_rank_correlation(sources),
                decision_stability=_decision_stability(sources),
                confidence_calibration=_confidence_calibration(sources),
                evidence_completeness=_percent(len(available), len(sources)),
                historical_consistency=_mean(tuple(_source_consistency(source) for source in sources)),
                prediction_reliability=_mean((hit_rate, precision, recall, _confidence_calibration(sources))),
                scenario_reliability=_scenario_reliability(engine, sources),
                evidence_ids=tuple(sorted({evidence_id for source in sources for evidence_id in source.evidence_ids})),
                repository_ids=tuple(
                    sorted({repository_id for source in sources for repository_id in source.repository_ids})
                ),
            )
        )
    return tuple(rows)


def _project_metrics(validation_run: MarketValidationRun) -> tuple[ProjectBacktestMetric, ...]:
    rows = []
    for result in validation_run.project_results:
        sources = ensure_complete_engine_sources(result, timestamp=validation_run.effective_at)
        available = tuple(source for source in sources if _available(source))
        rows.append(
            ProjectBacktestMetric(
                project_id=result.project_id,
                historical_coverage=_percent(len(available), len(sources)),
                confidence=result.confidence,
                evidence_completeness=_percent(len(available), len(sources)),
                historical_consistency=_mean(tuple(_source_consistency(source) for source in sources)),
                engines_available=len(available),
                engines_missing=len(sources) - len(available),
                evidence_ids=tuple(sorted({evidence_id for source in sources for evidence_id in source.evidence_ids})),
                repository_ids=tuple(
                    sorted({repository_id for source in sources for repository_id in source.repository_ids})
                ),
            )
        )
    return tuple(sorted(rows, key=lambda item: item.project_id))


def _calibration(
    timestamp: datetime,
    engine_metrics: tuple[EngineBacktestMetric, ...],
    previous_runs: tuple[BacktestRun, ...],
) -> CalibrationReport:
    weak = tuple(
        sorted(
            item.engine
            for item in engine_metrics
            if item.historical_coverage < 50.0 or item.prediction_reliability < 50.0
        )
    )
    strong = tuple(
        sorted(
            item.engine
            for item in engine_metrics
            if item.historical_coverage >= 75.0 and item.prediction_reliability >= 75.0
        )
    )
    gaps = tuple(sorted(item.engine for item in engine_metrics if item.historical_coverage < 50.0))
    adjustments = {item.engine: round((item.prediction_reliability - 50.0) / 1000.0, 4) for item in engine_metrics}
    drift = 0.0
    if previous_runs:
        previous = previous_runs[-1]
        current_reliability = _mean(tuple(item.prediction_reliability for item in engine_metrics)) / 100.0
        drift = abs(current_reliability - previous.historical_consistency)
    return CalibrationReport(
        calibration_id=identity(
            "calibration-report",
            {
                "timestamp": timestamp,
                "engines": tuple((item.engine, item.prediction_reliability) for item in engine_metrics),
            },
        ),
        generated_at=timestamp,
        confidence_calibration=_mean(tuple(item.confidence_calibration for item in engine_metrics)),
        evidence_quality=_mean(tuple(item.evidence_completeness for item in engine_metrics)),
        coverage_gaps=gaps,
        weak_engines=weak,
        strong_engines=strong,
        historical_drift=round(drift, 4),
        recommended_weight_adjustments=adjustments,
    )


def _historical_run_count(
    acquisition_repository: FileAcquisitionRepository,
    technology_repository: TechnologyGraphRepository,
    economic_repository: EconomicGraphRepository,
    scenario_repository: ScenarioRepository,
) -> int:
    return (
        len(acquisition_repository.history())
        + len(technology_repository.runs())
        + len(economic_repository.runs())
        + len(scenario_repository.runs())
    )


def _available(source: EngineValidationSource) -> bool:
    return source.status == "AVAILABLE" and source.confidence > 0.0 and bool(source.evidence_ids)


def _source_consistency(source: EngineValidationSource) -> float:
    if not _available(source):
        return 0.0
    validation = 1.0 if source.validation_status == "VALID" else 0.0
    return _mean((source.confidence, source.freshness, validation))


def _confidence_calibration(sources: tuple[EngineValidationSource, ...]) -> float:
    if not sources:
        return 0.0
    errors = tuple(abs(source.confidence - (1.0 if _available(source) else 0.0)) for source in sources)
    return round(max(0.0, 1.0 - _mean(errors)) * 100.0, 2)


def _decision_stability(sources: tuple[EngineValidationSource, ...]) -> float:
    available = tuple(source for source in sources if _available(source))
    if not available:
        return 0.0
    scores = tuple(source.score for source in available)
    spread = max(scores) - min(scores) if scores else 0.0
    return round(max(0.0, 1.0 - spread) * 100.0, 2)


def _rank_correlation(sources: tuple[EngineValidationSource, ...]) -> float:
    available = tuple(source for source in sources if _available(source))
    if len(available) < 2:
        return 100.0 if available else 0.0
    score_order = {id(source): index for index, source in enumerate(sorted(available, key=lambda item: item.score))}
    confidence_order = {
        id(source): index for index, source in enumerate(sorted(available, key=lambda item: item.confidence))
    }
    n = len(available)
    distance = sum(abs(score_order[id(source)] - confidence_order[id(source)]) for source in available)
    max_distance = max(n * (n - 1), 1)
    return round(max(0.0, 1.0 - (distance / max_distance)) * 100.0, 2)


def _scenario_reliability(engine: str, sources: tuple[EngineValidationSource, ...]) -> float:
    scenario = tuple(source for source in sources if source.source == "scenario-simulation")
    if not scenario:
        return 0.0
    return _mean(tuple(_source_consistency(source) for source in scenario)) * 100.0


def _calibration_completeness(
    calibration: CalibrationReport,
    engine_metrics: tuple[EngineBacktestMetric, ...],
) -> float:
    covered = len(engine_metrics) - len(calibration.coverage_gaps)
    return _percent(covered, len(engine_metrics))


def _percent(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round((numerator / denominator) * 100.0, 2)


def _mean(values: tuple[float, ...]) -> float:
    if not values:
        return 0.0
    return round(sum(values) / len(values), 4)
