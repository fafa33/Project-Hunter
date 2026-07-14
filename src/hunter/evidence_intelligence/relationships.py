from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from hunter.evidence_intelligence.models import ClaimStatus, KnowledgeClaim, KnowledgeRelationship, PredicateRegistry
from hunter.evidence_intelligence.repository import EvidenceIntelligenceRepository
from hunter.execution.identity import identity

RELATIONSHIP_PROJECTION_VERSION = "relationship-projection-v1"
ACTIVE_DOCUMENT_STATUSES = frozenset({"active", "disputed", "historical_only"})
USABLE_AUTHORITY_STATUSES = frozenset(
    {
        "verified_official",
        "verified_affiliated",
        "verified_governance",
        "verified_repository",
        "third_party",
        "community",
        "ambiguous",
        "unverified",
    }
)
ACTIVE_CONFLICT_STATUSES = frozenset({"detected", "disputed"})


@dataclass(frozen=True)
class RelationshipView:
    relationship_id: str
    claim_id: str
    subject_entity_id: str
    predicate_id: str
    object_entity_id: str
    status: ClaimStatus
    confidence: float
    conflict_statuses: tuple[str, ...]
    document_statuses: tuple[str, ...]
    authority_statuses: tuple[str, ...]


class RelationshipProjectionService:
    def __init__(self, repository: EvidenceIntelligenceRepository) -> None:
        self.repository = repository

    def project_claim(
        self,
        claim: KnowledgeClaim,
        predicate_registry: PredicateRegistry,
        *,
        created_at: datetime,
    ) -> KnowledgeRelationship | None:
        if claim.object_entity_id is None:
            return None
        predicate = predicate_registry.get(claim.predicate_id)
        if predicate.schema_version != claim.predicate_schema_version:
            return None
        if not predicate.graph_projection_eligible:
            return None
        if not predicate.requires_object_entity:
            return None
        return KnowledgeRelationship(
            relationship_id=identity(
                "evidence-knowledge-relationship-projection",
                {
                    "claim_id": claim.claim_id,
                    "projection_version": RELATIONSHIP_PROJECTION_VERSION,
                },
            ),
            claim_id=claim.claim_id,
            subject_entity_id=claim.subject_entity_id,
            predicate_id=claim.predicate_id,
            object_entity_id=claim.object_entity_id,
            direction=predicate.direction,
            inverse_predicate_id=predicate.inverse_predicate,
            scope=claim.scope,
            polarity=claim.polarity,
            modality=claim.modality,
            valid_from=claim.valid_from,
            valid_to=claim.valid_to,
            confidence=claim.confidence,
            status=claim.status,
            projection_version=RELATIONSHIP_PROJECTION_VERSION,
            created_at=created_at,
        )

    def refresh(
        self,
        claims: tuple[KnowledgeClaim, ...],
        predicate_registry: PredicateRegistry,
        *,
        created_at: datetime,
    ) -> tuple[KnowledgeRelationship, ...]:
        projections = tuple(
            projection
            for claim in claims
            if (projection := self.project_claim(claim, predicate_registry, created_at=created_at)) is not None
        )
        for projection in projections:
            self.repository.save_relationship(projection)
        return projections

    def rebuild(
        self,
        claims: tuple[KnowledgeClaim, ...],
        predicate_registry: PredicateRegistry,
        *,
        created_at: datetime,
    ) -> tuple[KnowledgeRelationship, ...]:
        self.repository.clear_relationship_projections()
        return self.refresh(claims, predicate_registry, created_at=created_at)

    def view_at(
        self,
        relationship_id: str,
        cutoff: datetime,
        *,
        strict_known_by_hunter: bool = False,
    ) -> RelationshipView | None:
        projection = self.repository.relationship_projection(relationship_id)
        if projection is None:
            return None
        claim = self.repository.current_claim(str(projection["claim_id"]))
        if claim is None:
            return None
        claim_status = self.repository.claim_status_at(
            str(projection["claim_id"]),
            cutoff,
            strict_known_by_hunter=strict_known_by_hunter,
        )
        if claim_status is None:
            return None

        lineage = self.repository.claim_lineage(str(projection["claim_id"]))
        document_statuses = self._document_statuses(lineage, cutoff, strict_known_by_hunter)
        if any(status not in ACTIVE_DOCUMENT_STATUSES for status in document_statuses):
            claim_status = "unavailable"
        authority_statuses = self._authority_statuses(lineage, cutoff, strict_known_by_hunter)
        if any(status not in USABLE_AUTHORITY_STATUSES for status in authority_statuses):
            claim_status = "unavailable"
        conflict_statuses = self._conflict_statuses(lineage, cutoff, strict_known_by_hunter)
        if any(status in ACTIVE_CONFLICT_STATUSES for status in conflict_statuses):
            claim_status = "disputed"

        return RelationshipView(
            relationship_id=str(projection["relationship_id"]),
            claim_id=str(projection["claim_id"]),
            subject_entity_id=str(projection["subject_entity_id"]),
            predicate_id=str(projection["predicate_id"]),
            object_entity_id=str(projection["object_entity_id"]),
            status=claim_status,  # type: ignore[arg-type]
            confidence=float(claim["confidence"]),
            conflict_statuses=conflict_statuses,
            document_statuses=document_statuses,
            authority_statuses=authority_statuses,
        )

    def _document_statuses(
        self,
        lineage: dict[str, tuple[dict[str, Any], ...]],
        cutoff: datetime,
        strict_known_by_hunter: bool,
    ) -> tuple[str, ...]:
        statuses: list[str] = []
        for document in lineage["documents"]:
            status = self.repository.document_status_at(
                str(document["document_id"]),
                cutoff,
                strict_known_by_hunter=strict_known_by_hunter,
            )
            statuses.append(status or "unavailable")
        return tuple(statuses)

    def _authority_statuses(
        self,
        lineage: dict[str, tuple[dict[str, Any], ...]],
        cutoff: datetime,
        strict_known_by_hunter: bool,
    ) -> tuple[str, ...]:
        statuses: list[str] = []
        for document in lineage["documents"]:
            status = self.repository.authority_status_at(
                str(document["document_id"]),
                cutoff,
                strict_known_by_hunter=strict_known_by_hunter,
            )
            statuses.append(status or "unavailable")
        return tuple(statuses)

    def _conflict_statuses(
        self,
        lineage: dict[str, tuple[dict[str, Any], ...]],
        cutoff: datetime,
        strict_known_by_hunter: bool,
    ) -> tuple[str, ...]:
        statuses: list[str] = []
        for conflict in lineage["conflicts"]:
            status = self.repository.conflict_status_at(
                str(conflict["conflict_id"]),
                cutoff,
                strict_known_by_hunter=strict_known_by_hunter,
            )
            if status is not None:
                statuses.append(status)
        return tuple(statuses)
