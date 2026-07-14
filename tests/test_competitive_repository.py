from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from hunter.competitive import (
    AlgorithmicPeerRelationship,
    ComparisonDimension,
    CompetitiveAssessment,
    CompetitiveCheckpoint,
    CompetitiveConflictLink,
    CompetitiveProcessingRun,
    CompetitiveRelationship,
    CompetitiveRelationshipEvidenceLink,
    CompetitiveRelationshipSpanLink,
    CompetitiveRepository,
    PeerSet,
    PeerSetEvidenceLink,
    PeerSetMember,
    PeerSetSpanLink,
)
from hunter.evidence_intelligence import EvidenceIntelligenceRepository

NOW = datetime(2026, 1, 1, tzinfo=UTC)
LATER = datetime(2026, 2, 1, tzinfo=UTC)
BEFORE_RECORDED = datetime(2025, 12, 31, tzinfo=UTC)


def test_phase_two_competitive_schema_creates_authoritative_tables_and_indexes(tmp_path) -> None:
    repository = CompetitiveRepository(tmp_path / "competitive.sqlite")

    tables = set(repository.table_names())
    assert {
        "competitive_peer_sets",
        "competitive_peer_set_members",
        "competitive_relationships",
        "algorithmic_peer_relationships",
        "competitive_comparison_dimensions",
        "competitive_assessments",
        "competitive_relationship_evidence_links",
        "competitive_relationship_span_links",
        "peer_set_evidence_links",
        "peer_set_span_links",
        "competitive_conflict_links",
        "competitive_processing_runs",
        "competitive_checkpoints",
    }.issubset(tables)

    indexes = set(repository.index_names())
    assert {
        "competitive_peer_sets_subject_status_idx",
        "competitive_peer_sets_scope_version_idx",
        "competitive_relationships_subject_status_idx",
        "competitive_relationships_peer_type_idx",
        "competitive_relationships_type_time_idx",
        "algorithmic_peer_relationships_policy_idx",
        "competitive_relationship_evidence_links_evidence_idx",
        "competitive_relationship_span_links_span_idx",
        "peer_set_evidence_links_evidence_idx",
        "competitive_checkpoints_processor_target_idx",
    }.issubset(indexes)


def test_phase_two_competitive_writes_are_idempotent(tmp_path) -> None:
    repository = CompetitiveRepository(tmp_path / "competitive.sqlite")
    persist_competitive_graph(repository)
    persist_competitive_graph(repository)

    assert repository.count("competitive_relationships") == 1
    assert repository.count("algorithmic_peer_relationships") == 1
    assert repository.count("competitive_peer_sets") == 1
    assert repository.count("competitive_peer_set_members") == 1
    assert repository.count("competitive_comparison_dimensions") == 1
    assert repository.count("competitive_assessments") == 1
    assert repository.count("competitive_relationship_evidence_links") == 1
    assert repository.count("competitive_relationship_span_links") == 1
    assert repository.count("peer_set_evidence_links") == 1
    assert repository.count("peer_set_span_links") == 1
    assert repository.count("competitive_conflict_links") == 1
    assert repository.count("competitive_processing_runs") == 1
    assert repository.count("competitive_checkpoints") == 1


def test_phase_two_reconstructs_relationship_and_peer_set_lineage_from_normalized_tables(tmp_path) -> None:
    repository = CompetitiveRepository(tmp_path / "competitive.sqlite")
    persist_competitive_graph(repository)

    relationship_lineage = repository.relationship_lineage("competitive-relationship-1")
    peer_set_lineage = repository.peer_set_lineage("peer-set-1")

    assert relationship_lineage["relationship"][0]["relationship_id"] == "competitive-relationship-1"
    assert relationship_lineage["source_evidence"][0]["source_evidence_id"] == "source-evidence-1"
    assert relationship_lineage["spans"][0]["span_id"] == "span-1"
    assert relationship_lineage["conflicts"][0]["conflict_id"] == "conflict-1"
    assert peer_set_lineage["peer_set"][0]["peer_set_id"] == "peer-set-1"
    assert peer_set_lineage["members"][0]["relationship_kind"] == "evidence_backed"
    assert peer_set_lineage["source_evidence"][0]["source_evidence_id"] == "source-evidence-1"
    assert peer_set_lineage["spans"][0]["span_id"] == "span-1"


def test_phase_two_point_in_time_queries_use_effective_and_recorded_time(tmp_path) -> None:
    repository = CompetitiveRepository(tmp_path / "competitive.sqlite")
    persist_competitive_graph(repository)

    assert repository.competitive_relationship_at("competitive-relationship-1", NOW) is not None
    assert (
        repository.competitive_relationship_at(
            "competitive-relationship-1",
            BEFORE_RECORDED,
            strict_known_by_hunter=True,
        )
        is None
    )
    assert repository.peer_set_at("peer-set-1", NOW, strict_known_by_hunter=True) is not None


def test_phase_two_versioned_writes_preserve_prior_historical_state(tmp_path) -> None:
    repository = CompetitiveRepository(tmp_path / "competitive.sqlite")
    first_relationship = competitive_relationship()
    later_relationship = replace(first_relationship, confidence=0.2, recorded_at=LATER)
    first_algorithmic = algorithmic_relationship()
    later_algorithmic = replace(first_algorithmic, confidence=0.3, recorded_at=LATER)
    first_peer_set = peer_set()
    later_peer_set = replace(first_peer_set, confidence=0.4, recorded_at=LATER)
    first_dimension = comparison_dimension()
    later_dimension = replace(first_dimension, match_status="missing", recorded_at=LATER)

    repository.save_competitive_relationship(first_relationship)
    repository.save_competitive_relationship(later_relationship)
    repository.save_algorithmic_peer_relationship(first_algorithmic)
    repository.save_algorithmic_peer_relationship(later_algorithmic)
    repository.save_peer_set(first_peer_set)
    repository.save_peer_set(later_peer_set)
    repository.save_comparison_dimension(first_dimension)
    repository.save_comparison_dimension(later_dimension)

    assert repository.count("competitive_relationships") == 2
    assert repository.count("algorithmic_peer_relationships") == 2
    assert repository.count("competitive_peer_sets") == 2
    assert repository.count("competitive_comparison_dimensions") == 2
    relationship_at_cutoff = repository.competitive_relationship_at(
        "competitive-relationship-1", NOW, strict_known_by_hunter=True
    )
    assert relationship_at_cutoff is not None
    assert relationship_at_cutoff["confidence"] == 0.8
    assert repository.competitive_relationships_for_subject("candidate-a")[0]["confidence"] == 0.2
    assert repository.peer_sets_for_subject_at("candidate-a", NOW, strict_known_by_hunter=True)[0]["confidence"] == 0.75
    assert repository.peer_sets_for_subject("candidate-a")[0]["confidence"] == 0.4
    assert (
        repository.algorithmic_relationships_for_subject_at("candidate-a", NOW, strict_known_by_hunter=True)[0][
            "confidence"
        ]
        == 0.7
    )
    assert repository.algorithmic_relationships_for_subject("candidate-a")[0]["confidence"] == 0.3
    assert (
        repository.comparison_dimensions_for_relationship_at(
            "algorithmic-relationship-1",
            NOW,
            strict_known_by_hunter=True,
        )[0]["match_status"]
        == "matched"
    )
    assert (
        repository.comparison_dimensions_for_relationship("algorithmic-relationship-1")[0]["match_status"] == "missing"
    )


def test_phase_two_existing_evidence_repository_still_initializes_unchanged(tmp_path) -> None:
    repository = EvidenceIntelligenceRepository(tmp_path / "evidence-intelligence.sqlite")

    assert "knowledge_claims" in repository.table_names()
    assert "competitive_relationships" not in repository.table_names()


def persist_competitive_graph(repository: CompetitiveRepository) -> None:
    repository.save_relationship_with_lineage(
        competitive_relationship(),
        evidence_links=(relationship_evidence_link(),),
        span_links=(relationship_span_link(),),
        conflict_links=(conflict_link(),),
    )
    repository.save_algorithmic_peer_relationship(algorithmic_relationship())
    repository.save_peer_set_with_lineage(
        peer_set(),
        members=(peer_set_member(),),
        evidence_links=(peer_set_evidence_link(),),
        span_links=(peer_set_span_link(),),
    )
    repository.save_comparison_dimension(comparison_dimension())
    repository.save_assessment(competitive_assessment())
    repository.save_processing_run(processing_run())
    repository.save_checkpoint(checkpoint())


def competitive_relationship() -> CompetitiveRelationship:
    return CompetitiveRelationship(
        relationship_id="competitive-relationship-1",
        subject_candidate_id="candidate-a",
        peer_candidate_id="candidate-b",
        relationship_type="direct_competitor",
        status="active",
        predicate_id="competes_with",
        predicate_schema_version="competitive-predicate-v1",
        claim_id="claim-1",
        subject_entity_id="entity-a",
        peer_entity_id="entity-b",
        scope="decentralized-exchange",
        modality="asserted",
        polarity="positive",
        confidence=0.8,
        freshness=0.9,
        effective_at=NOW,
        recorded_at=NOW,
        schema_version="competitive-relationship-v1",
    )


def algorithmic_relationship() -> AlgorithmicPeerRelationship:
    return AlgorithmicPeerRelationship(
        relationship_id="algorithmic-relationship-1",
        subject_candidate_id="candidate-a",
        peer_candidate_id="candidate-b",
        relationship_type="same_category_similarity",
        status="active",
        policy_id="policy-1",
        policy_version="policy-v1",
        scope="defi",
        compared_dimension_count=2,
        matched_dimension_count=1,
        missing_dimension_count=0,
        similarity=0.5,
        confidence=0.7,
        freshness=0.9,
        effective_at=NOW,
        recorded_at=NOW,
        schema_version="algorithmic-peer-relationship-v1",
    )


def peer_set() -> PeerSet:
    return PeerSet(
        peer_set_id="peer-set-1",
        subject_candidate_id="candidate-a",
        scope="defi",
        status="active",
        peer_set_version="peer-set-v1",
        policy_id="policy-1",
        policy_version="policy-v1",
        evidence_backed_count=1,
        algorithmic_peer_count=1,
        confidence=0.75,
        coverage=0.5,
        freshness=0.9,
        effective_at=NOW,
        recorded_at=NOW,
        schema_version="competitive-peer-set-v1",
    )


def peer_set_member() -> PeerSetMember:
    return PeerSetMember(
        member_id="member-1",
        peer_set_id="peer-set-1",
        peer_candidate_id="candidate-b",
        member_role="evidence_backed_competitor",
        relationship_kind="evidence_backed",
        relationship_id="competitive-relationship-1",
        status="active",
        confidence=0.75,
        freshness=0.9,
        position=0,
        effective_at=NOW,
        recorded_at=NOW,
        schema_version="competitive-peer-set-member-v1",
    )


def comparison_dimension() -> ComparisonDimension:
    return ComparisonDimension(
        dimension_id="dimension-1",
        subject_candidate_id="candidate-a",
        peer_candidate_id="candidate-b",
        dimension_type="market_category",
        subject_value="defi",
        peer_value="defi",
        match_status="matched",
        relationship_kind="algorithmic_similarity",
        relationship_id="algorithmic-relationship-1",
        policy_id="policy-1",
        policy_version="policy-v1",
        confidence=0.75,
        effective_at=NOW,
        recorded_at=NOW,
        schema_version="competitive-comparison-dimension-v1",
    )


def competitive_assessment() -> CompetitiveAssessment:
    return CompetitiveAssessment(
        assessment_id="assessment-1",
        subject_candidate_id="candidate-a",
        peer_set_id="peer-set-1",
        status="active",
        evidence_backed_competitors=1,
        algorithmic_peers=1,
        missing_evidence_count=0,
        conflict_count=1,
        confidence=0.75,
        coverage=0.5,
        freshness=0.9,
        mode="current",
        effective_at=NOW,
        recorded_at=NOW,
        schema_version="competitive-assessment-v1",
    )


def relationship_evidence_link() -> CompetitiveRelationshipEvidenceLink:
    return CompetitiveRelationshipEvidenceLink(
        link_id="relationship-evidence-link-1",
        relationship_id="competitive-relationship-1",
        source_evidence_id="source-evidence-1",
        role="supporting",
        position=0,
        created_at=NOW,
        schema_version="competitive-link-v1",
    )


def relationship_span_link() -> CompetitiveRelationshipSpanLink:
    return CompetitiveRelationshipSpanLink(
        link_id="relationship-span-link-1",
        relationship_id="competitive-relationship-1",
        span_id="span-1",
        role="supporting",
        position=0,
        created_at=NOW,
        schema_version="competitive-link-v1",
    )


def peer_set_evidence_link() -> PeerSetEvidenceLink:
    return PeerSetEvidenceLink(
        link_id="peer-set-evidence-link-1",
        peer_set_id="peer-set-1",
        source_evidence_id="source-evidence-1",
        role="supporting",
        position=0,
        created_at=NOW,
        schema_version="competitive-link-v1",
    )


def peer_set_span_link() -> PeerSetSpanLink:
    return PeerSetSpanLink(
        link_id="peer-set-span-link-1",
        peer_set_id="peer-set-1",
        span_id="span-1",
        role="supporting",
        position=0,
        created_at=NOW,
        schema_version="competitive-link-v1",
    )


def conflict_link() -> CompetitiveConflictLink:
    return CompetitiveConflictLink(
        link_id="competitive-conflict-link-1",
        relationship_id="competitive-relationship-1",
        conflict_id="conflict-1",
        role="participant",
        created_at=NOW,
        schema_version="competitive-conflict-link-v1",
    )


def processing_run() -> CompetitiveProcessingRun:
    return CompetitiveProcessingRun(
        run_id="competitive-run-1",
        run_type="phase-two-test",
        status="succeeded",
        started_at=NOW,
        finished_at=NOW,
        schema_version="competitive-processing-run-v1",
    )


def checkpoint() -> CompetitiveCheckpoint:
    return CompetitiveCheckpoint(
        checkpoint_id="competitive-checkpoint-1",
        processor_name="phase-two-test",
        target_id="candidate-a",
        cursor="cursor-1",
        updated_at=NOW,
        schema_version="competitive-checkpoint-v1",
    )
