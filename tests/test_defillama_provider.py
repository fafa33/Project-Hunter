from __future__ import annotations

from datetime import UTC, datetime

from hunter.acquisition import AcquisitionConfig, AcquisitionPipeline, AcquisitionRequest, CacheConfig, RetryConfig
from hunter.acquisition.project_identifiers import (
    ProjectIdentifier,
    defillama_sync_ids,
    defillama_target_map,
    resolve_defillama_identifiers,
)
from hunter.acquisition.providers.defillama import (
    DefiLlamaEvidenceNormalizer,
    DefiLlamaHTTPError,
    DefiLlamaProvider,
    DefiLlamaProviderConfig,
    DefiLlamaRateLimiter,
)
from hunter.acquisition.repositories import InMemoryAcquisitionRepository
from hunter.acquisition.validator import EvidenceAcquisitionValidator
from hunter.cli import main

NOW = datetime(2026, 7, 11, tzinfo=UTC)


class FakeDefiLlamaTransport:
    def __init__(self, *, fail_once: bool = False) -> None:
        self.fail_once = fail_once
        self.calls: list[tuple[str, dict[str, object]]] = []

    def get_json(self, path: str, params: dict[str, object] | None = None) -> object:
        self.calls.append((path, dict(params or {})))
        if self.fail_once:
            self.fail_once = False
            raise DefiLlamaHTTPError(429, "rate limited", retry_after=0)
        if path == "/protocols":
            return [{"slug": "aave", "name": "Aave"}, {"slug": "uniswap", "name": "Uniswap"}]
        if path.startswith("/protocol/"):
            return _protocol(path.rsplit("/", 1)[-1])
        if path.startswith("/summary/fees/"):
            if dict(params or {}).get("dataType") == "dailyRevenue":
                return _revenue()
            return _fees()
        raise DefiLlamaHTTPError(404, "not found")


def request(
    slugs: tuple[str, ...] = ("aave",),
    *,
    mode: str = "incremental",
    checkpoint: str | None = None,
) -> AcquisitionRequest:
    return AcquisitionRequest(
        domain="protocol",
        metric="defillama_protocol_profile",
        target_id="configured-projects",
        requested_at=NOW,
        mode=mode,  # type: ignore[arg-type]
        checkpoint=checkpoint,
        parameters={"project_ids": slugs, "target_map": {"aave": "aave", "uniswap": "uniswap"}},
    )


def provider(transport: FakeDefiLlamaTransport, *, min_interval_seconds: float = 0.0) -> DefiLlamaProvider:
    return DefiLlamaProvider(
        DefiLlamaProviderConfig(max_attempts=2, backoff_seconds=0, min_interval_seconds=min_interval_seconds),
        transport=transport,
        rate_limiter=DefiLlamaRateLimiter(min_interval_seconds=min_interval_seconds),
        sleeper=lambda _delay: None,
    )


def test_defillama_api_tvl_revenue_fee_parsing_and_normalization() -> None:
    raw = provider(FakeDefiLlamaTransport()).fetch(request())
    normalized = DefiLlamaEvidenceNormalizer().normalize(raw, request())

    assert raw[0].payload["protocol_name"] == "Aave"
    assert raw[0].payload["protocol_slug"] == "aave"
    assert raw[0].payload["tvl"] == 125.0
    assert raw[0].payload["daily_fees"] == 4.0
    assert raw[0].payload["daily_revenue"] == 2.0
    assert raw[0].payload["chain_list"] == ("Ethereum", "Polygon")
    assert normalized == DefiLlamaEvidenceNormalizer().normalize(raw, request())
    assert normalized[0].confidence == 1.0


def test_defillama_mapping_validation_and_invalid_slug_rejection() -> None:
    resolutions = resolve_defillama_identifiers(
        ("aave", "missing", "unsupported"),
        {
            "aave": ProjectIdentifier("aave", defillama_slug="aave"),
            "unsupported": ProjectIdentifier("unsupported", defillama_unsupported=True),
        },
        {"aave"},
    )

    assert {item.project_id: item.status for item in resolutions} == {
        "aave": "RESOLVED",
        "missing": "INVALID_ID",
        "unsupported": "UNSUPPORTED",
    }
    assert defillama_sync_ids(resolutions) == ("aave",)
    assert defillama_target_map(resolutions) == {"aave": "aave"}


def test_defillama_persistence_resume_cache_retry_and_duplicate_detection() -> None:
    transport = FakeDefiLlamaTransport(fail_once=True)
    repository = InMemoryAcquisitionRepository()
    pipeline = AcquisitionPipeline(
        normalizer=DefiLlamaEvidenceNormalizer(),
        repository=repository,
        config=AcquisitionConfig(retry=RetryConfig(max_attempts=1), cache=CacheConfig(enabled=True, ttl_seconds=300)),
    )
    defi = provider(transport)

    first = pipeline.sync(defi, request(("aave", "uniswap")))
    second = pipeline.sync(defi, request(("aave", "uniswap")))
    resumed = pipeline.sync(defi, request(("aave", "uniswap"), mode="resume"))
    duplicate_validations = EvidenceAcquisitionValidator().validate(
        DefiLlamaEvidenceNormalizer().normalize(
            (next(iter(repository.raw.values())), next(iter(repository.raw.values()))), request()
        ),
        as_of=NOW,
    )

    assert first.raw_count == 2
    assert second.raw_count == 2
    assert resumed.raw_count == 1
    assert len(repository.raw) == 2
    assert len(repository.normalized) == 2
    assert len(repository.validations) == 2
    assert {item.status for item in duplicate_validations} == {"valid", "duplicate"}


def test_defillama_cli_commands_execute_without_enabled_provider(tmp_path) -> None:
    config = tmp_path / "acquisition.yaml"
    config.write_text(
        """
enabled: true
providers:
  - name: defillama
    enabled: false
    capabilities: [protocol]
    supported_metrics: [defillama_protocol_profile]
""",
        encoding="utf-8",
    )

    assert main(["defillama", "--acquisition-config", str(config), "status"]) == 0
    assert main(["defillama", "--acquisition-config", str(config), "validate"]) == 0
    assert main(["defillama", "--acquisition-config", str(config), "sync"]) == 0
    assert main(["defillama", "--acquisition-config", str(config), "unresolved"]) == 0
    assert main(["defillama", "--acquisition-config", str(config), "resolve"]) == 0


def _protocol(slug: str) -> dict[str, object]:
    return {
        "name": "Aave" if slug == "aave" else "Uniswap",
        "slug": slug,
        "symbol": "AAVE" if slug == "aave" else "UNI",
        "category": "Lending" if slug == "aave" else "Dexes",
        "parentProtocol": None,
        "chains": ["Ethereum", "Polygon"],
        "tvl": [
            {"date": 1_783_728_000, "totalLiquidityUSD": 100.0},
            {"date": 1_783_814_400, "totalLiquidityUSD": 125.0},
        ],
    }


def _fees() -> dict[str, object]:
    return {"dailyFees": 4.0, "monthlyFees": 120.0, "totalAllTime": 1_000.0}


def _revenue() -> dict[str, object]:
    return {"dailyRevenue": 2.0, "monthlyRevenue": 60.0, "totalAllTime": 500.0}
