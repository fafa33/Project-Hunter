from __future__ import annotations

from dataclasses import fields
from datetime import UTC, datetime

import pytest

from hunter.sufficiency import (
    AVAILABILITY_STATES,
    DEGRADED_MODE_OUTCOMES,
    DIRECTNESS_VALUES,
    PROXY_SIGNAL_TYPES,
    SUFFICIENCY_STATES,
    DataAvailability,
    DataRequirement,
    DataSufficiencyAssessment,
    DegradedModePolicy,
    ProxySignalPolicy,
    SourceDisagreement,
    data_requirement_id,
    default_data_requirement_registry,
    default_degraded_mode_policy,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def test_phase_one_models_validate_required_fields_and_enums() -> None:
    assert requirement().requirement_kind == "direct_observation"
    assert availability().availability_state == "available"
    assert assessment().sufficiency_state == "sufficient"
    assert disagreement().disagreement_state == "disagreement"

    assert AVAILABILITY_STATES == {"available", "stale", "partial", "unavailable"}
    assert DIRECTNESS_VALUES >= {"direct_observation", "proxy_signal", "derived_from_direct", "unavailable"}
    assert SUFFICIENCY_STATES >= {"sufficient", "degraded", "insufficient", "unavailable"}
    assert DEGRADED_MODE_OUTCOMES >= {"normal", "blocked_insufficient_evidence", "unavailable"}
    assert "market_proxy" in PROXY_SIGNAL_TYPES

    with pytest.raises(ValueError, match="engine_id is required"):
        requirement(engine_id="")
    with pytest.raises(ValueError, match="availability_state must be one of"):
        availability(availability_state="missing")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="sufficiency_state must be one of"):
        assessment(sufficiency_state="recommendation")  # type: ignore[arg-type]


def test_phase_one_proxy_allowed_requirements_must_define_proxy_types() -> None:
    with pytest.raises(ValueError, match="proxy-allowed requirements must define accepted_proxy_types"):
        requirement(proxy_allowed=True, accepted_proxy_types=())

    with pytest.raises(ValueError, match="accepted_proxy_types require proxy_allowed"):
        requirement(proxy_allowed=False, accepted_proxy_types=("market_proxy",))

    assert requirement(proxy_allowed=True, accepted_proxy_types=("market_proxy",)).accepted_proxy_types == (
        "market_proxy",
    )


def test_phase_one_required_direct_observation_cannot_be_satisfied_by_proxy() -> None:
    direct_required = requirement(
        direct_observation_required=True, proxy_allowed=True, accepted_proxy_types=("market_proxy",)
    )
    proxy_available = availability(
        availability_state="available",
        directness="proxy_signal",
        proxy_type="market_proxy",
        evidence_count=1,
    )

    assert direct_required.directness_satisfies_requirement("direct_observation") is True
    assert direct_required.directness_satisfies_requirement("proxy_signal") is False

    policy = default_degraded_mode_policy(effective_at=NOW, recorded_at=NOW)
    decision = policy.decide(requirement=direct_required, availability=proxy_available)

    assert decision.outcome == "blocked_insufficient_evidence"
    assert decision.blocks_output is True
    assert decision.preserves_score is True
    assert decision.treats_missing_as_negative is False
    assert decision.reason == "proxy_signal_cannot_satisfy_required_direct_observation"


def test_phase_one_proxy_signal_policy_cannot_masquerade_as_direct_observation() -> None:
    with pytest.raises(ValueError, match="proxy signals may not satisfy direct observations"):
        proxy_policy(may_satisfy_direct_observation=True)

    policy = proxy_policy()
    assert policy.proxy_type == "market_proxy"
    assert policy.may_satisfy_direct_observation is False
    assert "not direct evidence" in policy.limitation_text


def test_phase_one_availability_keeps_proxy_and_unavailable_states_explicit() -> None:
    with pytest.raises(ValueError, match="proxy_signal directness requires proxy_type"):
        availability(directness="proxy_signal", proxy_type=None)

    with pytest.raises(ValueError, match="proxy_type is only allowed"):
        availability(directness="direct_observation", proxy_type="market_proxy")

    with pytest.raises(ValueError, match="unavailable state requires unavailable directness"):
        availability(availability_state="unavailable", directness="direct_observation", missing_reason="provider down")

    unavailable = availability(
        availability_state="unavailable",
        directness="unavailable",
        proxy_type=None,
        evidence_count=0,
        missing_reason="provider unavailable",
    )
    assert unavailable.missing_reason == "provider unavailable"


def test_phase_one_default_registry_is_versioned_deterministic_and_queryable() -> None:
    first = default_data_requirement_registry(created_at=NOW)
    second = default_data_requirement_registry(created_at=NOW)

    assert first == second
    assert first.registry_version == "data-requirement-registry-v1"
    assert first.degraded_mode_policy.policy_version == "data-sufficiency-policy-v1"
    assert first.by_engine("competitive", analysis_purpose="peer_set_report")
    assert first.proxy_policy("market_proxy").confidence_impact == 0.25
    assert first.get(first.requirements[0].requirement_id) == first.requirements[0]

    with pytest.raises(KeyError, match="unknown requirement_id"):
        first.get("missing")


def test_phase_one_policy_versions_are_stable_and_deterministic() -> None:
    assert data_requirement_id(
        engine_id="competitive",
        analysis_purpose="peer_set_report",
        output_field="evidence_backed_competitors",
        requirement_kind="direct_observation",
        policy_id="policy",
        policy_version="v1",
        schema_version="schema-v1",
    ) == data_requirement_id(
        engine_id="competitive",
        analysis_purpose="peer_set_report",
        output_field="evidence_backed_competitors",
        requirement_kind="direct_observation",
        policy_id="policy",
        policy_version="v1",
        schema_version="schema-v1",
    )

    with pytest.raises(ValueError, match="proxy_for_direct_outcome cannot be normal"):
        DegradedModePolicy(
            policy_id="policy",
            policy_version="v1",
            unavailable_required_outcome="blocked_insufficient_evidence",
            partial_required_outcome="degraded_material_limitation",
            stale_required_outcome="degraded_material_limitation",
            proxy_for_direct_outcome="normal",
            optional_missing_outcome="degraded_non_blocking",
            effective_at=NOW,
            recorded_at=NOW,
            schema_version="schema-v1",
        )


def test_phase_one_models_do_not_embed_sql_authoritative_lineage_lists() -> None:
    forbidden = {
        "source_evidence_ids",
        "supporting_span_ids",
        "claim_ids",
        "conflict_ids",
        "candidate_ids",
    }
    for model in (DataRequirement, DataAvailability, DataSufficiencyAssessment, SourceDisagreement):
        assert forbidden.isdisjoint({field.name for field in fields(model)})


def requirement(**overrides: object) -> DataRequirement:
    values = {
        "requirement_id": "requirement-1",
        "engine_id": "competitive",
        "analysis_purpose": "peer_set_report",
        "output_field": "evidence_backed_competitors",
        "requirement_kind": "direct_observation",
        "evidence_domain": "competitive",
        "required_entity_type": "candidate",
        "required_source_types": ("competitive_relationship", "knowledge_claim"),
        "direct_observation_required": True,
        "proxy_allowed": False,
        "accepted_proxy_types": (),
        "minimum_freshness_seconds": 86_400,
        "minimum_source_authority": "verified_or_persisted_hunter_evidence",
        "minimum_lineage_depth": 1,
        "minimum_confidence": 0.0,
        "historical_required": True,
        "blocking_level": "required_for_high_confidence",
        "policy_id": "data-sufficiency-default-policy",
        "policy_version": "data-sufficiency-policy-v1",
        "effective_at": NOW,
        "recorded_at": NOW,
        "schema_version": "data-sufficiency-v1",
    }
    values.update(overrides)
    return DataRequirement(**values)  # type: ignore[arg-type]


def availability(**overrides: object) -> DataAvailability:
    values = {
        "availability_id": "availability-1",
        "requirement_id": "requirement-1",
        "candidate_id": "candidate-1",
        "engine_id": "competitive",
        "analysis_purpose": "peer_set_report",
        "availability_state": "available",
        "directness": "direct_observation",
        "proxy_type": None,
        "freshness_seconds": 60,
        "source_quality": "high",
        "lineage_complete": True,
        "conflict_state": "none",
        "evidence_count": 1,
        "missing_reason": "",
        "effective_at": NOW,
        "recorded_at": NOW,
        "cutoff_at": None,
        "replay_mode": "current",
        "processing_run_id": "run-1",
        "schema_version": "data-sufficiency-v1",
    }
    values.update(overrides)
    return DataAvailability(**values)  # type: ignore[arg-type]


def assessment(**overrides: object) -> DataSufficiencyAssessment:
    values = {
        "assessment_id": "assessment-1",
        "candidate_id": "candidate-1",
        "engine_id": "competitive",
        "analysis_purpose": "peer_set_report",
        "assessment_scope": "candidate_report",
        "sufficiency_state": "sufficient",
        "degraded_mode": "normal",
        "coverage_score": 1.0,
        "freshness_state": "fresh",
        "source_quality_state": "high",
        "lineage_state": "complete",
        "conflict_state": "none",
        "direct_observation_coverage": 1.0,
        "proxy_signal_coverage": 0.0,
        "material_missing_count": 0,
        "limitations_summary": "",
        "policy_id": "data-sufficiency-default-policy",
        "policy_version": "data-sufficiency-policy-v1",
        "effective_at": NOW,
        "recorded_at": NOW,
        "cutoff_at": None,
        "replay_mode": "current",
        "processing_run_id": "run-1",
        "schema_version": "data-sufficiency-v1",
    }
    values.update(overrides)
    return DataSufficiencyAssessment(**values)  # type: ignore[arg-type]


def disagreement(**overrides: object) -> SourceDisagreement:
    values = {
        "disagreement_id": "disagreement-1",
        "candidate_id": "candidate-1",
        "requirement_id": "requirement-1",
        "engine_id": "competitive",
        "analysis_purpose": "peer_set_report",
        "disagreement_state": "disagreement",
        "compared_source_count": 2,
        "compatible_scope": True,
        "reason": "provider_values_disagree",
        "effective_at": NOW,
        "recorded_at": NOW,
        "replay_mode": "current",
        "processing_run_id": "run-1",
        "schema_version": "data-sufficiency-v1",
    }
    values.update(overrides)
    return SourceDisagreement(**values)  # type: ignore[arg-type]


def proxy_policy(**overrides: object) -> ProxySignalPolicy:
    values = {
        "policy_id": "proxy-policy-1",
        "policy_version": "data-sufficiency-policy-v1",
        "proxy_type": "market_proxy",
        "allowed_requirement_kinds": ("proxy_context", "freshness"),
        "limitation_text": "Market proxy is context only and not direct evidence.",
        "confidence_impact": 0.25,
        "may_satisfy_direct_observation": False,
        "effective_at": NOW,
        "recorded_at": NOW,
        "schema_version": "data-sufficiency-v1",
    }
    values.update(overrides)
    return ProxySignalPolicy(**values)  # type: ignore[arg-type]
