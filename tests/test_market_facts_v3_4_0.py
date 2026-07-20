from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from hunter.market_facts import (
    CoinGeckoObservedMarketFactProvider,
    MarketFactAcquisitionResult,
    MarketFactAuthorityError,
    MarketFactIdentity,
    MarketFactRequest,
    MarketFactSourceRegistry,
    NormalizedMarketFact,
    ObservedMarketFactRepository,
    ObservedMarketFactService,
    RepositoryAuthorizationError,
)
from hunter.market_facts.repository import MARKET_FACTS_MIGRATION_ID, MarketFactWritePlan

T0 = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
T1 = T0 + timedelta(minutes=5)
T2 = T0 + timedelta(minutes=6)


class StaticTransport:
    def __init__(self, payload: object) -> None:
        self.payload = payload
        self.urls: list[str] = []

    def get_json(self, url: str, timeout_seconds: float) -> object:
        assert timeout_seconds > 0
        self.urls.append(url)
        return self.payload


def registry(*, enabled: bool = True) -> MarketFactSourceRegistry:
    return MarketFactSourceRegistry.from_mapping(
        {
            "sources": [
                {
                    "source_id": "coingecko-coin-market-facts",
                    "provider_id": "coingecko",
                    "endpoint_template": "https://api.coingecko.com/api/v3/coins/{listing_id}",
                    "allowed_hosts": ["api.coingecko.com"],
                    "parser_version": "coingecko-coin-v1",
                    "enabled": enabled,
                    "capabilities": [
                        "spot_price",
                        "circulating_supply",
                        "total_supply",
                        "max_supply",
                        "market_capitalization",
                        "fully_diluted_valuation",
                        "trading_volume",
                    ],
                    "quote_currencies": ["usd"],
                    "units": {
                        "spot_price": "quote_currency_per_unit",
                        "circulating_supply": "native_units",
                        "total_supply": "native_units",
                        "max_supply": "native_units",
                        "market_capitalization": "quote_currency",
                        "fully_diluted_valuation": "quote_currency",
                        "trading_volume": "quote_currency_per_24h",
                    },
                    "supported_entity_scope": ["canonical_asset_representation"],
                    "freshness_seconds": 3600,
                    "historical_support": "current-only",
                    "limitations": "observed facts only",
                }
            ]
        }
    )


def identity() -> MarketFactIdentity:
    return MarketFactIdentity(
        entity_id="entity:bitcoin",
        asset_id="asset:btc",
        representation_id="representation:btc-native",
        chain="",
        contract_address="",
        provider_listing_id="bitcoin",
    )


def request(*, source_id: str = "coingecko-coin-market-facts") -> MarketFactRequest:
    return MarketFactRequest(
        source_id=source_id,
        provider_id="coingecko",
        identity=identity(),
        quote_currency="usd",
        requested_fact_types=("spot_price", "circulating_supply", "market_capitalization"),
        requested_at=T0,
    )


def payload(*, price: int = 100, last_updated: str = "2026-01-01T00:04:00Z") -> dict[str, object]:
    return {
        "id": "bitcoin",
        "last_updated": last_updated,
        "market_data": {
            "current_price": {"usd": price},
            "circulating_supply": 19_000_000,
            "total_supply": 21_000_000,
            "max_supply": 21_000_000,
            "market_cap": {"usd": price * 19_000_000},
            "fully_diluted_valuation": {"usd": price * 21_000_000},
            "total_volume": {"usd": 1_000_000},
        },
    }


def provider_result(*, price: int = 100, source_registry: MarketFactSourceRegistry | None = None):
    source_registry = source_registry or registry()
    transport = StaticTransport(payload(price=price))
    provider = CoinGeckoObservedMarketFactProvider(
        source_registry,
        transport,
        clock=lambda: T1,
    )
    return provider.collect(request())


def service(tmp_path: Path, source_registry: MarketFactSourceRegistry | None = None):
    repo = ObservedMarketFactRepository(tmp_path / "market-facts.sqlite")
    return repo, ObservedMarketFactService(repo, source_registry or registry())


def test_provider_parses_only_requested_observed_facts() -> None:
    result = provider_result()
    assert result.status == "success"
    assert [fact.fact_type for fact in result.facts] == [
        "spot_price",
        "circulating_supply",
        "market_capitalization",
    ]
    assert result.facts[0].value == "100"
    assert result.facts[0].quote_currency == "usd"
    assert result.facts[1].quote_currency is None


def test_unregistered_and_disabled_sources_fail_closed(tmp_path: Path) -> None:
    repo = ObservedMarketFactRepository(tmp_path / "facts.sqlite")
    svc = ObservedMarketFactService(repo, MarketFactSourceRegistry(()))
    forged = provider_result()
    with pytest.raises(ValueError, match="not registered"):
        svc.ingest(request(), forged, recorded_at=T2)

    disabled = registry(enabled=False)
    disabled_svc = ObservedMarketFactService(repo, disabled)
    with pytest.raises(ValueError, match="disabled"):
        disabled_svc.ingest(request(), forged, recorded_at=T2)


def test_forged_provider_endpoint_parser_and_registry_are_rejected(tmp_path: Path) -> None:
    _, svc = service(tmp_path)
    valid = provider_result()
    for forged in (
        replace(valid, provider_id="other"),
        replace(valid, endpoint="https://example.com/bitcoin"),
        replace(valid, parser_version="forged"),
        replace(valid, registry_fingerprint="sha256:forged"),
    ):
        with pytest.raises(MarketFactAuthorityError):
            svc.ingest(request(), forged, recorded_at=T2)


def test_provider_listing_cannot_substitute_for_canonical_identity(tmp_path: Path) -> None:
    bad_identity = replace(identity(), entity_id="bitcoin")
    bad_request = replace(request(), identity=bad_identity)
    valid = provider_result()
    forged = replace(valid, request=bad_request)
    _, svc = service(tmp_path)
    with pytest.raises(MarketFactAuthorityError, match="provider listing"):
        svc.ingest(bad_request, forged, recorded_at=T2)


def test_quote_currency_unit_and_cross_request_mismatch_are_rejected(tmp_path: Path) -> None:
    _, svc = service(tmp_path)
    valid = provider_result()
    bad_quote = replace(valid.facts[0], quote_currency="eur")
    bad_unit = replace(valid.facts[0], unit="ambiguous")
    for fact in (bad_quote, bad_unit):
        forged = replace(valid, facts=(fact,))
        with pytest.raises(MarketFactAuthorityError):
            svc.ingest(request(), forged, recorded_at=T2)

    other_request = replace(request(), identity=replace(identity(), representation_id="representation:wrapped-btc"))
    with pytest.raises(MarketFactAuthorityError, match="request does not match"):
        svc.ingest(other_request, valid, recorded_at=T2)


def test_negative_values_use_fact_specific_validation(tmp_path: Path) -> None:
    _, svc = service(tmp_path)
    valid = provider_result()
    negative = replace(valid.facts[0], value="-1")
    with pytest.raises(ValueError, match="spot_price must be positive"):
        svc.ingest(request(), replace(valid, facts=(negative,)), recorded_at=T2)


def test_success_is_immutable_idempotent_and_exactly_retrievable(tmp_path: Path) -> None:
    repo, svc = service(tmp_path)
    result = provider_result()
    first = svc.ingest(request(), result, recorded_at=T2)
    second = svc.ingest(request(), result, recorded_at=T2)
    assert first == second
    assert repo.count("observed_market_facts") == 3
    assert repo.record(first[0].record_id) == first[0]
    assert repo.migration_ids() == (MARKET_FACTS_MIGRATION_ID,)


def test_repository_rejects_unauthorized_write_plan(tmp_path: Path) -> None:
    repo, svc = service(tmp_path)
    record = svc.ingest(request(), provider_result(), recorded_at=T2)[0]
    with pytest.raises(RepositoryAuthorizationError):
        repo.apply(MarketFactWritePlan(records=(record,), authority=object()))


def test_unavailable_result_creates_only_operational_event(tmp_path: Path) -> None:
    repo, svc = service(tmp_path)
    valid = provider_result()
    unavailable = replace(valid, status="unavailable", facts=(), failure_reason="provider_down")
    assert svc.ingest(request(), unavailable, recorded_at=T2) == ()
    assert repo.count("observed_market_facts") == 0
    events = repo.availability_events()
    assert len(events) == 1
    assert events[0]["status"] == "unavailable"
    assert "value" not in events[0]


def test_strict_known_replay_enforces_effective_recorded_and_known_cutoffs(tmp_path: Path) -> None:
    _, svc = service(tmp_path)
    record = svc.ingest(request(), provider_result(), recorded_at=T2)[0]
    common = {
        "entity_id": record.identity.entity_id,
        "representation_id": record.identity.representation_id,
        "fact_type": record.fact_type,
        "quote_currency": record.quote_currency,
    }
    assert svc.strict_known_fact(effective_as_of=T0, known_by=T2, **common) is None
    assert svc.strict_known_fact(effective_as_of=T2, known_by=T0, **common) is None
    assert svc.strict_known_fact(effective_as_of=T2, known_by=T1, **common) is None
    assert svc.strict_known_fact(effective_as_of=T2, known_by=T2, **common) == record


def test_stale_and_open_conflict_records_are_preserved_but_not_replay_eligible(tmp_path: Path) -> None:
    repo, svc = service(tmp_path)
    valid = provider_result()
    stale_fact = replace(valid.facts[0], effective_at=T0 - timedelta(hours=2))
    stale_record = svc.ingest(request(), replace(valid, facts=(stale_fact,)), recorded_at=T2)[0]
    assert stale_record.quality_state == "stale"
    assert repo.record(stale_record.record_id) == stale_record
    assert (
        svc.strict_known_fact(
            entity_id=stale_record.identity.entity_id,
            representation_id=stale_record.identity.representation_id,
            fact_type=stale_record.fact_type,
            quote_currency=stale_record.quote_currency,
            effective_as_of=T2,
            known_by=T2,
        )
        is None
    )

    conflict_fact = replace(valid.facts[0], conflict_state="open")
    conflict_record = svc.ingest(request(), replace(valid, facts=(conflict_fact,)), recorded_at=T2)[0]
    assert conflict_record.conflict_state == "open"
    assert repo.record(conflict_record.record_id) == conflict_record


def test_corrections_are_append_only_and_historical_selection_is_cutoff_safe(tmp_path: Path) -> None:
    repo, svc = service(tmp_path)
    original = svc.ingest(request(), provider_result(price=100), recorded_at=T2)[0]

    correction_known = T2 + timedelta(minutes=4)
    correction_recorded = T2 + timedelta(minutes=5)
    correction_result = replace(
        provider_result(price=120),
        acquired_at=correction_known,
        known_at=correction_known,
        facts=(
            replace(
                provider_result(price=120).facts[0],
                observed_at=correction_known,
                effective_at=T1,
            ),
        ),
    )
    corrected = svc.correct(
        original.record_id,
        request(),
        correction_result,
        recorded_at=correction_recorded,
        reason="provider correction",
    )[0]
    assert corrected.supersedes_record_id == original.record_id
    assert repo.lineage(original.logical_id) == (original, corrected)

    before = svc.strict_known_fact(
        entity_id=original.identity.entity_id,
        representation_id=original.identity.representation_id,
        fact_type=original.fact_type,
        quote_currency=original.quote_currency,
        effective_as_of=correction_recorded,
        known_by=T2,
    )
    after = svc.strict_known_fact(
        entity_id=original.identity.entity_id,
        representation_id=original.identity.representation_id,
        fact_type=original.fact_type,
        quote_currency=original.quote_currency,
        effective_as_of=correction_recorded,
        known_by=correction_recorded,
    )
    assert before == original
    assert after == corrected


def test_models_reject_naive_time_and_identity_scope_mismatch() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        replace(request(), requested_at=datetime(2026, 1, 1))
    with pytest.raises(ValueError, match="chain and contract_address"):
        replace(identity(), chain="ethereum", contract_address="")
