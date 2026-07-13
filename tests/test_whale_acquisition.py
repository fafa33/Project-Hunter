from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from hunter.cli import main
from hunter.market_validation import MarketValidationRunner
from hunter.market_validation.acquisition_sources import _whale_engine_sources
from hunter.market_validation.configuration import load_market_validation_config
from hunter.market_validation.runner import SourceBackedV1ProjectExecutor
from hunter.whale import (
    REQUIRED_WHALE_METRICS,
    WhaleIntelligenceEvidenceEngine,
    WhaleProviderRegistry,
    WhaleRepository,
    load_whale_config,
)
from hunter.whale.configuration import WhaleProviderConfig
from hunter.whale.models import WhaleMetric
from hunter.whale.providers import (
    BinanceDerivativesProvider,
    BybitDerivativesProvider,
    OkxDerivativesProvider,
    WhaleProviderError,
)
from hunter.whale.validation import validate_metric

NOW = datetime(2026, 7, 12, tzinfo=UTC)


def test_binance_provider_parses_open_interest_and_funding_rate() -> None:
    provider = BinanceDerivativesProvider(
        WhaleProviderConfig(
            name="binance_derivatives",
            enabled=True,
            base_url="https://fapi.binance.com",
            assets={"BTC": "BTCUSDT"},
            metrics={"open_interest": "open_interest", "funding_rate": "funding_rate"},
        ),
        transport=lambda url: (
            json.dumps({"symbol": "BTCUSDT", "openInterest": "1000.5"})
            if "openInterest" in url
            else json.dumps({"symbol": "BTCUSDT", "lastFundingRate": "0.0001", "time": "1783814400000"})
        ),
    )

    metrics = provider.collect()

    assert {metric.name for metric in metrics} == {"open_interest", "funding_rate"}
    assert {metric.asset for metric in metrics} == {"BTC"}
    assert all(metric.source_url.startswith("https://fapi.binance.com") for metric in metrics)


def test_provider_schema_failure_is_classified_without_fabricating_values() -> None:
    provider = BinanceDerivativesProvider(
        WhaleProviderConfig(
            "binance_derivatives",
            True,
            "https://fapi.binance.com",
            {"open_interest": "open_interest"},
            {"BTC": "BTCUSDT"},
        ),
        transport=lambda _url: json.dumps({"symbol": "BTCUSDT"}),
    )

    assert provider.collect() == ()
    assert provider.failures[0].reason == "SCHEMA_MISMATCH"


def test_bybit_and_okx_public_schemas_parse() -> None:
    bybit = BybitDerivativesProvider(
        WhaleProviderConfig(
            "bybit_derivatives",
            True,
            "https://api.bybit.com",
            {"open_interest": "open_interest", "funding_rate": "funding_rate"},
            {"BTC": "BTCUSDT"},
        ),
        transport=lambda url: (
            json.dumps({"retCode": 0, "result": {"list": [{"openInterest": "42.5", "timestamp": "1783814400000"}]}})
            if "open-interest" in url
            else json.dumps(
                {"retCode": 0, "result": {"list": [{"fundingRate": "0.0002", "fundingRateTimestamp": "1783814400000"}]}}
            )
        ),
    )
    okx = OkxDerivativesProvider(
        WhaleProviderConfig(
            "okx_derivatives",
            True,
            "https://www.okx.com",
            {"open_interest": "open_interest", "funding_rate": "funding_rate"},
            {"BTC": "BTC-USDT-SWAP"},
        ),
        transport=lambda url: (
            json.dumps({"code": "0", "data": [{"oiCcy": "40.0", "ts": "1783814400000"}]})
            if "open-interest" in url
            else json.dumps({"code": "0", "data": [{"fundingRate": "0.0001", "fundingTime": "1783814400000"}]})
        ),
    )

    assert {metric.name for metric in bybit.collect()} == {"open_interest", "funding_rate"}
    assert {metric.name for metric in okx.collect()} == {"open_interest", "funding_rate"}


def test_missing_public_sources_are_classified() -> None:
    config = load_whale_config()
    statuses = {metric: "NO_DOCUMENTED_FREE_SOURCE" for metric in config.providers[-1].metrics}

    assert statuses["exchange_inflows"] == "NO_DOCUMENTED_FREE_SOURCE"
    assert statuses["institutional_accumulation"] == "NO_DOCUMENTED_FREE_SOURCE"


def test_validation_rejects_future_and_stale_data() -> None:
    future = _metric("open_interest", timestamp=NOW + timedelta(minutes=2))
    stale = _metric("open_interest", timestamp=NOW - timedelta(days=3))

    assert validate_metric(future, now=NOW, stale_after_hours=24).validation_status == "INVALID"
    assert validate_metric(stale, now=NOW, stale_after_hours=24).validation_status == "STALE"


def test_repository_persistence_and_snapshot_confidence(tmp_path) -> None:
    repository = WhaleRepository(tmp_path)
    registry = WhaleProviderRegistry(
        (
            WhaleProviderConfig(
                "binance_derivatives",
                True,
                "https://fapi.binance.com",
                {"open_interest": "open_interest", "funding_rate": "funding_rate"},
                {"BTC": "BTCUSDT"},
            ),
        ),
        transport=_whale_transport,
    )

    snapshot = WhaleIntelligenceEvidenceEngine(repository=repository, registry=registry).sync(now=NOW)

    assert snapshot.evidence
    assert snapshot.confidence > 0.0
    assert repository.latest_snapshot() == snapshot
    assert repository.evidence()[0].repository_id.startswith("whale:")


def test_sync_preserves_previous_valid_evidence_when_later_provider_fails(tmp_path) -> None:
    repository = WhaleRepository(tmp_path)
    good = WhaleProviderRegistry(
        (
            WhaleProviderConfig(
                "binance_derivatives",
                True,
                "https://fapi.binance.com",
                {"open_interest": "open_interest"},
                {"BTC": "BTCUSDT"},
            ),
        ),
        transport=_whale_transport,
    )
    bad = WhaleProviderRegistry(
        (
            WhaleProviderConfig(
                "binance_derivatives",
                True,
                "https://fapi.binance.com",
                {"open_interest": "open_interest"},
                {"BTC": "BTCUSDT"},
            ),
        ),
        transport=lambda _url: _raise(WhaleProviderError("provider unavailable", reason="PROVIDER_BLOCKED")),
    )
    WhaleIntelligenceEvidenceEngine(repository=repository, registry=good).sync(now=NOW)

    snapshot = WhaleIntelligenceEvidenceEngine(repository=repository, registry=bad).sync(now=NOW + timedelta(hours=1))

    assert "open_interest" in snapshot.normalized_metrics
    assert repository.failures()[-1].reason == "PROVIDER_BLOCKED"


def test_derived_metrics_require_all_inputs(tmp_path) -> None:
    repository = WhaleRepository(tmp_path)
    evidence = validate_metric(_metric("exchange_inflows"), now=NOW, stale_after_hours=24)
    repository.save_evidence((evidence,))

    snapshot = WhaleIntelligenceEvidenceEngine(repository=repository).build_snapshot(generated_at=NOW)

    assert "net_exchange_flows" not in snapshot.normalized_metrics


def test_multi_provider_disagreement_is_exposed_without_incompatible_raw_average(tmp_path) -> None:
    repository = WhaleRepository(tmp_path)
    rows = (
        validate_metric(
            _metric("funding_rate", provider="binance_derivatives", value=0.0001), now=NOW, stale_after_hours=24
        ),
        validate_metric(
            _metric("funding_rate", provider="okx_derivatives", value=-0.0001), now=NOW, stale_after_hours=24
        ),
    )
    repository.save_evidence(rows)

    snapshot = WhaleIntelligenceEvidenceEngine(repository=repository).build_snapshot(generated_at=NOW)

    assert "funding_rate_provider_disagreement" in snapshot.normalized_metrics
    assert "funding_rate" not in snapshot.raw_metrics
    assert "binance_derivatives.BTC.funding_rate" in snapshot.raw_metrics


def test_derivatives_data_does_not_infer_wallet_metrics(tmp_path) -> None:
    repository = WhaleRepository(tmp_path)
    repository.save_evidence((validate_metric(_metric("open_interest"), now=NOW, stale_after_hours=24),))

    snapshot = WhaleIntelligenceEvidenceEngine(repository=repository).build_snapshot(generated_at=NOW)

    assert "top_holder_concentration" not in snapshot.normalized_metrics
    assert "whale_accumulation" not in snapshot.normalized_metrics


def test_whale_source_connects_to_market_validation_weighting(tmp_path, monkeypatch) -> None:
    repository = WhaleRepository(tmp_path)
    registry = WhaleProviderRegistry(
        (
            WhaleProviderConfig(
                "binance_derivatives",
                True,
                "https://fapi.binance.com",
                {"open_interest": "open_interest"},
                {"BTC": "BTCUSDT"},
            ),
        ),
        transport=_whale_transport,
    )
    WhaleIntelligenceEvidenceEngine(repository=repository, registry=registry).sync(now=NOW)
    monkeypatch.setattr("hunter.market_validation.acquisition_sources.WhaleRepository", lambda: repository)

    sources = _whale_engine_sources(as_of=NOW)

    assert {source.engine for source in sources}.issuperset({"whale_intelligence", "risk", "committee"})
    assert all(source.evidence_ids for source in sources)


def test_whale_evidence_reaches_explainability_and_committee_path(tmp_path, monkeypatch) -> None:
    repository = WhaleRepository(tmp_path)
    registry = WhaleProviderRegistry(
        (
            WhaleProviderConfig(
                "binance_derivatives",
                True,
                "https://fapi.binance.com",
                {"open_interest": "open_interest"},
                {"BTC": "BTCUSDT"},
            ),
        ),
        transport=_whale_transport,
    )
    WhaleIntelligenceEvidenceEngine(repository=repository, registry=registry).sync(now=NOW)
    monkeypatch.setattr("hunter.market_validation.acquisition_sources.WhaleRepository", lambda: repository)
    market_config = load_market_validation_config()
    sources = {"bitcoin": _whale_engine_sources(as_of=NOW)}

    result = next(
        item
        for item in MarketValidationRunner(
            market_config, executor=SourceBackedV1ProjectExecutor(market_config.effective_at, sources)
        )
        .run()
        .project_results
        if item.project_id == "bitcoin"
    )

    whale = next(source for source in result.engine_sources if source.engine == "whale_intelligence")
    assert whale.base_weight > 0.0
    assert whale.weighted_contribution > 0.0
    assert result.committee_decision == "INSUFFICIENT_EVIDENCE"


def test_whale_cli_commands_execute(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs" / "whale.yaml").write_text(
        """
enabled: true
stale_after_hours: 24
providers: {}
""".strip(),
        encoding="utf-8",
    )

    assert main(["whale", "--whale-config", "configs/whale.yaml", "status"]) == 0
    assert main(["whale", "--whale-config", "configs/whale.yaml", "providers"]) == 0
    assert main(["whale", "--whale-config", "configs/whale.yaml", "coverage"]) == 0
    assert main(["whale", "--whale-config", "configs/whale.yaml", "failures"]) == 0
    assert main(["whale", "--whale-config", "configs/whale.yaml", "validate"]) == 0
    assert main(["whale", "--whale-config", "configs/whale.yaml", "history"]) == 0


def test_required_whale_metric_contract_is_complete() -> None:
    assert set(REQUIRED_WHALE_METRICS).issuperset(
        {
            "exchange_inflows",
            "exchange_outflows",
            "net_exchange_flows",
            "whale_accumulation",
            "whale_distribution",
            "smart_money_activity",
            "open_interest",
            "funding_rate",
            "institutional_accumulation",
        }
    )


def _metric(name: str, *, timestamp: datetime = NOW, provider: str = "fixture", value: float = 100.0) -> WhaleMetric:
    return WhaleMetric(
        name=name,
        provider=provider,
        source_url="https://example.com/whale",
        asset="BTC",
        timestamp=timestamp,
        retrieval_time=NOW,
        value=value,
        raw_payload={"value": value},
    )


def _whale_transport(url: str) -> str:
    if "openInterest" in url:
        return json.dumps({"symbol": "BTCUSDT", "openInterest": "1000.5"})
    return json.dumps({"symbol": "BTCUSDT", "lastFundingRate": "0.0001", "time": "1783814400000"})


def _raise(exc: Exception) -> str:
    raise exc
