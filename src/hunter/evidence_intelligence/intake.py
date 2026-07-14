from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any

from hunter.evidence_intelligence.models import (
    AuthorityStatus,
    DocumentLifecycleEvent,
    EvidenceDocument,
    EvidenceDocumentVersion,
    EvidenceSpan,
    EvidenceSpanLink,
    SourceAuthorityVerificationEvent,
    VerificationMethod,
    VerifierType,
)
from hunter.evidence_intelligence.repository import EvidenceIntelligenceRepository
from hunter.execution.identity import identity

DOCUMENT_SCHEMA_VERSION = "evidence-document-v1"
DOCUMENT_EVENT_SCHEMA_VERSION = "document-lifecycle-event-v1"
AUTHORITY_SCHEMA_VERSION = "source-authority-verification-event-v1"
SPAN_SCHEMA_VERSION = "evidence-span-v1"
DOCUMENT_VERSION_SCHEMA_VERSION = "evidence-document-version-v1"
NORMALIZATION_VERSION = "plain-text-normalization-v1"
PARSER_ID = "plain-text-intake-parser-v1"
CHUNK_VERSION = "stable-span-chunk-v1"
MAX_SPAN_CHARS = 1200


@dataclass(frozen=True)
class EvidenceIntakeReference:
    source_evidence_id: str
    raw_evidence_id: str
    normalized_evidence_id: str
    candidate_id: str
    identity_resolution_status: str
    source_url: str
    source_provider: str
    source_type: str
    source_claimed_authority: str
    title: str
    content: str
    content_type: str = "text/plain"
    language: str = "en"
    source_published_at: datetime | None = None
    observed_at: datetime | None = None
    retrieved_at: datetime | None = None
    available_at: datetime | None = None
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in (
            "source_evidence_id",
            "raw_evidence_id",
            "normalized_evidence_id",
            "candidate_id",
            "identity_resolution_status",
            "source_provider",
            "source_type",
            "source_claimed_authority",
            "title",
            "content",
            "content_type",
            "language",
        ):
            if not str(getattr(self, name)).strip():
                msg = f"{name} is required"
                raise ValueError(msg)
        for name in (
            "source_published_at",
            "observed_at",
            "retrieved_at",
            "available_at",
            "valid_from",
            "valid_to",
        ):
            value = getattr(self, name)
            if value is not None and value.tzinfo is None:
                msg = f"{name} must be timezone-aware"
                raise ValueError(msg)


@dataclass(frozen=True)
class EvidenceIntakeResult:
    document: EvidenceDocument
    document_version: EvidenceDocumentVersion
    document_event: DocumentLifecycleEvent
    authority_event: SourceAuthorityVerificationEvent
    spans: tuple[EvidenceSpan, ...]


class EvidenceIntelligenceIntakeService:
    def __init__(self, repository: EvidenceIntelligenceRepository) -> None:
        self.repository = repository

    def ingest(
        self,
        reference: EvidenceIntakeReference,
        *,
        processing_run_id: str,
        processed_at: datetime | None = None,
        authority_status: AuthorityStatus = "unverified",
        verification_method: VerificationMethod = "provider_claim_only",
        verifier_type: VerifierType = "provider_claim",
        authority_evidence_id: str | None = None,
        authority_reason: str = "authority recorded from persisted evidence reference",
    ) -> EvidenceIntakeResult:
        if processed_at is None:
            processed_at = datetime.now(tz=UTC)
        if processed_at.tzinfo is None:
            msg = "processed_at must be timezone-aware"
            raise ValueError(msg)
        if not processing_run_id.strip():
            msg = "processing_run_id is required"
            raise ValueError(msg)

        observed_at = reference.observed_at or processed_at
        retrieved_at = reference.retrieved_at or observed_at
        available_at = reference.available_at or retrieved_at
        normalized_content = normalize_content(reference.content)
        content_hash = _digest(reference.content)
        normalized_content_hash = _digest(normalized_content)
        document_id = identity(
            "evidence-intelligence-document",
            {
                "source_evidence_id": reference.source_evidence_id,
                "raw_evidence_id": reference.raw_evidence_id,
                "normalized_content_hash": normalized_content_hash,
            },
        )
        rendition_id = identity(
            "evidence-intelligence-rendition",
            {
                "document_id": document_id,
                "normalized_content_hash": normalized_content_hash,
                "normalization_version": NORMALIZATION_VERSION,
                "parser_id": PARSER_ID,
            },
        )
        document = EvidenceDocument(
            document_id=document_id,
            source_evidence_id=reference.source_evidence_id,
            raw_evidence_id=reference.raw_evidence_id,
            normalized_evidence_id=reference.normalized_evidence_id,
            candidate_id=reference.candidate_id,
            identity_resolution_status=reference.identity_resolution_status,
            source_url=reference.source_url,
            source_provider=reference.source_provider,
            source_type=reference.source_type,
            source_claimed_authority=reference.source_claimed_authority,
            title=reference.title,
            content_hash=content_hash,
            normalized_content_hash=normalized_content_hash,
            normalization_version=NORMALIZATION_VERSION,
            parser_id=PARSER_ID,
            rendition_id=rendition_id,
            content_type=reference.content_type,
            language=reference.language,
            source_published_at=reference.source_published_at,
            observed_at=observed_at,
            retrieved_at=retrieved_at,
            available_at=available_at,
            processed_at=processed_at,
            valid_from=reference.valid_from,
            valid_to=reference.valid_to,
            document_status="active",
            processing_status="processed",
            freshness=1.0,
            confidence=1.0,
            source_verified_authority=reference.source_claimed_authority if authority_status != "unverified" else "",
            authority_verification_method=verification_method,
            authority_verification_evidence_id=authority_evidence_id or reference.source_evidence_id,
            authority_verified_at=processed_at,
            authority_status=authority_status,
            metadata=dict(reference.metadata),
        )
        document_version = EvidenceDocumentVersion(
            version_id=identity(
                "evidence-intelligence-document-version",
                {
                    "document_id": document_id,
                    "normalized_content_hash": normalized_content_hash,
                    "normalization_version": NORMALIZATION_VERSION,
                    "parser_id": PARSER_ID,
                },
            ),
            document_id=document_id,
            content_hash=content_hash,
            normalized_content_hash=normalized_content_hash,
            normalization_version=NORMALIZATION_VERSION,
            parser_id=PARSER_ID,
            rendition_id=rendition_id,
            created_at=processed_at,
            schema_version=DOCUMENT_VERSION_SCHEMA_VERSION,
        )
        spans = _spans_for_document(document, normalized_content, processed_at)
        document_event = DocumentLifecycleEvent(
            event_id=identity(
                "evidence-intelligence-document-event",
                {
                    "document_id": document_id,
                    "event_type": "accepted",
                    "effective_at": available_at,
                    "source_evidence_id": reference.source_evidence_id,
                },
            ),
            document_id=document_id,
            event_type="accepted",
            effective_at=available_at,
            recorded_at=processed_at,
            source_evidence_id=reference.source_evidence_id,
            reason="document accepted from immutable persisted evidence reference",
            previous_status=None,
            new_status="active",
            processing_run_id=processing_run_id,
            schema_version=DOCUMENT_EVENT_SCHEMA_VERSION,
        )
        authority_event = SourceAuthorityVerificationEvent(
            verification_id=identity(
                "evidence-intelligence-authority-event",
                {
                    "document_id": document_id,
                    "authority_status": authority_status,
                    "verification_method": verification_method,
                    "authority_evidence_id": authority_evidence_id or reference.source_evidence_id,
                    "effective_at": available_at,
                },
            ),
            document_id=document_id,
            authority_status=authority_status,
            verification_method=verification_method,
            authority_evidence_id=authority_evidence_id or reference.source_evidence_id,
            effective_at=available_at,
            recorded_at=processed_at,
            verifier_type=verifier_type,
            reason=authority_reason,
            processing_run_id=processing_run_id,
            schema_version=AUTHORITY_SCHEMA_VERSION,
        )

        self.repository.save_document(document)
        self.repository.save_document_version(document_version)
        for span in spans:
            self.repository.save_span(span)
        self.repository.save_document_lifecycle_event(document_event)
        self.repository.save_authority_event(authority_event)
        self.repository.save_span_links(
            "document_lifecycle_event_span_links",
            _document_event_span_links(document_event, spans, processed_at),
        )
        return EvidenceIntakeResult(
            document=document,
            document_version=document_version,
            document_event=document_event,
            authority_event=authority_event,
            spans=spans,
        )


def normalize_content(content: str) -> str:
    lines = content.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    normalized = "\n".join(line.rstrip() for line in lines).strip()
    return normalized or " "


def _spans_for_document(
    document: EvidenceDocument, normalized_content: str, created_at: datetime
) -> tuple[EvidenceSpan, ...]:
    spans: list[EvidenceSpan] = []
    start = 0
    chunk_index = 0
    while start < len(normalized_content):
        end = min(start + MAX_SPAN_CHARS, len(normalized_content))
        excerpt = normalized_content[start:end]
        chunk_id = identity(
            "evidence-intelligence-span-chunk",
            {
                "document_id": document.document_id,
                "normalized_content_hash": document.normalized_content_hash,
                "chunk_index": chunk_index,
                "start_offset": start,
                "end_offset": end,
            },
        )
        spans.append(
            EvidenceSpan(
                span_id=identity(
                    "evidence-intelligence-span",
                    {
                        "document_id": document.document_id,
                        "chunk_id": chunk_id,
                        "text_hash": _digest(excerpt),
                    },
                ),
                document_id=document.document_id,
                source_evidence_id=document.source_evidence_id,
                normalized_content_hash=document.normalized_content_hash,
                normalization_version=document.normalization_version,
                parser_id=document.parser_id,
                rendition_id=document.rendition_id,
                offset_encoding="unicode_codepoint",
                start_offset=start,
                end_offset=end,
                chunk_id=chunk_id,
                chunk_version=CHUNK_VERSION,
                text_hash=_digest(excerpt),
                excerpt=excerpt,
                section_title="",
                locator=f"offset:{start}:{end}",
                span_status="active",
                created_at=created_at,
                validated_at=created_at,
            )
        )
        start = end
        chunk_index += 1
    return tuple(spans)


def _document_event_span_links(
    event: DocumentLifecycleEvent,
    spans: tuple[EvidenceSpan, ...],
    created_at: datetime,
) -> tuple[EvidenceSpanLink, ...]:
    return tuple(
        EvidenceSpanLink(
            link_id=identity(
                "evidence-intelligence-document-event-span-link",
                {
                    "event_id": event.event_id,
                    "span_id": span.span_id,
                    "role": "supporting",
                    "position": position,
                },
            ),
            owner_id=event.event_id,
            span_id=span.span_id,
            role="supporting",
            position=position,
            created_at=created_at,
            schema_version=SPAN_SCHEMA_VERSION,
        )
        for position, span in enumerate(spans)
    )


def _digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()
