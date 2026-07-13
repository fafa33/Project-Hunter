from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import pytest

from hunter.discovery import CandidateDiscoveryEngine, CandidateLifecycleTransition, CandidateRegistryRepository
from hunter.discovery.automation import discovery_automation_status, install_discovery_jobs
from hunter.discovery.configuration import DiscoveryConfig
from hunter.discovery.providers import DiscoveredCandidate, DiscoveryProvider


@dataclass(frozen=True)
class StaticDiscoveryProvider:
    name: str
    rows: tuple[DiscoveredCandidate, ...]

    def discover(self, *, limit: int) -> tuple[DiscoveredCandidate, ...]:
        return self.rows[:limit]


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
    assert status["installed_jobs"] == 3


def _provider(provider: StaticDiscoveryProvider) -> DiscoveryProvider:
    return provider
