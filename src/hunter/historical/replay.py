from __future__ import annotations

from datetime import UTC, datetime, timedelta

from hunter.execution.identity import identity
from hunter.historical.benchmarks import benchmark_outcomes
from hunter.historical.bias_controls import validate_bias_controls
from hunter.historical.configuration import HistoricalValidationConfig, load_historical_validation_config
from hunter.historical.models import (
    HistoricalBacktestRun,
    HistoricalBenchmarkOutcome,
    HistoricalBiasValidation,
    HistoricalChallengeResult,
    HistoricalCommitteeAssessment,
    HistoricalEngineOutput,
    HistoricalEvidenceSnapshot,
    HistoricalOutcome,
    HistoricalRankingSnapshot,
    HistoricalValidationCase,
)
from hunter.historical.outcomes import build_outcome
from hunter.historical.repository import HistoricalValidationRepository
from hunter.historical.snapshot_builder import HistoricalSnapshotBuilder
from hunter.historical.validation import calibration_metric, engine_metrics
from hunter.historical_acquisition.repository import HistoricalEvidenceRepository
from hunter.market_validation.configuration import load_market_validation_config
from hunter.market_validation.models import EngineValidationSource, ProjectValidationTarget
from hunter.market_validation.runner import SourceBackedV1ProjectExecutor


class HistoricalPointInTimeValidationEngine:
    def __init__(
        self,
        *,
        config: HistoricalValidationConfig | None = None,
        snapshot_builder: HistoricalSnapshotBuilder | None = None,
        repository: HistoricalValidationRepository | None = None,
        allow_snapshot_corrections: bool = False,
        append_snapshots: bool = True,
    ) -> None:
        self.config = config or load_historical_validation_config()
        self.snapshot_builder = snapshot_builder or HistoricalSnapshotBuilder()
        self.repository = repository or HistoricalValidationRepository()
        self.allow_snapshot_corrections = allow_snapshot_corrections
        self.append_snapshots = append_snapshots

    def run(self, *, case_id: str | None = None, as_of: datetime | None = None) -> HistoricalBacktestRun:
        timestamp = (as_of or datetime.now(tz=UTC)).astimezone(UTC)
        cases = tuple(case for case in self.config.challenge_cases if case_id in {None, case.case_id, case.project_id})
        current_universe = tuple(project.project_id for project in load_market_validation_config().project_universe)
        snapshots = tuple(self._snapshot(case) for case in cases)
        engine_outputs: list[HistoricalEngineOutput] = []
        committee_assessments: list[HistoricalCommitteeAssessment] = []
        rankings: list[HistoricalRankingSnapshot] = []
        outcomes: list[HistoricalOutcome] = []
        benchmarks: list[HistoricalBenchmarkOutcome] = []
        bias: list[HistoricalBiasValidation] = []
        challenges: list[HistoricalChallengeResult] = []
        run_id = identity(
            "historical-validation-run", {"cases": tuple(case.case_id for case in cases), "timestamp": timestamp}
        )
        for case, snapshot in zip(cases, snapshots, strict=True):
            outputs, committee, ranking = replay_case(case, snapshot, run_id=run_id)
            outcome = build_outcome(case, self._outcome_observations(case))
            benchmark = benchmark_outcomes(
                outcome,
                self._benchmark_returns(self.config.benchmarks[0], case) if self.config.benchmarks else {},
                benchmark_id=self.config.benchmarks[0] if self.config.benchmarks else "none",
            )
            validation = validate_bias_controls(case, snapshot, current_universe=current_universe)
            engine_outputs.extend(outputs)
            committee_assessments.append(committee)
            rankings.append(ranking)
            outcomes.append(outcome)
            benchmarks.extend(benchmark)
            bias.append(validation)
            challenges.append(_challenge(case, outputs, committee, ranking, outcome, benchmark, validation))
        calibration = calibration_metric(
            tuple(engine_outputs), tuple(outcomes), minimum_sample_size=self.config.minimum_sample_size
        )
        metrics = engine_metrics(tuple(engine_outputs), tuple(outcomes))
        historical_coverage = _coverage(snapshots)
        run = HistoricalBacktestRun(
            run_id=run_id,
            generated_at=timestamp,
            cases=cases,
            snapshots=snapshots,
            engine_outputs=tuple(engine_outputs),
            committee_assessments=tuple(committee_assessments),
            ranking_snapshots=tuple(rankings),
            outcomes=tuple(outcomes),
            benchmark_outcomes=tuple(benchmarks),
            calibration_metrics=(calibration,),
            engine_metrics=metrics,
            challenge_results=tuple(challenges),
            bias_validations=tuple(bias),
            historical_coverage=historical_coverage,
            leakage_passed=all(item.leakage_passed for item in bias),
            survivorship_passed=all(item.survivorship_passed for item in bias),
            sample_size_status=calibration.sample_size_status,
        )
        return self.repository.save(run, append_snapshots=self.append_snapshots)

    def _snapshot(self, case: HistoricalValidationCase) -> HistoricalEvidenceSnapshot:
        if not self.allow_snapshot_corrections:
            return self.snapshot_builder.build(case)
        previous = tuple(snapshot for snapshot in self.repository.snapshots() if snapshot.case_id == case.case_id)
        if not previous:
            return self.snapshot_builder.build(case)
        latest = sorted(previous, key=lambda item: item.version)[-1]
        return self.snapshot_builder.build(
            case,
            version=latest.version + 1,
            previous_snapshot_id=latest.snapshot_id,
            correction_reason="historical acquisition append",
            changed_fields=("evidence",),
        )

    def _outcome_observations(self, case: HistoricalValidationCase) -> dict[int, tuple[float, ...]]:
        return {
            days: _prices_for_window(
                self.snapshot_builder.historical_repository,
                project_id=case.project_id,
                start=case.evaluation_timestamp,
                end=case.evaluation_timestamp + _days(days),
            )
            for days in self.config.evaluation_windows
        }

    def _benchmark_returns(self, benchmark_id: str, case: HistoricalValidationCase) -> dict[int, float]:
        rows = {}
        for days in self.config.evaluation_windows:
            prices = _prices_for_window(
                self.snapshot_builder.historical_repository,
                project_id=benchmark_id,
                start=case.evaluation_timestamp,
                end=case.evaluation_timestamp + _days(days),
            )
            if len(prices) >= 2 and prices[0]:
                rows[days] = round((prices[-1] / prices[0]) - 1.0, 6)
        return rows


def replay_case(
    case: HistoricalValidationCase,
    snapshot: HistoricalEvidenceSnapshot,
    *,
    run_id: str,
) -> tuple[tuple[HistoricalEngineOutput, ...], HistoricalCommitteeAssessment, HistoricalRankingSnapshot]:
    sources = tuple(_source(record) for record in snapshot.evidence)
    result = SourceBackedV1ProjectExecutor(
        case.evaluation_timestamp,
        {case.project_id: sources},
    ).execute_project(
        ProjectValidationTarget(case.project_id, case.project_name, case.sector),
        run_id=run_id,
    )
    outputs = tuple(
        HistoricalEngineOutput(
            case_id=case.case_id,
            engine=source.engine,
            score=source.score,
            confidence=source.confidence,
            evidence_ids=source.evidence_ids,
            repository_ids=source.repository_ids,
            status=source.status,
        )
        for source in result.engine_sources
    )
    committee = HistoricalCommitteeAssessment(
        case_id=case.case_id,
        committee_decision=result.committee_decision,
        committee_confidence=result.committee_confidence,
        champion_project_id=result.project_id if result.committee_decision == "QUALIFIED_CANDIDATE" else None,
        no_qualified_candidate=result.committee_decision != "QUALIFIED_CANDIDATE",
    )
    ranking = HistoricalRankingSnapshot(
        case_id=case.case_id,
        project_id=case.project_id,
        historical_rank=result.rank,
        sector_rank=result.sector_rank,
        hunter_score=result.hunter_score,
    )
    return outputs, committee, ranking


def _source(record) -> EngineValidationSource:
    score = _score(record.normalized_metrics, record.confidence)
    return EngineValidationSource(
        engine=record.engine,
        score=score,
        confidence=record.confidence,
        timestamp=record.publication_timestamp,
        freshness=record.freshness,
        source_record_ids=record.source_record_ids,
        evidence_ids=record.evidence_ids,
        source=record.source_provider,
        collector="historical-snapshot",
        repository_ids=record.repository_ids,
        validation_status=record.validation_status,
        status="AVAILABLE",
        raw_input_metrics={
            key: value
            for key, value in record.raw_metrics.items()
            if isinstance(value, str | int | float | bool) or value is None
        },
        normalized_inputs=record.normalized_metrics,
    )


def _score(metrics: dict[str, float], fallback: float) -> float:
    if not metrics:
        return fallback
    return round(sum(metrics.values()) / len(metrics), 4)


def _challenge(
    case: HistoricalValidationCase,
    outputs: tuple[HistoricalEngineOutput, ...],
    committee: HistoricalCommitteeAssessment,
    ranking: HistoricalRankingSnapshot,
    outcome: HistoricalOutcome,
    benchmarks: tuple[HistoricalBenchmarkOutcome, ...],
    bias: HistoricalBiasValidation,
) -> HistoricalChallengeResult:
    output_by_engine = {output.engine: output for output in outputs}
    benchmark = next((item for item in benchmarks if item.excess_return is not None), None)
    max_drawdown = next(
        (window.maximum_drawdown for window in outcome.windows if window.maximum_drawdown is not None), None
    )
    enough = outcome.final_success_label != "INSUFFICIENT_OUTCOME_DATA"
    return HistoricalChallengeResult(
        case_id=case.case_id,
        project_id=case.project_id,
        evaluation_timestamp=case.evaluation_timestamp,
        historical_cutoff_timestamp=case.historical_cutoff_timestamp,
        hunter_decision=committee.committee_decision,
        historical_rank=ranking.historical_rank,
        committee_decision=committee.committee_decision,
        probability=output_by_engine.get(
            "probability", HistoricalEngineOutput(case.case_id, "probability", 0, 0, (), (), "UNAVAILABLE")
        ).score,
        opportunity=output_by_engine.get(
            "opportunity_timing",
            HistoricalEngineOutput(case.case_id, "opportunity_timing", 0, 0, (), (), "UNAVAILABLE"),
        ).score,
        risk=output_by_engine.get(
            "risk", HistoricalEngineOutput(case.case_id, "risk", 0, 0, (), (), "UNAVAILABLE")
        ).score,
        positive_drivers=tuple(output.engine for output in outputs if output.score >= 0.5),
        negative_drivers=tuple(output.engine for output in outputs if output.score < 0.5),
        warning_signals=tuple(output.engine for output in outputs if output.status == "UNAVAILABLE"),
        realized_outcome=outcome.final_success_label,
        benchmark_outcome=benchmark.benchmark_id if benchmark else "INSUFFICIENT_OUTCOME_DATA",
        excess_return=benchmark.excess_return if benchmark else None,
        maximum_drawdown=max_drawdown,
        was_hunter_correct="INSUFFICIENT_OUTCOME_DATA" if not enough else "UNDETERMINED",
        correctness_reason=(
            "insufficient realized point-in-time outcome data" if not enough else "deterministic comparison available"
        ),
        leakage_validation="PASS" if bias.leakage_passed else "FAIL",
        survivorship_validation="PASS" if bias.survivorship_passed else "FAIL",
    )


def _coverage(snapshots: tuple[HistoricalEvidenceSnapshot, ...]) -> float:
    if not snapshots:
        return 0.0
    available = sum(len(snapshot.evidence) for snapshot in snapshots)
    total = sum(len(snapshot.evidence) + len(snapshot.missing_evidence) for snapshot in snapshots)
    return round((available / max(total, 1)) * 100.0, 2)


def _prices_for_window(
    repository: HistoricalEvidenceRepository,
    *,
    project_id: str,
    start: datetime,
    end: datetime,
) -> tuple[float, ...]:
    valid_ids = {item.evidence_id for item in repository.validations() if item.status == "valid"}
    rows = []
    for evidence in repository.normalized():
        if evidence.evidence_id not in valid_ids:
            continue
        if evidence.project_id != project_id or evidence.metric != "historical_market":
            continue
        if evidence.event_timestamp > end:
            continue
        price = evidence.raw_metrics.get("price")
        if isinstance(price, int | float) and price > 0:
            rows.append((evidence.event_timestamp, float(price)))
    ordered = sorted(rows)
    start_point = next(((timestamp, price) for timestamp, price in reversed(ordered) if timestamp <= start), None)
    end_point = next(((timestamp, price) for timestamp, price in reversed(ordered) if timestamp <= end), None)
    if start_point is None or end_point is None or end_point[0] <= start_point[0]:
        return ()
    middle = tuple((timestamp, price) for timestamp, price in ordered if start < timestamp < end_point[0])
    return tuple(price for _, price in (start_point, *middle, end_point))


def _days(days: int) -> timedelta:
    return timedelta(days=days)
