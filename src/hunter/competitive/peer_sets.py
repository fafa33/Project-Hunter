from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from hunter.competitive.identity import competitive_id, peer_set_id
from hunter.competitive.models import (
    AlgorithmicPeerRelationship,
    CompetitiveRelationship,
    PeerSet,
    PeerSetEvidenceLink,
    PeerSetMember,
    PeerSetSpanLink,
    PeerSetStatus,
)
from hunter.competitive.policies import DEFAULT_POLICY_ID, DEFAULT_POLICY_VERSION
from hunter.competitive.repository import CompetitiveRepository

PEER_SET_SCHEMA_VERSION = "competitive-peer-set-v1"
PEER_SET_MEMBER_SCHEMA_VERSION = "competitive-peer-set-member-v1"
PEER_SET_LINK_SCHEMA_VERSION = "competitive-peer-set-link-v1"


@dataclass(frozen=True)
class PeerSetProjection:
    confidence: float
    coverage: float
    freshness: float
    completeness: float
    status: PeerSetStatus
    components: dict[str, Any]


@dataclass(frozen=True)
class PeerSetBuildResult:
    peer_set: PeerSet
    members: tuple[PeerSetMember, ...]
    evidence_links: tuple[PeerSetEvidenceLink, ...]
    span_links: tuple[PeerSetSpanLink, ...]
    projection: PeerSetProjection


class PeerSetBuilder:
    def __init__(self, *, repository: CompetitiveRepository) -> None:
        self.repository = repository

    def build(
        self,
        *,
        subject_candidate_id: str,
        scope: str,
        effective_at: datetime,
        recorded_at: datetime,
        peer_set_version: str = PEER_SET_SCHEMA_VERSION,
        policy_id: str = DEFAULT_POLICY_ID,
        policy_version: str = DEFAULT_POLICY_VERSION,
        evidence_backed_relationships: Iterable[CompetitiveRelationship] = (),
        algorithmic_relationships: Iterable[AlgorithmicPeerRelationship] = (),
        persist: bool = True,
    ) -> PeerSetBuildResult:
        evidence_relationships = tuple(evidence_backed_relationships) or _competitive_relationships(
            self.repository.competitive_relationships_for_subject(subject_candidate_id)
        )
        algorithmic_peers = tuple(algorithmic_relationships) or _algorithmic_relationships(
            self.repository.algorithmic_relationships_for_subject(subject_candidate_id)
        )
        projection = self._project(evidence_relationships, algorithmic_peers)
        peer_set = PeerSet(
            peer_set_id=peer_set_id(
                subject_candidate_id=subject_candidate_id,
                scope=scope,
                peer_set_version=peer_set_version,
                policy_id=policy_id,
                policy_version=policy_version,
            ),
            subject_candidate_id=subject_candidate_id,
            scope=scope,
            status=projection.status,
            peer_set_version=peer_set_version,
            policy_id=policy_id,
            policy_version=policy_version,
            evidence_backed_count=len(evidence_relationships),
            algorithmic_peer_count=len(algorithmic_peers),
            confidence=projection.confidence,
            coverage=projection.coverage,
            freshness=projection.freshness,
            effective_at=effective_at,
            recorded_at=recorded_at,
            schema_version=PEER_SET_SCHEMA_VERSION,
            conflict_status=str(projection.components["conflict_status"]),
            metadata={
                "confidence_components": projection.components,
                "relationship_source": "persisted_competitive_and_algorithmic_outputs",
            },
        )
        members = _members(peer_set, evidence_relationships, algorithmic_peers)
        evidence_links, span_links = self._lineage_links(peer_set.peer_set_id, evidence_relationships, recorded_at)
        if persist:
            self.repository.save_peer_set_with_lineage(
                peer_set,
                members=members,
                evidence_links=evidence_links,
                span_links=span_links,
            )
        return PeerSetBuildResult(
            peer_set=peer_set,
            members=members,
            evidence_links=evidence_links,
            span_links=span_links,
            projection=projection,
        )

    def _project(
        self,
        evidence_relationships: tuple[CompetitiveRelationship, ...],
        algorithmic_peers: tuple[AlgorithmicPeerRelationship, ...],
    ) -> PeerSetProjection:
        all_relationships = (*evidence_relationships, *algorithmic_peers)
        if not all_relationships:
            return PeerSetProjection(
                confidence=0.0,
                coverage=0.0,
                freshness=0.0,
                completeness=0.0,
                status="unavailable",
                components={
                    "evidence_backed_confidence": 0.0,
                    "algorithmic_confidence": 0.0,
                    "evidence_lineage_coverage": 0.0,
                    "algorithmic_dimension_coverage": 0.0,
                    "conflict_status": "none",
                },
            )
        evidence_confidence = _average(item.confidence for item in evidence_relationships)
        algorithmic_confidence = _average(item.confidence for item in algorithmic_peers)
        evidence_coverage = self._evidence_lineage_coverage(evidence_relationships)
        algorithmic_coverage = self._algorithmic_dimension_coverage(algorithmic_peers)
        coverage = _weighted_average(
            (evidence_coverage, len(evidence_relationships)),
            (algorithmic_coverage, len(algorithmic_peers)),
        )
        confidence = _weighted_average(
            (evidence_confidence * evidence_coverage, len(evidence_relationships)),
            (algorithmic_confidence * algorithmic_coverage, len(algorithmic_peers)),
        )
        freshness = _average(item.freshness for item in all_relationships)
        completeness = coverage
        conflict_status = _conflict_status(evidence_relationships, algorithmic_peers)
        status = _peer_set_status(
            conflict_status=conflict_status,
            completeness=completeness,
            relationships=all_relationships,
        )
        return PeerSetProjection(
            confidence=confidence,
            coverage=coverage,
            freshness=freshness,
            completeness=completeness,
            status=status,
            components={
                "evidence_backed_confidence": evidence_confidence,
                "algorithmic_confidence": algorithmic_confidence,
                "evidence_lineage_coverage": evidence_coverage,
                "algorithmic_dimension_coverage": algorithmic_coverage,
                "completeness": completeness,
                "conflict_status": conflict_status,
            },
        )

    def _evidence_lineage_coverage(self, relationships: tuple[CompetitiveRelationship, ...]) -> float:
        if not relationships:
            return 0.0
        covered = 0
        for relationship in relationships:
            lineage = self.repository.relationship_lineage(relationship.relationship_id)
            if lineage["source_evidence"] and lineage["spans"]:
                covered += 1
        return round(covered / len(relationships), 4)

    def _algorithmic_dimension_coverage(self, relationships: tuple[AlgorithmicPeerRelationship, ...]) -> float:
        if not relationships:
            return 0.0
        coverages: list[float] = []
        for relationship in relationships:
            dimensions = self.repository.comparison_dimensions_for_relationship(relationship.relationship_id)
            if not dimensions:
                coverages.append(0.0)
                continue
            available = sum(1 for dimension in dimensions if str(dimension["match_status"]) != "missing")
            coverages.append(available / len(dimensions))
        return round(sum(coverages) / len(coverages), 4)

    def _lineage_links(
        self,
        peer_set_id_value: str,
        relationships: tuple[CompetitiveRelationship, ...],
        created_at: datetime,
    ) -> tuple[tuple[PeerSetEvidenceLink, ...], tuple[PeerSetSpanLink, ...]]:
        evidence_ids: list[str] = []
        span_ids: list[str] = []
        for relationship in relationships:
            lineage = self.repository.relationship_lineage(relationship.relationship_id)
            evidence_ids.extend(str(link["source_evidence_id"]) for link in lineage["source_evidence"])
            span_ids.extend(str(link["span_id"]) for link in lineage["spans"])
        evidence_links = tuple(
            PeerSetEvidenceLink(
                link_id=competitive_id(
                    "peer-set-evidence-link",
                    {"peer_set_id": peer_set_id_value, "source_evidence_id": source_evidence_id, "position": position},
                ),
                peer_set_id=peer_set_id_value,
                source_evidence_id=source_evidence_id,
                role="supporting",
                position=position,
                created_at=created_at,
                schema_version=PEER_SET_LINK_SCHEMA_VERSION,
            )
            for position, source_evidence_id in enumerate(tuple(dict.fromkeys(evidence_ids)))
        )
        span_links = tuple(
            PeerSetSpanLink(
                link_id=competitive_id(
                    "peer-set-span-link",
                    {"peer_set_id": peer_set_id_value, "span_id": span_id, "position": position},
                ),
                peer_set_id=peer_set_id_value,
                span_id=span_id,
                role="supporting",
                position=position,
                created_at=created_at,
                schema_version=PEER_SET_LINK_SCHEMA_VERSION,
            )
            for position, span_id in enumerate(tuple(dict.fromkeys(span_ids)))
        )
        return evidence_links, span_links


def _members(
    peer_set: PeerSet,
    evidence_relationships: tuple[CompetitiveRelationship, ...],
    algorithmic_peers: tuple[AlgorithmicPeerRelationship, ...],
) -> tuple[PeerSetMember, ...]:
    members: list[PeerSetMember] = []
    position = 0
    for relationship in evidence_relationships:
        members.append(
            PeerSetMember(
                member_id=competitive_id(
                    "peer-set-member",
                    {"peer_set_id": peer_set.peer_set_id, "relationship_id": relationship.relationship_id},
                ),
                peer_set_id=peer_set.peer_set_id,
                peer_candidate_id=relationship.peer_candidate_id,
                member_role="evidence_backed_competitor",
                relationship_kind="evidence_backed",
                relationship_id=relationship.relationship_id,
                status=peer_set.status,
                confidence=relationship.confidence,
                freshness=relationship.freshness,
                position=position,
                effective_at=peer_set.effective_at,
                recorded_at=peer_set.recorded_at,
                schema_version=PEER_SET_MEMBER_SCHEMA_VERSION,
            )
        )
        position += 1
    for relationship in algorithmic_peers:
        members.append(
            PeerSetMember(
                member_id=competitive_id(
                    "peer-set-member",
                    {"peer_set_id": peer_set.peer_set_id, "relationship_id": relationship.relationship_id},
                ),
                peer_set_id=peer_set.peer_set_id,
                peer_candidate_id=relationship.peer_candidate_id,
                member_role="algorithmic_peer",
                relationship_kind="algorithmic_similarity",
                relationship_id=relationship.relationship_id,
                status=peer_set.status,
                confidence=relationship.confidence,
                freshness=relationship.freshness,
                position=position,
                effective_at=peer_set.effective_at,
                recorded_at=peer_set.recorded_at,
                schema_version=PEER_SET_MEMBER_SCHEMA_VERSION,
            )
        )
        position += 1
    return tuple(members)


def _competitive_relationships(rows: tuple[dict[str, Any], ...]) -> tuple[CompetitiveRelationship, ...]:
    return tuple(
        CompetitiveRelationship(
            **{
                **row,
                "effective_at": _datetime_value(row["effective_at"]),
                "recorded_at": _datetime_value(row["recorded_at"]),
                "valid_from": _datetime_value(row.get("valid_from")),
                "valid_to": _datetime_value(row.get("valid_to")),
                "metadata": _metadata(row.get("metadata")),
            }
        )
        for row in rows
    )


def _algorithmic_relationships(rows: tuple[dict[str, Any], ...]) -> tuple[AlgorithmicPeerRelationship, ...]:
    return tuple(
        AlgorithmicPeerRelationship(
            **{
                **row,
                "effective_at": _datetime_value(row["effective_at"]),
                "recorded_at": _datetime_value(row["recorded_at"]),
                "metadata": _metadata(row.get("metadata")),
            }
        )
        for row in rows
    )


def _peer_set_status(
    *,
    conflict_status: str,
    completeness: float,
    relationships: tuple[CompetitiveRelationship | AlgorithmicPeerRelationship, ...],
) -> PeerSetStatus:
    if conflict_status != "none":
        return "disputed"
    if any(relationship.status == "historical_only" for relationship in relationships):
        return "historical_only"
    if completeness < 1.0:
        return "partial"
    return "active"


def _conflict_status(
    evidence_relationships: tuple[CompetitiveRelationship, ...],
    algorithmic_peers: tuple[AlgorithmicPeerRelationship, ...],
) -> str:
    if any(
        relationship.status == "disputed" or relationship.conflict_status != "none"
        for relationship in evidence_relationships
    ):
        return "disputed"
    if any(relationship.status == "disputed" for relationship in algorithmic_peers):
        return "disputed"
    return "none"


def _weighted_average(*items: tuple[float, int]) -> float:
    weight = sum(item_weight for _, item_weight in items)
    if weight <= 0:
        return 0.0
    return round(sum(value * item_weight for value, item_weight in items) / weight, 4)


def _average(values: Iterable[float]) -> float:
    collected = tuple(float(value) for value in values)
    if not collected:
        return 0.0
    return round(sum(collected) / len(collected), 4)


def _metadata(value: Any) -> dict[str, Any]:
    if isinstance(value, str) and value:
        return dict(json.loads(value))
    if isinstance(value, dict):
        return value
    return {}


def _datetime_value(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        return datetime.fromisoformat(value)
    return None
