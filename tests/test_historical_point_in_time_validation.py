from __future__ import annotations

from datetime import UTC, datetime

import pytest

from hunter.acquisition.models import EvidenceValidation, NormalizedEvidence
from hunter.acquisition.repositories import FileAcquisitionRepository
from hunter.historical.benchmarks import benchmark_outcomes, peer_relative_return
from hunter.historical.bias_controls import survivorship_scan, validate_bias_controls
from hunter.historical.configuration import HistoricalValidationConfig
from hunter.historical.cutoff import evidence_is_cutoff_eligible, reject_future_evidence
from hunter.historical.models import HistoricalValidationCase
from hunter.historical.outcomes import build_outcome, maximum_drawdown
from hunter.historical.performance import performance_metrics
from hunter.historical.replay import HistoricalPointInTimeValidationEngine
from hunter.historical.repository import HistoricalValidationRepository
from hunter.historical.snapshot_builder import HistoricalSnapshotBuilder
from hunter.historical.validation import calibration_metric
from hunter.historical_acquisition.pipeline import HistoricalAcquisitionPipeline
from hunter.historical_acquisition.providers import ReconstructedHistoricalEvidenceProvider
from hunter.historical_acquisition.repository import HistoricalEvidenceRepository
from hunter.macro.models import MacroEvidence, MacroMetric
from hunter.macro.repository import MacroRepository

NOW = datetime(2020, 1, 1, tzinfo=UTC)


def test_historical_cutoff_rejects_future_publication_and_ingestion(tmp_path) -> None:
    repository = FileAcquisitionRepository(tmp_path / "acquisition")
    repository.save_normalized(
        (
            _evidence("past", "ethereum", "2019-12-01T00:00:00+00:00", "2019-12-02T00:00:00+00:00"),
            _evidence("future", "ethereum", "2020-02-01T00:00:00+00:00", "2020-02-02T00:00:00+00:00"),
        )
    )
    repository.save_validations((_validation("past"), _validation("future")))

    snapshot = HistoricalSnapshotBuilder(repository).build(_case("ethereum-case", "ethereum", "EARLY_WINNER"))

    assert tuple(record.evidence_ids[0] for record in snapshot.evidence) == ("past", "past", "past", "past", "past")
    assert all(evidence_is_cutoff_eligible(record) for record in snapshot.evidence)
    assert reject_future_evidence(snapshot.evidence) == ()


def test_immutable_snapshots_and_correction_versioning(tmp_path) -> None:
    repository = FileAcquisitionRepository(tmp_path / "acquisition")
    repository.save_normalized(
        (_evidence("past", "ethereum", "2019-12-01T00:00:00+00:00", "2019-12-02T00:00:00+00:00"),)
    )
    repository.save_validations((_validation("past"),))
    historical = HistoricalValidationRepository(tmp_path / "historical")
    config = _config((_case("ethereum-case", "ethereum", "EARLY_WINNER"),))
    engine = HistoricalPointInTimeValidationEngine(
        config=config,
        snapshot_builder=HistoricalSnapshotBuilder(repository),
        repository=historical,
    )

    first = engine.run(as_of=NOW)
    with pytest.raises(ValueError, match="immutable historical snapshots"):
        engine.run(as_of=NOW)
    corrected = HistoricalSnapshotBuilder(repository).build(
        config.challenge_cases[0],
        version=2,
        previous_snapshot_id=first.snapshots[0].snapshot_id,
        correction_reason="test correction",
        changed_fields=("confidence",),
    )

    assert corrected.previous_snapshot_id == first.snapshots[0].snapshot_id
    assert corrected.version == 2
    assert corrected.correction_reason == "test correction"


def test_outcomes_benchmarks_drawdown_and_missing_windows_are_deterministic() -> None:
    case = _case("winner", "ethereum", "EARLY_WINNER")
    outcome = build_outcome(case, {7: (1.0, 2.0, 3.0), 30: ()})
    benchmarks = benchmark_outcomes(outcome, {7: 0.25}, benchmark_id="bitcoin")

    assert outcome.windows[0].simple_return == 2.0
    assert outcome.windows[1].simple_return is None
    assert maximum_drawdown((10.0, 8.0, 12.0, 6.0)) == -0.5
    assert benchmarks[0].excess_return == 1.75
    assert peer_relative_return(0.5, (0.1, 0.2, 0.4)) == 0.3


def test_survivorship_failed_delisted_migration_and_rename_controls(tmp_path) -> None:
    failed = _case("failed", "bitconnect", "DELISTED_PROJECT", lifecycle="failed", token_state="delisted")
    migrated = _case("migrated", "render", "MIGRATED_TOKEN", token_state="migrated")
    renamed = _case("renamed", "matic-network", "RENAMED_PROJECT", lifecycle="renamed")
    snapshot = HistoricalSnapshotBuilder(FileAcquisitionRepository(tmp_path / "acquisition")).build(failed)
    validation = validate_bias_controls(failed, snapshot, current_universe=("ethereum",))

    assert validation.survivorship_passed
    assert failed.token_lifecycle_state == "delisted"
    assert migrated.token_lifecycle_state == "migrated"
    assert renamed.project_lifecycle_state == "renamed"
    assert "missing_lifecycle_state:abandoned" in survivorship_scan((failed, migrated, renamed))


def test_historical_replay_committee_no_candidate_metrics_and_repository_boundaries(tmp_path) -> None:
    repository = FileAcquisitionRepository(tmp_path / "acquisition")
    repository.save_normalized(
        (_evidence("past", "ethereum", "2019-12-01T00:00:00+00:00", "2019-12-02T00:00:00+00:00"),)
    )
    repository.save_validations((_validation("past"),))
    config = _config((_case("ethereum-case", "ethereum", "EARLY_WINNER"),))

    run = HistoricalPointInTimeValidationEngine(
        config=config,
        snapshot_builder=HistoricalSnapshotBuilder(repository),
        repository=HistoricalValidationRepository(tmp_path / "historical"),
    ).run(as_of=NOW)
    calibration = calibration_metric(run.engine_outputs, run.outcomes, minimum_sample_size=30)

    assert run.committee_assessments[0].no_qualified_candidate
    assert run.challenge_results[0].was_hunter_correct == "INSUFFICIENT_OUTCOME_DATA"
    assert calibration.sample_size_status == "INSUFFICIENT_SAMPLE_SIZE"
    assert run.leakage_passed
    assert run.historical_coverage > 0
    assert run.decision_outcomes[0].decision_date == NOW
    assert run.explanations[0].historical_evidence_ids == ("past", "past", "past", "past", "past")
    assert run.performance_metrics is not None


def test_historical_replay_rejects_future_snapshot_evidence(tmp_path) -> None:
    repository = FileAcquisitionRepository(tmp_path / "acquisition")
    repository.save_normalized(
        (_evidence("future", "ethereum", "2020-02-01T00:00:00+00:00", "2019-12-02T00:00:00+00:00"),)
    )
    repository.save_validations((_validation("future"),))
    config = _config((_case("ethereum-case", "ethereum", "EARLY_WINNER"),))

    run = HistoricalPointInTimeValidationEngine(
        config=config,
        snapshot_builder=HistoricalSnapshotBuilder(repository),
        repository=HistoricalValidationRepository(tmp_path / "historical"),
    ).run(as_of=NOW)

    assert run.snapshots[0].evidence == ()


def test_performance_metrics_are_deterministic_and_complete() -> None:
    case = _case("winner", "ethereum", "EARLY_WINNER")
    challenge = _challenge_result(case)
    metrics = performance_metrics((challenge,))

    assert metrics.accuracy == 1.0
    assert metrics.precision == 1.0
    assert metrics.recall == 1.0
    assert metrics.f1 == 1.0
    assert metrics.win_rate == 1.0
    assert metrics.sample_count == 1


def test_reconstructed_historical_macro_evidence_expands_point_in_time_coverage(tmp_path) -> None:
    macro_repository = MacroRepository(tmp_path / "macro")
    macro_repository.save_evidence(
        (
            MacroEvidence(
                evidence_id="macro-2019",
                repository_id="macro-repo-2019",
                metric=MacroMetric(
                    name="liquidity",
                    provider="public-macro-fixture",
                    source_url="https://example.test/macro",
                    timestamp=datetime(2019, 12, 1, tzinfo=UTC),
                    value=0.8,
                    raw_payload={"liquidity": 0.8},
                ),
                normalized_value=0.8,
                validation_status="VALID",
            ),
        )
    )
    historical_repository = HistoricalEvidenceRepository(tmp_path / "historical-evidence")
    run = HistoricalAcquisitionPipeline(historical_repository).sync(
        ReconstructedHistoricalEvidenceProvider(macro_repository=macro_repository),
        (_case("ethereum-case", "ethereum", "EARLY_WINNER"),),
    )
    snapshot = HistoricalSnapshotBuilder(historical_repository=historical_repository).build(
        _case("ethereum-case", "ethereum", "EARLY_WINNER")
    )

    assert run.valid_count == 1
    assert any(record.engine == "macro_intelligence" for record in snapshot.evidence)
    assert historical_repository.snapshots(project_id="ethereum", engine="macro_intelligence")[0].status == "AVAILABLE"
    assert (
        historical_repository.snapshots(project_id="ethereum", engine="whale_intelligence")[0].status == "UNAVAILABLE"
    )
    assert all(record.publication_timestamp <= record.evaluation_cutoff_timestamp for record in snapshot.evidence)


def _case(
    case_id: str,
    project_id: str,
    case_type: str,
    *,
    lifecycle: str = "active",
    token_state: str = "active",
) -> HistoricalValidationCase:
    return HistoricalValidationCase(
        case_id=case_id,
        project_id=project_id,
        project_slug=project_id,
        project_name=project_id.title(),
        symbol=project_id[:4].upper(),
        sector="smart_contracts",
        case_type=case_type,  # type: ignore[arg-type]
        evaluation_timestamp=NOW,
        historical_cutoff_timestamp=NOW,
        project_lifecycle_state=lifecycle,
        token_lifecycle_state=token_state,
    )


def _config(cases: tuple[HistoricalValidationCase, ...]) -> HistoricalValidationConfig:
    return HistoricalValidationConfig(
        evaluation_windows=(7, 30, 90),
        minimum_sample_size=30,
        success_threshold=0.0,
        failure_threshold=-0.5,
        benchmarks=("bitcoin",),
        calibration_buckets=(0.0, 0.5, 1.0),
        acceptable_freshness=0.5,
        required_evidence=("valuation",),
        maximum_missing_evidence=21,
        leakage_rules=("publication_timestamp_lte_cutoff",),
        snapshot_versioning=True,
        challenge_cases=cases,
    )


def _evidence(evidence_id: str, project_id: str, publication: str, retrieved: str) -> NormalizedEvidence:
    return NormalizedEvidence(
        evidence_id=evidence_id,
        repository_id=f"repo:{evidence_id}",
        provider="coingecko",
        collector="fixture",
        raw_source_id=f"raw:{evidence_id}",
        domain="market",
        metric="coingecko_market_profile",
        target_id=project_id,
        value=project_id,
        raw_metrics={"publication_timestamp": publication, "event_timestamp": publication},
        normalized_metrics={"score": 0.8},
        source_url="https://example.test",
        retrieved_at=datetime.fromisoformat(retrieved),
        normalized_at=datetime.fromisoformat(retrieved),
        confidence=1.0,
        freshness=1.0,
        raw_evidence_id=f"raw-evidence:{evidence_id}",
    )


def _validation(evidence_id: str) -> EvidenceValidation:
    return EvidenceValidation(evidence_id=evidence_id, status="valid", validated_at=NOW, confidence=1.0, freshness=1.0)


def _challenge_result(case: HistoricalValidationCase):
    from hunter.historical.models import HistoricalChallengeResult

    return HistoricalChallengeResult(
        case_id=case.case_id,
        project_id=case.project_id,
        evaluation_timestamp=case.evaluation_timestamp,
        historical_cutoff_timestamp=case.historical_cutoff_timestamp,
        hunter_decision="QUALIFIED_CANDIDATE",
        historical_rank=1,
        committee_decision="QUALIFIED_CANDIDATE",
        probability=0.9,
        opportunity=0.8,
        risk=0.2,
        positive_drivers=("valuation",),
        negative_drivers=(),
        warning_signals=(),
        realized_outcome="MAJOR_WINNER",
        benchmark_outcome="bitcoin",
        excess_return=1.2,
        maximum_drawdown=-0.1,
        was_hunter_correct="YES",
        correctness_reason="matched outcome",
        leakage_validation="PASS",
        survivorship_validation="PASS",
    )
