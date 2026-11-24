from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

import pytest

from hunter.acquisition.exceptions import ProviderUnavailableError
from hunter.discovery import (
    CandidateDiscoveryEngine,
    CandidateIdentityResolutionEngine,
    CandidateLifecycleTransition,
    CandidateRegistryRepository,
)
from hunter.discovery.automation import discovery_automation_status, install_discovery_jobs
from hunter.discovery.configuration import DiscoveryConfig, DiscoveryProviderConfig
from hunter.discovery.providers import (
    DexScreenerDiscoveryProvider,
    DiscoveredCandidate,
    DiscoveryProvider,
    GeckoTerminalDiscoveryProvider,
    _request_json,
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


def test_identity_resolution_exact_for_configured_seed(tmp_path) -> None:
    repository = CandidateRegistryRepository(tmp_path / "candidates.sqlite")
    config = DiscoveryConfig(
        registry_path=str(tmp_path / "candidates.sqlite"),
        market_validation_config="configs/market_validation.yaml",
        project_identifiers_config="configs/project_identifiers.yaml",
        providers={},
    )
    CandidateDiscoveryEngine(repository, config, providers={}).sync(provider="seed")

    summary = CandidateIdentityResolutionEngine(repository).resolve_all()
    aave = repository.get_by_slug("aave")
    result = repository.latest_identity_by_candidate()[aave.candidate_id]

    assert summary.by_outcome["exact"] == 50
    assert result.outcome == "exact"
    assert result.confidence == 1.0
    assert repository.get_by_slug("aave").identity_resolution_status == "exact"


def test_identity_resolution_probable_for_single_contract_candidate(tmp_path) -> None:
    repository = CandidateRegistryRepository(tmp_path / "candidates.sqlite")
    config = DiscoveryConfig(registry_path=str(tmp_path / "candidates.sqlite"), providers={})
    provider = StaticDiscoveryProvider(
        "geckoterminal",
        (
            DiscoveredCandidate(
                provider="geckoterminal",
                provider_id="eth_0xabc",
                slug="eth-0xabc",
                name="Contract Token",
                symbol="CT",
                primary_chain="eth",
                candidate_type="token",
                metadata={"chain": "eth", "contract_address": "0xabc", "reserve_usd": "1000"},
            ),
        ),
    )
    engine = CandidateDiscoveryEngine(repository, config, providers={"geckoterminal": _provider(provider)})
    engine.sync(provider="geckoterminal", limit=10)

    CandidateIdentityResolutionEngine(repository).resolve_all()
    candidate = repository.find_by_identifier("contract:eth", "0xabc")
    result = repository.latest_identity_by_candidate()[candidate.candidate_id]

    assert result.outcome == "probable"
    assert repository.get(candidate.candidate_id).lifecycle_status == "identified"


def test_identity_resolution_ambiguous_for_ticker_collision(tmp_path) -> None:
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

    summary = CandidateIdentityResolutionEngine(repository).resolve_all()

    assert summary.by_outcome["ambiguous"] == 2
    assert len(repository.conflicts()) == 2
    assert all(result.outcome == "ambiguous" for result in repository.identity_results())


def test_identity_resolution_conflict_for_provider_disagreement(tmp_path) -> None:
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
        metadata={"chain": "eth", "contract_address": "0xabc", "reserve_usd": "2000"},
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

    CandidateIdentityResolutionEngine(repository).resolve_all()
    result = repository.identity_results()[0]

    assert result.outcome == "conflict"
    assert "provider_metadata_disagreement" in result.conflicts


def test_identity_resolution_rejected_and_unresolved_outcomes(tmp_path) -> None:
    repository = CandidateRegistryRepository(tmp_path / "candidates.sqlite")
    config = DiscoveryConfig(registry_path=str(tmp_path / "candidates.sqlite"), providers={})
    provider = StaticDiscoveryProvider(
        "thin",
        (
            DiscoveredCandidate(
                provider="thin",
                provider_id="bad-token",
                slug="bad-token",
                name="Bad Token",
                symbol="BAD",
                candidate_type="token",
                metadata={"blocked": True},
            ),
            DiscoveredCandidate(
                provider="thin",
                provider_id="unknown-token",
                slug="unknown-token",
                name="Unknown Token",
                symbol="UNK",
                candidate_type="unknown",
            ),
        ),
    )
    CandidateDiscoveryEngine(repository, config, providers={"thin": _provider(provider)}).sync(
        provider="thin", limit=10
    )

    summary = CandidateIdentityResolutionEngine(repository).resolve_all()

    assert summary.by_outcome["rejected"] == 1
    assert summary.by_outcome["unresolved"] == 1


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
    assert second_metadata["contract_address"] == "So111"
    assert rows[1].provider_id == "solana:So111"


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
        return _JsonResponse(
            payloads.get(path, [])
            if path.startswith(("/token-pairs/v1/", "/api/v2/networks/", "/api/v3/coins/"))
            else payloads[path]
        )

    return opener


def test_request_json_honors_bounded_retry_after_on_http_429(monkeypatch) -> None:
    sleeps: list[float] = []
    calls: list[str] = []

    def opener(request: urllib.request.Request, timeout: int) -> _JsonResponse:
        calls.append(request.full_url)
        if len(calls) == 1:
            raise urllib.error.HTTPError(
                request.full_url,
                429,
                "Too Many Requests",
                {"Retry-After": "90"},
                None,
            )
        return _JsonResponse({"ok": True})

    monkeypatch.setattr(urllib.request, "urlopen", opener)
    payload = _request_json(
        "https://example.invalid/api/v3/coins/markets",
        provider="CoinGecko",
        timeout_seconds=1,
        max_attempts=2,
        backoff_seconds=0.001,
        sleeper=sleeps.append,
        max_retry_after_seconds=5.0,
    )

    assert payload == {"ok": True}
    assert len(calls) == 2
    assert sleeps == [5.0]  # provider window honored, capped at the configured ceiling


def test_solana_case_sensitive_addresses_survive_discovery_and_registry(tmp_path, monkeypatch) -> None:
    addresses = ("AbC123", "aBc123")
    payload = {
        "data": [],
        "included": [
            {"id": f"solana_{address}", "type": "token", "attributes": {"name": "Token", "address": address}}
            for address in addresses
        ],
    }
    monkeypatch.setattr(urllib.request, "urlopen", _urlopen(payload))
    gecko = GeckoTerminalDiscoveryProvider().discover(limit=10)
    assert [(row.metadata or {})["contract_address"] for row in gecko] == list(addresses)
    assert len({row.slug for row in gecko}) == 2
    profiles = {
        "/token-profiles/latest/v1": [{"chainId": "solana", "tokenAddress": address} for address in addresses],
        "/token-boosts/top/v1": [],
    }
    monkeypatch.setattr(urllib.request, "urlopen", _urlopen_by_path(profiles))
    dex = DexScreenerDiscoveryProvider().discover(limit=10)
    assert [(row.metadata or {})["contract_address"] for row in dex] == list(addresses)
    repository = CandidateRegistryRepository(tmp_path / "candidates.sqlite")
    engine = CandidateDiscoveryEngine(
        repository,
        DiscoveryConfig(providers={}),
        providers={
            "geckoterminal": StaticDiscoveryProvider("geckoterminal", gecko),
            "dexscreener": StaticDiscoveryProvider("dexscreener", dex),
        },
    )
    for _ in range(2):
        engine.sync(provider="geckoterminal", limit=10)
        engine.sync(provider="dexscreener", limit=10)
    assert repository.stats().total_candidates == 2
    for address in addresses:
        candidate = repository.find_by_identifier("contract:solana", address)
        assert candidate is not None
        assert {source.provider for source in candidate.sources} == {"geckoterminal", "dexscreener"}
    assert repository.find_by_identifier("contract:solana", "abc123") is None


def test_dex_market_evidence_matches_exact_base_contract(monkeypatch) -> None:
    profiles = {
        "/token-profiles/latest/v1": [
            {"chainId": "solana", "tokenAddress": "AbC", "links": [{"url": "https://example.invalid"}]}
        ],
        "/token-boosts/top/v1": [],
    }

    def pair(address, liquidity, chain="solana"):
        return {
            "chainId": chain,
            "pairAddress": address,
            "baseToken": {"address": address, "name": "Real", "symbol": "REAL"},
            "liquidity": {"usd": liquidity},
            "volume": {"h24": 42},
            "marketCap": 1000,
            "fdv": 2000,
        }

    profiles["/token-pairs/v1/solana/AbC"] = [
        pair("abc", 9999),
        pair("AbC", 9999, "base"),
        pair("AbC", 100),
        pair("AbC", 200),
    ]
    monkeypatch.setattr(urllib.request, "urlopen", _urlopen_by_path(profiles))
    row = DexScreenerDiscoveryProvider().discover(limit=1)[0]
    assert row.name == "Real" and row.symbol == "REAL"
    assert row.metadata is not None
    assert row.metadata["liquidity_usd"] == 200
    assert row.metadata["volume_usd"] == 42
    assert row.metadata["market_cap"] == 1000
    assert row.metadata["fdv"] == 2000
    assert row.metadata["claimed_links"] == ("https://example.invalid",)
    assert "official_links" not in row.metadata


def test_paid_boost_is_not_market_evidence(tmp_path) -> None:
    from hunter.discovery.engine import _has_market_or_protocol_measure
    from hunter.discovery.identity import _has_official_domain

    repository = CandidateRegistryRepository(tmp_path / "candidates.sqlite")
    engine = CandidateDiscoveryEngine(repository, DiscoveryConfig(providers={}))
    row = DiscoveredCandidate(
        provider="dexscreener",
        provider_id="solana:AbC",
        slug="token",
        name="Token",
        metadata={
            "chain": "solana",
            "contract_address": "AbC",
            "boost_amount": 100,
            "official_links": ["https://example.invalid"],
        },
    )
    from datetime import UTC, datetime

    candidate = engine._provider_candidate(row, observed_at=datetime.now(UTC))
    assert not _has_market_or_protocol_measure(candidate)
    assert not _has_official_domain(candidate)


def test_quote_pair_does_not_inherit_base_valuation(monkeypatch) -> None:
    payload = [
        {
            "chainId": "solana",
            "pairAddress": "pair",
            "baseToken": {"address": "Other"},
            "quoteToken": {"address": "AbC", "name": "Quote"},
            "liquidity": {"usd": 100},
            "marketCap": 900,
            "priceUsd": "2",
            "fdv": 1000,
        }
    ]
    monkeypatch.setattr(urllib.request, "urlopen", _urlopen(payload))
    evidence = DexScreenerDiscoveryProvider()._market_evidence("solana", "AbC")
    assert evidence["market_pair_side"] == "quote"
    assert evidence["market_cap"] is None and evidence["price_usd"] is None and evidence["fdv"] is None
    assert evidence["liquidity_usd"] == 100


def test_gecko_fallback_and_contract_bound_link_corroboration(monkeypatch) -> None:
    payloads = {
        "/api/v2/networks/solana/tokens/AbC": {
            "data": {"attributes": {"address": "AbC", "total_reserve_in_usd": "100", "market_cap_usd": "200"}}
        },
        "/api/v3/coins/solana/contract/AbC": {
            "platforms": {"solana": "AbC"},
            "links": {"homepage": ["https://www.example.org", "https://x.com/other"]},
        },
    }
    monkeypatch.setattr(urllib.request, "urlopen", _urlopen_by_path(payloads))
    provider = DexScreenerDiscoveryProvider()
    assert provider._gecko_evidence("solana", "AbC")["market_cap"] == 200
    assert provider._corroborated_links(
        "solana", "AbC", ("https://example.org", "https://x.com/fake", "https://example.org.evil.net")
    )["verified_official_links"] == ("https://example.org",)
    payloads["/api/v3/coins/solana/contract/AbC"] = {"platforms": {"solana": "abc"}}
    assert not provider._corroborated_links("solana", "AbC", ("https://example.org",))


def test_market_scan_resumes_persisted_page_and_wraps(tmp_path, monkeypatch) -> None:
    from hunter.discovery.providers import CoinGeckoDiscoveryProvider  # noqa: PLC0415

    pages = []

    def fetch(self, path, params, meter=None):
        page = params["page"]
        pages.append(page)
        return [{"id": f"asset-{page}-{i}", "name": f"Asset {page}-{i}"} for i in range(2)] if page < 3 else []

    monkeypatch.setattr(CoinGeckoDiscoveryProvider, "_get", fetch)
    repository = CandidateRegistryRepository(tmp_path / "candidates.sqlite")
    config = DiscoveryConfig(providers={})
    provider = CoinGeckoDiscoveryProvider(per_page=2)
    # One bounded page per normal cycle.
    from unittest.mock import patch

    with patch.object(DiscoveryConfig, "provider") as configured:
        configured.return_value.limit = 2
        for _ in range(4):
            engine = CandidateDiscoveryEngine(repository, config, providers={"coingecko": provider})
            engine.sync(provider="coingecko")
    assert pages == [1, 2, 3, 1]
    assert repository.stats().total_candidates == 4
    assert repository.discovery_page_cursor("coingecko") == 1


class _FakeGeckoApi:
    def __init__(self, catalogues: dict[int, list[dict[str, object]]], pools: dict[tuple[str, int], object]):
        self.catalogues = catalogues
        self.pools = pools
        self.calls: list[tuple[str, int]] = []

    def __call__(self, provider: object, path: str, params: dict[str, object], meter: object = None) -> object:
        if meter is not None:
            meter.consume(time.sleep)
        page = int(str(params.get("page", 1)))
        self.calls.append((path, page))
        if path == "/networks":
            return {
                "data": self.catalogues.get(page, []),
                "links": {"next": page + 1} if page < max(self.catalogues, default=0) else None,
            }
        if path == "/networks/trending_pools":
            return self.pools.get(("trending", page), _gecko_pool_payload("eth", "0xtrending", "0xtoken1"))
        if path.startswith("/networks/") and path.endswith("/pools"):
            network = path.split("/")[2]
            return self.pools.get((network, page), {"data": [], "included": []})
        raise AssertionError(f"unexpected path: {path}")


def _gecko_pool_payload(network: str, pool_address: str, token_address: str) -> dict[str, object]:
    quote = "So111" if network == "solana" else "0xweth"
    return {
        "data": [
            {
                "attributes": {"address": pool_address, "name": "Pool", "reserve_in_usd": "100"},
                "relationships": {
                    "base_token": {"data": {"id": f"{network}_{token_address}"}},
                    "quote_token": {"data": {"id": f"{network}_{quote}"}},
                    "dex": {"data": {"id": "dex"}},
                },
            }
        ],
        "included": [
            {
                "id": f"{network}_{token_address}",
                "type": "token",
                "attributes": {"name": "Base Token", "symbol": "BASE", "address": token_address},
            },
            {
                "id": f"{network}_{quote}",
                "type": "token",
                "attributes": {"name": "Quote Token", "symbol": "QUOTE", "address": quote},
            },
        ],
    }


def _candidate_metadata(row: DiscoveredCandidate) -> dict[str, Any]:
    return row.metadata or {}


def test_geckoterminal_enumerates_network_catalogue_and_resumes_pages(tmp_path, monkeypatch) -> None:
    from hunter.discovery.providers import ScanBudget

    api = _FakeGeckoApi(
        {1: [{"id": "eth"}], 2: [{"id": "solana"}]},
        {
            ("eth", 1): _gecko_pool_payload("eth", "0xpool1", "0xtoken1"),
            ("solana", 1): _gecko_pool_payload("solana", "pool1", "TokenA"),
        },
    )
    monkeypatch.setattr(
        GeckoTerminalDiscoveryProvider,
        "_get",
        lambda source, path, params, meter=None: api(source, path, params, meter),
    )
    provider = GeckoTerminalDiscoveryProvider()
    budget = ScanBudget(max_requests=3)

    first = provider.discover_scan(state=None, budget=budget)
    second = provider.discover_scan(state=first.state, budget=budget)

    assert first.status == "partial"  # bounded by the request budget
    assert [_candidate_metadata(row)["chain"] for row in first.candidates] == ["eth", "eth"]
    assert ("/networks", 2) in api.calls  # second catalogue page enumerated after resume
    assert ("/networks/solana/pools", 1) in api.calls
    assert second.state["passes"] >= 1  # catalogue exhausted and a new pass started
    assert api.calls[0] == ("/networks", 1)
    assert ("/networks/eth/pools", 1) in api.calls
    assert (
        len(
            {
                (_candidate_metadata(row)["chain"], _candidate_metadata(row)["contract_address"])
                for row in first.candidates + second.candidates
            }
        )
        >= 2
    )


def test_geckoterminal_checkpoint_recovers_and_detects_new_pools(tmp_path, monkeypatch) -> None:
    api = _FakeGeckoApi(
        {1: [{"id": "eth"}]},
        {("eth", 1): _gecko_pool_payload("eth", "0xpool1", "0xtoken1")},
    )
    monkeypatch.setattr(
        GeckoTerminalDiscoveryProvider,
        "_get",
        lambda source, path, params, meter=None: api(source, path, params, meter),
    )
    repository = CandidateRegistryRepository(tmp_path / "candidates.sqlite")
    config = DiscoveryConfig(
        providers={
            "geckoterminal": DiscoveryProviderConfig(enabled=True, max_requests_per_cycle=3),
        }
    )
    first_engine = CandidateDiscoveryEngine(
        repository, config, providers={"geckoterminal": GeckoTerminalDiscoveryProvider()}
    )
    first = first_engine.sync(provider="geckoterminal")
    first_state = repository.scan_state("geckoterminal")

    # A fresh engine and a fresh provider must resume from the persisted state.
    second_engine = CandidateDiscoveryEngine(
        repository, config, providers={"geckoterminal": GeckoTerminalDiscoveryProvider()}
    )
    second = second_engine.sync(provider="geckoterminal")
    second_state = repository.scan_state("geckoterminal")

    assert first_state is not None and second_state is not None
    assert second_state["passes"] == first_state["passes"] + 1
    assert second_state["catalogue_page"] == first_state["catalogue_page"]
    assert second_state["observed_pools"] >= first_state["observed_pools"]
    assert first.candidates_created == 2
    assert second.candidates_created == 0
    assert len(repository.pool_observations()) == 1
    assert second_state["new_pools"] == 0
    assert first.status in {"succeeded", "partial"} and second.status in {"succeeded", "partial"}

    api.pools[("eth", 2)] = _gecko_pool_payload("eth", "0xpool2", "0xtoken2")
    third = second_engine.sync(provider="geckoterminal")
    assert len(repository.pool_observations()) == 2
    third_state = repository.scan_state("geckoterminal")
    assert third_state is not None
    assert third_state["new_pools"] == 1
    assert third.candidates_created == 1  # only the new pool's base token is new


def test_geckoterminal_rate_budget_and_failure_recovery_keep_partial_state(monkeypatch) -> None:
    from hunter.discovery.providers import ScanBudget

    api = _FakeGeckoApi(
        {1: [{"id": "eth"}]},
        {("eth", 1): _gecko_pool_payload("eth", "0xpool1", "0xtoken1")},
    )
    monkeypatch.setattr(
        GeckoTerminalDiscoveryProvider,
        "_get",
        lambda source, path, params, meter=None: api(source, path, params, meter),
    )
    provider = GeckoTerminalDiscoveryProvider()

    bounded = provider.discover_scan(state=None, budget=ScanBudget(max_requests=2))
    assert bounded.requests == 2
    assert bounded.status == "partial"
    assert len(bounded.candidates) == 2
    assert bounded.state["pool_page"] == 2

    def failing(provider: object, path: str, params: dict[str, object], meter: object = None) -> object:
        if path == "/networks":
            return api(provider, path, params, meter)
        raise ProviderUnavailableError("rate limited")

    monkeypatch.setattr(GeckoTerminalDiscoveryProvider, "_get", failing)
    recovered = provider.discover_scan(state=None, budget=ScanBudget(max_requests=4))
    assert recovered.status == "partial"
    assert recovered.candidates == ()
    assert recovered.error is not None
    assert recovered.state["last_error"] == recovered.error
    assert recovered.state["pool_page"] == 1


def test_geckoterminal_contract_dedup_across_pool_pages(monkeypatch) -> None:
    from hunter.discovery.providers import ScanBudget

    api = _FakeGeckoApi(
        {1: [{"id": "eth"}]},
        {
            ("eth", 1): _gecko_pool_payload("eth", "0xpool1", "0xtoken1"),
            ("eth", 2): _gecko_pool_payload("eth", "0xpool2", "0xtoken1"),
        },
    )
    monkeypatch.setattr(
        GeckoTerminalDiscoveryProvider,
        "_get",
        lambda source, path, params, meter=None: api(source, path, params, meter),
    )
    result = GeckoTerminalDiscoveryProvider().discover_scan(state=None, budget=ScanBudget(max_requests=5))
    contracts = [
        (_candidate_metadata(row)["chain"], _candidate_metadata(row)["contract_address"]) for row in result.candidates
    ]
    assert len(contracts) == len(set(contracts))
    assert ("eth", "0xweth") in contracts


def test_universe_enumeration_measures_real_coverage_without_fabrication(tmp_path, monkeypatch) -> None:
    from hunter.discovery.providers import CoinGeckoDiscoveryProvider

    def fake_get(self: object, path: str, params: dict[str, object], meter: object = None) -> object:
        if path == "/coins/list":
            return [{"id": "bitcoin"}, {"id": "aave"}, {"id": "unseen"}]
        page = int(str(params.get("page", 1)))
        return [{"id": "bitcoin", "name": "Bitcoin"}, {"id": "aave", "name": "Aave"}] if page == 1 else []

    monkeypatch.setattr(CoinGeckoDiscoveryProvider, "_get", fake_get)
    repository = CandidateRegistryRepository(tmp_path / "candidates.sqlite")
    config = DiscoveryConfig(providers={"coingecko": DiscoveryProviderConfig(enabled=True, limit=2)})
    engine = CandidateDiscoveryEngine(repository, config, providers={"coingecko": CoinGeckoDiscoveryProvider()})

    unmeasured = cast(dict[str, Any], engine.global_coverage())
    assert unmeasured["universe"]["coverage"] is None
    assert unmeasured["networks"]["coverage"] is None
    assert unmeasured["pools"]["coverage"] is None
    assert {item["provider"] for item in unmeasured["unsupported_scope"]} == {
        "dexscreener",
        "defillama",
        "geckoterminal",
    }

    universe = engine.enumerate_universe()
    engine.sync(provider="coingecko", limit=2)
    measured = cast(dict[str, Any], engine.global_coverage())
    assert universe["status"] == "succeeded" and universe["asset_count"] == 3
    assert repository.universe_assets("coingecko") == ("aave", "bitcoin", "unseen")
    assert measured["universe"]["accessible_assets"] == 3
    assert measured["universe"]["matched_assets"] == 2
    assert measured["universe"]["coverage"] == round(2 / 3, 6)
    assert measured["universe"]["observed_at"] is not None
    assert measured["freshness"]["newest_observation"] is not None


def test_trending_feed_is_distinct_from_global_enumeration(monkeypatch) -> None:
    from hunter.discovery.providers import ScanBudget

    api = _FakeGeckoApi({1: [{"id": "eth"}]}, {("eth", 1): _gecko_pool_payload("eth", "0xpool1", "0xtoken1")})
    monkeypatch.setattr(
        GeckoTerminalDiscoveryProvider,
        "_get",
        lambda source, path, params, meter=None: api(source, path, params, meter),
    )
    provider = GeckoTerminalDiscoveryProvider()

    trending = provider.discover(limit=10)
    enumerated = provider.discover_scan(state=None, budget=ScanBudget(max_requests=3))

    assert trending and all(_candidate_metadata(row)["discovery_mode"] == "trending_feed" for row in trending)
    assert enumerated.candidates and all(
        _candidate_metadata(row)["discovery_mode"] == "network_pool_enumeration" for row in enumerated.candidates
    )
