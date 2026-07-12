from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from hunter.acquisition import (
    AcquisitionConfig,
    AcquisitionPipeline,
    AcquisitionRequest,
    CacheConfig,
    CanonicalEvidenceNormalizer,
    EvidenceAcquisitionValidator,
    InMemoryAcquisitionRepository,
    ProviderHealth,
    ProviderMetadata,
    ProviderRegistry,
    RateLimit,
    RawEvidence,
    RetryConfig,
    load_acquisition_config,
)
from hunter.acquisition.collector import ProviderCollector
from hunter.acquisition.configuration import acquisition_config_from_mapping
from hunter.acquisition.exceptions import AcquisitionRegistryError
from hunter.cli import main

NOW = datetime(2026, 7, 11, tzinfo=UTC)


@dataclass
class FixtureProvider:
    metadata: ProviderMetadata
    fail_times: int = 0
    calls: int = 0

    def health(self) -> ProviderHealth:
        return ProviderHealth(
            provider_name=self.metadata.name,
            availability=self.metadata.availability,
            checked_at=NOW,
            last_sync=self.metadata.last_sync,
            message="fixture",
        )

    def fetch(self, request: AcquisitionRequest) -> tuple[RawEvidence, ...]:
        self.calls += 1
        if self.fail_times > 0:
            self.fail_times -= 1
            raise RuntimeError("temporary provider failure")
        suffix = request.checkpoint or "first"
        return (
            RawEvidence(
                provider=self.metadata.name,
                collector="fixture-collector",
                raw_source_id=f"{request.target_id}:{request.metric}:{suffix}",
                domain=request.domain,
                metric=request.metric,
                target_id=request.target_id,
                retrieved_at=request.requested_at,
                payload={"value": 0.72, "confidence": 0.8, "freshness": 0.9},
                source_url="fixture://provider",
            ),
        )


def request(*, mode: str = "incremental", checkpoint: str | None = None) -> AcquisitionRequest:
    return AcquisitionRequest(
        domain="github",
        metric="commit_frequency",
        target_id="bitcoin",
        requested_at=NOW,
        mode=mode,  # type: ignore[arg-type]
        checkpoint=checkpoint,
    )


def provider(*, fail_times: int = 0) -> FixtureProvider:
    return FixtureProvider(
        metadata=ProviderMetadata(
            name="fixture",
            capabilities=("github", "developer"),
            supported_metrics=("commit_frequency",),
            rate_limits=(RateLimit(10, 60),),
            last_sync=NOW - timedelta(hours=1),
            availability="available",
        ),
        fail_times=fail_times,
    )


def test_collector_fetches_raw_data_without_scoring() -> None:
    raw = ProviderCollector().collect(provider(), request())

    assert raw[0].payload["value"] == 0.72
    assert not hasattr(raw[0], "score")
    assert not hasattr(raw[0], "rank")


def test_normalizer_is_deterministic_and_preserves_audit_fields() -> None:
    raw = ProviderCollector().collect(provider(), request())
    normalizer = CanonicalEvidenceNormalizer()

    first = normalizer.normalize(raw, request())
    second = normalizer.normalize(raw, request())

    assert first == second
    assert first[0].provider == "fixture"
    assert first[0].collector == "fixture-collector"
    assert first[0].raw_source_id
    assert first[0].repository_id
    assert first[0].evidence_id


def test_validator_detects_duplicates_stale_invalid_values_and_is_deterministic() -> None:
    raw = ProviderCollector().collect(provider(), request())
    normalized = CanonicalEvidenceNormalizer().normalize((raw[0], raw[0]), request())
    stale_request = AcquisitionRequest(
        domain="github",
        metric="commit_frequency",
        target_id="bitcoin",
        requested_at=NOW - timedelta(days=3),
    )
    stale = CanonicalEvidenceNormalizer().normalize(
        (
            RawEvidence(
                provider="fixture",
                collector="fixture-collector",
                raw_source_id="stale",
                domain="github",
                metric="commit_frequency",
                target_id="bitcoin",
                retrieved_at=NOW - timedelta(days=3),
                payload={"value": 0.5, "confidence": 0.0, "freshness": 0.0},
            ),
        ),
        stale_request,
    )
    validator = EvidenceAcquisitionValidator(stale_after_seconds=60)

    first = validator.validate((*normalized, *stale), as_of=NOW)
    second = validator.validate((*normalized, *stale), as_of=NOW)

    assert first == second
    assert {item.status for item in first} == {"valid", "duplicate", "stale"}
    assert any(issue.code == "confidence" for item in first for issue in item.issues)


def test_provider_health_and_registry_loading() -> None:
    registry = ProviderRegistry()
    fixture = registry.register(provider())

    assert fixture.health().availability == "available"
    assert registry.by_capability("github") == (fixture,)
    assert registry.by_metric("commit_frequency") == (fixture,)
    with pytest.raises(AcquisitionRegistryError):
        registry.register(provider())


def test_retry_cache_incremental_resume_persistence_and_coverage() -> None:
    repo = InMemoryAcquisitionRepository()
    fixture = provider(fail_times=1)
    pipeline = AcquisitionPipeline(
        repository=repo,
        config=AcquisitionConfig(retry=RetryConfig(max_attempts=2), cache=CacheConfig(enabled=True, ttl_seconds=300)),
    )

    first = pipeline.sync(fixture, request())
    second = pipeline.sync(fixture, request())
    resumed = pipeline.sync(fixture, request(mode="resume"))

    assert first.raw_count == 1
    assert second.raw_count == 1
    assert resumed.checkpoint is not None
    assert fixture.calls == 3
    assert len(repo.raw) >= 1
    assert len(repo.normalized) >= 1
    assert len(repo.validations) >= 1
    assert len(repo.history()) == 3
    assert all(item.status == "valid" for item in repo.validations.values())


def test_acquisition_configuration_and_cli_commands_execute() -> None:
    config = load_acquisition_config("configs/acquisition.yaml")
    mapped = acquisition_config_from_mapping(
        {
            "enabled": True,
            "retry": {"max_attempts": 3, "backoff_seconds": 2},
            "cache": {"enabled": True, "ttl_seconds": 10},
            "providers": [
                {
                    "name": "optional-fixture",
                    "enabled": False,
                    "capabilities": ["github"],
                    "supported_metrics": ["stars"],
                }
            ],
        }
    )

    assert config.enabled is True
    assert mapped.retry.max_attempts == 3
    assert mapped.providers[0].enabled is False
    assert main(["acquisition", "status"]) == 0
    assert main(["acquisition", "providers"]) == 0
    assert main(["acquisition", "validate"]) == 0
    assert main(["acquisition", "sync"]) == 0
    assert main(["acquisition", "history"]) == 0
    assert main(["acquisition", "health"]) == 0
