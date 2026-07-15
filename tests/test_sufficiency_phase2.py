from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from hunter.evidence_intelligence import EvidenceIntelligenceRepository
from hunter.sufficiency import (
    DataAvailability,
    DataRequirement,
    DataSufficiencyAssessment,
    DataSufficiencyCheckpoint,
    DataSufficiencyClaimLink,
    DataSufficiencyConflictLink,
    DataSufficiencyEvidenceLink,
    DataSufficiencyProcessingRun,
    DataSufficiencyRepository,
    DataSufficiencySpanLink,
    SourceDisagreement,
    SourceValidationResult,
    default_degraded_mode_policy,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)
LATER = datetime(2026, 2, 1, tzinfo=UTC)
BEFORE_RECORDED = datetime(2025, 12, 31, tzinfo=UTC)


def test_phase_two_sufficiency_schema_creates_authoritative_tables_and_indexes(tmp_path) -> None:
    repository = DataSufficiencyRepository(tmp_path / "sufficiency.sqlite")

    tables = set(repository.table_names())
    assert {
        "data_requirements",
        "data_requirement_source_types",
        "data_requirement_proxy_types",
        "data_availability",
        "data_sufficiency_assessments",
        "degraded_mode_policies",
        "data_source_validation_results",
        "data_disagreement_records",
        "data_sufficiency_evidence_links",
        "data_sufficiency_span_links",
        "data_sufficiency_claim_links",
        "data_sufficiency_conflict_links",
        "data_sufficiency_processing_runs",
        "data_sufficiency_checkpoints",
    }.issubset(tables)

    indexes = set(repository.index_names())
    assert {
        "data_requirements_engine_purpose_field_policy_idx",
        "data_availability_candidate_requirement_state_time_idx",
        "data_sufficiency_assessments_candidate_engine_purpose_state_time_idx",
        "data_sufficiency_evidence_links_evidence_idx",
        "data_sufficiency_span_links_span_idx",
        "data_sufficiency_claim_links_claim_idx",
        "data_sufficiency_conflict_links_conflict_idx",
        "data_sufficiency_processing_runs_status_idx",
        "data_sufficiency_checkpoints_processor_target_idx",
    }.issubset(indexes)


def test_phase_two_sufficiency_writes_are_idempotent(tmp_path) -> None:
    repository = DataSufficiencyRepository(tmp_path / "sufficiency.sqlite")
    persist_sufficiency_graph(repository)
    persist_sufficiency_graph(repository)

    assert repository.count("data_requirements") == 1
    assert repository.count("data_requirement_source_types") == 2
    assert repository.count("data_requirement_proxy_types") == 1
    assert repository.count("degraded_mode_policies") == 1
    assert repository.count("data_availability") == 1
    assert repository.count("data_sufficiency_assessments") == 1
    assert repository.count("data_source_validation_results") == 1
    assert repository.count("data_disagreement_records") == 1
    assert repository.count("data_sufficiency_evidence_links") == 1
    assert repository.count("data_sufficiency_span_links") == 1
    assert repository.count("data_sufficiency_claim_links") == 1
    assert repository.count("data_sufficiency_conflict_links") == 1
    assert repository.count("data_sufficiency_processing_runs") == 1
    assert repository.count("data_sufficiency_checkpoints") == 1


def test_phase_two_sufficiency_lineage_reconstructs_from_normalized_tables(tmp_path) -> None:
    repository = DataSufficiencyRepository(tmp_path / "sufficiency.sqlite")
    persist_sufficiency_graph(repository)

    lineage = repository.lineage("availability", "availability-1")

    assert lineage["source_evidence"][0]["source_evidence_id"] == "source-evidence-1"
    assert lineage["spans"][0]["span_id"] == "span-1"
    assert lineage["claims"][0]["claim_id"] == "claim-1"
    assert lineage["conflicts"][0]["conflict_id"] == "conflict-1"


def test_phase_two_sufficiency_point_in_time_queries_use_effective_and_recorded_time(tmp_path) -> None:
    repository = DataSufficiencyRepository(tmp_path / "sufficiency.sqlite")
    persist_sufficiency_graph(repository)

    assert repository.requirement_at("requirement-1", NOW, strict_known_by_hunter=True) is not None
    assert repository.availability_at("availability-1", NOW) is not None
    assert repository.assessment_at("assessment-1", NOW, strict_known_by_hunter=True) is not None
    assert repository.availability_at("availability-1", BEFORE_RECORDED, strict_known_by_hunter=True) is None
    assert (
        repository.availability_for_candidate_at("candidate-1", NOW, strict_known_by_hunter=True)[0][
            "availability_state"
        ]
        == "partial"
    )
    assert (
        repository.assessments_for_candidate_at("candidate-1", NOW, strict_known_by_hunter=True)[0]["sufficiency_state"]
        == "degraded"
    )


def test_phase_two_sufficiency_versioned_writes_preserve_prior_historical_state(tmp_path) -> None:
    repository = DataSufficiencyRepository(tmp_path / "sufficiency.sqlite")
    first_availability = availability()
    later_availability = replace(first_availability, availability_state="available", recorded_at=LATER)
    first_assessment = assessment()
    later_assessment = replace(
        first_assessment,
        sufficiency_state="sufficient_with_limitations",
        coverage_score=0.8,
        limitations_summary="limited historical source coverage",
        recorded_at=LATER,
    )

    repository.save_availability(first_availability)
    repository.save_availability(later_availability)
    repository.save_assessment(first_assessment)
    repository.save_assessment(later_assessment)

    assert repository.count("data_availability") == 2
    assert repository.count("data_sufficiency_assessments") == 2
    historical_availability = repository.availability_at("availability-1", NOW, strict_known_by_hunter=True)
    current_availability = repository.availability_for_candidate("candidate-1")[0]
    historical_assessment = repository.assessment_at("assessment-1", NOW, strict_known_by_hunter=True)
    current_assessment = repository.assessments_for_candidate("candidate-1")[0]

    assert historical_availability is not None
    assert historical_assessment is not None
    assert historical_availability["availability_state"] == "partial"
    assert current_availability["availability_state"] == "available"
    assert historical_assessment["sufficiency_state"] == "degraded"
    assert current_assessment["sufficiency_state"] == "sufficient_with_limitations"


def test_phase_two_existing_evidence_repository_still_initializes_unchanged(tmp_path) -> None:
    repository = EvidenceIntelligenceRepository(tmp_path / "evidence-intelligence.sqlite")

    assert "knowledge_claims" in repository.table_names()
    assert "data_requirements" not in repository.table_names()


def persist_sufficiency_graph(repository: DataSufficiencyRepository) -> None:
    repository.save_requirement(requirement())
    repository.save_degraded_mode_policy(default_degraded_mode_policy(effective_at=NOW, recorded_at=NOW))
    repository.save_availability_with_lineage(
        availability(),
        evidence_links=(evidence_link(),),
        span_links=(span_link(),),
        claim_links=(claim_link(),),
        conflict_links=(conflict_link(),),
    )
    repository.save_assessment(assessment())
    repository.save_source_validation_result(source_validation())
    repository.save_disagreement(disagreement())
    repository.save_processing_run(processing_run())
    repository.save_checkpoint(checkpoint())


def requirement() -> DataRequirement:
    return DataRequirement(
        requirement_id="requirement-1",
        engine_id="competitive",
        analysis_purpose="peer_set_report",
        output_field="evidence_backed_competitors",
        requirement_kind="direct_observation",
        evidence_domain="competitive",
        required_entity_type="candidate",
        required_source_types=("competitive_relationship", "knowledge_claim"),
        direct_observation_required=True,
        proxy_allowed=True,
        accepted_proxy_types=("competitive_proxy",),
        minimum_freshness_seconds=86_400,
        minimum_source_authority="verified_or_persisted_hunter_evidence",
        minimum_lineage_depth=1,
        minimum_confidence=0.0,
        historical_required=True,
        blocking_level="required_for_high_confidence",
        policy_id="data-sufficiency-default-policy",
        policy_version="data-sufficiency-policy-v1",
        effective_at=NOW,
        recorded_at=NOW,
        schema_version="data-sufficiency-v1",
    )


def availability() -> DataAvailability:
    return DataAvailability(
        availability_id="availability-1",
        requirement_id="requirement-1",
        candidate_id="candidate-1",
        engine_id="competitive",
        analysis_purpose="peer_set_report",
        availability_state="partial",
        directness="direct_observation",
        proxy_type=None,
        freshness_seconds=60,
        source_quality="medium",
        lineage_complete=True,
        conflict_state="none",
        evidence_count=1,
        missing_reason="secondary peer evidence unavailable",
        effective_at=NOW,
        recorded_at=NOW,
        cutoff_at=NOW,
        replay_mode="historical_strict_known_by_hunter",
        processing_run_id="run-1",
        schema_version="data-sufficiency-v1",
    )


def assessment() -> DataSufficiencyAssessment:
    return DataSufficiencyAssessment(
        assessment_id="assessment-1",
        candidate_id="candidate-1",
        engine_id="competitive",
        analysis_purpose="peer_set_report",
        assessment_scope="candidate_report",
        sufficiency_state="degraded",
        degraded_mode="degraded_material_limitation",
        coverage_score=0.5,
        freshness_state="fresh",
        source_quality_state="medium",
        lineage_state="complete",
        conflict_state="none",
        direct_observation_coverage=0.5,
        proxy_signal_coverage=0.0,
        material_missing_count=1,
        limitations_summary="Secondary peer evidence is unavailable.",
        policy_id="data-sufficiency-default-policy",
        policy_version="data-sufficiency-policy-v1",
        effective_at=NOW,
        recorded_at=NOW,
        cutoff_at=NOW,
        replay_mode="historical_strict_known_by_hunter",
        processing_run_id="run-1",
        schema_version="data-sufficiency-v1",
    )


def source_validation() -> SourceValidationResult:
    return SourceValidationResult(
        validation_id="validation-1",
        candidate_id="candidate-1",
        requirement_id="requirement-1",
        engine_id="competitive",
        analysis_purpose="peer_set_report",
        source_a="knowledge_claim",
        source_b="competitive_relationship",
        validation_status="agreement",
        compatible_scope=True,
        source_authority_state="high",
        freshness_state="fresh",
        reason="source observations agree",
        effective_at=NOW,
        recorded_at=NOW,
        cutoff_at=NOW,
        replay_mode="historical_strict_known_by_hunter",
        processing_run_id="run-1",
        schema_version="data-sufficiency-v1",
    )


def disagreement() -> SourceDisagreement:
    return SourceDisagreement(
        disagreement_id="disagreement-1",
        candidate_id="candidate-1",
        requirement_id="requirement-1",
        engine_id="competitive",
        analysis_purpose="peer_set_report",
        disagreement_state="disagreement",
        compared_source_count=2,
        compatible_scope=True,
        reason="provider values disagree",
        effective_at=NOW,
        recorded_at=NOW,
        replay_mode="historical_strict_known_by_hunter",
        processing_run_id="run-1",
        schema_version="data-sufficiency-v1",
    )


def evidence_link() -> DataSufficiencyEvidenceLink:
    return DataSufficiencyEvidenceLink(
        link_id="evidence-link-1",
        owner_type="availability",
        owner_id="availability-1",
        source_evidence_id="source-evidence-1",
        role="supporting_evidence",
        position=0,
        created_at=NOW,
        schema_version="data-sufficiency-v1",
    )


def span_link() -> DataSufficiencySpanLink:
    return DataSufficiencySpanLink(
        link_id="span-link-1",
        owner_type="availability",
        owner_id="availability-1",
        span_id="span-1",
        role="supporting_span",
        position=0,
        created_at=NOW,
        schema_version="data-sufficiency-v1",
    )


def claim_link() -> DataSufficiencyClaimLink:
    return DataSufficiencyClaimLink(
        link_id="claim-link-1",
        owner_type="availability",
        owner_id="availability-1",
        claim_id="claim-1",
        role="supporting_claim",
        position=0,
        created_at=NOW,
        schema_version="data-sufficiency-v1",
    )


def conflict_link() -> DataSufficiencyConflictLink:
    return DataSufficiencyConflictLink(
        link_id="conflict-link-1",
        owner_type="availability",
        owner_id="availability-1",
        conflict_id="conflict-1",
        role="related_conflict",
        position=0,
        created_at=NOW,
        schema_version="data-sufficiency-v1",
    )


def processing_run() -> DataSufficiencyProcessingRun:
    return DataSufficiencyProcessingRun(
        run_id="run-1",
        run_type="availability_refresh",
        status="succeeded",
        started_at=NOW,
        finished_at=NOW,
        replay_mode="historical_strict_known_by_hunter",
        cutoff_at=NOW,
        schema_version="data-sufficiency-v1",
    )


def checkpoint() -> DataSufficiencyCheckpoint:
    return DataSufficiencyCheckpoint(
        checkpoint_id="checkpoint-1",
        processor_name="availability_refresh",
        target_id="candidate-1",
        cursor="candidate-1:requirement-1",
        updated_at=NOW,
        schema_version="data-sufficiency-v1",
    )
