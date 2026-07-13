from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from hunter.cli import main
from hunter.macro import MacroIntelligenceEvidenceEngine, MacroProviderRegistry, MacroRepository, load_macro_config
from hunter.macro.configuration import MacroProviderConfig
from hunter.macro.engine import REQUIRED_MACRO_METRICS
from hunter.macro.models import MacroMetric
from hunter.macro.providers import CsvSeriesProvider, JsonMacroProvider, MacroProviderError
from hunter.macro.validation import validate_metric
from hunter.market_validation import MarketValidationRunner
from hunter.market_validation.acquisition_sources import _macro_engine_sources
from hunter.market_validation.configuration import load_market_validation_config
from hunter.market_validation.runner import SourceBackedV1ProjectExecutor

NOW = datetime(2026, 7, 12, tzinfo=UTC)


def test_csv_provider_parses_latest_real_observation_shape() -> None:
    provider = CsvSeriesProvider(
        MacroProviderConfig(
            name="fred",
            enabled=True,
            base_url="https://fred.stlouisfed.org/graph/fredgraph.csv",
            metrics={"federal_funds_rate": "FEDFUNDS"},
        ),
        transport=lambda _url: "observation_date,FEDFUNDS\n2026-05-01,4.33\n2026-06-01,4.25\n",
    )

    metric = provider.collect()[0]

    assert metric.name == "federal_funds_rate"
    assert metric.value == 4.25
    assert metric.source_url.startswith("https://fred.stlouisfed.org")


def test_ecb_policy_rate_csv_schema_parses_public_mapping() -> None:
    provider = CsvSeriesProvider(
        MacroProviderConfig(
            name="fred",
            enabled=True,
            base_url="https://fred.stlouisfed.org/graph/fredgraph.csv",
            metrics={"ecb_interest_rate": "ECBMRRFR"},
        ),
        transport=lambda _url: "observation_date,ECBMRRFR\n2026-07-10,2.40\n",
    )

    metric = provider.collect()[0]

    assert metric.name == "ecb_interest_rate"
    assert metric.raw_payload["series_id"] == "ECBMRRFR"
    assert metric.value == 2.4


def test_json_providers_parse_crypto_and_fear_greed_metrics() -> None:
    coingecko = JsonMacroProvider(
        MacroProviderConfig("coingecko_global", True, "https://api.coingecko.com/api/v3/global", {}),
        transport=lambda _url: json.dumps(
            {"data": {"market_cap_percentage": {"btc": 51.2}, "total_market_cap": {"usd": 2_400_000_000_000}}}
        ),
    )
    fear = JsonMacroProvider(
        MacroProviderConfig("alternative_me", True, "https://api.alternative.me/fng/", {}),
        transport=lambda _url: json.dumps({"data": [{"value": "74", "timestamp": "1783814400"}]}),
    )

    metrics = coingecko.collect() + fear.collect()

    assert {metric.name for metric in metrics} == {
        "bitcoin_dominance",
        "total_crypto_market_cap",
        "fear_greed_index",
    }


def test_json_provider_records_schema_failures_without_values() -> None:
    provider = JsonMacroProvider(
        MacroProviderConfig(
            "coingecko_global",
            True,
            "https://api.coingecko.com/api/v3/global",
            {"bitcoin_dominance": "bitcoin_dominance"},
        ),
        transport=lambda _url: "{broken",
    )

    metrics = provider.collect()

    assert metrics == ()
    assert provider.failures[0].reason == "SCHEMA_MISMATCH"


def test_validation_rejects_future_and_stale_data_without_estimates() -> None:
    future = MacroMetric(
        name="vix",
        provider="cboe",
        source_url="https://cdn.cboe.com/vix.csv",
        timestamp=NOW + timedelta(days=1),
        value=20,
        raw_payload={"value": 20},
    )
    stale = MacroMetric(
        name="vix",
        provider="cboe",
        source_url="https://cdn.cboe.com/vix.csv",
        timestamp=NOW - timedelta(days=10),
        value=20,
        raw_payload={"value": 20},
    )

    assert validate_metric(future, now=NOW, stale_after_hours=72).validation_status == "INVALID"
    assert validate_metric(stale, now=NOW, stale_after_hours=72).validation_status == "STALE"


def test_metric_specific_freshness_accepts_monthly_policy_data() -> None:
    monthly = MacroMetric(
        name="federal_funds_rate",
        provider="fred",
        source_url="https://fred.stlouisfed.org/graph/fredgraph.csv?id=FEDFUNDS",
        timestamp=NOW - timedelta(days=60),
        value=4.25,
        raw_payload={"series_id": "FEDFUNDS"},
    )

    assert validate_metric(monthly, now=NOW, stale_after_hours=24).validation_status == "VALID"


def test_collection_clock_skew_does_not_reject_current_json_metrics() -> None:
    current = MacroMetric(
        name="stablecoin_market_cap",
        provider="defillama_stablecoins",
        source_url="https://stablecoins.llama.fi/stablecoins",
        timestamp=NOW + timedelta(seconds=30),
        value=300_000_000_000,
        raw_payload={"totalCirculatingUSD": {"peggedUSD": 300_000_000_000}},
    )

    assert validate_metric(current, now=NOW, stale_after_hours=24).validation_status == "VALID"


def test_future_macro_values_are_not_fabricated_or_accepted() -> None:
    future = MacroMetric(
        name="stablecoin_market_cap",
        provider="defillama_stablecoins",
        source_url="https://stablecoins.llama.fi/stablecoins",
        timestamp=NOW + timedelta(minutes=2),
        value=300_000_000_000,
        raw_payload={"totalCirculatingUSD": {"peggedUSD": 300_000_000_000}},
    )

    evidence = validate_metric(future, now=NOW, stale_after_hours=24)

    assert evidence.validation_status == "INVALID"
    assert evidence.validation_errors == ("future_timestamp",)


def test_missing_observations_are_skipped_and_failures_are_classified() -> None:
    provider = CsvSeriesProvider(
        MacroProviderConfig(
            name="fred",
            enabled=True,
            base_url="https://fred.stlouisfed.org/graph/fredgraph.csv",
            metrics={"federal_funds_rate": "FEDFUNDS", "pmi": "NAPM"},
        ),
        transport=lambda url: (
            "observation_date,FEDFUNDS\n2026-05-01,.\n2026-06-01,4.25\n"
            if "FEDFUNDS" in url
            else (_raise(MacroProviderError("HTTP Error 404: Not Found", reason="MISCONFIGURED")))
        ),
    )

    metrics = provider.collect()

    assert metrics[0].value == 4.25
    assert provider.failures[0].metric == "pmi"
    assert provider.failures[0].reason == "MISCONFIGURED"


def test_macro_engine_persists_snapshot_and_confidence(tmp_path) -> None:
    config = load_macro_config()
    repository = MacroRepository(tmp_path)
    registry = MacroProviderRegistry(
        (
            MacroProviderConfig("fred", True, "https://fred.stlouisfed.org/graph/fredgraph.csv", {"pmi": "NAPM"}),
            MacroProviderConfig("alternative_me", True, "https://api.alternative.me/fng/", {}),
        ),
        transport=_macro_transport,
    )

    snapshot = MacroIntelligenceEvidenceEngine(config=config, repository=repository, registry=registry).sync(now=NOW)

    assert snapshot.evidence
    assert snapshot.macro_confidence > 0.0
    assert repository.latest_snapshot() == snapshot
    assert repository.evidence()


def test_sync_preserves_previous_valid_evidence_when_later_provider_fails(tmp_path) -> None:
    repository = MacroRepository(tmp_path)
    good = MacroProviderRegistry(
        (MacroProviderConfig("fred", True, "https://fred.stlouisfed.org/graph/fredgraph.csv", {"pmi": "NAPM"}),),
        transport=_macro_transport,
    )
    bad = MacroProviderRegistry(
        (MacroProviderConfig("fred", True, "https://fred.stlouisfed.org/graph/fredgraph.csv", {"pmi": "NAPM"}),),
        transport=lambda _url: _raise(MacroProviderError("provider unavailable", reason="PROVIDER_BLOCKED")),
    )
    MacroIntelligenceEvidenceEngine(repository=repository, registry=good).sync(now=NOW)

    snapshot = MacroIntelligenceEvidenceEngine(repository=repository, registry=bad).sync(now=NOW + timedelta(hours=1))

    assert "pmi" in snapshot.normalized_metrics
    assert repository.failures()[-1].reason == "PROVIDER_BLOCKED"


def test_duplicate_evidence_can_refresh_validation_without_duplicate_raw_records(tmp_path) -> None:
    repository = MacroRepository(tmp_path)
    metric = MacroMetric(
        name="stablecoin_market_cap",
        provider="defillama_stablecoins",
        source_url="https://stablecoins.llama.fi/stablecoins",
        timestamp=NOW + timedelta(seconds=30),
        value=300_000_000_000,
        raw_payload={"totalCirculatingUSD": {"peggedUSD": 300_000_000_000}},
    )
    invalid = validate_metric(metric, now=NOW - timedelta(minutes=2), stale_after_hours=24)
    valid = validate_metric(metric, now=NOW, stale_after_hours=24)

    repository.save_evidence((invalid,))
    repository.save_evidence((valid,))

    assert len(repository.evidence()) == 1
    assert repository.evidence()[0].validation_status == "VALID"


def test_derived_metrics_require_all_inputs(tmp_path) -> None:
    repository = MacroRepository(tmp_path)
    registry = MacroProviderRegistry(
        (
            MacroProviderConfig(
                "fred",
                True,
                "https://fred.stlouisfed.org/graph/fredgraph.csv",
                {"treasury_10y": "DGS10"},
            ),
        ),
        transport=lambda _url: "observation_date,DGS10\n2026-07-11,4.2\n",
    )

    snapshot = MacroIntelligenceEvidenceEngine(repository=repository, registry=registry).sync(now=NOW)

    assert "yield_curve_spread" not in snapshot.normalized_metrics


def test_no_unofficial_etf_flow_fallback_is_configured() -> None:
    config = load_macro_config()
    etf_provider = next(provider for provider in config.providers if provider.name == "etf_flows")

    assert etf_provider.enabled is False
    assert "bitcoin_etf_net_flows" in etf_provider.metrics
    assert "ethereum_etf_net_flows" in etf_provider.metrics


def test_macro_source_connects_to_market_validation_weighting(tmp_path, monkeypatch) -> None:
    repository = MacroRepository(tmp_path)
    config = load_macro_config()
    registry = MacroProviderRegistry(
        (MacroProviderConfig("fred", True, "https://fred.stlouisfed.org/graph/fredgraph.csv", {"pmi": "NAPM"}),),
        transport=_macro_transport,
    )
    MacroIntelligenceEvidenceEngine(config=config, repository=repository, registry=registry).sync(now=NOW)
    monkeypatch.setattr("hunter.market_validation.acquisition_sources.MacroRepository", lambda: repository)

    sources = _macro_engine_sources(as_of=NOW)

    assert {source.engine for source in sources}.issuperset({"macro_intelligence", "risk", "committee"})
    assert all(source.evidence_ids for source in sources)


def test_macro_evidence_reaches_explainability_and_committee_path(tmp_path, monkeypatch) -> None:
    repository = MacroRepository(tmp_path)
    config = load_macro_config()
    registry = MacroProviderRegistry(
        (MacroProviderConfig("fred", True, "https://fred.stlouisfed.org/graph/fredgraph.csv", {"pmi": "NAPM"}),),
        transport=_macro_transport,
    )
    MacroIntelligenceEvidenceEngine(config=config, repository=repository, registry=registry).sync(now=NOW)
    monkeypatch.setattr("hunter.market_validation.acquisition_sources.MacroRepository", lambda: repository)
    market_config = load_market_validation_config()
    sources = {"bitcoin": _macro_engine_sources(as_of=NOW)}

    result = next(
        item
        for item in MarketValidationRunner(
            market_config, executor=SourceBackedV1ProjectExecutor(market_config.effective_at, sources)
        )
        .run()
        .project_results
        if item.project_id == "bitcoin"
    )

    macro = next(source for source in result.engine_sources if source.engine == "macro_intelligence")
    assert macro.base_weight > 0.0
    assert macro.weighted_contribution > 0.0
    assert result.committee_decision == "INSUFFICIENT_EVIDENCE"


def test_macro_cli_commands_execute(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs" / "macro.yaml").write_text(
        """
enabled: true
stale_after_hours: 72
providers: {}
""".strip(),
        encoding="utf-8",
    )

    assert main(["macro", "--macro-config", "configs/macro.yaml", "status"]) == 0
    assert main(["macro", "--macro-config", "configs/macro.yaml", "providers"]) == 0
    assert main(["macro", "--macro-config", "configs/macro.yaml", "coverage"]) == 0
    assert main(["macro", "--macro-config", "configs/macro.yaml", "missing"]) == 0
    assert main(["macro", "--macro-config", "configs/macro.yaml", "failures"]) == 0
    assert main(["macro", "--macro-config", "configs/macro.yaml", "validate"]) == 0
    assert main(["macro", "--macro-config", "configs/macro.yaml", "history"]) == 0


def test_required_macro_metric_contract_is_complete() -> None:
    assert set(REQUIRED_MACRO_METRICS).issuperset(
        {
            "federal_funds_rate",
            "ecb_interest_rate",
            "us_cpi",
            "us_ppi",
            "pmi",
            "us_unemployment",
            "dxy",
            "global_m2_liquidity",
            "bitcoin_dominance",
            "stablecoin_market_cap",
            "bitcoin_etf_net_flows",
            "ethereum_etf_net_flows",
            "fear_greed_index",
            "vix",
            "oil_wti",
            "gold",
            "dollar_liquidity_indicators",
        }
    )


def _macro_transport(url: str) -> str:
    if "alternative.me" in url:
        return json.dumps({"data": [{"value": "65", "timestamp": "1783814400"}]})
    return "observation_date,NAPM\n2026-07-11,54.2\n"


def _raise(exc: Exception) -> str:
    raise exc
