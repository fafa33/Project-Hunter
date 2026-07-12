from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from hunter.acquisition.models import EvidenceValidation, NormalizedEvidence
from hunter.acquisition.repositories import FileAcquisitionRepository
from hunter.cli import main
from hunter.historical.configuration import HistoricalValidationConfig
from hunter.historical.models import HistoricalValidationCase
from hunter.historical.replay import HistoricalPointInTimeValidationEngine
from hunter.historical.repository import HistoricalValidationRepository
from hunter.historical.snapshot_builder import HistoricalSnapshotBuilder
from hunter.historical_acquisition.models import HistoricalProviderMetadata, RawHistoricalEvidence
from hunter.historical_acquisition.pipeline import HistoricalAcquisitionPipeline
from hunter.historical_acquisition.providers import HistoricalRSSAnnouncementsProvider
from hunter.historical_acquisition.repository import HistoricalEvidenceRepository

NOW = datetime(2020, 1, 1, tzinfo=UTC)


class StaticHistoricalProvider:
    metadata = HistoricalProviderMetadata(
        name="fixture-historical",
        collector="fixture-history",
        supported_metrics=("historical_market",),
    )

    def __init__(self, rows: tuple[RawHistoricalEvidence, ...]) -> None:
        self.rows = rows

    def collect(self, cases: tuple[HistoricalValidationCase, ...]) -> tuple[RawHistoricalEvidence, ...]:
        case_ids = {case.case_id for case in cases}
        return tuple(row for row in self.rows if row.case_id in case_ids)


def test_historical_acquisition_normalizes_validates_and_persists(tmp_path) -> None:
    repository = HistoricalEvidenceRepository(tmp_path / "historical-acquisition")
    run = HistoricalAcquisitionPipeline(repository).sync(StaticHistoricalProvider((_raw("ethereum-case"),)), (_case(),))

    assert run.raw_count == 1
    assert run.normalized_count == 4
    assert run.valid_count == 4
    assert len(repository.raw()) == 1
    assert len(repository.normalized()) == 4
    assert {item.status for item in repository.validations()} == {"valid"}


def test_future_timestamps_and_invalid_chronology_are_rejected(tmp_path) -> None:
    future = _raw("ethereum-case", event=datetime(2100, 1, 1, tzinfo=UTC), publication=datetime(2100, 1, 1, tzinfo=UTC))
    invalid_chronology = _raw("ethereum-case", event=NOW, publication=NOW - timedelta(days=1), suffix="chronology")
    repository = HistoricalEvidenceRepository(tmp_path / "historical-acquisition")

    run = HistoricalAcquisitionPipeline(repository).sync(
        StaticHistoricalProvider((future, invalid_chronology)),
        (_case(),),
    )

    assert run.valid_count == 0
    assert run.invalid_count == 8
    assert {item.status for item in repository.validations()} == {"future", "invalid"}


def test_duplicate_detection_is_deterministic(tmp_path) -> None:
    repository = HistoricalEvidenceRepository(tmp_path / "historical-acquisition")
    pipeline = HistoricalAcquisitionPipeline(repository)
    provider = StaticHistoricalProvider((_raw("ethereum-case"), _raw("ethereum-case")))

    first = pipeline.sync(provider, (_case(),))
    second = pipeline.sync(provider, (_case(),))

    assert first.duplicate_count == 4
    assert second.normalized_count == 0
    assert second.duplicate_count == 8
    assert len(repository.normalized()) == 4


def test_snapshot_builder_consumes_historical_repository_with_cutoff_correctness(tmp_path) -> None:
    historical_repository = HistoricalEvidenceRepository(tmp_path / "historical-acquisition")
    HistoricalAcquisitionPipeline(historical_repository).sync(
        StaticHistoricalProvider((_raw("ethereum-case"),)), (_case(),)
    )

    snapshot = HistoricalSnapshotBuilder(
        historical_repository=historical_repository,
    ).build(_case())

    assert {record.engine for record in snapshot.evidence} == {
        "valuation",
        "comparative_valuation",
        "mispricing",
        "asymmetry",
    }
    assert all(record.data_availability_timestamp <= NOW for record in snapshot.evidence)


def test_replay_appends_corrected_snapshot_without_overwriting_existing_snapshot(tmp_path) -> None:
    case = _case()
    config = _config((case,))
    validation_repository = HistoricalValidationRepository(tmp_path / "historical-validation")
    acquisition_repository = HistoricalEvidenceRepository(tmp_path / "historical-acquisition")
    builder = HistoricalSnapshotBuilder(historical_repository=acquisition_repository)
    first = HistoricalPointInTimeValidationEngine(
        config=config,
        snapshot_builder=builder,
        repository=validation_repository,
    ).run(as_of=NOW)

    HistoricalAcquisitionPipeline(acquisition_repository).sync(
        StaticHistoricalProvider((_raw("ethereum-case"),)), (case,)
    )
    corrected = HistoricalPointInTimeValidationEngine(
        config=config,
        snapshot_builder=builder,
        repository=validation_repository,
        allow_snapshot_corrections=True,
    ).run(as_of=NOW)

    assert corrected.snapshots[0].version == 2
    assert corrected.snapshots[0].previous_snapshot_id == first.snapshots[0].snapshot_id
    assert corrected.historical_coverage > first.historical_coverage


def test_snapshot_replay_still_rejects_duplicate_without_correction_mode(tmp_path) -> None:
    config = _config((_case(),))
    repository = HistoricalValidationRepository(tmp_path / "historical-validation")
    engine = HistoricalPointInTimeValidationEngine(config=config, repository=repository)

    engine.run(as_of=NOW)

    with pytest.raises(ValueError, match="immutable historical snapshots"):
        engine.run(as_of=NOW)


def test_historical_rss_provider_uses_only_persisted_timestamped_narrative(tmp_path) -> None:
    repository = FileAcquisitionRepository(tmp_path / "acquisition")
    repository.save_normalized(
        (_narrative("valid", "ethereum", NOW), _narrative("future", "ethereum", NOW + timedelta(days=1)))
    )
    repository.save_validations((_narrative_validation("valid"), _narrative_validation("future")))

    rows = HistoricalRSSAnnouncementsProvider(repository).collect((_case(),))

    assert len(rows) == 1
    assert rows[0].metric == "historical_narrative"
    assert rows[0].event_timestamp == NOW


def test_historical_build_cli_is_idempotent_and_does_not_crash_on_rerun() -> None:
    assert main(["historical", "build"]) == 0
    assert main(["historical", "build"]) == 0
    assert main(["historical", "replay"]) == 0


def _case() -> HistoricalValidationCase:
    return HistoricalValidationCase(
        case_id="ethereum-case",
        project_id="ethereum",
        project_slug="ethereum",
        project_name="Ethereum",
        symbol="ETH",
        sector="smart_contracts",
        case_type="EARLY_WINNER",
        evaluation_timestamp=NOW,
        historical_cutoff_timestamp=NOW,
        project_lifecycle_state="active",
        token_lifecycle_state="active",
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


def _raw(
    case_id: str,
    *,
    event: datetime = NOW,
    publication: datetime = NOW,
    availability: datetime = NOW,
    suffix: str = "market",
) -> RawHistoricalEvidence:
    return RawHistoricalEvidence(
        provider="fixture-historical",
        collector="fixture-history",
        raw_source_id=f"raw:{case_id}:{suffix}",
        case_id=case_id,
        project_id="ethereum",
        metric="historical_market",
        event_timestamp=event,
        publication_timestamp=publication,
        data_availability_timestamp=availability,
        retrieval_timestamp=datetime(2026, 1, 1, tzinfo=UTC),
        payload={"price": 100.0, "market_cap": 1_000_000.0, "volume": 50_000.0},
        source_url="https://example.test/history",
        repository_id=f"fixture:{case_id}:{suffix}",
    )


def _narrative(evidence_id: str, project_id: str, timestamp: datetime) -> NormalizedEvidence:
    return NormalizedEvidence(
        evidence_id=evidence_id,
        repository_id=f"narrative:{evidence_id}",
        provider="narrative",
        collector="rss",
        raw_source_id=f"raw:{evidence_id}",
        domain="narrative",
        metric="narrative_item",
        target_id=project_id,
        value="https://example.test/post",
        raw_metrics={
            "timestamp": timestamp.isoformat(),
            "title": "roadmap update",
            "description": "release notes",
            "topics": ("roadmap",),
            "entities": ("ethereum",),
        },
        normalized_metrics={"schema_completeness": 1.0},
        source_url="https://example.test/post",
        retrieved_at=timestamp,
        normalized_at=timestamp,
        confidence=1.0,
        freshness=1.0,
        raw_evidence_id=f"raw:{evidence_id}",
    )


def _narrative_validation(evidence_id: str) -> EvidenceValidation:
    return EvidenceValidation(
        evidence_id=evidence_id,
        status="valid",
        validated_at=NOW,
        confidence=1.0,
        freshness=1.0,
    )
