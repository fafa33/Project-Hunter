from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from hunter.sufficiency import (
    ComparableSourceObservation,
    CrossSourceValidationContext,
    CrossSourceValidationService,
    DataRequirement,
    DataSufficiencyRepository,
    ProviderAvailabilityState,
    SourceMetricContext,
    SourceObservation,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)
LATER = datetime(2026, 1, 2, tzinfo=UTC)


def test_phase_four_agreement_records_validation_and_normalized_lineage(tmp_path) -> None:
    output = service().validate(
        requirement(required_source_types=("knowledge_claim", "official_report")),
        context(
            observations=(
                comparable(source_type="knowledge_claim", value=42, evidence_id="evidence-a", span_id="span-a"),
                comparable(source_type="official_report", value=42, evidence_id="evidence-b", claim_id="claim-b"),
            )
        ),
    )

    assert len(output.validations) == 1
    record = output.validations[0]
    assert record.validation.validation_status == "agreement"
    assert record.validation.compatible_scope
    assert record.validation.metadata["project_negative_evidence"] is False
    assert len(record.evidence_links) == 2
    assert not output.disagreements

    repository = DataSufficiencyRepository(tmp_path / "sufficiency.sqlite")
    repository.save_source_validation_with_lineage(
        record.validation,
        evidence_links=record.evidence_links,
        span_links=record.span_links,
        claim_links=record.claim_links,
        conflict_links=record.conflict_links,
    )

    rows = repository.source_validation_results(candidate_id="candidate-1", validation_status="agreement")
    assert rows[0]["validation_status"] == "agreement"
    lineage = repository.lineage("validation", record.validation.validation_id)
    assert {item["source_evidence_id"] for item in lineage["source_evidence"]} == {"evidence-a", "evidence-b"}


def test_phase_four_incompatible_metric_context_does_not_create_false_disagreement() -> None:
    output = service().validate(
        requirement(required_source_types=("defillama", "coingecko")),
        context(
            observations=(
                comparable(source_type="defillama", value=10, metric_context=metric(chain="ethereum")),
                comparable(source_type="coingecko", value=12, metric_context=metric(chain="solana")),
            )
        ),
    )

    assert output.validations[0].validation.validation_status == "incompatible_scope"
    assert not output.validations[0].validation.compatible_scope
    assert output.validations[0].validation.reason == "incompatible_chain"
    assert not output.disagreements


def test_phase_four_disagreement_is_data_quality_not_project_negative_evidence(tmp_path) -> None:
    output = service().validate(
        requirement(required_source_types=("defillama", "coingecko")),
        context(
            observations=(
                comparable(source_type="defillama", value=100),
                comparable(source_type="coingecko", value=112),
            )
        ),
    )

    assert output.validations[0].validation.validation_status == "disagreement"
    assert json.loads(json.dumps(dict(output.validations[0].validation.metadata)))["project_negative_evidence"] is False
    assert output.disagreements[0].disagreement.disagreement_state == "disagreement"
    assert output.disagreements[0].disagreement.reason == "data_quality_state:compatible_sources_disagree"

    repository = DataSufficiencyRepository(tmp_path / "sufficiency.sqlite")
    disagreement = output.disagreements[0]
    repository.save_disagreement_with_lineage(
        disagreement.disagreement,
        evidence_links=disagreement.evidence_links,
        span_links=disagreement.span_links,
        claim_links=disagreement.claim_links,
        conflict_links=disagreement.conflict_links,
    )

    rows = repository.disagreements(candidate_id="candidate-1")
    assert rows[0]["disagreement_state"] == "disagreement"
    assert rows[0]["compatible_scope"] == 1
    assert len(repository.lineage("disagreement", rows[0]["disagreement_id"])["source_evidence"]) == 2


def test_phase_four_distinguishes_provider_unavailable_stale_and_conflict() -> None:
    req = requirement(required_source_types=("defillama", "coingecko"), minimum_freshness_seconds=60)

    missing_provider = service().validate(
        req,
        context(
            provider_states=(
                ProviderAvailabilityState(
                    source_type="defillama",
                    status="unavailable",
                    checked_at=NOW,
                    recorded_at=NOW,
                ),
            )
        ),
    )
    stale = service().validate(
        req,
        context(observations=(comparable(source_type="defillama", effective_at=NOW - timedelta(seconds=120)),)),
    )
    conflict = service().validate(
        req,
        context(
            observations=(
                comparable(source_type="defillama", conflict_state="conflicted", conflict_id="conflict-1"),
                comparable(source_type="coingecko"),
            )
        ),
    )

    assert missing_provider.validations[0].validation.validation_status == "missing_provider"
    assert stale.validations[0].validation.validation_status == "stale_source"
    assert conflict.validations[0].validation.validation_status == "conflict"
    assert conflict.validations[0].conflict_links[0].conflict_id == "conflict-1"


def test_phase_four_replay_and_lifecycle_are_source_authority_safe() -> None:
    req = requirement(required_source_types=("defillama", "coingecko"))
    later_recorded = comparable(source_type="defillama", recorded_at=LATER)
    inactive = comparable(source_type="coingecko", lifecycle_state="retracted")

    strict = service().validate(
        req,
        context(
            observations=(later_recorded, inactive),
            cutoff_at=NOW,
            replay_mode="historical_strict_known_by_hunter",
            evaluated_at=LATER,
        ),
    )
    reconstructed = service().validate(
        req,
        context(
            observations=(later_recorded, comparable(source_type="coingecko", recorded_at=LATER)),
            cutoff_at=NOW,
            replay_mode="reconstructed_after_cutoff",
            evaluated_at=LATER,
        ),
    )

    assert strict.validations[0].validation.validation_status == "unavailable"
    assert reconstructed.validations[0].validation.validation_status == "agreement"
    assert reconstructed.validations[0].validation.replay_mode == "reconstructed_after_cutoff"


def test_phase_four_proxy_observations_do_not_conceal_missing_direct_data() -> None:
    req = requirement(
        required_source_types=("knowledge_claim",),
        direct_observation_required=True,
        proxy_allowed=True,
        accepted_proxy_types=("market_proxy",),
    )
    output = service().validate(
        req,
        context(
            observations=(
                comparable(
                    source_type="knowledge_claim",
                    directness="proxy_signal",
                    proxy_type="market_proxy",
                ),
            )
        ),
    )

    assert output.validations[0].validation.validation_status == "unavailable"
    assert output.validations[0].validation.reason == "direct_observation_missing_proxy_only"
    assert not output.disagreements


def service() -> CrossSourceValidationService:
    return CrossSourceValidationService()


def context(**overrides: object) -> CrossSourceValidationContext:
    values = {
        "candidate_id": "candidate-1",
        "observations": (),
        "provider_states": (),
        "evaluated_at": NOW,
        "cutoff_at": None,
        "replay_mode": "current",
        "processing_run_id": "run-1",
    }
    values.update(overrides)
    return CrossSourceValidationContext(**values)  # type: ignore[arg-type]


def comparable(**overrides: object) -> ComparableSourceObservation:
    observation_overrides = {
        key: overrides.pop(key)
        for key in tuple(overrides)
        if key
        in {
            "source_type",
            "effective_at",
            "recorded_at",
            "source_quality",
            "lineage_depth",
            "lifecycle_state",
            "directness",
            "proxy_type",
            "conflict_state",
            "evidence_id",
            "span_id",
            "claim_id",
            "conflict_id",
        }
    }
    values = {
        "observation": observation(**observation_overrides),
        "metric_context": metric(),
        "value": 42,
    }
    values.update(overrides)
    return ComparableSourceObservation(**values)  # type: ignore[arg-type]


def observation(**overrides: object) -> SourceObservation:
    values = {
        "observation_id": f"observation-{overrides.get('source_type', 'source')}",
        "candidate_id": "candidate-1",
        "source_type": "knowledge_claim",
        "evidence_domain": "evidence_intelligence",
        "directness": "direct_observation",
        "effective_at": NOW,
        "recorded_at": NOW,
        "confidence": 0.9,
        "source_quality": "high",
        "lineage_depth": 1,
        "lifecycle_state": "active",
        "proxy_type": None,
        "conflict_state": "none",
        "evidence_id": f"evidence-{overrides.get('source_type', 'source')}",
        "span_id": None,
        "claim_id": None,
        "conflict_id": None,
    }
    values.update(overrides)
    return SourceObservation(**values)  # type: ignore[arg-type]


def metric(**overrides: object) -> SourceMetricContext:
    values = {
        "metric_name": "tvl",
        "unit": "usd",
        "scope": "protocol",
        "chain": "ethereum",
        "product": "protocol-total",
        "period_start": NOW,
        "period_end": NOW,
    }
    values.update(overrides)
    return SourceMetricContext(**values)  # type: ignore[arg-type]


def requirement(
    *,
    required_source_types: tuple[str, ...],
    direct_observation_required: bool = True,
    proxy_allowed: bool = False,
    accepted_proxy_types: tuple[str, ...] = (),
    minimum_freshness_seconds: int = 86_400,
) -> DataRequirement:
    return DataRequirement(
        requirement_id="requirement-evidence-cross-source-tvl",
        engine_id="evidence_intelligence",
        analysis_purpose="cross_source_validation",
        output_field="tvl",
        requirement_kind="direct_observation" if direct_observation_required else "proxy_context",
        evidence_domain="evidence_intelligence",
        required_entity_type="candidate",
        required_source_types=required_source_types,
        direct_observation_required=direct_observation_required,
        proxy_allowed=proxy_allowed,
        accepted_proxy_types=accepted_proxy_types,
        minimum_freshness_seconds=minimum_freshness_seconds,
        minimum_source_authority="medium",
        minimum_lineage_depth=1,
        minimum_confidence=0.0,
        historical_required=True,
        blocking_level="required_for_output",
        policy_id="data-sufficiency-default-policy",
        policy_version="data-sufficiency-policy-v1",
        effective_at=NOW,
        recorded_at=NOW,
        schema_version="data-sufficiency-v1",
    )
