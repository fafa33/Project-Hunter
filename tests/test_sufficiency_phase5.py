from __future__ import annotations

from datetime import UTC, datetime

from hunter.sufficiency import (
    DataAvailability,
    DataRequirement,
    DataSufficiencyAssessor,
    DataSufficiencyRepository,
    DegradedModePolicyEngine,
    SourceDisagreement,
    SufficiencyAssessmentContext,
    default_degraded_mode_policy,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)
LATER = datetime(2026, 1, 2, tzinfo=UTC)


def test_phase_five_assessment_aggregates_availability_freshness_quality_direct_and_proxy_states() -> None:
    requirements = (
        requirement("market_cap"),
        requirement("developer_activity", direct_observation_required=False, proxy_allowed=True),
        requirement("treasury"),
    )
    result = assessor().assess(
        requirements=requirements,
        availabilities=(
            availability("market_cap", state="available", directness="direct_observation", source_quality="high"),
            availability(
                "developer_activity",
                state="available",
                directness="proxy_signal",
                proxy_type="developer_proxy",
                source_quality="medium",
            ),
            availability("treasury", state="stale", missing_reason="required_data_stale", source_quality="medium"),
        ),
        context=context(),
    )

    assert result.assessment.sufficiency_state == "degraded"
    assert result.assessment.degraded_mode == "degraded_material_limitation"
    assert result.assessment.coverage_score == 0.6667
    assert result.assessment.freshness_state == "stale"
    assert result.assessment.source_quality_state == "medium"
    assert result.assessment.direct_observation_coverage == 0.5
    assert result.assessment.proxy_signal_coverage == 1.0
    assert "treasury: required_data_stale" in result.limitations


def test_phase_five_degraded_mode_states_missing_supportable_and_unsupported_without_score_effect() -> None:
    requirements = (requirement("market_cap"), requirement("treasury"))
    result = assessor().assess(
        requirements=requirements,
        availabilities=(availability("market_cap", state="available"),),
        context=context(),
    )

    assert result.assessment.sufficiency_state == "insufficient"
    assert result.missing_requirements == ("treasury",)
    assert result.supportable_conclusions == ("market_cap",)
    assert result.unsupported_conclusions == ("treasury",)
    assert result.assessment.metadata["preserves_score"] is True
    assert result.assessment.metadata["treats_missing_as_negative"] is False
    assert result.assessment.metadata["report_field_gating"]["treasury"]["supportable"] is False


def test_phase_five_proxy_for_direct_requirement_remains_labeled_and_blocked() -> None:
    req = requirement("market_cap", direct_observation_required=True, proxy_allowed=True)
    result = assessor().assess(
        requirements=(req,),
        availabilities=(
            availability("market_cap", state="available", directness="proxy_signal", proxy_type="market_proxy"),
        ),
        context=context(),
    )

    assert result.assessment.sufficiency_state == "insufficient"
    assert result.assessment.degraded_mode == "blocked_insufficient_evidence"
    assert result.assessment.proxy_signal_coverage == 1.0
    assert result.assessment.direct_observation_coverage == 0.0
    assert result.assessment.metadata["report_field_gating"]["market_cap"]["directness"] == "proxy_signal"
    assert "market_cap: proxy signal only" in result.limitations


def test_phase_five_cross_source_disagreement_is_limitation_not_project_negative() -> None:
    result = assessor().assess(
        requirements=(requirement("market_cap"),),
        availabilities=(availability("market_cap", state="available"),),
        disagreements=(disagreement("market_cap"),),
        context=context(),
    )

    assert result.assessment.sufficiency_state == "sufficient_with_limitations"
    assert result.assessment.source_quality_state == "conflicted"
    assert result.assessment.conflict_state == "disputed"
    assert result.assessment.metadata["disagreement_ids"] == ("disagreement-market_cap",)
    assert "cross-source disagreement is data-quality metadata, not project-negative evidence" in result.limitations


def test_phase_five_strict_and_reconstructed_assessments_are_cutoff_distinct() -> None:
    req = requirement("market_cap")
    later_recorded = availability("market_cap", state="available", effective_at=NOW, recorded_at=LATER)

    strict = assessor().assess(
        requirements=(req,),
        availabilities=(later_recorded,),
        context=context(
            cutoff_at=NOW,
            evaluated_at=LATER,
            replay_mode="historical_strict_known_by_hunter",
        ),
    )
    reconstructed = assessor().assess(
        requirements=(req,),
        availabilities=(later_recorded,),
        context=context(
            cutoff_at=NOW,
            evaluated_at=LATER,
            replay_mode="reconstructed_after_cutoff",
        ),
    )

    assert strict.assessment.sufficiency_state == "unavailable"
    assert strict.assessment.replay_mode == "historical_strict_known_by_hunter"
    assert "strict historical assessment uses only records known by Hunter at cutoff" in strict.limitations
    assert reconstructed.assessment.sufficiency_state == "sufficient"
    assert reconstructed.assessment.replay_mode == "reconstructed_after_cutoff"
    assert (
        "reconstructed assessment uses later-recorded records and is not known-at-cutoff" in reconstructed.limitations
    )


def test_phase_five_repository_preserves_historical_assessment_versions(tmp_path) -> None:
    repository = DataSufficiencyRepository(tmp_path / "sufficiency.sqlite")
    req = requirement("market_cap")
    first = assessor().assess(
        requirements=(req,),
        availabilities=(),
        context=context(cutoff_at=NOW, replay_mode="historical_strict_known_by_hunter"),
    )
    later = assessor().assess(
        requirements=(req,),
        availabilities=(availability("market_cap", state="available", effective_at=LATER, recorded_at=LATER),),
        context=context(evaluated_at=LATER),
    )

    repository.save_assessment(first.assessment)
    repository.save_assessment(later.assessment)

    assert repository.count("data_sufficiency_assessments") == 2
    historical = repository.assessment_at(first.assessment.assessment_id, NOW, strict_known_by_hunter=True)
    assert historical is not None
    assert historical["sufficiency_state"] == "unavailable"
    assert repository.assessments_for_candidate("candidate-1")[0]["sufficiency_state"] == "sufficient"


def assessor() -> DataSufficiencyAssessor:
    return DataSufficiencyAssessor(
        policy_engine=DegradedModePolicyEngine(default_degraded_mode_policy(effective_at=NOW, recorded_at=NOW))
    )


def context(**overrides: object) -> SufficiencyAssessmentContext:
    values = {
        "candidate_id": "candidate-1",
        "assessment_scope": "candidate_report",
        "evaluated_at": NOW,
        "cutoff_at": None,
        "replay_mode": "current",
        "processing_run_id": "run-1",
    }
    values.update(overrides)
    return SufficiencyAssessmentContext(**values)  # type: ignore[arg-type]


def requirement(
    output_field: str,
    *,
    direct_observation_required: bool = True,
    proxy_allowed: bool = False,
) -> DataRequirement:
    return DataRequirement(
        requirement_id=f"requirement-{output_field}",
        engine_id="market_validation",
        analysis_purpose="candidate_report",
        output_field=output_field,
        requirement_kind="direct_observation" if direct_observation_required else "proxy_context",
        evidence_domain="market",
        required_entity_type="candidate",
        required_source_types=("market_data",),
        direct_observation_required=direct_observation_required,
        proxy_allowed=proxy_allowed,
        accepted_proxy_types=("market_proxy", "developer_proxy") if proxy_allowed else (),
        minimum_freshness_seconds=86_400,
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


def availability(
    output_field: str,
    *,
    state: str,
    directness: str = "direct_observation",
    proxy_type: str | None = None,
    missing_reason: str = "",
    source_quality: str = "high",
    effective_at: datetime | None = None,
    recorded_at: datetime | None = None,
) -> DataAvailability:
    return DataAvailability(
        availability_id=f"availability-{output_field}-{recorded_at or NOW}",
        requirement_id=f"requirement-{output_field}",
        candidate_id="candidate-1",
        engine_id="market_validation",
        analysis_purpose="candidate_report",
        availability_state=state,
        directness=directness,
        proxy_type=proxy_type,
        freshness_seconds=60 if state != "unavailable" else None,
        source_quality=source_quality,
        lineage_complete=True,
        conflict_state="none",
        evidence_count=1 if state != "unavailable" else 0,
        missing_reason=missing_reason,
        effective_at=effective_at or NOW,
        recorded_at=recorded_at or NOW,
        cutoff_at=None,
        replay_mode="current",
        processing_run_id="run-1",
        schema_version="data-sufficiency-v1",
    )


def disagreement(output_field: str) -> SourceDisagreement:
    return SourceDisagreement(
        disagreement_id=f"disagreement-{output_field}",
        candidate_id="candidate-1",
        requirement_id=f"requirement-{output_field}",
        engine_id="market_validation",
        analysis_purpose="candidate_report",
        disagreement_state="disagreement",
        compared_source_count=2,
        compatible_scope=True,
        reason="data_quality_state:compatible_sources_disagree",
        effective_at=NOW,
        recorded_at=NOW,
        replay_mode="current",
        processing_run_id="run-1",
        schema_version="data-sufficiency-v1",
    )
