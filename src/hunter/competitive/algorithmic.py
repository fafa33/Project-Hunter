from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from hunter.competitive.identity import algorithmic_peer_relationship_id, competitive_id
from hunter.competitive.models import AlgorithmicPeerRelationship, ComparisonDimension
from hunter.competitive.policies import AlgorithmicPeerDecision, AlgorithmicPeerPolicy
from hunter.competitive.repository import CompetitiveRepository

ALGORITHMIC_PEER_RELATIONSHIP_SCHEMA_VERSION = "algorithmic-peer-relationship-v1"
ALGORITHMIC_COMPARISON_DIMENSION_SCHEMA_VERSION = "algorithmic-comparison-dimension-v1"


@dataclass(frozen=True)
class AlgorithmicPeerBuildResult:
    decision: AlgorithmicPeerDecision
    relationship: AlgorithmicPeerRelationship | None
    dimensions: tuple[ComparisonDimension, ...]


class AlgorithmicPeerBuilder:
    def __init__(
        self,
        *,
        policy: AlgorithmicPeerPolicy | None = None,
        repository: CompetitiveRepository | None = None,
    ) -> None:
        self.policy = policy or AlgorithmicPeerPolicy()
        self.repository = repository

    def build(
        self,
        *,
        subject_candidate_id: str,
        peer_candidate_id: str,
        subject_dimensions: Mapping[str, str | None],
        peer_dimensions: Mapping[str, str | None],
        scope: str,
        effective_at: datetime,
        recorded_at: datetime,
        freshness: float = 1.0,
        persist: bool = True,
    ) -> AlgorithmicPeerBuildResult:
        decision = self.policy.evaluate(
            subject_dimensions=subject_dimensions,
            peer_dimensions=peer_dimensions,
        )
        if not decision.accepted:
            return AlgorithmicPeerBuildResult(decision=decision, relationship=None, dimensions=())
        confidence = _confidence(decision)
        relationship = AlgorithmicPeerRelationship(
            relationship_id=algorithmic_peer_relationship_id(
                subject_candidate_id=subject_candidate_id,
                peer_candidate_id=peer_candidate_id,
                relationship_type=decision.relationship_type,
                policy_id=decision.policy_id,
                policy_version=decision.policy_version,
                scope=scope,
            ),
            subject_candidate_id=subject_candidate_id,
            peer_candidate_id=peer_candidate_id,
            relationship_type=decision.relationship_type,
            status="active",
            policy_id=decision.policy_id,
            policy_version=decision.policy_version,
            scope=scope,
            compared_dimension_count=decision.compared_dimension_count,
            matched_dimension_count=decision.matched_dimension_count,
            missing_dimension_count=decision.missing_dimension_count,
            similarity=decision.similarity,
            confidence=confidence,
            freshness=freshness,
            effective_at=effective_at,
            recorded_at=recorded_at,
            schema_version=ALGORITHMIC_PEER_RELATIONSHIP_SCHEMA_VERSION,
            metadata={
                "relationship_kind": "algorithmic_similarity",
                "not_evidence_backed_competition": True,
                "reason": decision.reason,
            },
        )
        dimensions = _comparison_dimensions(
            relationship=relationship,
            decision=decision,
            effective_at=effective_at,
            recorded_at=recorded_at,
        )
        if persist and self.repository is not None:
            self.repository.save_algorithmic_peer_relationship(relationship)
            for dimension in dimensions:
                self.repository.save_comparison_dimension(dimension)
        return AlgorithmicPeerBuildResult(decision=decision, relationship=relationship, dimensions=dimensions)


def _comparison_dimensions(
    *,
    relationship: AlgorithmicPeerRelationship,
    decision: AlgorithmicPeerDecision,
    effective_at: datetime,
    recorded_at: datetime,
) -> tuple[ComparisonDimension, ...]:
    return tuple(
        ComparisonDimension(
            dimension_id=competitive_id(
                "algorithmic-comparison-dimension",
                {
                    "relationship_id": relationship.relationship_id,
                    "dimension_type": dimension.dimension_type,
                    "policy_id": decision.policy_id,
                    "policy_version": decision.policy_version,
                },
            ),
            subject_candidate_id=relationship.subject_candidate_id,
            peer_candidate_id=relationship.peer_candidate_id,
            dimension_type=dimension.dimension_type,
            subject_value=dimension.subject_value,
            peer_value=dimension.peer_value,
            match_status=dimension.match_status,
            relationship_kind="algorithmic_similarity",
            relationship_id=relationship.relationship_id,
            policy_id=decision.policy_id,
            policy_version=decision.policy_version,
            confidence=relationship.confidence,
            effective_at=effective_at,
            recorded_at=recorded_at,
            schema_version=ALGORITHMIC_COMPARISON_DIMENSION_SCHEMA_VERSION,
        )
        for dimension in decision.dimension_results
    )


def _confidence(decision: AlgorithmicPeerDecision) -> float:
    coverage = 0.0
    if decision.compared_dimension_count:
        coverage = (
            decision.compared_dimension_count - decision.missing_dimension_count
        ) / decision.compared_dimension_count
    return round(decision.similarity * coverage, 4)
