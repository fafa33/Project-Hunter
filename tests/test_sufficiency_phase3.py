from __future__ import annotations

from datetime import UTC, datetime, timedelta

from hunter.sufficiency import (
    AvailabilityEvaluationContext,
    AvailabilityEvaluationPolicy,
    CandidateTrustState,
    DataAvailabilityEvaluator,
    DataRequirement,
    DataRequirementSelector,
    DataSufficiencyCheckpoint,
    DataSufficiencyRepository,
    ProviderAvailabilityState,
    SourceObservation,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)
LATER = datetime(2026, 1, 2, tzinfo=UTC)


def test_phase_three_requirement_selection_filters_by_engine_purpose_field_candidate_and_checkpoint(tmp_path) -> None:
    repository = DataSufficiencyRepository(tmp_path / "sufficiency.sqlite")
    repository.save_requirement(requirement("market_cap", engine_id="market_validation", purpose="market_context"))
    repository.save_requirement(requirement("competitors", engine_id="competitive", purpose="peer_set_report"))
    repository.save_checkpoint(
        DataSufficiencyCheckpoint(
            checkpoint_id="checkpoint-1",
            processor_name="availability_refresh",
            target_id="candidate-1",
            cursor="cursor-1",
            updated_at=NOW,
            schema_version="data-sufficiency-v1",
        )
    )

    selector = DataRequirementSelector.from_repository(repository)
    selection = selector.select(
        engine_id="competitive",
        analysis_purpose="peer_set_report",
        output_field="competitors",
        candidate_ids=("candidate-1", "candidate-1"),
        checkpoint_processor="availability_refresh",
        checkpoint_target="candidate-1",
    )

    assert [item.output_field for item in selection.requirements] == ["competitors"]
    assert selection.candidate_ids == ("candidate-1",)
    assert selection.checkpoint_cursor == "cursor-1"


def test_phase_three_available_direct_observation_preserves_lineage_and_freshness() -> None:
    result = evaluator().evaluate(
        requirement("source_authority"),
        context(observations=(observation(effective_at=NOW - timedelta(seconds=60)),)),
    )

    assert result.availability.availability_state == "available"
    assert result.availability.directness == "direct_observation"
    assert result.availability.freshness_seconds == 60
    assert result.availability.evidence_count == 1
    assert result.evidence_links[0].source_evidence_id == "evidence-1"
    assert result.span_links[0].span_id == "span-1"
    assert result.claim_links[0].claim_id == "claim-1"


def test_phase_three_missing_data_returns_explicit_unavailable_not_negative_evidence() -> None:
    result = evaluator().evaluate(requirement("source_authority"), context(observations=()))

    assert result.availability.availability_state == "unavailable"
    assert result.availability.directness == "unavailable"
    assert result.availability.missing_reason == "missing_required_source_types:knowledge_claim"
    assert result.availability.evidence_count == 0


def test_phase_three_provider_unavailable_is_explicit_unavailable_state() -> None:
    result = evaluator().evaluate(
        requirement("source_authority"),
        context(
            observations=(),
            provider_states=(
                ProviderAvailabilityState(
                    source_type="knowledge_claim",
                    status="unavailable",
                    checked_at=NOW,
                    recorded_at=NOW,
                    reason="provider down",
                ),
            ),
        ),
    )

    assert result.availability.availability_state == "unavailable"
    assert result.availability.missing_reason == "provider_unavailable:knowledge_claim"


def test_phase_three_stale_data_returns_stale_and_not_live() -> None:
    result = evaluator().evaluate(
        requirement("source_authority", minimum_freshness_seconds=60),
        context(observations=(observation(effective_at=NOW - timedelta(seconds=120)),)),
    )

    assert result.availability.availability_state == "stale"
    assert result.availability.missing_reason == "required_data_stale"


def test_phase_three_partial_data_preserves_missing_requirement_detail() -> None:
    req = requirement("source_authority", required_source_types=("knowledge_claim", "source_authority_event"))
    result = evaluator().evaluate(req, context(observations=(observation(source_type="knowledge_claim"),)))

    assert result.availability.availability_state == "partial"
    assert "missing_required_source_types:source_authority_event" in result.availability.missing_reason


def test_phase_three_proxy_signal_cannot_satisfy_direct_requirement_without_explicit_policy() -> None:
    req = requirement(
        "competitors",
        direct_observation_required=True,
        proxy_allowed=True,
        accepted_proxy_types=("competitive_proxy",),
    )
    proxy = observation(directness="proxy_signal", proxy_type="competitive_proxy", source_type="knowledge_claim")

    result = evaluator().evaluate(req, context(observations=(proxy,)))

    assert result.availability.availability_state == "unavailable"
    assert result.availability.directness == "unavailable"
    assert result.availability.missing_reason == "missing_required_source_types:knowledge_claim"


def test_phase_three_explicit_proxy_policy_records_proxy_as_partial_not_direct() -> None:
    req = requirement(
        "competitors",
        direct_observation_required=True,
        proxy_allowed=True,
        accepted_proxy_types=("competitive_proxy",),
    )
    proxy = observation(directness="proxy_signal", proxy_type="competitive_proxy", source_type="knowledge_claim")

    result = evaluator(allow_proxy_for_direct_requirement=True).evaluate(req, context(observations=(proxy,)))

    assert result.availability.availability_state == "partial"
    assert result.availability.directness == "proxy_signal"
    assert result.availability.proxy_type == "competitive_proxy"
    assert result.availability.missing_reason == "proxy_signal_cannot_fully_satisfy_direct_observation"


def test_phase_three_proxy_signal_can_satisfy_proxy_allowed_context_requirement() -> None:
    req = requirement(
        "market_context",
        direct_observation_required=False,
        proxy_allowed=True,
        accepted_proxy_types=("market_proxy",),
    )
    proxy = observation(directness="proxy_signal", proxy_type="market_proxy", source_type="knowledge_claim")

    result = evaluator().evaluate(req, context(observations=(proxy,)))

    assert result.availability.availability_state == "available"
    assert result.availability.directness == "proxy_signal"
    assert result.availability.proxy_type == "market_proxy"


def test_phase_three_identity_trust_gating_cannot_be_bypassed_by_explicit_candidate_selection() -> None:
    result = evaluator().evaluate(
        requirement("source_authority"),
        context(candidate_trust=trust(trusted=False, identity_state="conflict"), observations=(observation(),)),
    )

    assert result.availability.availability_state == "unavailable"
    assert result.availability.missing_reason == "candidate_identity_not_trusted"
    assert result.availability.evidence_count == 0


def test_phase_three_strict_known_by_hunter_excludes_later_recorded_evidence() -> None:
    req = requirement("source_authority")
    later_recorded = observation(effective_at=NOW, recorded_at=LATER)

    strict = evaluator().evaluate(
        req,
        context(
            observations=(later_recorded,),
            cutoff_at=NOW,
            replay_mode="historical_strict_known_by_hunter",
            evaluated_at=LATER,
        ),
    )
    reconstructed = evaluator().evaluate(
        req,
        context(
            observations=(later_recorded,),
            cutoff_at=NOW,
            replay_mode="reconstructed_after_cutoff",
            evaluated_at=LATER,
        ),
    )

    assert strict.availability.availability_state == "unavailable"
    assert reconstructed.availability.availability_state == "available"
    assert reconstructed.availability.replay_mode == "reconstructed_after_cutoff"


def test_phase_three_inactive_claim_lifecycle_is_unavailable_at_cutoff() -> None:
    result = evaluator().evaluate(
        requirement("source_authority"),
        context(observations=(observation(lifecycle_state="retracted"),)),
    )

    assert result.availability.availability_state == "unavailable"


def test_phase_three_competitive_point_in_time_input_uses_cutoff_semantics() -> None:
    req = requirement("evidence_backed_competitors", engine_id="competitive", purpose="peer_set_report")
    competitive_record = observation(
        source_type="competitive_relationship",
        evidence_domain="competitive",
        effective_at=NOW,
        recorded_at=LATER,
    )

    result = evaluator().evaluate(
        req,
        context(
            observations=(competitive_record,),
            cutoff_at=NOW,
            replay_mode="historical_strict_known_by_hunter",
            evaluated_at=LATER,
        ),
    )

    assert result.availability.availability_state == "unavailable"


def test_phase_three_low_authority_or_incomplete_lineage_is_partial() -> None:
    result = evaluator().evaluate(
        requirement("source_authority", minimum_source_authority="high", minimum_lineage_depth=2),
        context(observations=(observation(source_quality="medium", lineage_depth=1),)),
    )

    assert result.availability.availability_state == "partial"
    assert "lineage_incomplete" in result.availability.missing_reason
    assert "source_authority_below_requirement" in result.availability.missing_reason


def test_phase_three_evaluate_many_does_not_modify_runtime_behavior() -> None:
    results = evaluator().evaluate_many((requirement("source_authority"),), (context(observations=(observation(),)),))

    assert len(results) == 1
    assert results[0].availability.processing_run_id == "run-1"


def evaluator(*, allow_proxy_for_direct_requirement: bool = False) -> DataAvailabilityEvaluator:
    return DataAvailabilityEvaluator(
        policy=AvailabilityEvaluationPolicy(allow_proxy_for_direct_requirement=allow_proxy_for_direct_requirement)
    )


def context(**overrides: object) -> AvailabilityEvaluationContext:
    values = {
        "candidate_id": "candidate-1",
        "candidate_trust": trust(),
        "observations": (),
        "provider_states": (),
        "evaluated_at": NOW,
        "cutoff_at": None,
        "replay_mode": "current",
        "processing_run_id": "run-1",
    }
    values.update(overrides)
    return AvailabilityEvaluationContext(**values)  # type: ignore[arg-type]


def trust(**overrides: object) -> CandidateTrustState:
    values = {
        "candidate_id": "candidate-1",
        "identity_state": "exact",
        "trusted": True,
        "effective_at": NOW,
        "recorded_at": NOW,
        "reason": "verified identity",
    }
    values.update(overrides)
    return CandidateTrustState(**values)  # type: ignore[arg-type]


def observation(**overrides: object) -> SourceObservation:
    values = {
        "observation_id": "observation-1",
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
        "evidence_id": "evidence-1",
        "span_id": "span-1",
        "claim_id": "claim-1",
    }
    values.update(overrides)
    return SourceObservation(**values)  # type: ignore[arg-type]


def requirement(
    output_field: str,
    *,
    engine_id: str = "evidence_intelligence",
    purpose: str = "claim_explainability",
    required_source_types: tuple[str, ...] = ("knowledge_claim",),
    direct_observation_required: bool = True,
    proxy_allowed: bool = False,
    accepted_proxy_types: tuple[str, ...] = (),
    minimum_freshness_seconds: int = 86_400,
    minimum_source_authority: str = "medium",
    minimum_lineage_depth: int = 1,
) -> DataRequirement:
    return DataRequirement(
        requirement_id=f"requirement-{engine_id}-{purpose}-{output_field}",
        engine_id=engine_id,
        analysis_purpose=purpose,
        output_field=output_field,
        requirement_kind="direct_observation" if direct_observation_required else "proxy_context",
        evidence_domain="competitive" if engine_id == "competitive" else "evidence_intelligence",
        required_entity_type="candidate",
        required_source_types=required_source_types,
        direct_observation_required=direct_observation_required,
        proxy_allowed=proxy_allowed,
        accepted_proxy_types=accepted_proxy_types,
        minimum_freshness_seconds=minimum_freshness_seconds,
        minimum_source_authority=minimum_source_authority,
        minimum_lineage_depth=minimum_lineage_depth,
        minimum_confidence=0.0,
        historical_required=True,
        blocking_level="required_for_output",
        policy_id="data-sufficiency-default-policy",
        policy_version="data-sufficiency-policy-v1",
        effective_at=NOW,
        recorded_at=NOW,
        schema_version="data-sufficiency-v1",
    )
