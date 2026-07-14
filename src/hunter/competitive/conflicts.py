from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from hunter.competitive.identity import competitive_id
from hunter.competitive.models import CompetitiveConflictLink, CompetitiveRelationship, PeerSet
from hunter.competitive.repository import CompetitiveRepository

COMPETITIVE_CONFLICT_SCHEMA_VERSION = "competitive-conflict-v1"
COMPETITIVE_REPLAY_MODE = Literal["current", "historical_strict_known_by_hunter", "reconstructed_after_cutoff"]

CONFLICT_ACTIVE_STATUSES = frozenset(
    {"active", "disputed", "historical_only", "superseded", "retracted", "source_removed"}
)
CONFLICT_RESOLVED_STATUSES = frozenset({"superseded", "retracted", "source_removed"})


@dataclass(frozen=True)
class CompetitiveConflictCandidate:
    conflict_id: str
    left_relationship_id: str
    right_relationship_id: str
    status: str
    reason: str


@dataclass(frozen=True)
class CompetitiveReplayContext:
    cutoff: datetime | None = None
    strict_known_by_hunter: bool = False

    @property
    def mode(self) -> COMPETITIVE_REPLAY_MODE:
        if self.cutoff is None:
            return "current"
        if self.strict_known_by_hunter:
            return "historical_strict_known_by_hunter"
        return "reconstructed_after_cutoff"

    @property
    def known_at_cutoff(self) -> bool:
        return self.cutoff is None or self.strict_known_by_hunter


@dataclass(frozen=True)
class CompetitiveReplayView:
    mode: COMPETITIVE_REPLAY_MODE
    known_at_cutoff: bool
    relationships: tuple[CompetitiveRelationship, ...]
    peer_sets: tuple[PeerSet, ...]
    conflict_links: tuple[dict[str, Any], ...]


class CompetitiveConflictDetector:
    def __init__(self, repository: CompetitiveRepository) -> None:
        self.repository = repository

    def detect(
        self,
        relationships: tuple[CompetitiveRelationship, ...],
        *,
        persist: bool = True,
        detected_at: datetime | None = None,
    ) -> tuple[CompetitiveConflictCandidate, ...]:
        detected = detected_at or datetime.now(tz=UTC)
        candidates: list[CompetitiveConflictCandidate] = []
        for index, left in enumerate(relationships):
            if left.status not in CONFLICT_ACTIVE_STATUSES:
                continue
            for right in relationships[index + 1 :]:
                if right.status not in CONFLICT_ACTIVE_STATUSES:
                    continue
                reason = _conflict_reason(left, right)
                if reason == "":
                    continue
                candidate = CompetitiveConflictCandidate(
                    conflict_id=competitive_id(
                        "conflict",
                        {
                            "left": min(left.relationship_id, right.relationship_id),
                            "right": max(left.relationship_id, right.relationship_id),
                            "predicate": left.predicate_id,
                            "scope": left.scope,
                            "qualifier": left.qualifier,
                            "modality": left.modality,
                        },
                    ),
                    left_relationship_id=left.relationship_id,
                    right_relationship_id=right.relationship_id,
                    status=_conflict_status(left, right),
                    reason=reason,
                )
                candidates.append(candidate)
                if persist:
                    self.repository.save_conflict_links(_conflict_links(candidate, left, right, detected))
        return tuple(candidates)


class CompetitiveReplayQuery:
    def __init__(self, repository: CompetitiveRepository) -> None:
        self.repository = repository

    def view_for_subject(
        self,
        subject_candidate_id: str,
        context: CompetitiveReplayContext,
    ) -> CompetitiveReplayView:
        relationships = self.relationships_for_subject(subject_candidate_id, context)
        peer_sets = self.peer_sets_for_subject(subject_candidate_id, context)
        conflict_links: list[dict[str, Any]] = []
        for relationship in relationships:
            conflict_links.extend(
                self.repository.conflict_links_for_relationship(
                    relationship.relationship_id,
                    cutoff=context.cutoff,
                    strict_known_by_hunter=context.strict_known_by_hunter,
                )
            )
        return CompetitiveReplayView(
            mode=context.mode,
            known_at_cutoff=context.known_at_cutoff,
            relationships=relationships,
            peer_sets=peer_sets,
            conflict_links=tuple(conflict_links),
        )

    def relationships_for_subject(
        self,
        subject_candidate_id: str,
        context: CompetitiveReplayContext,
    ) -> tuple[CompetitiveRelationship, ...]:
        if context.cutoff is None:
            rows = self.repository.competitive_relationships_for_subject(subject_candidate_id)
        else:
            rows = self.repository.competitive_relationships_for_subject_at(
                subject_candidate_id,
                context.cutoff,
                strict_known_by_hunter=context.strict_known_by_hunter,
            )
        return _competitive_relationships(rows)

    def peer_sets_for_subject(
        self,
        subject_candidate_id: str,
        context: CompetitiveReplayContext,
    ) -> tuple[PeerSet, ...]:
        if context.cutoff is None:
            rows = self.repository.peer_sets_for_subject(subject_candidate_id)
        else:
            rows = self.repository.peer_sets_for_subject_at(
                subject_candidate_id,
                context.cutoff,
                strict_known_by_hunter=context.strict_known_by_hunter,
            )
        return _peer_sets(rows)


def _conflict_reason(left: CompetitiveRelationship, right: CompetitiveRelationship) -> str:
    if left.relationship_id == right.relationship_id:
        return ""
    if left.subject_candidate_id != right.subject_candidate_id:
        return ""
    if left.peer_candidate_id != right.peer_candidate_id:
        return ""
    if left.predicate_id != right.predicate_id:
        return ""
    if left.scope != right.scope:
        return ""
    if left.qualifier != right.qualifier:
        return ""
    if left.modality != right.modality:
        return ""
    if not _overlapping_validity(left, right):
        return ""
    if left.polarity != right.polarity:
        return "opposite_polarity_same_subject_peer_predicate_scope_qualifier_modality_and_time"
    if left.status in CONFLICT_RESOLVED_STATUSES or right.status in CONFLICT_RESOLVED_STATUSES:
        return "resolved_or_superseded_relationship_preserved_for_history"
    return ""


def _overlapping_validity(left: CompetitiveRelationship, right: CompetitiveRelationship) -> bool:
    left_start = left.valid_from or left.effective_at
    right_start = right.valid_from or right.effective_at
    left_end = left.valid_to or datetime.max.replace(tzinfo=UTC)
    right_end = right.valid_to or datetime.max.replace(tzinfo=UTC)
    return left_start <= right_end and right_start <= left_end


def _conflict_status(left: CompetitiveRelationship, right: CompetitiveRelationship) -> str:
    if left.status in CONFLICT_RESOLVED_STATUSES or right.status in CONFLICT_RESOLVED_STATUSES:
        return "resolved"
    if left.status == "historical_only" or right.status == "historical_only":
        return "historical_only"
    return "detected"


def _conflict_links(
    candidate: CompetitiveConflictCandidate,
    left: CompetitiveRelationship,
    right: CompetitiveRelationship,
    created_at: datetime,
) -> tuple[CompetitiveConflictLink, ...]:
    role = "participant"
    if candidate.status == "resolved":
        role = "superseded_by"
    return (
        _conflict_link(candidate.conflict_id, left.relationship_id, role, created_at),
        _conflict_link(candidate.conflict_id, right.relationship_id, role, created_at),
    )


def _conflict_link(
    conflict_id: str,
    relationship_id: str,
    role: str,
    created_at: datetime,
) -> CompetitiveConflictLink:
    return CompetitiveConflictLink(
        link_id=competitive_id(
            "conflict-link",
            {"conflict_id": conflict_id, "relationship_id": relationship_id, "role": role},
        ),
        relationship_id=relationship_id,
        conflict_id=conflict_id,
        role=role,  # type: ignore[arg-type]
        created_at=created_at,
        schema_version=COMPETITIVE_CONFLICT_SCHEMA_VERSION,
    )


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


def _peer_sets(rows: tuple[dict[str, Any], ...]) -> tuple[PeerSet, ...]:
    return tuple(
        PeerSet(
            **{
                **row,
                "effective_at": _datetime_value(row["effective_at"]),
                "recorded_at": _datetime_value(row["recorded_at"]),
                "metadata": _metadata(row.get("metadata")),
            }
        )
        for row in rows
    )


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
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed
    return None
