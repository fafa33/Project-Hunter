from __future__ import annotations

from datetime import UTC, datetime

from hunter.acquisition.models import EvidenceValidation, NormalizedEvidence
from hunter.acquisition.repositories import InMemoryAcquisitionRepository
from hunter.market_validation.acquisition_sources import acquisition_engine_sources
from hunter.market_validation.models import ProjectValidationTarget
from hunter.market_validation.runner import EvidenceBackedProjectExecutor

NOW = datetime(2026, 7, 11, tzinfo=UTC)


def test_coingecko_profile_cannot_populate_valuation_family() -> None:
    repository = InMemoryAcquisitionRepository()
    evidence = _evidence(
        "cg-1",
        provider="coingecko",
        metric="coingecko_market_profile",
        repository_id="coingecko:bitcoin",
        normalized_metrics={"mandatory_completeness": 1.0, "schema_completeness": 0.8},
    )
    repository.save_normalized((evidence,))
    repository.save_validations((_validation("cg-1"),))

    sources = acquisition_engine_sources(repository, as_of=NOW)
    result = _execute(sources)
    source_by_engine = {source.engine: source for source in result.engine_sources}

    for engine in ("valuation", "comparative_valuation", "mispricing", "asymmetry"):
        assert getattr(result, engine) == 0.0
        assert source_by_engine[engine].status == "UNAVAILABLE"
        assert source_by_engine[engine].evidence_ids == ()
        assert f"contract_unavailable:{engine}" in source_by_engine[engine].warnings
        assert engine in result.missing_evidence
    assert result.committee_decision == "INSUFFICIENT_EVIDENCE"


def test_missing_evidence_returns_unavailable_without_provider_access() -> None:
    sources = acquisition_engine_sources(InMemoryAcquisitionRepository(), as_of=NOW)
    result = _execute(sources)

    assert result.valuation == 0.0
    assert next(source for source in result.engine_sources if source.engine == "valuation").status == "UNAVAILABLE"


def test_repository_only_sources_connect_developer_protocol_risk_and_validation_health() -> None:
    repository = InMemoryAcquisitionRepository()
    github = _evidence(
        "gh-1",
        provider="github",
        metric="github_repository_profile",
        repository_id="github:bitcoin:bitcoin/bitcoin",
        normalized_metrics={"has_commits": 1.0, "has_contributors": 1.0, "has_releases": 0.0},
    )
    protocol = _evidence(
        "dl-1",
        provider="defillama",
        metric="defillama_protocol_profile",
        repository_id="defillama:bitcoin",
        normalized_metrics={"schema_completeness": 1.0},
    )
    repository.save_normalized((github, protocol))
    repository.save_validations((_validation("gh-1"), _validation("dl-1")))

    result = _execute(acquisition_engine_sources(repository, as_of=NOW))
    source_by_engine = {source.engine: source for source in result.engine_sources}

    assert source_by_engine["developer"].status == "AVAILABLE"
    assert source_by_engine["protocol"].status == "AVAILABLE"
    assert source_by_engine["risk"].evidence_ids == ("dl-1",)
    assert source_by_engine["validation_health"].evidence_ids == ("dl-1", "gh-1")


def test_different_coingecko_profile_values_remain_unavailable_and_deterministic() -> None:
    low = _run_with_market_score(0.25)
    high = _run_with_market_score(0.75)
    repeated = _run_with_market_score(0.75)

    assert low.valuation == high.valuation == 0.0
    assert high.valuation == repeated.valuation
    assert high.result_id == repeated.result_id


def test_invalid_evidence_is_not_promoted() -> None:
    repository = InMemoryAcquisitionRepository()
    repository.save_normalized(
        (
            _evidence(
                "cg-invalid",
                provider="coingecko",
                metric="coingecko_market_profile",
                repository_id="coingecko:bitcoin",
                normalized_metrics={"mandatory_completeness": 1.0},
            ),
        )
    )
    repository.save_validations((_validation("cg-invalid", status="invalid"),))

    assert acquisition_engine_sources(repository, as_of=NOW) == {}


def _run_with_market_score(score: float):
    repository = InMemoryAcquisitionRepository()
    repository.save_normalized(
        (
            _evidence(
                f"cg-{score}",
                provider="coingecko",
                metric="coingecko_market_profile",
                repository_id="coingecko:bitcoin",
                normalized_metrics={"market_score": score},
            ),
        )
    )
    repository.save_validations((_validation(f"cg-{score}"),))
    return _execute(acquisition_engine_sources(repository, as_of=NOW))


def _execute(sources):
    return EvidenceBackedProjectExecutor(NOW, sources).execute_project(
        ProjectValidationTarget("bitcoin", "Bitcoin", "store-of-value"),
        run_id="run",
    )


def _evidence(
    evidence_id: str,
    *,
    provider: str,
    metric: str,
    repository_id: str,
    normalized_metrics: dict[str, float],
) -> NormalizedEvidence:
    return NormalizedEvidence(
        evidence_id=evidence_id,
        repository_id=repository_id,
        provider=provider,
        collector=f"{provider}-collector",
        raw_source_id="source",
        domain="market",
        metric=metric,
        target_id="bitcoin",
        value="source",
        raw_metrics={"raw": evidence_id},
        normalized_metrics=normalized_metrics,
        source_url="https://example.test",
        retrieved_at=NOW,
        normalized_at=NOW,
        confidence=1.0,
        freshness=1.0,
        raw_evidence_id=f"raw-{evidence_id}",
    )


def _validation(evidence_id: str, *, status: str = "valid") -> EvidenceValidation:
    return EvidenceValidation(
        evidence_id=evidence_id,
        status=status,  # type: ignore[arg-type]
        validated_at=NOW,
        confidence=1.0,
        freshness=1.0,
    )
