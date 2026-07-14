from __future__ import annotations

from dataclasses import fields
from datetime import UTC, datetime

import pytest

from hunter.competitive import (
    ALGORITHMIC_PEER_RELATIONSHIP_TYPES,
    COMPETITIVE_RELATIONSHIP_TYPES,
    AlgorithmicPeerBuilder,
    AlgorithmicPeerPolicy,
    AlgorithmicPeerRelationship,
    ComparisonDimension,
    CompetitiveAssessment,
    CompetitiveRelationship,
    CompetitiveRepository,
    PeerSet,
    PeerSetMember,
    algorithmic_peer_relationship_id,
    competitive_predicate_registry,
    competitive_relationship_id,
    peer_set_id,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def test_phase_one_competitive_models_validate_required_fields() -> None:
    assert competitive_relationship().relationship_type == "direct_competitor"
    assert algorithmic_relationship().relationship_type == "same_category_similarity"
    assert peer_set().status == "active"
    assert peer_set_member().member_role == "evidence_backed_competitor"
    assert comparison_dimension().dimension_type == "market_category"
    assert competitive_assessment().status == "active"


def test_phase_one_evidence_backed_and_algorithmic_relationships_are_separate() -> None:
    assert "direct_competitor" in COMPETITIVE_RELATIONSHIP_TYPES
    assert "same_category_similarity" not in COMPETITIVE_RELATIONSHIP_TYPES
    assert "same_category_similarity" in ALGORITHMIC_PEER_RELATIONSHIP_TYPES
    assert "direct_competitor" not in ALGORITHMIC_PEER_RELATIONSHIP_TYPES

    with pytest.raises(ValueError, match="relationship_type must be one of"):
        competitive_relationship(relationship_type="same_category_similarity")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="relationship_type must be one of"):
        algorithmic_relationship(relationship_type="direct_competitor")  # type: ignore[arg-type]


def test_phase_one_peer_set_member_role_must_match_relationship_kind() -> None:
    with pytest.raises(ValueError, match="evidence-backed members require"):
        peer_set_member(relationship_kind="algorithmic_similarity")  # type: ignore[arg-type]
    assert (
        peer_set_member(
            member_role="algorithmic_peer",
            relationship_kind="algorithmic_similarity",
            relationship_id="algorithmic-relationship-1",
        ).member_role
        == "algorithmic_peer"
    )


def test_phase_one_algorithmic_peer_policy_is_deterministic_and_preserves_missing_dimensions() -> None:
    policy = AlgorithmicPeerPolicy(dimensions=("market_category", "chain", "use_case"))

    first = policy.evaluate(
        subject_dimensions={"market_category": "DeFi", "chain": "Ethereum", "use_case": None},
        peer_dimensions={"market_category": "defi", "chain": "Base", "use_case": None},
    )
    second = policy.evaluate(
        subject_dimensions={"market_category": "DeFi", "chain": "Ethereum", "use_case": None},
        peer_dimensions={"market_category": "defi", "chain": "Base", "use_case": None},
    )

    assert first == second
    assert first.accepted is True
    assert first.matched_dimension_count == 1
    assert first.missing_dimension_count == 1
    assert first.similarity == 0.5
    assert first.reason == "deterministic_similarity_policy_matched"
    assert [result.match_status for result in first.dimension_results] == ["matched", "different", "missing"]


def test_phase_one_algorithmic_policy_rejects_unsupported_or_forbidden_dimensions() -> None:
    with pytest.raises(ValueError, match="unsupported comparison dimension"):
        AlgorithmicPeerPolicy(dimensions=("co_mention",))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="minimum_matched_dimensions must be positive"):
        AlgorithmicPeerPolicy(minimum_matched_dimensions=0)


def test_phase_five_algorithmic_builder_persists_separate_relationship_and_dimensions(tmp_path) -> None:
    repository = CompetitiveRepository(tmp_path / "competitive.sqlite")
    policy = AlgorithmicPeerPolicy(dimensions=("market_category", "chain", "use_case"))
    builder = AlgorithmicPeerBuilder(policy=policy, repository=repository)

    result = builder.build(
        subject_candidate_id="candidate-a",
        peer_candidate_id="candidate-b",
        subject_dimensions={"market_category": "DeFi", "chain": "Ethereum", "use_case": None},
        peer_dimensions={"market_category": "defi", "chain": "Base", "use_case": None},
        scope="defi",
        effective_at=NOW,
        recorded_at=NOW,
    )
    repeat = builder.build(
        subject_candidate_id="candidate-a",
        peer_candidate_id="candidate-b",
        subject_dimensions={"market_category": "DeFi", "chain": "Ethereum", "use_case": None},
        peer_dimensions={"market_category": "defi", "chain": "Base", "use_case": None},
        scope="defi",
        effective_at=NOW,
        recorded_at=NOW,
    )

    assert result.relationship is not None
    assert repeat.relationship is not None
    assert result.relationship.relationship_id == repeat.relationship.relationship_id
    assert result.relationship.relationship_type == "same_category_similarity"
    assert result.relationship.metadata["relationship_kind"] == "algorithmic_similarity"
    assert result.relationship.metadata["not_evidence_backed_competition"] is True
    assert repository.count("algorithmic_peer_relationships") == 1
    assert repository.count("competitive_relationships") == 0
    assert repository.count("competitive_comparison_dimensions") == 3
    assert [dimension.match_status for dimension in result.dimensions] == ["matched", "different", "missing"]


def test_phase_five_algorithmic_builder_preserves_below_threshold_and_missing_dimensions(tmp_path) -> None:
    repository = CompetitiveRepository(tmp_path / "competitive.sqlite")
    builder = AlgorithmicPeerBuilder(
        policy=AlgorithmicPeerPolicy(dimensions=("market_category", "chain"), minimum_matched_dimensions=2),
        repository=repository,
    )

    result = builder.build(
        subject_candidate_id="candidate-a",
        peer_candidate_id="candidate-b",
        subject_dimensions={"market_category": "DeFi", "chain": None},
        peer_dimensions={"market_category": "Gaming", "chain": None},
        scope="global",
        effective_at=NOW,
        recorded_at=NOW,
    )

    assert result.decision.accepted is False
    assert result.decision.missing_dimension_count == 1
    assert result.relationship is None
    assert result.dimensions == ()
    assert repository.count("algorithmic_peer_relationships") == 0


def test_phase_one_predicate_extensions_are_versioned_and_graph_ready_where_eligible() -> None:
    registry = competitive_predicate_registry(created_at=NOW)

    competes = registry.get("competes_with")
    assert competes.schema_version == registry.schema_version
    assert competes.graph_projection_eligible is True
    assert competes.symmetric is True
    assert "co-mention is insufficient" in competes.support_requirements

    target_segment = registry.get("targets_market_segment")
    assert target_segment.allows_literal_value is True
    assert target_segment.graph_projection_eligible is False

    registry.validate_claim_shape(
        predicate_id="competes_with",
        subject_type="protocol",
        object_type="protocol",
        literal_value_type=None,
        modality="asserted",
        polarity="positive",
    )


def test_phase_one_deterministic_ids_are_stable() -> None:
    assert competitive_relationship_id(
        subject_candidate_id="candidate-a",
        peer_candidate_id="candidate-b",
        relationship_type="direct_competitor",
        claim_id="claim-1",
        scope="dex",
        schema_version="competitive-relationship-v1",
    ) == competitive_relationship_id(
        subject_candidate_id="candidate-a",
        peer_candidate_id="candidate-b",
        relationship_type="direct_competitor",
        claim_id="claim-1",
        scope="dex",
        schema_version="competitive-relationship-v1",
    )
    assert algorithmic_peer_relationship_id(
        subject_candidate_id="candidate-a",
        peer_candidate_id="candidate-b",
        relationship_type="same_category_similarity",
        policy_id="policy-1",
        policy_version="policy-v1",
        scope="defi",
    ).startswith("competitive-algorithmic-peer-relationship:")
    assert peer_set_id(
        subject_candidate_id="candidate-a",
        scope="defi",
        peer_set_version="peer-set-v1",
        policy_id="policy-1",
        policy_version="policy-v1",
    ).startswith("competitive-peer-set:")


def test_phase_one_models_do_not_embed_sql_authoritative_lineage_lists() -> None:
    forbidden = {
        "source_evidence_ids",
        "supporting_span_ids",
        "claim_ids",
        "relationship_ids",
        "conflict_ids",
        "peer_candidate_ids",
    }
    for model in (
        CompetitiveRelationship,
        AlgorithmicPeerRelationship,
        PeerSet,
        PeerSetMember,
        ComparisonDimension,
        CompetitiveAssessment,
    ):
        assert forbidden.isdisjoint({field.name for field in fields(model)})


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
        "scope": "decentralized-exchange",
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


def algorithmic_relationship(**overrides: object) -> AlgorithmicPeerRelationship:
    values = {
        "relationship_id": "algorithmic-relationship-1",
        "subject_candidate_id": "candidate-a",
        "peer_candidate_id": "candidate-b",
        "relationship_type": "same_category_similarity",
        "status": "active",
        "policy_id": "policy-1",
        "policy_version": "policy-v1",
        "scope": "defi",
        "compared_dimension_count": 3,
        "matched_dimension_count": 2,
        "missing_dimension_count": 1,
        "similarity": 1.0,
        "confidence": 0.7,
        "freshness": 0.9,
        "effective_at": NOW,
        "recorded_at": NOW,
        "schema_version": "algorithmic-peer-relationship-v1",
    }
    values.update(overrides)
    return AlgorithmicPeerRelationship(**values)  # type: ignore[arg-type]


def peer_set(**overrides: object) -> PeerSet:
    values = {
        "peer_set_id": "peer-set-1",
        "subject_candidate_id": "candidate-a",
        "scope": "defi",
        "status": "active",
        "peer_set_version": "peer-set-v1",
        "policy_id": "policy-1",
        "policy_version": "policy-v1",
        "evidence_backed_count": 1,
        "algorithmic_peer_count": 1,
        "confidence": 0.75,
        "coverage": 0.5,
        "freshness": 0.9,
        "effective_at": NOW,
        "recorded_at": NOW,
        "schema_version": "competitive-peer-set-v1",
    }
    values.update(overrides)
    return PeerSet(**values)  # type: ignore[arg-type]


def peer_set_member(**overrides: object) -> PeerSetMember:
    values = {
        "member_id": "member-1",
        "peer_set_id": "peer-set-1",
        "peer_candidate_id": "candidate-b",
        "member_role": "evidence_backed_competitor",
        "relationship_kind": "evidence_backed",
        "relationship_id": "competitive-relationship-1",
        "status": "active",
        "confidence": 0.75,
        "freshness": 0.9,
        "position": 0,
        "effective_at": NOW,
        "recorded_at": NOW,
        "schema_version": "competitive-peer-set-member-v1",
    }
    values.update(overrides)
    return PeerSetMember(**values)  # type: ignore[arg-type]


def comparison_dimension(**overrides: object) -> ComparisonDimension:
    values = {
        "dimension_id": "dimension-1",
        "subject_candidate_id": "candidate-a",
        "peer_candidate_id": "candidate-b",
        "dimension_type": "market_category",
        "subject_value": "defi",
        "peer_value": "defi",
        "match_status": "matched",
        "relationship_kind": "algorithmic_similarity",
        "relationship_id": "algorithmic-relationship-1",
        "policy_id": "policy-1",
        "policy_version": "policy-v1",
        "confidence": 0.75,
        "effective_at": NOW,
        "recorded_at": NOW,
        "schema_version": "competitive-comparison-dimension-v1",
    }
    values.update(overrides)
    return ComparisonDimension(**values)  # type: ignore[arg-type]


def competitive_assessment(**overrides: object) -> CompetitiveAssessment:
    values = {
        "assessment_id": "assessment-1",
        "subject_candidate_id": "candidate-a",
        "peer_set_id": "peer-set-1",
        "status": "active",
        "evidence_backed_competitors": 1,
        "algorithmic_peers": 1,
        "missing_evidence_count": 0,
        "conflict_count": 0,
        "confidence": 0.75,
        "coverage": 0.5,
        "freshness": 0.9,
        "mode": "current",
        "effective_at": NOW,
        "recorded_at": NOW,
        "schema_version": "competitive-assessment-v1",
    }
    values.update(overrides)
    return CompetitiveAssessment(**values)  # type: ignore[arg-type]
