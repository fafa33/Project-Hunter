from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from hunter import cli as cli_module
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
from hunter.historical_acquisition.providers import HistoricalRSSAnnouncementsProvider, _closest_market_point
from hunter.historical_acquisition.repository import HistoricalEvidenceRepository
from hunter.historical_acquisition.usd_valuation import HistoricalUsdValuator
from hunter.onchain.configuration import load_onchain_config
from hunter.onchain.models import RawOnChainObservation

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
    assert run.normalized_count == 5
    assert run.valid_count == 5
    assert len(repository.raw()) == 1
    assert len(repository.normalized()) == 5
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
    assert run.invalid_count == 10
    assert {item.status for item in repository.validations()} == {"future", "invalid"}


def test_duplicate_detection_is_deterministic(tmp_path) -> None:
    repository = HistoricalEvidenceRepository(tmp_path / "historical-acquisition")
    pipeline = HistoricalAcquisitionPipeline(repository)
    provider = StaticHistoricalProvider((_raw("ethereum-case"), _raw("ethereum-case")))

    first = pipeline.sync(provider, (_case(),))
    second = pipeline.sync(provider, (_case(),))

    assert first.duplicate_count == 5
    assert second.normalized_count == 0
    assert second.duplicate_count == 10
    assert len(repository.normalized()) == 5


def test_snapshot_builder_consumes_historical_repository_with_cutoff_correctness(tmp_path) -> None:
    historical_repository = HistoricalEvidenceRepository(tmp_path / "historical-acquisition")
    HistoricalAcquisitionPipeline(historical_repository).sync(
        StaticHistoricalProvider((_raw("ethereum-case"),)), (_case(),)
    )

    snapshot = HistoricalSnapshotBuilder(
        historical_repository=historical_repository,
    ).build(_case())

    assert {record.engine for record in snapshot.evidence} == {
        "market",
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


def test_historical_build_cli_is_idempotent_and_does_not_crash_on_rerun(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = HistoricalValidationRepository(tmp_path / "historical-validation")
    monkeypatch.setattr(cli_module, "HistoricalValidationRepository", lambda: repository)
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


def test_closest_market_point_never_selects_a_future_point() -> None:
    earlier = datetime(2026, 11, 25, 9, 0, tzinfo=UTC)
    later = datetime(2026, 11, 25, 10, 0, tzinfo=UTC)
    points = {earlier: {"price": 0.9995440023519918}, later: {"price": 0.9995318372782404}}

    selected = _closest_market_point(points, datetime(2026, 11, 25, 9, 41, tzinfo=UTC))
    assert selected is not None
    selected_at, values = selected

    assert selected_at == earlier
    assert values["price"] == 0.9995440023519918


def test_historical_usd_valuation_is_exact_and_evidence_bound(tmp_path) -> None:
    repository = HistoricalEvidenceRepository(tmp_path / "historical-acquisition")
    repository.save_raw((_usdc_price_evidence(),))

    result = HistoricalUsdValuator(load_onchain_config(), repository, max_price_age=timedelta(hours=1)).value(
        _usdc_observation()
    )

    assert result.status == "AVAILABLE"
    assert result.reason is None
    assert result.chain_id == 1
    assert result.asset_contract == "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
    assert result.coingecko_id == "usd-coin"
    assert result.price_usd == Decimal("0.9995440023519918")
    assert result.amount == Decimal("17000")
    assert result.value_usd == Decimal("16992.2480399838606")
    assert result.effective_at == datetime(2026, 11, 25, 9, 0, tzinfo=UTC)
    assert result.target_at == datetime(2026, 11, 25, 9, 41, 35, tzinfo=UTC)
    assert result.freshness_seconds == 2495
    assert result.source_record_id == "historical:usdc:09-00"
    assert result.source_url == "https://api.coingecko.com/api/v3/coins/usd-coin/market_chart/range"


def test_historical_usd_valuation_fails_closed_for_wrong_chain_stale_and_future_points(tmp_path) -> None:
    repository = HistoricalEvidenceRepository(tmp_path / "historical-acquisition")
    repository.save_raw((_usdc_price_evidence(),))
    valuator = HistoricalUsdValuator(load_onchain_config(), repository, max_price_age=timedelta(seconds=60))

    wrong_chain = valuator.value(_usdc_observation(chain_id=42161))
    assert wrong_chain.status == "UNAVAILABLE"
    assert "exact" in (wrong_chain.reason or "")

    stale = valuator.value(_usdc_observation())
    assert stale.status == "UNAVAILABLE"
    assert "freshness" in (stale.reason or "")

    future_repository = HistoricalEvidenceRepository(tmp_path / "future-acquisition")
    future = _usdc_price_evidence()
    future_repository.save_raw(
        (
            RawHistoricalEvidence(
                **{
                    **future.__dict__,
                    "event_timestamp": datetime(2026, 11, 25, 10, 0, tzinfo=UTC),
                    "publication_timestamp": datetime(2026, 11, 25, 10, 0, tzinfo=UTC),
                    "data_availability_timestamp": datetime(2026, 11, 25, 10, 0, tzinfo=UTC),
                    "raw_source_id": "historical:usdc:10-00",
                    "repository_id": "historical:usdc:10-00",
                }
            ),
        )
    )
    causal = HistoricalUsdValuator(load_onchain_config(), future_repository).value(_usdc_observation())
    assert causal.status == "UNAVAILABLE"
    assert "causal" in (causal.reason or "")


def test_conflicting_price_points_only_block_the_selected_causal_point(tmp_path) -> None:
    source = _usdc_price_evidence()
    future = datetime(2026, 11, 25, 10, 0, tzinfo=UTC)
    future_conflicts = (
        replace(
            source,
            raw_source_id="historical:usdc:10-00-a",
            repository_id="historical:usdc:10-00-a",
            data_availability_timestamp=future,
            payload={"coingecko_id": "usd-coin", "price": 0.1},
        ),
        replace(
            source,
            raw_source_id="historical:usdc:10-00-b",
            repository_id="historical:usdc:10-00-b",
            data_availability_timestamp=future,
            payload={"coingecko_id": "usd-coin", "price": 0.2},
        ),
    )
    repository = HistoricalEvidenceRepository(tmp_path / "historical-acquisition")
    repository.save_raw((source, *future_conflicts))

    result = HistoricalUsdValuator(load_onchain_config(), repository, max_price_age=timedelta(hours=1)).value(
        _usdc_observation()
    )

    assert result.status == "AVAILABLE"
    assert result.price_usd == Decimal("0.9995440023519918")
    assert result.source_record_id == "historical:usdc:09-00"

    selected_conflict = replace(
        source,
        raw_source_id="historical:usdc:09-00-b",
        repository_id="historical:usdc:09-00-b",
        payload={"coingecko_id": "usd-coin", "price": 0.3},
    )
    selected_repository = HistoricalEvidenceRepository(tmp_path / "selected-conflict")
    selected_repository.save_raw((source, selected_conflict))

    blocked = HistoricalUsdValuator(load_onchain_config(), selected_repository).value(_usdc_observation())

    assert blocked.status == "UNAVAILABLE"
    assert "conflicting historical USD price points" in (blocked.reason or "")


def _usdc_price_evidence() -> RawHistoricalEvidence:
    effective = datetime(2026, 11, 25, 9, 0, tzinfo=UTC)
    return RawHistoricalEvidence(
        provider="coingecko-historical",
        collector="coingecko-history",
        raw_source_id="historical:usdc:09-00",
        case_id="usdc-point",
        project_id="compound",
        metric="historical_market",
        event_timestamp=effective,
        publication_timestamp=effective,
        data_availability_timestamp=effective,
        retrieval_timestamp=datetime(2026, 11, 25, 10, 0, tzinfo=UTC),
        payload={"coingecko_id": "usd-coin", "price": 0.9995440023519918},
        source_url="https://api.coingecko.com/api/v3/coins/usd-coin/market_chart/range",
        repository_id="historical:usdc:09-00",
    )


def _usdc_observation(*, chain_id: int = 1) -> RawOnChainObservation:
    return RawOnChainObservation(
        project="compound",
        chain_id=chain_id,
        provider="evm-json-rpc",
        endpoint_identity="fixture",
        block_number=26491366,
        block_hash="0xblock",
        block_timestamp=datetime(2026, 11, 25, 9, 41, 35, tzinfo=UTC),
        observed_address="0xc3d688b66703497daa19211eedff47f25384cdc3",
        acquisition_timestamp=datetime(2026, 11, 25, 9, 42, tzinfo=UTC),
        finality_status="finalized",
        source_reference="fixture",
        evidence_id="onchain-evm-log:fixture",
        event_type="erc20_transfer",
        asset_contract="0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
        asset_symbol="USDC",
        raw_amount="17000000000",
        decimals=6,
        normalized_amount=Decimal("17000"),
        direction="inflow",
    )
