from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from hunter.evidence_intelligence.models import (
    AuthorityStatus,
    ClaimLifecycleEvent,
    EvidenceSpan,
    EvidenceSpanLink,
    KnowledgeClaim,
    SourceEvidenceLink,
)
from hunter.evidence_intelligence.repository import EvidenceIntelligenceRepository
from hunter.evidence_intelligence.validation import ValidatedClaimProposal
from hunter.execution.identity import identity

CLAIM_SCHEMA_VERSION = "knowledge-claim-v1"
CLAIM_EVENT_SCHEMA_VERSION = "claim-lifecycle-event-v1"
CLAIM_LINK_SCHEMA_VERSION = "claim-link-v1"


@dataclass(frozen=True)
class ClaimPersistenceInput:
    proposal: ValidatedClaimProposal
    subject_entity_id: str
    object_entity_id: str | None
    subject_candidate_id: str | None
    predicate_schema_version: str
    source_evidence_ids: tuple[str, ...]
    spans: tuple[EvidenceSpan, ...]
    authority_status: AuthorityStatus
    processing_provider: str
    processing_artifact_id: str
    observed_at: datetime
    available_at: datetime
    retrieved_at: datetime
    processed_at: datetime
    effective_at: datetime
    recorded_at: datetime
    processing_run_id: str
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    unit: str = ""
    scope: str = ""

    def __post_init__(self) -> None:
        for name in (
            "subject_entity_id",
            "predicate_schema_version",
            "processing_provider",
            "processing_artifact_id",
            "processing_run_id",
        ):
            if not str(getattr(self, name)).strip():
                msg = f"{name} is required"
                raise ValueError(msg)
        if self.proposal.object_type is not None and self.object_entity_id is None:
            msg = "object_entity_id is required for object entity claims"
            raise ValueError(msg)
        if not self.source_evidence_ids:
            msg = "source_evidence_ids are required"
            raise ValueError(msg)
        if not self.spans:
            msg = "spans are required"
            raise ValueError(msg)
        for name in (
            "observed_at",
            "available_at",
            "retrieved_at",
            "processed_at",
            "effective_at",
            "recorded_at",
            "valid_from",
            "valid_to",
        ):
            value = getattr(self, name)
            if value is not None and value.tzinfo is None:
                msg = f"{name} must be timezone-aware"
                raise ValueError(msg)


@dataclass(frozen=True)
class PersistedClaim:
    claim: KnowledgeClaim
    lifecycle_event: ClaimLifecycleEvent
    source_links: tuple[SourceEvidenceLink, ...]
    span_links: tuple[EvidenceSpanLink, ...]


class DeterministicConfidenceProjector:
    def project(self, item: ClaimPersistenceInput) -> tuple[float, dict[str, float]]:
        support = 0.85 if item.proposal.support_level == "literal_support" else 0.7
        authority = _authority_component(item.authority_status)
        lineage = min(
            1.0, 0.45 + (0.15 * len(set(item.source_evidence_ids))) + (0.1 * len({span.span_id for span in item.spans}))
        )
        confidence = round((support * 0.45) + (authority * 0.35) + (lineage * 0.2), 4)
        return (
            confidence,
            {
                "support": round(support, 4),
                "authority": round(authority, 4),
                "lineage": round(lineage, 4),
            },
        )


class ClaimPersistenceService:
    def __init__(
        self,
        repository: EvidenceIntelligenceRepository,
        *,
        confidence_projector: DeterministicConfidenceProjector | None = None,
    ) -> None:
        self.repository = repository
        self.confidence_projector = confidence_projector or DeterministicConfidenceProjector()

    def persist(self, item: ClaimPersistenceInput) -> PersistedClaim:
        confidence, confidence_components = self.confidence_projector.project(item)
        claim = KnowledgeClaim(
            claim_id=_claim_id(item),
            subject_entity_id=item.subject_entity_id,
            subject_candidate_id=item.subject_candidate_id,
            predicate_id=item.proposal.predicate_id,
            predicate_schema_version=item.predicate_schema_version,
            object_entity_id=item.object_entity_id,
            literal_value=item.proposal.literal_value,
            literal_value_type=item.proposal.literal_value_type,
            unit=item.unit,
            scope=item.scope,
            polarity=item.proposal.polarity,
            modality=item.proposal.modality,
            valid_from=item.valid_from,
            valid_to=item.valid_to,
            observed_at=item.observed_at,
            available_at=item.available_at,
            retrieved_at=item.retrieved_at,
            processed_at=item.processed_at,
            support_level=item.proposal.support_level,
            confidence=confidence,
            confidence_components=confidence_components,
            status="active",
            authority_status=item.authority_status,
            processing_provider=item.processing_provider,
            processing_artifact_id=item.processing_artifact_id,
            schema_version=CLAIM_SCHEMA_VERSION,
            created_at=item.processed_at,
        )
        event = ClaimLifecycleEvent(
            event_id=identity(
                "evidence-claim-lifecycle-event",
                {
                    "claim_id": claim.claim_id,
                    "event_type": "accepted",
                    "effective_at": item.effective_at,
                    "source_evidence_id": item.source_evidence_ids[0],
                },
            ),
            claim_id=claim.claim_id,
            event_type="accepted",
            effective_at=item.effective_at,
            recorded_at=item.recorded_at,
            source_evidence_id=item.source_evidence_ids[0],
            reason="validated extraction persisted as canonical knowledge claim",
            previous_status=None,
            new_status="active",
            processing_run_id=item.processing_run_id,
            schema_version=CLAIM_EVENT_SCHEMA_VERSION,
        )
        source_links = _source_links(claim.claim_id, item.source_evidence_ids, item.processed_at)
        span_links = _span_links(claim.claim_id, item.spans, item.processed_at)
        self.repository.save_claim_with_lifecycle(claim, event, source_links, span_links)
        return PersistedClaim(claim=claim, lifecycle_event=event, source_links=source_links, span_links=span_links)

    def append_lifecycle_event(self, event: ClaimLifecycleEvent, *, confidence: float | None = None) -> None:
        self.repository.append_claim_lifecycle_event(event, confidence=confidence)


def _claim_id(item: ClaimPersistenceInput) -> str:
    return identity(
        "evidence-knowledge-claim",
        {
            "subject_entity_id": item.subject_entity_id,
            "predicate_id": item.proposal.predicate_id,
            "predicate_schema_version": item.predicate_schema_version,
            "object_entity_id": item.object_entity_id,
            "literal_value": item.proposal.literal_value,
            "literal_value_type": item.proposal.literal_value_type,
            "scope": item.scope,
            "polarity": item.proposal.polarity,
            "modality": item.proposal.modality,
            "valid_from": item.valid_from,
            "valid_to": item.valid_to,
            "source_evidence_ids": tuple(sorted(set(item.source_evidence_ids))),
            "span_ids": tuple(sorted(span.span_id for span in item.spans)),
        },
    )


def _source_links(
    claim_id: str, source_evidence_ids: tuple[str, ...], created_at: datetime
) -> tuple[SourceEvidenceLink, ...]:
    return tuple(
        SourceEvidenceLink(
            link_id=identity(
                "evidence-claim-source-link",
                {"claim_id": claim_id, "source_evidence_id": source_evidence_id, "position": position},
            ),
            owner_id=claim_id,
            source_evidence_id=source_evidence_id,
            role="supporting",
            position=position,
            created_at=created_at,
            schema_version=CLAIM_LINK_SCHEMA_VERSION,
        )
        for position, source_evidence_id in enumerate(source_evidence_ids)
    )


def _span_links(claim_id: str, spans: tuple[EvidenceSpan, ...], created_at: datetime) -> tuple[EvidenceSpanLink, ...]:
    return tuple(
        EvidenceSpanLink(
            link_id=identity(
                "evidence-claim-span-link",
                {"claim_id": claim_id, "span_id": span.span_id, "position": position},
            ),
            owner_id=claim_id,
            span_id=span.span_id,
            role="supporting",
            position=position,
            created_at=created_at,
            schema_version=CLAIM_LINK_SCHEMA_VERSION,
        )
        for position, span in enumerate(spans)
    )


def _authority_component(status: AuthorityStatus) -> float:
    return {
        "verified_official": 1.0,
        "verified_affiliated": 0.9,
        "verified_governance": 0.9,
        "verified_repository": 0.85,
        "third_party": 0.65,
        "community": 0.45,
        "ambiguous": 0.3,
        "impersonation_suspected": 0.05,
        "unverified": 0.2,
        "unavailable": 0.0,
    }[status]
