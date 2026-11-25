from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from urllib.parse import urlsplit

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from hunter.cli import main
from hunter.market_validation import MarketValidationRunner
from hunter.market_validation.acquisition_sources import _whale_engine_sources
from hunter.market_validation.configuration import load_market_validation_config
from hunter.market_validation.runner import EvidenceBackedProjectExecutor
from hunter.onchain.configuration import load_onchain_config
from hunter.onchain.engine import TRANSFER_TOPIC, evm_log_evidence_id
from hunter.onchain.evidence import (
    CORROBORATION_POLICY,
    load_trust,
    receipt_log_digest,
    sign_observation,
    write_signing_key,
    write_trust_document,
)
from hunter.onchain.models import RawOnChainObservation
from hunter.onchain.repository import OnChainRepository
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
    for metric in metrics:
        source = urlsplit(metric.source_url)
        assert source.scheme == "https"
        assert source.hostname == "fapi.binance.com"


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

    sources = _whale_engine_sources(as_of=NOW)

    assert all(source.status == "UNAVAILABLE" for source in sources)
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
    market_config = load_market_validation_config()
    sources = {"bitcoin": _whale_engine_sources(as_of=NOW)}

    result = next(
        item
        for item in MarketValidationRunner(
            market_config, executor=EvidenceBackedProjectExecutor(market_config.effective_at, sources)
        )
        .run()
        .project_results
        if item.project_id == "bitcoin"
    )

    whale = next(source for source in result.engine_sources if source.engine == "whale_intelligence")
    assert whale.status == "UNAVAILABLE"
    assert whale.weighted_contribution == 0.0
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


def _signed_capital_row(tmp_path):
    cfg = load_onchain_config()
    surface = next(item for item in cfg.surfaces if item.active and item.project == "aave" and item.chain_id == 1)
    key = Ed25519PrivateKey.generate()
    key_path = tmp_path / "signing.key"
    trust_path = tmp_path / "trust.json"
    write_signing_key(key_path, key)
    write_trust_document(trust_path, key)
    tx = "0x" + "ab" * 32
    counterparty = "0x" + "22" * 20
    row = RawOnChainObservation(
        project="aave",
        chain_id=1,
        provider="evm-json-rpc",
        endpoint_identity="eth.drpc.org",
        block_number=900,
        block_hash="0x" + "ef" * 32,
        block_timestamp=NOW - timedelta(minutes=1),
        observed_address=surface.address,
        acquisition_timestamp=NOW,
        finality_status="finalized",
        source_reference="https://example.com/tx/" + tx,
        evidence_id=evm_log_evidence_id(1, tx, 0),
        transaction_hash=tx,
        log_index=0,
        counterparty_address=counterparty,
        asset_contract="0x" + "cd" * 20,
        asset_symbol="TEST",
        raw_amount="1000000",
        decimals=6,
        normalized_amount=Decimal("1"),
        direction="in",
        event_type="erc20_transfer",
        log_endpoint="https://eth.drpc.org",
        receipt_endpoint="https://eth.drpc.org",
        corroborating_endpoints=("https://rpc.flashbots.net", "https://rpc.mevblocker.io"),
        receipt_validation_policy=CORROBORATION_POLICY,
        receipt_verified_at=NOW,
        log_topics=(
            TRANSFER_TOPIC,
            "0x" + counterparty[2:].zfill(64),
            "0x" + surface.address[2:].zfill(64),
        ),
        log_data="0x" + format(1000000, "064x"),
    )
    row = replace(row, receipt_log_digest=receipt_log_digest(row))
    repo = OnChainRepository(tmp_path / "runtime")
    repo.save_raw((row,))
    repo.save_evidence_signatures((sign_observation(row, surface, key, verification_timestamp=NOW),))
    return row, cfg, repo, load_trust(trust_path)


def test_capital_context_is_project_scoped_and_never_weighted(tmp_path) -> None:
    from hunter.market_validation.acquisition_sources import _capital_whale_context

    row, cfg, repo, trust = _signed_capital_row(tmp_path)
    source = _capital_whale_context("aave", (row, row), as_of=NOW, config=cfg, repository=repo, trust=trust)
    assert source is not None
    assert source.status == "UNAVAILABLE"
    assert source.confidence == source.score == 0
    assert source.evidence_ids == (row.evidence_id,)
    assert _capital_whale_context("bitcoin", (row,), as_of=NOW, config=cfg, repository=repo, trust=trust) is None

    hostile = (
        replace(row, evidence_id="forged"),
        replace(row, log_index=-1),
        replace(row, block_hash="fake"),
        replace(row, finality_status="pending"),
        replace(row, block_timestamp=NOW - timedelta(days=2)),
    )
    for candidate in hostile:
        assert _capital_whale_context("aave", (candidate,), as_of=NOW, config=cfg, repository=repo, trust=trust) is None
