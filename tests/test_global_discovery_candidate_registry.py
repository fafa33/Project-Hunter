from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest

from hunter.acquisition.exceptions import ProviderUnavailableError
from hunter.discovery import CandidateDiscoveryEngine, CandidateLifecycleTransition, CandidateRegistryRepository
from hunter.discovery.automation import discovery_automation_status, install_discovery_jobs
from hunter.discovery.configuration import DiscoveryConfig
from hunter.discovery.providers import (
    DexScreenerDiscoveryProvider,
    DiscoveredCandidate,
    DiscoveryProvider,
    GeckoTerminalDiscoveryProvider,
)


@dataclass(frozen=True)
class StaticDiscoveryProvider:
    name: str
    rows: tuple[DiscoveredCandidate, ...]

    def discover(self, *, limit: int) -> tuple[DiscoveredCandidate, ...]:
        return self.rows[:limit]


@dataclass(frozen=True)
class FailingDiscoveryProvider:
    name: str = "coingecko"

    def discover(self, *, limit: int) -> tuple[DiscoveredCandidate, ...]:
        raise ProviderUnavailableError("provider unavailable")


def test_configured_universe_seeds_candidate_registry(tmp_path) -> None:
    repository = CandidateRegistryRepository(tmp_path / "candidates.sqlite")
    config = DiscoveryConfig(
        registry_path=str(tmp_path / "candidates.sqlite"),
        market_validation_config="configs/market_validation.yaml",
        project_identifiers_config="configs/project_identifiers.yaml",
        providers={},
    )
    run = CandidateDiscoveryEngine(repository, config, providers={}).sync(provider="seed")

    stats = repository.stats()
    assert run.status == "succeeded"
    assert run.candidates_seen == 50
    assert stats.total_candidates == 50
    assert stats.configured_candidates == 50
    assert stats.by_status == {"analyzable": 50}
    assert repository.find_by_identifier("coingecko", "worldcoin-wld").slug == "worldcoin"


def test_provider_discovery_is_incremental_and_idempotent(tmp_path) -> None:
    repository = CandidateRegistryRepository(tmp_path / "candidates.sqlite")
    config = DiscoveryConfig(registry_path=str(tmp_path / "candidates.sqlite"), providers={})
    provider = StaticDiscoveryProvider(
        "coingecko",
        (
            DiscoveredCandidate(
                provider="coingecko",
                provider_id="new-protocol",
                slug="new-protocol",
                name="New Protocol",
                symbol="NEW",
                candidate_type="token",
                source_url="https://example.invalid/new-protocol",
            ),
        ),
    )
    engine = CandidateDiscoveryEngine(repository, config, providers={"coingecko": _provider(provider)})

    first = engine.sync(provider="coingecko", limit=10)
    second = engine.sync(provider="coingecko", limit=10)

    assert first.candidates_created == 1
    assert second.candidates_created == 0
    assert second.candidates_updated == 1
    stats = repository.stats()
    assert stats.total_candidates == 1
    assert stats.by_status == {"screenable": 1}
    assert repository.find_by_identifier("coingecko", "new-protocol").slug == "new-protocol"
    assert repository.find_by_identifier("coingecko_symbol", "NEW") is None


def test_provider_failure_does_not_corrupt_registry(tmp_path) -> None:
    repository = CandidateRegistryRepository(tmp_path / "candidates.sqlite")
    config = DiscoveryConfig(registry_path=str(tmp_path / "candidates.sqlite"), providers={})
    engine = CandidateDiscoveryEngine(repository, config, providers={"coingecko": FailingDiscoveryProvider()})

    run = engine.sync(provider="coingecko", limit=10)

    assert run.status == "unavailable"
    assert repository.stats().total_candidates == 0
    assert repository.runs()[0].status == "unavailable"


def test_provider_candidate_merges_with_seed_by_slug(tmp_path) -> None:
    repository = CandidateRegistryRepository(tmp_path / "candidates.sqlite")
    config = DiscoveryConfig(
        registry_path=str(tmp_path / "candidates.sqlite"),
        market_validation_config="configs/market_validation.yaml",
        project_identifiers_config="configs/project_identifiers.yaml",
        providers={},
    )
    provider = StaticDiscoveryProvider(
        "coingecko",
        (
            DiscoveredCandidate(
                provider="coingecko",
                provider_id="aave",
                slug="aave",
                name="Aave",
                symbol="AAVE",
                candidate_type="token",
                source_url="https://example.invalid/aave",
            ),
        ),
    )
    engine = CandidateDiscoveryEngine(repository, config, providers={"coingecko": _provider(provider)})

    engine.sync(provider="seed")
    engine.sync(provider="coingecko", limit=1)

    aave = repository.get_by_slug("aave")
    assert aave is not None
    assert aave.lifecycle_status == "analyzable"
    assert {item.namespace for item in aave.identifiers} >= {"hunter_project", "coingecko", "github_repository"}
    assert repository.stats().total_candidates == 50


def test_provider_identifier_merge_preserves_seed_canonical_slug(tmp_path) -> None:
    repository = CandidateRegistryRepository(tmp_path / "candidates.sqlite")
    config = DiscoveryConfig(
        registry_path=str(tmp_path / "candidates.sqlite"),
        market_validation_config="configs/market_validation.yaml",
        project_identifiers_config="configs/project_identifiers.yaml",
        providers={},
    )
    provider = StaticDiscoveryProvider(
        "defillama",
        (
            DiscoveredCandidate(
                provider="defillama",
                provider_id="aave-v3",
                slug="aave-v3",
                name="Aave V3",
                symbol="AAVE",
                sector="Lending",
                candidate_type="protocol",
                metadata={"tvl": 1000},
            ),
        ),
    )
    engine = CandidateDiscoveryEngine(repository, config, providers={"defillama": _provider(provider)})

    engine.sync(provider="seed")
    engine.sync(provider="defillama", limit=1)

    aave = repository.get_by_slug("aave")
    assert aave is not None
    assert aave.candidate_type == "project"
    assert aave.sector == "defi"
    assert repository.get_by_slug("aave-v3") is None
    assert repository.find_by_identifier("defillama", "aave-v3").slug == "aave"


def test_ticker_collision_does_not_merge_candidates(tmp_path) -> None:
    repository = CandidateRegistryRepository(tmp_path / "candidates.sqlite")
    config = DiscoveryConfig(registry_path=str(tmp_path / "candidates.sqlite"), providers={})
    provider = StaticDiscoveryProvider(
        "coingecko",
        (
            DiscoveredCandidate(
                provider="coingecko",
                provider_id="alpha-token",
                slug="alpha-token",
                name="Alpha Token",
                symbol="SAME",
                candidate_type="token",
                metadata={"market_cap": 100},
            ),
            DiscoveredCandidate(
                provider="coingecko",
                provider_id="beta-token",
                slug="beta-token",
                name="Beta Token",
                symbol="SAME",
                candidate_type="token",
                metadata={"market_cap": 200},
            ),
        ),
    )

    CandidateDiscoveryEngine(repository, config, providers={"coingecko": _provider(provider)}).sync(
        provider="coingecko", limit=10
    )

    assert repository.stats().total_candidates == 2
    assert repository.get_by_slug("alpha-token").candidate_id != repository.get_by_slug("beta-token").candidate_id


def test_contract_identity_merges_market_sources_without_symbol_merge(tmp_path) -> None:
    repository = CandidateRegistryRepository(tmp_path / "candidates.sqlite")
    config = DiscoveryConfig(registry_path=str(tmp_path / "candidates.sqlite"), providers={})
    gecko = DiscoveredCandidate(
        provider="geckoterminal",
        provider_id="eth_0xabc",
        slug="eth-0xabc",
        name="Shared Token",
        symbol="SHARED",
        primary_chain="eth",
        candidate_type="token",
        metadata={"chain": "eth", "contract_address": "0xabc", "reserve_usd": "1000"},
    )
    screener = DiscoveredCandidate(
        provider="dexscreener",
        provider_id="eth:0xabc",
        slug="eth-0xabc",
        name="Shared Token profile",
        symbol="SHARED",
        primary_chain="eth",
        candidate_type="token",
        metadata={"chain": "eth", "contract_address": "0xabc", "boost_amount": 10},
    )
    engine = CandidateDiscoveryEngine(
        repository,
        config,
        providers={
            "geckoterminal": StaticDiscoveryProvider("geckoterminal", (gecko,)),
            "dexscreener": StaticDiscoveryProvider("dexscreener", (screener,)),
        },
    )

    engine.sync(provider="geckoterminal", limit=10)
    engine.sync(provider="dexscreener", limit=10)

    assert repository.stats().total_candidates == 1
    candidate = repository.find_by_identifier("contract:eth", "0xabc")
    assert candidate is not None
    assert {source.provider for source in candidate.sources} == {"geckoterminal", "dexscreener"}


def test_geckoterminal_provider_normalizes_trending_pool_tokens(monkeypatch) -> None:
    payload = {
        "data": [
            {
                "attributes": {
                    "address": "0xpool",
                    "name": "TOKEN / WETH",
                    "reserve_in_usd": "1200000",
                    "volume_usd": {"h24": "450000"},
                },
                "relationships": {
                    "base_token": {"data": {"id": "eth_0xtoken"}},
                    "quote_token": {"data": {"id": "eth_0xweth"}},
                    "dex": {"data": {"id": "uniswap_v3"}},
                },
            }
        ],
        "included": [
            {
                "id": "eth_0xtoken",
                "type": "token",
                "attributes": {"name": "Token", "symbol": "TOKEN", "address": "0xToken"},
            },
            {
                "id": "eth_0xweth",
                "type": "token",
                "attributes": {"name": "Wrapped Ether", "symbol": "WETH", "address": "0xWeth"},
            },
        ],
    }
    monkeypatch.setattr(urllib.request, "urlopen", _urlopen(payload))

    rows = GeckoTerminalDiscoveryProvider(backoff_seconds=0).discover(limit=10)

    assert len(rows) == 2
    assert rows[0].provider == "geckoterminal"
    assert rows[0].primary_chain == "eth"
    first_metadata = rows[0].metadata or {}
    assert first_metadata["contract_address"] == "0xtoken"
    assert first_metadata["reserve_usd"] == "1200000"


def test_dexscreener_provider_normalizes_profiles_and_boosts(monkeypatch) -> None:
    payloads = {
        "/token-profiles/latest/v1": [
            {
                "chainId": "base",
                "tokenAddress": "0xToken",
                "url": "https://dexscreener.com/base/0xtoken",
                "description": "Base Token",
                "links": [{"url": "https://example.invalid"}],
            }
        ],
        "/token-boosts/top/v1": [
            {
                "chainId": "solana",
                "tokenAddress": "So111",
                "url": "https://dexscreener.com/solana/so111",
                "description": "Solana Token",
                "amount": 10,
                "totalAmount": 20,
            }
        ],
    }
    monkeypatch.setattr(urllib.request, "urlopen", _urlopen_by_path(payloads))

    rows = DexScreenerDiscoveryProvider(backoff_seconds=0).discover(limit=10)

    assert len(rows) == 2
    assert rows[0].provider == "dexscreener"
    assert rows[0].primary_chain == "base"
    first_metadata = rows[0].metadata or {}
    second_metadata = rows[1].metadata or {}
    assert first_metadata["contract_address"] == "0xtoken"
    assert second_metadata["boost_amount"] == 10


def test_registry_has_indexed_lookup_surfaces(tmp_path) -> None:
    repository = CandidateRegistryRepository(tmp_path / "candidates.sqlite")
    indexes = set(repository.index_names())

    assert "sqlite_autoindex_candidates_2" in indexes or "candidates_status_idx" in indexes
    assert "identifiers_candidate_idx" in indexes


def test_screening_and_queue_are_deterministic_and_idempotent(tmp_path) -> None:
    repository = CandidateRegistryRepository(tmp_path / "candidates.sqlite")
    config = DiscoveryConfig(
        registry_path=str(tmp_path / "candidates.sqlite"),
        market_validation_config="configs/market_validation.yaml",
        project_identifiers_config="configs/project_identifiers.yaml",
        providers={},
    )
    engine = CandidateDiscoveryEngine(repository, config, providers={})

    engine.sync(provider="seed")
    first_screening = engine.screen_candidates()
    first_queue = engine.refresh_queue()
    second_queue = engine.refresh_queue()

    assert len(first_screening) == 50
    assert len(first_queue) == 50
    assert [entry.queue_entry_id for entry in first_queue] == [entry.queue_entry_id for entry in second_queue]
    assert first_queue[0].eligible_for_deep_analysis is True


def test_screening_defers_and_rejects_low_quality_candidates(tmp_path) -> None:
    repository = CandidateRegistryRepository(tmp_path / "candidates.sqlite")
    config = DiscoveryConfig(registry_path=str(tmp_path / "candidates.sqlite"), providers={})
    provider = StaticDiscoveryProvider(
        "coingecko",
        (
            DiscoveredCandidate(
                provider="coingecko",
                provider_id="thin-evidence",
                slug="thin-evidence",
                name="Thin Evidence",
                symbol="THIN",
                candidate_type="token",
            ),
            DiscoveredCandidate(
                provider="coingecko",
                provider_id="blocked-evidence",
                slug="blocked-evidence",
                name="Blocked Evidence",
                symbol="BAD",
                candidate_type="token",
                metadata={"market_cap": 1000, "blocked": True},
            ),
        ),
    )
    engine = CandidateDiscoveryEngine(repository, config, providers={"coingecko": _provider(provider)})

    engine.sync(provider="coingecko", limit=10)
    results = {repository.get(result.candidate_id).slug: result for result in engine.screen_candidates()}

    assert results["thin-evidence"].status == "deferred"
    assert results["blocked-evidence"].status == "rejected"


def test_large_candidate_import_uses_idempotent_registry_writes(tmp_path) -> None:
    repository = CandidateRegistryRepository(tmp_path / "candidates.sqlite")
    observed_at = datetime.now(tz=UTC)
    candidates = tuple(
        CandidateDiscoveryEngine(repository, DiscoveryConfig(), providers={})._provider_candidate(
            DiscoveredCandidate(
                provider="coingecko",
                provider_id=f"asset-{index}",
                slug=f"asset-{index}",
                name=f"Asset {index}",
                symbol=f"A{index}",
                candidate_type="token",
                metadata={"market_cap": index + 1},
            ),
            observed_at=observed_at,
        )
        for index in range(1500)
    )

    first = repository.upsert_many(candidates)
    second = repository.upsert_many(candidates)

    assert first == (1500, 0)
    assert second == (0, 1500)
    assert repository.stats().total_candidates == 1500


def test_lifecycle_transition_validation_and_persistence(tmp_path) -> None:
    repository = CandidateRegistryRepository(tmp_path / "candidates.sqlite")
    config = DiscoveryConfig(
        registry_path=str(tmp_path / "candidates.sqlite"),
        market_validation_config="configs/market_validation.yaml",
        project_identifiers_config="configs/project_identifiers.yaml",
        providers={},
    )
    CandidateDiscoveryEngine(repository, config, providers={}).sync(provider="seed")
    candidate = repository.get_by_slug("aave")
    assert candidate is not None

    with pytest.raises(ValueError):
        repository.transition_lifecycle(
            CandidateLifecycleTransition(
                transition_id="bad-transition",
                candidate_id=candidate.candidate_id,
                previous_state="analyzable",
                new_state="screenable",
                transitioned_at=datetime.now(tz=UTC),
                reason="invalid backwards transition",
                supporting_evidence_ids=(),
                discovery_run_id="test",
            )
        )

    repository.transition_lifecycle(
        CandidateLifecycleTransition(
            transition_id="valid-transition",
            candidate_id=candidate.candidate_id,
            previous_state="analyzable",
            new_state="ranked",
            transitioned_at=datetime.now(tz=UTC),
            reason="queued for deeper analysis",
            supporting_evidence_ids=(),
            discovery_run_id="test",
        )
    )
    assert repository.get_by_slug("aave").lifecycle_status == "ranked"


def test_discovery_automation_install_is_idempotent(tmp_path) -> None:
    config_path = tmp_path / "automation.yaml"

    first = install_discovery_jobs(config_path)
    second = install_discovery_jobs(config_path)
    status = discovery_automation_status(config_path)

    assert first == second
    assert status["installed_jobs"] == 4
    assert status["expected_jobs"] == 4


def _provider(provider: StaticDiscoveryProvider) -> DiscoveryProvider:
    return provider


class _JsonResponse:
    def __init__(self, payload: object) -> None:
        self.payload = payload

    def __enter__(self) -> _JsonResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def _urlopen(payload: object):
    def opener(request: urllib.request.Request, timeout: int) -> _JsonResponse:
        return _JsonResponse(payload)

    return opener


def _urlopen_by_path(payloads: dict[str, object]):
    def opener(request: urllib.request.Request, timeout: int) -> _JsonResponse:
        path = urllib.parse.urlparse(request.full_url).path
        return _JsonResponse(payloads[path])

    return opener
