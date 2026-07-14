from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from hunter.competitive.identity import competitive_id, competitive_relationship_id
from hunter.competitive.inputs import EvidenceClaimInput, RelationshipProjectionInput
from hunter.competitive.models import (
    CompetitiveRelationship,
    CompetitiveRelationshipEvidenceLink,
    CompetitiveRelationshipSpanLink,
    CompetitiveRelationshipType,
)
from hunter.competitive.predicates import COMPETITIVE_PREDICATE_SCHEMA_VERSION
from hunter.competitive.repository import CompetitiveRepository
from hunter.evidence_intelligence.repository import EvidenceIntelligenceRepository

COMPETITIVE_RELATIONSHIP_SCHEMA_VERSION = "competitive-relationship-v1"
COMPETITIVE_LINK_SCHEMA_VERSION = "competitive-link-v1"

EVIDENCE_BACKED_RELATIONSHIP_TYPES: dict[str, CompetitiveRelationshipType] = {
    "competes_with": "direct_competitor",
    "substitutes_for": "substitute",
    "centralized_incumbent_of": "centralized_incumbent",
    "same_category_as": "category_peer",
    "same_use_case_as": "use_case_peer",
}


@dataclass(frozen=True)
class CompetitiveRelationshipBuildResult:
    relationships: tuple[CompetitiveRelationship, ...]
    skipped: tuple[str, ...]


class CompetitiveRelationshipBuilder:
    def __init__(
        self,
        *,
        evidence_repository: EvidenceIntelligenceRepository,
        competitive_repository: CompetitiveRepository | None = None,
    ) -> None:
        self.evidence_repository = evidence_repository
        self.competitive_repository = competitive_repository

    def build_from_inputs(
        self,
        *,
        claim_inputs: Iterable[EvidenceClaimInput],
        projection_inputs: Iterable[RelationshipProjectionInput],
        persist: bool = True,
    ) -> CompetitiveRelationshipBuildResult:
        claim_by_id = {claim.claim_id: claim for claim in claim_inputs}
        relationships: list[CompetitiveRelationship] = []
        skipped: list[str] = []
        for projection in projection_inputs:
            claim = claim_by_id.get(projection.claim_id)
            if claim is None:
                skipped.append(f"{projection.relationship_id}:missing_claim_input")
                continue
            built = self._build_relationship(claim, projection)
            if built is None:
                skipped.append(f"{projection.relationship_id}:unsupported_or_unavailable")
                continue
            relationship, evidence_links, span_links = built
            relationships.append(relationship)
            if persist and self.competitive_repository is not None:
                self.competitive_repository.save_relationship_with_lineage(
                    relationship,
                    evidence_links=evidence_links,
                    span_links=span_links,
                )
        return CompetitiveRelationshipBuildResult(relationships=tuple(relationships), skipped=tuple(skipped))

    def _build_relationship(
        self,
        claim_input: EvidenceClaimInput,
        projection_input: RelationshipProjectionInput,
    ) -> (
        tuple[
            CompetitiveRelationship,
            tuple[CompetitiveRelationshipEvidenceLink, ...],
            tuple[CompetitiveRelationshipSpanLink, ...],
        ]
        | None
    ):
        if not claim_input.available or not projection_input.available:
            return None
        relationship_type = EVIDENCE_BACKED_RELATIONSHIP_TYPES.get(projection_input.predicate_id)
        if relationship_type is None:
            return None
        if claim_input.claim_status not in {"active", "historical_only"}:
            return None
        if projection_input.projection_status not in {"active", "historical_only"}:
            return None
        polarity = claim_input.polarity or projection_input.polarity
        if polarity != "positive":
            return None
        peer_candidate_id = projection_input.object_candidate_id
        if not peer_candidate_id or peer_candidate_id == claim_input.subject_candidate_id:
            return None
        recorded_at = claim_input.processed_at or projection_input.created_at
        effective_at = claim_input.valid_from or claim_input.observed_at
        if recorded_at is None or effective_at is None:
            return None
        confidence = min(claim_input.confidence, projection_input.confidence)
        freshness = claim_input.freshness
        scope = claim_input.scope or projection_input.scope
        relationship = CompetitiveRelationship(
            relationship_id=competitive_relationship_id(
                subject_candidate_id=claim_input.subject_candidate_id,
                peer_candidate_id=peer_candidate_id,
                relationship_type=relationship_type,
                claim_id=claim_input.claim_id,
                scope=scope,
                schema_version=COMPETITIVE_RELATIONSHIP_SCHEMA_VERSION,
            ),
            subject_candidate_id=claim_input.subject_candidate_id,
            peer_candidate_id=peer_candidate_id,
            relationship_type=relationship_type,
            status=claim_input.claim_status,  # type: ignore[arg-type]
            predicate_id=projection_input.predicate_id,
            predicate_schema_version=claim_input.predicate_schema_version or COMPETITIVE_PREDICATE_SCHEMA_VERSION,
            claim_id=claim_input.claim_id,
            subject_entity_id=projection_input.subject_entity_id,
            peer_entity_id=projection_input.object_entity_id,
            scope=scope,
            modality=claim_input.modality or projection_input.modality or "asserted",
            polarity=polarity,
            confidence=confidence,
            freshness=freshness,
            effective_at=effective_at,
            recorded_at=recorded_at,
            schema_version=COMPETITIVE_RELATIONSHIP_SCHEMA_VERSION,
            projection_id=projection_input.relationship_id,
            qualifier="",
            valid_from=claim_input.valid_from,
            valid_to=claim_input.valid_to,
            metadata={
                "relationship_source": "evidence_intelligence_claim_projection",
                "replay_mode": projection_input.replay_mode,
                "known_at_cutoff": projection_input.known_at_cutoff,
            },
        )
        return (
            relationship,
            _evidence_links(relationship.relationship_id, claim_input.source_evidence_ids, recorded_at),
            _span_links(relationship.relationship_id, claim_input.span_ids, recorded_at),
        )


def _evidence_links(
    relationship_id: str,
    source_evidence_ids: tuple[str, ...],
    created_at: datetime,
) -> tuple[CompetitiveRelationshipEvidenceLink, ...]:
    return tuple(
        CompetitiveRelationshipEvidenceLink(
            link_id=competitive_id(
                "relationship-evidence-link",
                {"relationship_id": relationship_id, "source_evidence_id": source_evidence_id, "position": position},
            ),
            relationship_id=relationship_id,
            source_evidence_id=source_evidence_id,
            role="supporting",
            position=position,
            created_at=created_at,
            schema_version=COMPETITIVE_LINK_SCHEMA_VERSION,
        )
        for position, source_evidence_id in enumerate(source_evidence_ids)
    )


def _span_links(
    relationship_id: str,
    span_ids: tuple[str, ...],
    created_at: datetime,
) -> tuple[CompetitiveRelationshipSpanLink, ...]:
    return tuple(
        CompetitiveRelationshipSpanLink(
            link_id=competitive_id(
                "relationship-span-link",
                {"relationship_id": relationship_id, "span_id": span_id, "position": position},
            ),
            relationship_id=relationship_id,
            span_id=span_id,
            role="supporting",
            position=position,
            created_at=created_at,
            schema_version=COMPETITIVE_LINK_SCHEMA_VERSION,
        )
        for position, span_id in enumerate(span_ids)
    )
