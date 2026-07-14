from __future__ import annotations

from datetime import UTC, datetime

from hunter.competitive import (
    AlgorithmicPeerBuilder,
    AlgorithmicPeerPolicy,
    CompetitiveRelationship,
    CompetitiveRelationshipEvidenceLink,
    CompetitiveRelationshipSpanLink,
    CompetitiveRepository,
    PeerSetBuilder,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def test_phase_six_peer_set_builder_separates_evidence_backed_and_algorithmic_members(tmp_path) -> None:
    repository = CompetitiveRepository(tmp_path / "competitive.sqlite")
    relationship = competitive_relationship()
    repository.save_relationship_with_lineage(
        relationship,
        evidence_links=(relationship_evidence_link(),),
        span_links=(relationship_span_link(),),
    )
    AlgorithmicPeerBuilder(
        policy=AlgorithmicPeerPolicy(dimensions=("market_category", "chain")),
        repository=repository,
    ).build(
        subject_candidate_id="candidate-a",
        peer_candidate_id="candidate-c",
        subject_dimensions={"market_category": "DeFi", "chain": "Ethereum"},
        peer_dimensions={"market_category": "defi", "chain": "Base"},
        scope="defi",
        effective_at=NOW,
        recorded_at=NOW,
    )

    result = PeerSetBuilder(repository=repository).build(
        subject_candidate_id="candidate-a",
        scope="defi",
        effective_at=NOW,
        recorded_at=NOW,
    )

    assert result.peer_set.evidence_backed_count == 1
    assert result.peer_set.algorithmic_peer_count == 1
    assert {member.member_role for member in result.members} == {"evidence_backed_competitor", "algorithmic_peer"}
    assert {member.relationship_kind for member in result.members} == {"evidence_backed", "algorithmic_similarity"}
    lineage = repository.peer_set_lineage(result.peer_set.peer_set_id)
    assert lineage["source_evidence"][0]["source_evidence_id"] == "source-evidence-1"
    assert lineage["spans"][0]["span_id"] == "span-1"


def test_phase_six_confidence_coverage_and_freshness_use_persisted_lineage_and_dimensions(tmp_path) -> None:
    repository = CompetitiveRepository(tmp_path / "competitive.sqlite")
    repository.save_relationship_with_lineage(
        competitive_relationship(confidence=0.8, freshness=0.6),
        evidence_links=(relationship_evidence_link(),),
        span_links=(relationship_span_link(),),
    )
    AlgorithmicPeerBuilder(
        policy=AlgorithmicPeerPolicy(dimensions=("market_category", "chain", "use_case")),
        repository=repository,
    ).build(
        subject_candidate_id="candidate-a",
        peer_candidate_id="candidate-c",
        subject_dimensions={"market_category": "DeFi", "chain": "Ethereum", "use_case": None},
        peer_dimensions={"market_category": "DeFi", "chain": "Base", "use_case": None},
        scope="defi",
        effective_at=NOW,
        recorded_at=NOW,
        freshness=0.9,
    )

    result = PeerSetBuilder(repository=repository).build(
        subject_candidate_id="candidate-a",
        scope="defi",
        effective_at=NOW,
        recorded_at=NOW,
    )

    components = result.projection.components
    assert result.peer_set.status == "partial"
    assert result.peer_set.coverage == 0.8334
    assert result.peer_set.freshness == 0.75
    assert components["evidence_lineage_coverage"] == 1.0
    assert components["algorithmic_dimension_coverage"] == 0.6667
    assert "confidence_components" in result.peer_set.metadata


def test_phase_six_peer_set_status_is_conflict_aware_without_scoring_changes(tmp_path) -> None:
    repository = CompetitiveRepository(tmp_path / "competitive.sqlite")
    repository.save_relationship_with_lineage(
        competitive_relationship(status="disputed", conflict_status="disputed"),
        evidence_links=(relationship_evidence_link(),),
        span_links=(relationship_span_link(),),
    )

    result = PeerSetBuilder(repository=repository).build(
        subject_candidate_id="candidate-a",
        scope="defi",
        effective_at=NOW,
        recorded_at=NOW,
    )

    assert result.peer_set.status == "disputed"
    assert result.peer_set.conflict_status == "disputed"
    assert result.projection.components["conflict_status"] == "disputed"


def competitive_relationship(**overrides: object) -> CompetitiveRelationship:
    values = {
        "relationship_id": "competitive-relationship-1",
        "subject_candidate_id": "candidate-a",
        "peer_candidate_id": "candidate-b",
        "relationship_type": "direct_competitor",
        "status": "active",
        "predicate_id": "competes_with",
        "predicate_schema_version": "competitive-predicate-v1",
        "claim_id": "claim-1",
        "subject_entity_id": "entity-a",
        "peer_entity_id": "entity-b",
        "scope": "defi",
        "modality": "asserted",
        "polarity": "positive",
        "confidence": 0.8,
        "freshness": 0.9,
        "effective_at": NOW,
        "recorded_at": NOW,
        "schema_version": "competitive-relationship-v1",
    }
    values.update(overrides)
    return CompetitiveRelationship(**values)  # type: ignore[arg-type]


def relationship_evidence_link() -> CompetitiveRelationshipEvidenceLink:
    return CompetitiveRelationshipEvidenceLink(
        link_id="competitive-evidence-link-1",
        relationship_id="competitive-relationship-1",
        source_evidence_id="source-evidence-1",
        role="supporting",
        position=0,
        created_at=NOW,
        schema_version="competitive-link-v1",
    )


def relationship_span_link() -> CompetitiveRelationshipSpanLink:
    return CompetitiveRelationshipSpanLink(
        link_id="competitive-span-link-1",
        relationship_id="competitive-relationship-1",
        span_id="span-1",
        role="supporting",
        position=0,
        created_at=NOW,
        schema_version="competitive-link-v1",
    )
