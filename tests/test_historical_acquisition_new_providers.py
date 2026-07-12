from __future__ import annotations

from datetime import UTC, datetime

from hunter.historical.models import HistoricalValidationCase
from hunter.historical_acquisition import providers as providers_module
from hunter.historical_acquisition.providers import (
    CoinGeckoHistoricalProvider,
    DefiLlamaHistoricalProvider,
    GovernanceArchiveProvider,
    InternetArchiveSnapshotProvider,
)

NOW = datetime(2022, 6, 15, tzinfo=UTC)


def _case(evaluation: datetime = NOW) -> HistoricalValidationCase:
    return HistoricalValidationCase(
        case_id="ethereum-case",
        project_id="ethereum",
        project_slug="ethereum",
        project_name="Ethereum",
        symbol="ETH",
        sector="smart_contracts",
        case_type="EARLY_WINNER",
        evaluation_timestamp=evaluation,
        historical_cutoff_timestamp=evaluation,
        project_lifecycle_state="active",
        token_lifecycle_state="active",
    )


def test_internet_archive_provider_selects_closest_real_snapshot(monkeypatch) -> None:
    cdx_payload = [
        ["urlkey", "timestamp", "original", "mimetype", "statuscode", "digest", "length"],
        ["org,ethereum)/", "20220601000000", "https://ethereum.org/", "text/html", "200", "DIGEST1", "100"],
        ["org,ethereum)/", "20220620000000", "https://ethereum.org/", "text/html", "200", "DIGEST2", "110"],
    ]
    monkeypatch.setattr(providers_module, "_get_json", lambda url: cdx_payload)

    provider = InternetArchiveSnapshotProvider(domain_map={"ethereum": "ethereum.org"})
    rows = provider.collect((_case(),))

    assert len(rows) == 1
    row = rows[0]
    assert row.metric == "historical_archive_presence"
    assert row.payload["domain"] == "ethereum.org"
    assert row.payload["content_digest"] == "DIGEST2"
    assert row.source_url == "http://web.archive.org/web/20220620000000/https://ethereum.org/"


def test_internet_archive_provider_skips_projects_without_domain() -> None:
    provider = InternetArchiveSnapshotProvider(domain_map={})
    rows = provider.collect((_case(),))
    assert rows == ()


def test_governance_provider_filters_proposals_after_cutoff(monkeypatch) -> None:
    graphql_payload = {
        "data": {
            "proposals": [
                {
                    "id": "0xabc",
                    "title": "Pre-cutoff proposal",
                    "state": "closed",
                    "created": int(datetime(2022, 5, 1, tzinfo=UTC).timestamp()),
                    "author": "0x123",
                    "votes": 42,
                    "scores_total": 1000.0,
                },
                {
                    "id": "0xdef",
                    "title": "Post-cutoff proposal",
                    "state": "active",
                    "created": int(datetime(2022, 7, 1, tzinfo=UTC).timestamp()),
                    "author": "0x456",
                    "votes": 7,
                    "scores_total": 200.0,
                },
            ]
        }
    }
    monkeypatch.setattr(providers_module, "_post_json", lambda url, body: graphql_payload)

    provider = GovernanceArchiveProvider(space_map={"ethereum": "ens.eth"})
    rows = provider.collect((_case(),))

    assert len(rows) == 1
    assert rows[0].payload["proposal_id"] == "0xabc"
    assert rows[0].payload["space"] == "ens.eth"
    assert rows[0].source_url == "https://snapshot.org/#/ens.eth/proposal/0xabc"


def test_governance_provider_skips_projects_without_space() -> None:
    provider = GovernanceArchiveProvider(space_map={})
    rows = provider.collect((_case(),))
    assert rows == ()


def test_coingecko_provider_does_not_fabricate_evidence_on_out_of_range_error(monkeypatch) -> None:
    monkeypatch.setattr(
        providers_module,
        "_get_json",
        lambda url: {"error": {"status": {"error_code": 10012, "error_message": "exceeds allowed time range"}}},
    )

    provider = CoinGeckoHistoricalProvider(id_map={"ethereum": "ethereum"})
    rows = provider.collect((_case(),))

    assert rows == ()


def test_coingecko_provider_does_not_fabricate_evidence_on_empty_market_data(monkeypatch) -> None:
    monkeypatch.setattr(providers_module, "_get_json", lambda url: {"market_data": {}, "symbol": "eth"})

    provider = CoinGeckoHistoricalProvider(id_map={"ethereum": "ethereum"})
    rows = provider.collect((_case(),))

    assert rows == ()


def test_defillama_provider_merges_fees_and_revenue_history(monkeypatch) -> None:
    protocol_payload = {
        "category": "Lending",
        "chains": ["Ethereum"],
        "parentProtocol": None,
        "tvl": [{"date": int(datetime(2022, 6, 14, tzinfo=UTC).timestamp()), "totalLiquidityUSD": 900.0}],
    }
    fees_payload = {"totalDataChart": [[int(datetime(2022, 6, 14, tzinfo=UTC).timestamp()), 12.5]]}
    revenue_payload = {"totalDataChart": [[int(datetime(2022, 6, 14, tzinfo=UTC).timestamp()), 6.25]]}

    def fake_get(url: str):
        if "summary/fees" in url and "dataType=dailyRevenue" in url:
            return revenue_payload
        if "summary/fees" in url:
            return fees_payload
        return protocol_payload

    monkeypatch.setattr(providers_module, "_get_json", fake_get)

    provider = DefiLlamaHistoricalProvider(slug_map={"ethereum": "aave"})
    rows = provider.collect((_case(),))

    assert len(rows) == 1
    assert rows[0].payload["daily_fees_usd"] == 12.5
    assert rows[0].payload["daily_revenue_usd"] == 6.25
    assert rows[0].payload["tvl"] == 900.0
