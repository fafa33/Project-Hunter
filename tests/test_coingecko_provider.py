from __future__ import annotations

from datetime import UTC, datetime

from hunter.acquisition import AcquisitionConfig, AcquisitionPipeline, AcquisitionRequest, CacheConfig, RetryConfig
from hunter.acquisition.project_identifiers import (
    ProjectIdentifier,
    coingecko_sync_ids,
    coingecko_target_map,
    resolve_configured_identifiers,
)
from hunter.acquisition.providers.coingecko import (
    CoinGeckoEvidenceNormalizer,
    CoinGeckoHTTPError,
    CoinGeckoProvider,
    CoinGeckoProviderConfig,
    CoinGeckoRateLimiter,
)
from hunter.acquisition.repositories import InMemoryAcquisitionRepository
from hunter.acquisition.validator import EvidenceAcquisitionValidator
from hunter.cli import _coingecko_persistent_statistics, main

NOW = datetime(2026, 7, 11, tzinfo=UTC)


class FakeCoinGeckoTransport:
    def __init__(
        self,
        *,
        fail_once: bool = False,
        retry_after: float | None = None,
        fail_details: bool = False,
        fail_markets: bool = False,
    ) -> None:
        self.fail_once = fail_once
        self.retry_after = retry_after
        self.fail_details = fail_details
        self.fail_markets = fail_markets
        self.calls: list[tuple[str, dict[str, object]]] = []

    def get_json(self, path: str, params: dict[str, object]) -> object:
        self.calls.append((path, dict(params)))
        if self.fail_once:
            self.fail_once = False
            raise CoinGeckoHTTPError(429, "rate limited", retry_after=self.retry_after)
        if path == "/coins/markets":
            if self.fail_markets:
                raise CoinGeckoHTTPError(429, "market rate limited")
            ids = str(params.get("ids", "bitcoin")).split(",")
            page = int(str(params.get("page", 1)))
            return [_market(coin_id.strip(), page=page) for coin_id in ids if coin_id.strip()]
        if self.fail_details:
            raise CoinGeckoHTTPError(429, "detail rate limited")
        coin_id = path.rsplit("/", 1)[-1]
        return _detail(coin_id)


def request(
    project_ids: tuple[str, ...] = ("bitcoin",),
    *,
    mode: str = "incremental",
    checkpoint: str | None = None,
    detail_cache: dict[str, dict[str, object]] | None = None,
) -> AcquisitionRequest:
    parameters: dict[str, object] = {"project_ids": project_ids}
    if detail_cache is not None:
        parameters["detail_cache"] = detail_cache
    return AcquisitionRequest(
        domain="market",
        metric="coingecko_market_profile",
        target_id="configured-projects",
        requested_at=NOW,
        mode=mode,  # type: ignore[arg-type]
        checkpoint=checkpoint,
        parameters=parameters,
    )


def provider(
    transport: FakeCoinGeckoTransport,
    *,
    per_page: int = 250,
    min_interval_seconds: float = 0.0,
) -> CoinGeckoProvider:
    return CoinGeckoProvider(
        CoinGeckoProviderConfig(
            per_page=per_page,
            max_attempts=2,
            backoff_seconds=0,
            min_interval_seconds=min_interval_seconds,
        ),
        transport=transport,
        rate_limiter=CoinGeckoRateLimiter(min_interval_seconds=min_interval_seconds),
        sleeper=lambda _delay: None,
    )


def test_coingecko_api_parsing_and_deterministic_normalization() -> None:
    transport = FakeCoinGeckoTransport()
    raw = provider(transport).fetch(request())
    normalized = CoinGeckoEvidenceNormalizer().normalize(raw, request())
    market = next(item for item in raw if item.metric == "coingecko_market_profile")

    assert market.payload["id"] == "bitcoin"
    assert market.payload["symbol"] == "btc"
    assert market.payload["categories"] == ("Layer 1", "Store of Value")
    assert market.payload["github_links"] == ("https://github.com/bitcoin/bitcoin",)
    assert normalized == CoinGeckoEvidenceNormalizer().normalize(raw, request())
    assert any(item.repository_id == "coingecko:bitcoin" for item in normalized)
    assert any(item.confidence > 0.8 for item in normalized)


def test_coingecko_pagination_retry_rate_limiting_cache_persistence_and_resume() -> None:
    transport = FakeCoinGeckoTransport(fail_once=True)
    limiter = CoinGeckoRateLimiter(min_interval_seconds=1.0)
    coingecko = CoinGeckoProvider(
        CoinGeckoProviderConfig(per_page=1, max_attempts=2, backoff_seconds=0, min_interval_seconds=1.0),
        transport=transport,
        rate_limiter=limiter,
        sleeper=lambda _delay: None,
    )
    repository = InMemoryAcquisitionRepository()
    pipeline = AcquisitionPipeline(
        normalizer=CoinGeckoEvidenceNormalizer(),
        repository=repository,
        config=AcquisitionConfig(retry=RetryConfig(max_attempts=1), cache=CacheConfig(enabled=True, ttl_seconds=300)),
    )

    first = pipeline.sync(coingecko, request(("bitcoin", "ethereum")))
    second = pipeline.sync(coingecko, request(("bitcoin", "ethereum")))
    resumed = pipeline.sync(coingecko, request(("bitcoin", "ethereum"), mode="resume"))

    assert first.raw_count == 4
    assert second.raw_count == 4
    assert resumed.raw_count == 2
    assert resumed.checkpoint is not None
    assert resumed.checkpoint.cursor == "page:2"
    assert len(repository.raw) == 4
    assert len(repository.normalized) == 4
    assert len(repository.validations) == 4
    assert len(repository.history()) == 3
    assert any(path == "/coins/markets" for path, _ in transport.calls)
    assert limiter.delays


def test_coingecko_uses_retry_after_before_exponential_backoff() -> None:
    sleeps: list[float] = []
    coingecko = CoinGeckoProvider(
        CoinGeckoProviderConfig(per_page=1, max_attempts=2, backoff_seconds=3, jitter_seconds=1),
        transport=FakeCoinGeckoTransport(fail_once=True, retry_after=7),
        sleeper=sleeps.append,
    )

    raw = coingecko.fetch(request())

    assert sum(1 for item in raw if item.metric == "coingecko_market_profile") == 1
    assert sleeps == [7]
    assert coingecko.statistics.rate_limit_count == 1
    assert coingecko.statistics.retry_count == 1


def test_coingecko_accepts_missing_optional_detail_metadata_when_mandatory_market_fields_exist() -> None:
    raw = provider(FakeCoinGeckoTransport(fail_details=True)).fetch(request())
    normalized = CoinGeckoEvidenceNormalizer().normalize(raw, request())
    validations = EvidenceAcquisitionValidator(minimum_confidence=1.0).validate(normalized, as_of=NOW)
    market = next(item for item in raw if item.metric == "coingecko_market_profile")
    market_normalized = next(item for item in normalized if item.metric == "coingecko_market_profile")

    assert market.payload["detail_available"] is False
    assert market_normalized.confidence == 1.0
    assert market_normalized.normalized_metrics["optional_completeness"] < 1.0
    assert {item.status for item in validations} == {"valid"}


def test_coingecko_reuses_detail_metadata_until_ttl_expires() -> None:
    cache = {
        "bitcoin": {
            "retrieved_at": NOW.isoformat(),
            "payload": {
                "id": "bitcoin",
                "categories": ("Cached",),
                "homepage": ("https://cached.example",),
                "github_links": ("https://github.com/cached/bitcoin",),
                "developer_links": ("https://github.com/cached/bitcoin",),
                "last_updated": "2026-07-11T00:00:00Z",
            },
        }
    }
    transport = FakeCoinGeckoTransport()
    coingecko = provider(transport)

    raw = coingecko.fetch(request(detail_cache=cache))
    market = next(item for item in raw if item.metric == "coingecko_market_profile")

    assert market.payload["categories"] == ("Cached",)
    assert [path for path, _ in transport.calls] == ["/coins/markets"]
    assert coingecko.statistics.cached_detail_count == 1


def test_coingecko_refreshes_expired_detail_metadata() -> None:
    cache = {
        "bitcoin": {
            "retrieved_at": "2026-06-01T00:00:00+00:00",
            "payload": {"id": "bitcoin", "last_updated": "2026-06-01T00:00:00Z"},
        }
    }
    transport = FakeCoinGeckoTransport()

    raw = provider(transport).fetch(request(detail_cache=cache))

    assert any(item.metric == "coingecko_detail_metadata" for item in raw)
    assert any(path == "/coins/bitcoin" for path, _ in transport.calls)


def test_coingecko_degraded_mode_defers_detail_enrichment_without_rejecting_market() -> None:
    transport = FakeCoinGeckoTransport(fail_details=True)
    coingecko = CoinGeckoProvider(
        CoinGeckoProviderConfig(detail_rate_limit_threshold=1),
        transport=transport,
        sleeper=lambda _delay: None,
    )

    raw = coingecko.fetch(request(("bitcoin", "ethereum")))

    assert sum(1 for item in raw if item.metric == "coingecko_market_profile") == 2
    assert sum(1 for item in raw if item.metric == "coingecko_pending_detail_enrichment") == 2
    assert sum(1 for path, _ in transport.calls if path.startswith("/coins/") and path != "/coins/markets") == 1
    assert coingecko.health().availability == "degraded"


def test_coingecko_full_universe_pagination_and_no_duplicate_detail_requests() -> None:
    transport = FakeCoinGeckoTransport()
    coingecko = provider(transport, per_page=2)

    raw = coingecko.fetch(request(("bitcoin", "ethereum", "bitcoin", "solana", "cardano")))

    assert sum(1 for path, _ in transport.calls if path == "/coins/markets") == 3
    assert sum(1 for path, _ in transport.calls if path == "/coins/bitcoin") == 1
    assert sum(1 for item in raw if item.metric == "coingecko_market_profile") == 5


def test_coingecko_explicit_id_mapping_targets_hunter_project_slug() -> None:
    raw = provider(FakeCoinGeckoTransport()).fetch(
        AcquisitionRequest(
            domain="market",
            metric="coingecko_market_profile",
            target_id="configured-projects",
            requested_at=NOW,
            parameters={"project_ids": ("render-token",), "target_map": {"render-token": "render"}},
        )
    )
    market = next(item for item in raw if item.metric == "coingecko_market_profile")

    assert market.raw_source_id == "render-token"
    assert market.target_id == "render"
    assert market.repository_id == "coingecko:render"


def test_coingecko_identifier_resolution_rejects_ambiguous_invalid_and_unsupported() -> None:
    resolutions = resolve_configured_identifiers(
        ("render", "ambiguous", "missing", "unsupported"),
        {
            "render": ProjectIdentifier("render", "render-token"),
            "ambiguous": ProjectIdentifier("ambiguous", "token", ambiguous=True),
            "unsupported": ProjectIdentifier("unsupported", unsupported=True),
        },
        {"render-token"},
    )

    assert {item.project_id: item.status for item in resolutions} == {
        "render": "RESOLVED",
        "ambiguous": "AMBIGUOUS",
        "missing": "INVALID_ID",
        "unsupported": "UNSUPPORTED",
    }


def test_coingecko_identifier_resolution_reports_not_found_and_temporary_failures() -> None:
    identifiers = {"render": ProjectIdentifier("render", "render-token")}

    assert resolve_configured_identifiers(("render",), identifiers, set())[0].status == "NOT_FOUND"
    assert (
        resolve_configured_identifiers(("render",), identifiers, set(), rate_limited=True)[0].status == "RATE_LIMITED"
    )
    assert resolve_configured_identifiers(("render",), identifiers, set(), failed=True)[0].status == "REQUEST_FAILED"


def test_coingecko_resolved_identifiers_are_reused_without_silent_slug_guessing() -> None:
    resolutions = resolve_configured_identifiers(
        ("render", "unmapped"),
        {"render": ProjectIdentifier("render", "render-token")},
        {"render-token"},
    )

    assert coingecko_sync_ids(resolutions) == ("render-token",)
    assert coingecko_target_map(resolutions) == {"render-token": "render"}
    assert resolutions[1].status == "INVALID_ID"


def test_coingecko_previous_valid_evidence_survives_later_rate_limit_failure() -> None:
    repository = InMemoryAcquisitionRepository()
    pipeline = AcquisitionPipeline(
        normalizer=CoinGeckoEvidenceNormalizer(),
        repository=repository,
        config=AcquisitionConfig(retry=RetryConfig(max_attempts=1), cache=CacheConfig(enabled=False)),
    )
    first = pipeline.sync(provider(FakeCoinGeckoTransport()), request())
    second = pipeline.sync(provider(FakeCoinGeckoTransport(fail_markets=True), per_page=1), request())

    assert first.valid_count == 2
    assert second.raw_count == 0
    assert any(item.status == "valid" for item in repository.validations.values())


def test_coingecko_coverage_accounting_separates_market_and_detail() -> None:
    repository = InMemoryAcquisitionRepository()
    pipeline = AcquisitionPipeline(
        normalizer=CoinGeckoEvidenceNormalizer(),
        repository=repository,
        config=AcquisitionConfig(retry=RetryConfig(max_attempts=1), cache=CacheConfig(enabled=False)),
    )
    pipeline.sync(provider(FakeCoinGeckoTransport(fail_details=True)), request())
    stats = _coingecko_persistent_statistics(repository)  # type: ignore[arg-type]

    assert stats["market_coverage"] == 100.0
    assert stats["detail_coverage"] == 0.0
    assert stats["pending"] == 1


def test_coingecko_validation_and_duplicate_detection() -> None:
    raw = provider(FakeCoinGeckoTransport()).fetch(request())
    market = next(item for item in raw if item.metric == "coingecko_market_profile")
    normalized = CoinGeckoEvidenceNormalizer().normalize((market, market), request())
    validations = EvidenceAcquisitionValidator().validate(normalized, as_of=NOW)

    assert {item.status for item in validations} == {"valid", "duplicate"}


def test_coingecko_cli_commands_execute_without_enabled_provider(tmp_path) -> None:
    config = tmp_path / "acquisition.yaml"
    config.write_text(
        """
enabled: true
providers:
  - name: coingecko
    enabled: false
    capabilities: [market]
    supported_metrics: [coingecko_market_profile]
""",
        encoding="utf-8",
    )

    assert main(["coingecko", "--acquisition-config", str(config), "status"]) == 0
    assert main(["coingecko", "--acquisition-config", str(config), "validate"]) == 0
    assert main(["coingecko", "--acquisition-config", str(config), "sync"]) == 0
    assert main(["coingecko", "--acquisition-config", str(config), "resume"]) == 0
    assert main(["coingecko", "--acquisition-config", str(config), "health"]) == 0
    assert main(["coingecko", "--acquisition-config", str(config), "statistics"]) == 0
    assert main(["coingecko", "--acquisition-config", str(config), "universe"]) == 0
    assert main(["coingecko", "--acquisition-config", str(config), "unresolved"]) == 0
    assert main(["coingecko", "--acquisition-config", str(config), "resolve"]) == 0


def _market(coin_id: str, *, page: int) -> dict[str, object]:
    symbols = {"bitcoin": "btc", "ethereum": "eth"}
    names = {"bitcoin": "Bitcoin", "ethereum": "Ethereum"}
    return {
        "id": coin_id,
        "symbol": symbols.get(coin_id, coin_id[:3]),
        "name": names.get(coin_id, coin_id.title()),
        "current_price": 100.0,
        "market_cap": 1_000_000.0,
        "market_cap_rank": page,
        "fully_diluted_valuation": 1_200_000.0,
        "total_volume": 50_000.0,
        "circulating_supply": 19_000_000.0,
        "total_supply": 21_000_000.0,
        "max_supply": 21_000_000.0,
        "ath": 120.0,
        "atl": 1.0,
        "last_updated": "2026-07-11T00:00:00Z",
    }


def _detail(coin_id: str) -> dict[str, object]:
    return {
        "id": coin_id,
        "categories": ["Layer 1", "Store of Value"],
        "links": {
            "homepage": [f"https://{coin_id}.org", ""],
            "repos_url": {"github": [f"https://github.com/{coin_id}/{coin_id}"], "bitbucket": []},
        },
        "last_updated": "2026-07-11T00:00:00Z",
    }
