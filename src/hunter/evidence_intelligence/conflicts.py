from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from hunter.evidence_intelligence.models import (
    ClaimConflictLink,
    ConflictClaimLink,
    ConflictLifecycleEvent,
    EvidenceSpanLink,
    KnowledgeClaim,
    KnowledgeConflict,
    PredicateRegistry,
    SourceEvidenceLink,
)
from hunter.evidence_intelligence.repository import EvidenceIntelligenceRepository
from hunter.execution.identity import identity

CONFLICT_SCHEMA_VERSION = "knowledge-conflict-v1"
CONFLICT_EVENT_SCHEMA_VERSION = "conflict-lifecycle-event-v1"
CONFLICT_LINK_SCHEMA_VERSION = "conflict-link-v1"
ACTIVE_CLAIM_STATUSES = frozenset({"active", "disputed"})
ACTIVE_DOCUMENT_STATUSES = frozenset({"active", "disputed", "historical_only"})
DISQUALIFYING_AUTHORITY_STATUSES = frozenset({"unavailable", "impersonation_suspected"})
EXCLUSIVE_RULE_TOKENS = frozenset(
    {"exclusive", "exclusive_literal", "exclusive_object", "single_value", "mutually_exclusive"}
)


@dataclass(frozen=True)
class ConflictCandidate:
    claims: tuple[KnowledgeClaim, KnowledgeClaim]
    reason: str


@dataclass(frozen=True)
class PersistedConflict:
    conflict: KnowledgeConflict
    lifecycle_event: ConflictLifecycleEvent
    claim_links: tuple[ConflictClaimLink, ...]
    source_links: tuple[SourceEvidenceLink, ...]
    span_links: tuple[EvidenceSpanLink, ...]
    claim_conflict_links: tuple[ClaimConflictLink, ...]


class PredicateAwareConflictDetector:
    def __init__(self, repository: EvidenceIntelligenceRepository) -> None:
        self.repository = repository

    def detect(
        self, claims: tuple[KnowledgeClaim, ...], predicate_registry: PredicateRegistry
    ) -> tuple[ConflictCandidate, ...]:
        eligible = [claim for claim in claims if self._eligible_claim(claim)]
        groups: dict[tuple[str, str, str], list[KnowledgeClaim]] = {}
        for claim in eligible:
            groups.setdefault((claim.subject_entity_id, claim.predicate_id, claim.scope), []).append(claim)

        conflicts: list[ConflictCandidate] = []
        for grouped_claims in groups.values():
            for index, left in enumerate(grouped_claims):
                for right in grouped_claims[index + 1 :]:
                    reason = self._conflict_reason(left, right, predicate_registry)
                    if reason is not None:
                        conflicts.append(ConflictCandidate(claims=(left, right), reason=reason))
        return tuple(conflicts)

    def _eligible_claim(self, claim: KnowledgeClaim) -> bool:
        if claim.status not in ACTIVE_CLAIM_STATUSES:
            return False
        if claim.authority_status in DISQUALIFYING_AUTHORITY_STATUSES:
            return False
        lineage = self.repository.claim_lineage(claim.claim_id)
        documents = lineage.get("documents", ())
        if not documents:
            return True
        return all(str(document.get("document_status", "")) in ACTIVE_DOCUMENT_STATUSES for document in documents)

    def _conflict_reason(
        self,
        left: KnowledgeClaim,
        right: KnowledgeClaim,
        predicate_registry: PredicateRegistry,
    ) -> str | None:
        if left.claim_id == right.claim_id:
            return None
        if left.subject_entity_id != right.subject_entity_id:
            return None
        if left.predicate_id != right.predicate_id:
            return None
        if left.scope != right.scope:
            return None
        if not _validity_overlaps(left, right):
            return None
        if left.modality != right.modality:
            return None

        same_object = left.object_entity_id == right.object_entity_id and left.literal_value == right.literal_value
        if same_object and left.polarity != right.polarity:
            return "same scoped claim has opposite polarity"

        if not _predicate_has_exclusive_conflict_rule(left.predicate_id, predicate_registry):
            return None
        if left.polarity != right.polarity:
            return None
        if left.object_entity_id != right.object_entity_id or left.literal_value != right.literal_value:
            return "predicate-specific exclusive rule rejects simultaneous different values"
        return None


class ConflictPersistenceService:
    def __init__(self, repository: EvidenceIntelligenceRepository) -> None:
        self.repository = repository

    def persist(
        self,
        candidate: ConflictCandidate,
        *,
        effective_at: datetime,
        recorded_at: datetime,
        processing_run_id: str,
    ) -> PersistedConflict:
        for name, value in (("effective_at", effective_at), ("recorded_at", recorded_at)):
            if value.tzinfo is None:
                msg = f"{name} must be timezone-aware"
                raise ValueError(msg)
        claims = tuple(sorted(candidate.claims, key=lambda claim: claim.claim_id))
        source_evidence_ids, span_ids = self._lineage_ids(claims)
        conflict = KnowledgeConflict(
            conflict_id=identity(
                "evidence-knowledge-conflict",
                {
                    "claim_ids": tuple(claim.claim_id for claim in claims),
                    "predicate_id": claims[0].predicate_id,
                    "subject_entity_id": claims[0].subject_entity_id,
                    "scope": claims[0].scope,
                    "reason": candidate.reason,
                },
            ),
            predicate_id=claims[0].predicate_id,
            subject_entity_id=claims[0].subject_entity_id,
            scope=claims[0].scope,
            detected_at=recorded_at,
            effective_at=effective_at,
            resolved_at=None,
            status="detected",
            reason=candidate.reason,
            schema_version=CONFLICT_SCHEMA_VERSION,
        )
        event = ConflictLifecycleEvent(
            event_id=identity(
                "evidence-conflict-lifecycle-event",
                {
                    "conflict_id": conflict.conflict_id,
                    "event_type": "detected",
                    "effective_at": effective_at,
                },
            ),
            conflict_id=conflict.conflict_id,
            event_type="detected",
            effective_at=effective_at,
            recorded_at=recorded_at,
            source_evidence_id=source_evidence_ids[0],
            reason=candidate.reason,
            previous_status=None,
            new_status="detected",
            processing_run_id=processing_run_id,
            schema_version=CONFLICT_EVENT_SCHEMA_VERSION,
        )
        claim_links = _conflict_claim_links(conflict.conflict_id, claims, recorded_at)
        source_links = _source_links(conflict.conflict_id, source_evidence_ids, recorded_at)
        span_links = _span_links(conflict.conflict_id, span_ids, recorded_at)
        claim_conflict_links = _claim_conflict_links(conflict.conflict_id, claims, recorded_at)
        self.repository.save_conflict_with_lifecycle(
            conflict,
            event,
            claim_links,
            source_links,
            span_links,
            claim_conflict_links,
        )
        return PersistedConflict(
            conflict=conflict,
            lifecycle_event=event,
            claim_links=claim_links,
            source_links=source_links,
            span_links=span_links,
            claim_conflict_links=claim_conflict_links,
        )

    def append_lifecycle_event(self, event: ConflictLifecycleEvent) -> None:
        self.repository.append_conflict_lifecycle_event(event)

    def _lineage_ids(self, claims: tuple[KnowledgeClaim, ...]) -> tuple[tuple[str, ...], tuple[str, ...]]:
        source_evidence_ids: list[str] = []
        span_ids: list[str] = []
        for claim in claims:
            lineage = self.repository.claim_lineage(claim.claim_id)
            source_evidence_ids.extend(str(link["source_evidence_id"]) for link in lineage["source_evidence"])
            span_ids.extend(str(span["span_id"]) for span in lineage["spans"])
        return tuple(dict.fromkeys(source_evidence_ids)), tuple(dict.fromkeys(span_ids))


def _validity_overlaps(left: KnowledgeClaim, right: KnowledgeClaim) -> bool:
    left_start = left.valid_from
    left_end = left.valid_to
    right_start = right.valid_from
    right_end = right.valid_to
    if left_end is not None and right_start is not None and left_end < right_start:
        return False
    if right_end is not None and left_start is not None and right_end < left_start:
        return False
    return True


def _predicate_has_exclusive_conflict_rule(predicate_id: str, predicate_registry: PredicateRegistry) -> bool:
    try:
        rule = predicate_registry.get(predicate_id).predicate_specific_conflict_rules.lower()
    except KeyError:
        return False
    tokens = {token.strip() for token in rule.replace(",", " ").split()}
    return bool(tokens & EXCLUSIVE_RULE_TOKENS)


def _conflict_claim_links(
    conflict_id: str,
    claims: tuple[KnowledgeClaim, ...],
    created_at: datetime,
) -> tuple[ConflictClaimLink, ...]:
    return tuple(
        ConflictClaimLink(
            link_id=identity(
                "evidence-conflict-claim-link",
                {"conflict_id": conflict_id, "claim_id": claim.claim_id, "position": position},
            ),
            conflict_id=conflict_id,
            claim_id=claim.claim_id,
            role="participant",
            position=position,
            created_at=created_at,
            schema_version=CONFLICT_LINK_SCHEMA_VERSION,
        )
        for position, claim in enumerate(claims)
    )


def _claim_conflict_links(
    conflict_id: str,
    claims: tuple[KnowledgeClaim, ...],
    created_at: datetime,
) -> tuple[ClaimConflictLink, ...]:
    return tuple(
        ClaimConflictLink(
            link_id=identity(
                "evidence-claim-conflict-link",
                {"claim_id": claim.claim_id, "conflict_id": conflict_id},
            ),
            claim_id=claim.claim_id,
            conflict_id=conflict_id,
            role="participant",
            created_at=created_at,
            schema_version=CONFLICT_LINK_SCHEMA_VERSION,
        )
        for claim in claims
    )


def _source_links(
    conflict_id: str, source_evidence_ids: tuple[str, ...], created_at: datetime
) -> tuple[SourceEvidenceLink, ...]:
    return tuple(
        SourceEvidenceLink(
            link_id=identity(
                "evidence-conflict-source-link",
                {"conflict_id": conflict_id, "source_evidence_id": source_evidence_id, "position": position},
            ),
            owner_id=conflict_id,
            source_evidence_id=source_evidence_id,
            role="conflicting",
            position=position,
            created_at=created_at,
            schema_version=CONFLICT_LINK_SCHEMA_VERSION,
        )
        for position, source_evidence_id in enumerate(source_evidence_ids)
    )


def _span_links(conflict_id: str, span_ids: tuple[str, ...], created_at: datetime) -> tuple[EvidenceSpanLink, ...]:
    return tuple(
        EvidenceSpanLink(
            link_id=identity(
                "evidence-conflict-span-link",
                {"conflict_id": conflict_id, "span_id": span_id, "position": position},
            ),
            owner_id=conflict_id,
            span_id=span_id,
            role="conflicting",
            position=position,
            created_at=created_at,
            schema_version=CONFLICT_LINK_SCHEMA_VERSION,
        )
        for position, span_id in enumerate(span_ids)
    )
