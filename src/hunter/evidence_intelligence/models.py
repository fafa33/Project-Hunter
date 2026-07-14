from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any, Literal

DocumentStatus = Literal[
    "active",
    "superseded",
    "retracted",
    "source_removed",
    "historical_only",
    "disputed",
    "unavailable",
    "rejected",
]
DocumentLifecycleEventType = Literal[
    "accepted",
    "superseded",
    "retracted",
    "source_removed",
    "historical_only",
    "disputed",
    "unavailable",
    "rejected",
]
SpanStatus = Literal[
    "active",
    "stale_after_parser_upgrade",
    "source_changed",
    "source_removed",
    "invalid",
    "historical_only",
]
AuthorityStatus = Literal[
    "verified_official",
    "verified_affiliated",
    "verified_governance",
    "verified_repository",
    "third_party",
    "community",
    "ambiguous",
    "impersonation_suspected",
    "unverified",
    "unavailable",
]
VerificationMethod = Literal[
    "candidate_registry_official_domain",
    "identity_trust_layer",
    "verified_github_organization",
    "official_repository_reference",
    "governance_domain_reference",
    "existing_hunter_evidence",
    "manual_verified_evidence",
    "provider_claim_only",
]
VerifierType = Literal[
    "deterministic_system",
    "identity_trust_layer",
    "candidate_registry",
    "manual_review",
    "provider_claim",
]
EntityType = Literal[
    "project",
    "protocol",
    "token",
    "foundation",
    "dao",
    "company",
    "developer",
    "repository",
    "contract",
    "chain",
    "organization",
    "product",
    "standard",
    "market",
    "unknown",
]
ClaimStatus = Literal[
    "active",
    "superseded",
    "retracted",
    "source_removed",
    "historical_only",
    "disputed",
    "unavailable",
    "rejected",
]
ClaimLifecycleEventType = Literal[
    "accepted",
    "superseded",
    "retracted",
    "disputed",
    "source_removed",
    "historical_only",
    "unavailable",
    "rejected",
]
ConflictStatus = Literal["detected", "disputed", "resolved", "historical_only", "rejected"]
ConflictLifecycleEventType = Literal["detected", "disputed", "resolved", "historical_only", "rejected"]
Polarity = Literal["positive", "negative", "unknown"]
Modality = Literal["asserted", "planned", "conditional", "historical", "deprecated", "unavailable"]
SupportLevel = Literal["literal_support", "semantic_support"]
LiteralValueType = Literal[
    "string",
    "integer",
    "decimal",
    "boolean",
    "date",
    "datetime",
    "url",
    "address",
    "repository",
    "enum",
    "unavailable",
]
PredicateDirection = Literal["subject_to_object", "object_to_subject", "bidirectional"]
SourceEvidenceLinkRole = Literal[
    "supporting",
    "conflicting",
    "authority",
    "correction",
    "retraction",
    "supersession",
    "resolution",
]
SpanLinkRole = Literal[
    "supporting",
    "conflicting",
    "authority",
    "correction",
    "retraction",
    "supersession",
    "removal",
    "resolution",
    "rejection",
    "dispute",
]
ClaimConflictLinkRole = Literal["participant", "resolved_by", "superseded_by", "retracted_by"]
ClaimClaimRelationshipType = Literal[
    "conflicts_with",
    "supersedes",
    "superseded_by",
    "corrects",
    "corrected_by",
    "retracts",
    "retracted_by",
    "corroborates",
]
ConflictClaimLinkRole = Literal["participant", "winner", "loser", "resolved_by", "superseded_by", "retracted_by"]
ProviderHealthStatus = Literal["healthy", "degraded", "unavailable"]
ExtractionProposalStatus = Literal["proposed", "unavailable", "rejected"]
SecuritySeverity = Literal["low", "medium", "high", "critical"]

DOCUMENT_STATUSES: frozenset[str] = frozenset(DocumentStatus.__args__)  # type: ignore[attr-defined]
DOCUMENT_EVENT_TYPES: frozenset[str] = frozenset(DocumentLifecycleEventType.__args__)  # type: ignore[attr-defined]
SPAN_STATUSES: frozenset[str] = frozenset(SpanStatus.__args__)  # type: ignore[attr-defined]
AUTHORITY_STATUSES: frozenset[str] = frozenset(AuthorityStatus.__args__)  # type: ignore[attr-defined]
VERIFICATION_METHODS: frozenset[str] = frozenset(VerificationMethod.__args__)  # type: ignore[attr-defined]
VERIFIER_TYPES: frozenset[str] = frozenset(VerifierType.__args__)  # type: ignore[attr-defined]
ENTITY_TYPES: frozenset[str] = frozenset(EntityType.__args__)  # type: ignore[attr-defined]
CLAIM_STATUSES: frozenset[str] = frozenset(ClaimStatus.__args__)  # type: ignore[attr-defined]
CLAIM_EVENT_TYPES: frozenset[str] = frozenset(ClaimLifecycleEventType.__args__)  # type: ignore[attr-defined]
CONFLICT_STATUSES: frozenset[str] = frozenset(ConflictStatus.__args__)  # type: ignore[attr-defined]
CONFLICT_EVENT_TYPES: frozenset[str] = frozenset(ConflictLifecycleEventType.__args__)  # type: ignore[attr-defined]
POLARITIES: frozenset[str] = frozenset(Polarity.__args__)  # type: ignore[attr-defined]
MODALITIES: frozenset[str] = frozenset(Modality.__args__)  # type: ignore[attr-defined]
SUPPORT_LEVELS: frozenset[str] = frozenset(SupportLevel.__args__)  # type: ignore[attr-defined]
LITERAL_VALUE_TYPES: frozenset[str] = frozenset(LiteralValueType.__args__)  # type: ignore[attr-defined]
PREDICATE_DIRECTIONS: frozenset[str] = frozenset(PredicateDirection.__args__)  # type: ignore[attr-defined]
SOURCE_EVIDENCE_LINK_ROLES: frozenset[str] = frozenset(SourceEvidenceLinkRole.__args__)  # type: ignore[attr-defined]
SPAN_LINK_ROLES: frozenset[str] = frozenset(SpanLinkRole.__args__)  # type: ignore[attr-defined]
CLAIM_CONFLICT_LINK_ROLES: frozenset[str] = frozenset(ClaimConflictLinkRole.__args__)  # type: ignore[attr-defined]
CLAIM_CLAIM_RELATIONSHIP_TYPES: frozenset[str] = frozenset(ClaimClaimRelationshipType.__args__)  # type: ignore[attr-defined]
CONFLICT_CLAIM_LINK_ROLES: frozenset[str] = frozenset(ConflictClaimLinkRole.__args__)  # type: ignore[attr-defined]
PROVIDER_HEALTH_STATUSES: frozenset[str] = frozenset(ProviderHealthStatus.__args__)  # type: ignore[attr-defined]
EXTRACTION_PROPOSAL_STATUSES: frozenset[str] = frozenset(ExtractionProposalStatus.__args__)  # type: ignore[attr-defined]
SECURITY_SEVERITIES: frozenset[str] = frozenset(SecuritySeverity.__args__)  # type: ignore[attr-defined]


@dataclass(frozen=True)
class EvidenceDocument:
    document_id: str
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
    content_hash: str
    normalized_content_hash: str
    normalization_version: str
    parser_id: str
    rendition_id: str
    content_type: str
    language: str
    source_published_at: datetime | None
    observed_at: datetime
    retrieved_at: datetime
    available_at: datetime
    processed_at: datetime | None
    valid_from: datetime | None
    valid_to: datetime | None
    document_status: DocumentStatus
    processing_status: str
    freshness: float
    confidence: float
    source_verified_authority: str = ""
    authority_verification_method: str = ""
    authority_verification_evidence_id: str = ""
    authority_verified_at: datetime | None = None
    authority_status: AuthorityStatus = "unverified"
    superseded_at: datetime | None = None
    retracted_at: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in (
            "document_id",
            "source_evidence_id",
            "raw_evidence_id",
            "normalized_evidence_id",
            "candidate_id",
            "identity_resolution_status",
            "source_provider",
            "source_type",
            "source_claimed_authority",
            "title",
            "content_hash",
            "normalized_content_hash",
            "normalization_version",
            "parser_id",
            "rendition_id",
            "content_type",
            "language",
            "processing_status",
        ):
            _text(name, getattr(self, name))
        _optional_text("source_url", self.source_url)
        _member("document_status", self.document_status, DOCUMENT_STATUSES)
        _member("authority_status", self.authority_status, AUTHORITY_STATUSES)
        _range("freshness", self.freshness)
        _range("confidence", self.confidence)
        _set_datetime("source_published_at", self.source_published_at)
        _set_datetime("observed_at", self.observed_at)
        _set_datetime("retrieved_at", self.retrieved_at)
        _set_datetime("available_at", self.available_at)
        _set_datetime("processed_at", self.processed_at)
        _set_datetime("valid_from", self.valid_from)
        _set_datetime("valid_to", self.valid_to)
        _set_datetime("authority_verified_at", self.authority_verified_at)
        _set_datetime("superseded_at", self.superseded_at)
        _set_datetime("retracted_at", self.retracted_at)
        object.__setattr__(self, "metadata", _frozen_metadata(self.metadata))


@dataclass(frozen=True)
class EvidenceDocumentVersion:
    version_id: str
    document_id: str
    content_hash: str
    normalized_content_hash: str
    normalization_version: str
    parser_id: str
    rendition_id: str
    created_at: datetime
    schema_version: str

    def __post_init__(self) -> None:
        for name in (
            "version_id",
            "document_id",
            "content_hash",
            "normalized_content_hash",
            "normalization_version",
            "parser_id",
            "rendition_id",
            "schema_version",
        ):
            _text(name, getattr(self, name))
        _set_datetime("created_at", self.created_at)


@dataclass(frozen=True)
class DocumentLifecycleEvent:
    event_id: str
    document_id: str
    event_type: DocumentLifecycleEventType
    effective_at: datetime
    recorded_at: datetime
    source_evidence_id: str
    reason: str
    previous_status: DocumentStatus | None
    new_status: DocumentStatus
    processing_run_id: str
    schema_version: str

    def __post_init__(self) -> None:
        _lifecycle_event(
            event_id=self.event_id,
            owner_name="document_id",
            owner_id=self.document_id,
            event_type_name="event_type",
            event_type=self.event_type,
            valid_event_types=DOCUMENT_EVENT_TYPES,
            effective_at=self.effective_at,
            recorded_at=self.recorded_at,
            source_evidence_id=self.source_evidence_id,
            reason=self.reason,
            previous_status=self.previous_status,
            new_status=self.new_status,
            valid_statuses=DOCUMENT_STATUSES,
            processing_run_id=self.processing_run_id,
            schema_version=self.schema_version,
        )


@dataclass(frozen=True)
class SourceAuthorityVerificationEvent:
    verification_id: str
    document_id: str
    authority_status: AuthorityStatus
    verification_method: VerificationMethod
    authority_evidence_id: str
    effective_at: datetime
    recorded_at: datetime
    verifier_type: VerifierType
    reason: str
    processing_run_id: str
    schema_version: str

    def __post_init__(self) -> None:
        for name in (
            "verification_id",
            "document_id",
            "authority_evidence_id",
            "reason",
            "processing_run_id",
            "schema_version",
        ):
            _text(name, getattr(self, name))
        _member("authority_status", self.authority_status, AUTHORITY_STATUSES)
        _member("verification_method", self.verification_method, VERIFICATION_METHODS)
        _member("verifier_type", self.verifier_type, VERIFIER_TYPES)
        _set_datetime("effective_at", self.effective_at)
        _set_datetime("recorded_at", self.recorded_at)


@dataclass(frozen=True)
class EvidenceSpan:
    span_id: str
    document_id: str
    source_evidence_id: str
    normalized_content_hash: str
    normalization_version: str
    parser_id: str
    rendition_id: str
    offset_encoding: str
    start_offset: int
    end_offset: int
    chunk_id: str
    chunk_version: str
    text_hash: str
    excerpt: str
    section_title: str
    locator: str
    span_status: SpanStatus
    created_at: datetime
    validated_at: datetime

    def __post_init__(self) -> None:
        for name in (
            "span_id",
            "document_id",
            "source_evidence_id",
            "normalized_content_hash",
            "normalization_version",
            "parser_id",
            "rendition_id",
            "offset_encoding",
            "chunk_id",
            "chunk_version",
            "text_hash",
            "excerpt",
            "locator",
        ):
            _text(name, getattr(self, name))
        _optional_text("section_title", self.section_title)
        if self.start_offset < 0:
            msg = "start_offset must be non-negative"
            raise ValueError(msg)
        if self.end_offset <= self.start_offset:
            msg = "end_offset must be greater than start_offset"
            raise ValueError(msg)
        _member("span_status", self.span_status, SPAN_STATUSES)
        _set_datetime("created_at", self.created_at)
        _set_datetime("validated_at", self.validated_at)


@dataclass(frozen=True)
class KnowledgeEntity:
    entity_id: str
    canonical_name: str
    entity_type: EntityType
    candidate_id: str | None
    registry_identity_status: str
    confidence: float
    status: str
    first_seen_at: datetime
    last_seen_at: datetime
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        for name in ("entity_id", "canonical_name", "registry_identity_status", "status"):
            _text(name, getattr(self, name))
        if self.candidate_id is not None:
            _text("candidate_id", self.candidate_id)
        _member("entity_type", self.entity_type, ENTITY_TYPES)
        _range("confidence", self.confidence)
        _set_datetime("first_seen_at", self.first_seen_at)
        _set_datetime("last_seen_at", self.last_seen_at)
        _set_datetime("created_at", self.created_at)
        _set_datetime("updated_at", self.updated_at)


@dataclass(frozen=True)
class PredicateDefinition:
    predicate_id: str
    name: str
    description: str
    schema_version: str
    permitted_subject_types: tuple[EntityType, ...]
    permitted_object_entity_types: tuple[EntityType, ...] = ()
    permitted_literal_value_types: tuple[LiteralValueType, ...] = ()
    requires_object_entity: bool = False
    allows_literal_value: bool = False
    direction: PredicateDirection = "subject_to_object"
    inverse_predicate: str | None = None
    symmetric: bool = False
    asymmetric: bool = True
    valid_qualifiers: tuple[str, ...] = ()
    valid_modalities: tuple[Modality, ...] = ("asserted",)
    valid_polarities: tuple[Polarity, ...] = ("positive",)
    graph_projection_eligible: bool = False
    predicate_specific_conflict_rules: str = ""
    scope_requirements: str = ""
    temporal_requirements: str = ""
    support_requirements: str = ""
    authority_requirements: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))
    deprecated_at: datetime | None = None
    replacement_predicate: str | None = None

    def __post_init__(self) -> None:
        for name in ("predicate_id", "name", "description", "schema_version"):
            _text(name, getattr(self, name))
        if not self.permitted_subject_types:
            msg = "permitted_subject_types is required"
            raise ValueError(msg)
        for value in self.permitted_subject_types:
            _member("permitted_subject_types", value, ENTITY_TYPES)
        for value in self.permitted_object_entity_types:
            _member("permitted_object_entity_types", value, ENTITY_TYPES)
        for value in self.permitted_literal_value_types:
            _member("permitted_literal_value_types", value, LITERAL_VALUE_TYPES)
        if self.requires_object_entity and not self.permitted_object_entity_types:
            msg = "object entity predicates require permitted_object_entity_types"
            raise ValueError(msg)
        if self.allows_literal_value and not self.permitted_literal_value_types:
            msg = "literal predicates require permitted_literal_value_types"
            raise ValueError(msg)
        if not self.requires_object_entity and not self.allows_literal_value:
            msg = "predicate must allow an object entity or literal value"
            raise ValueError(msg)
        _member("direction", self.direction, PREDICATE_DIRECTIONS)
        if self.symmetric and self.asymmetric:
            msg = "predicate cannot be both symmetric and asymmetric"
            raise ValueError(msg)
        if self.inverse_predicate is not None:
            _text("inverse_predicate", self.inverse_predicate)
        for value in self.valid_modalities:
            _member("valid_modalities", value, MODALITIES)
        for value in self.valid_polarities:
            _member("valid_polarities", value, POLARITIES)
        _set_datetime("created_at", self.created_at)
        _set_datetime("deprecated_at", self.deprecated_at)
        if self.replacement_predicate is not None:
            _text("replacement_predicate", self.replacement_predicate)
        object.__setattr__(self, "permitted_subject_types", tuple(sorted(set(self.permitted_subject_types))))
        object.__setattr__(
            self, "permitted_object_entity_types", tuple(sorted(set(self.permitted_object_entity_types)))
        )
        object.__setattr__(
            self, "permitted_literal_value_types", tuple(sorted(set(self.permitted_literal_value_types)))
        )
        object.__setattr__(
            self, "valid_qualifiers", tuple(sorted({str(item) for item in self.valid_qualifiers if str(item)}))
        )
        object.__setattr__(self, "valid_modalities", tuple(sorted(set(self.valid_modalities))))
        object.__setattr__(self, "valid_polarities", tuple(sorted(set(self.valid_polarities))))


@dataclass(frozen=True)
class PredicateRegistry:
    schema_version: str
    predicates: tuple[PredicateDefinition, ...]

    def __post_init__(self) -> None:
        _text("schema_version", self.schema_version)
        if not self.predicates:
            msg = "predicates is required"
            raise ValueError(msg)
        keys: set[tuple[str, str]] = set()
        for predicate in self.predicates:
            if predicate.schema_version != self.schema_version:
                msg = "predicate schema_version must match registry schema_version"
                raise ValueError(msg)
            key = (predicate.predicate_id, predicate.schema_version)
            if key in keys:
                msg = f"duplicate predicate definition: {predicate.predicate_id}@{predicate.schema_version}"
                raise ValueError(msg)
            keys.add(key)
        object.__setattr__(self, "predicates", tuple(sorted(self.predicates, key=lambda item: item.predicate_id)))

    def get(self, predicate_id: str) -> PredicateDefinition:
        _text("predicate_id", predicate_id)
        for predicate in self.predicates:
            if predicate.predicate_id == predicate_id:
                return predicate
        msg = f"unsupported predicate: {predicate_id}"
        raise KeyError(msg)

    def validate_claim_shape(
        self,
        *,
        predicate_id: str,
        subject_type: EntityType,
        object_type: EntityType | None,
        literal_value_type: LiteralValueType | None,
        modality: Modality,
        polarity: Polarity,
    ) -> None:
        predicate = self.get(predicate_id)
        _member("subject_type", subject_type, set_as_frozenset(predicate.permitted_subject_types))
        if object_type is not None:
            _member("object_type", object_type, set_as_frozenset(predicate.permitted_object_entity_types))
        if literal_value_type is not None:
            _member("literal_value_type", literal_value_type, set_as_frozenset(predicate.permitted_literal_value_types))
        if predicate.requires_object_entity and object_type is None:
            msg = f"{predicate_id} requires object entity"
            raise ValueError(msg)
        if not predicate.allows_literal_value and literal_value_type is not None:
            msg = f"{predicate_id} does not allow literal values"
            raise ValueError(msg)
        _member("modality", modality, set_as_frozenset(predicate.valid_modalities))
        _member("polarity", polarity, set_as_frozenset(predicate.valid_polarities))


@dataclass(frozen=True)
class KnowledgeClaim:
    claim_id: str
    subject_entity_id: str
    subject_candidate_id: str | None
    predicate_id: str
    predicate_schema_version: str
    object_entity_id: str | None
    literal_value: str | int | float | bool | None
    literal_value_type: LiteralValueType | None
    unit: str
    scope: str
    polarity: Polarity
    modality: Modality
    valid_from: datetime | None
    valid_to: datetime | None
    observed_at: datetime
    available_at: datetime
    retrieved_at: datetime
    processed_at: datetime
    support_level: SupportLevel
    confidence: float
    confidence_components: dict[str, float]
    status: ClaimStatus
    authority_status: AuthorityStatus
    processing_provider: str
    processing_artifact_id: str
    schema_version: str
    created_at: datetime
    superseded_at: datetime | None = None
    retracted_at: datetime | None = None

    def __post_init__(self) -> None:
        for name in (
            "claim_id",
            "subject_entity_id",
            "predicate_id",
            "predicate_schema_version",
            "scope",
            "unit",
            "processing_provider",
            "processing_artifact_id",
            "schema_version",
        ):
            _optional_text(name, getattr(self, name))
        _text("claim_id", self.claim_id)
        _text("subject_entity_id", self.subject_entity_id)
        _text("predicate_id", self.predicate_id)
        _text("predicate_schema_version", self.predicate_schema_version)
        if self.subject_candidate_id is not None:
            _text("subject_candidate_id", self.subject_candidate_id)
        if self.object_entity_id is None and self.literal_value is None:
            msg = "claim requires object_entity_id or literal_value"
            raise ValueError(msg)
        if self.object_entity_id is not None:
            _text("object_entity_id", self.object_entity_id)
        if self.literal_value is not None and self.literal_value_type is None:
            msg = "literal claims require literal_value_type"
            raise ValueError(msg)
        if self.literal_value_type is not None:
            _member("literal_value_type", self.literal_value_type, LITERAL_VALUE_TYPES)
        _member("polarity", self.polarity, POLARITIES)
        _member("modality", self.modality, MODALITIES)
        _member("support_level", self.support_level, SUPPORT_LEVELS)
        _member("status", self.status, CLAIM_STATUSES)
        _member("authority_status", self.authority_status, AUTHORITY_STATUSES)
        _range("confidence", self.confidence)
        _set_datetime("valid_from", self.valid_from)
        _set_datetime("valid_to", self.valid_to)
        _set_datetime("observed_at", self.observed_at)
        _set_datetime("available_at", self.available_at)
        _set_datetime("retrieved_at", self.retrieved_at)
        _set_datetime("processed_at", self.processed_at)
        _set_datetime("created_at", self.created_at)
        _set_datetime("superseded_at", self.superseded_at)
        _set_datetime("retracted_at", self.retracted_at)
        object.__setattr__(
            self,
            "confidence_components",
            MappingProxyType({str(key): _clamped(value) for key, value in self.confidence_components.items()}),
        )


@dataclass(frozen=True)
class ClaimLifecycleEvent:
    event_id: str
    claim_id: str
    event_type: ClaimLifecycleEventType
    effective_at: datetime
    recorded_at: datetime
    source_evidence_id: str
    reason: str
    previous_status: ClaimStatus | None
    new_status: ClaimStatus
    processing_run_id: str
    schema_version: str

    def __post_init__(self) -> None:
        _lifecycle_event(
            event_id=self.event_id,
            owner_name="claim_id",
            owner_id=self.claim_id,
            event_type_name="event_type",
            event_type=self.event_type,
            valid_event_types=CLAIM_EVENT_TYPES,
            effective_at=self.effective_at,
            recorded_at=self.recorded_at,
            source_evidence_id=self.source_evidence_id,
            reason=self.reason,
            previous_status=self.previous_status,
            new_status=self.new_status,
            valid_statuses=CLAIM_STATUSES,
            processing_run_id=self.processing_run_id,
            schema_version=self.schema_version,
        )


@dataclass(frozen=True)
class KnowledgeConflict:
    conflict_id: str
    predicate_id: str
    subject_entity_id: str
    scope: str
    detected_at: datetime
    effective_at: datetime
    resolved_at: datetime | None
    status: ConflictStatus
    reason: str
    schema_version: str

    def __post_init__(self) -> None:
        for name in ("conflict_id", "predicate_id", "subject_entity_id", "reason", "schema_version"):
            _text(name, getattr(self, name))
        _optional_text("scope", self.scope)
        _member("status", self.status, CONFLICT_STATUSES)
        _set_datetime("detected_at", self.detected_at)
        _set_datetime("effective_at", self.effective_at)
        _set_datetime("resolved_at", self.resolved_at)


@dataclass(frozen=True)
class ConflictLifecycleEvent:
    event_id: str
    conflict_id: str
    event_type: ConflictLifecycleEventType
    effective_at: datetime
    recorded_at: datetime
    source_evidence_id: str
    reason: str
    previous_status: ConflictStatus | None
    new_status: ConflictStatus
    processing_run_id: str
    schema_version: str

    def __post_init__(self) -> None:
        _lifecycle_event(
            event_id=self.event_id,
            owner_name="conflict_id",
            owner_id=self.conflict_id,
            event_type_name="event_type",
            event_type=self.event_type,
            valid_event_types=CONFLICT_EVENT_TYPES,
            effective_at=self.effective_at,
            recorded_at=self.recorded_at,
            source_evidence_id=self.source_evidence_id,
            reason=self.reason,
            previous_status=self.previous_status,
            new_status=self.new_status,
            valid_statuses=CONFLICT_STATUSES,
            processing_run_id=self.processing_run_id,
            schema_version=self.schema_version,
        )


@dataclass(frozen=True)
class KnowledgeRelationship:
    relationship_id: str
    claim_id: str
    subject_entity_id: str
    predicate_id: str
    object_entity_id: str
    direction: PredicateDirection
    inverse_predicate_id: str | None
    scope: str
    polarity: Polarity
    modality: Modality
    valid_from: datetime | None
    valid_to: datetime | None
    confidence: float
    status: ClaimStatus
    projection_version: str
    created_at: datetime

    def __post_init__(self) -> None:
        for name in (
            "relationship_id",
            "claim_id",
            "subject_entity_id",
            "predicate_id",
            "object_entity_id",
            "projection_version",
        ):
            _text(name, getattr(self, name))
        if self.inverse_predicate_id is not None:
            _text("inverse_predicate_id", self.inverse_predicate_id)
        _optional_text("scope", self.scope)
        _member("direction", self.direction, PREDICATE_DIRECTIONS)
        _member("polarity", self.polarity, POLARITIES)
        _member("modality", self.modality, MODALITIES)
        _member("status", self.status, CLAIM_STATUSES)
        _range("confidence", self.confidence)
        _set_datetime("valid_from", self.valid_from)
        _set_datetime("valid_to", self.valid_to)
        _set_datetime("created_at", self.created_at)


@dataclass(frozen=True)
class SourceEvidenceLink:
    link_id: str
    owner_id: str
    source_evidence_id: str
    role: SourceEvidenceLinkRole
    position: int
    created_at: datetime
    schema_version: str

    def __post_init__(self) -> None:
        _link_common(
            link_id=self.link_id,
            owner_name="owner_id",
            owner_id=self.owner_id,
            target_name="source_evidence_id",
            target_id=self.source_evidence_id,
            role=self.role,
            allowed_roles=SOURCE_EVIDENCE_LINK_ROLES,
            position=self.position,
            created_at=self.created_at,
            schema_version=self.schema_version,
        )


@dataclass(frozen=True)
class EvidenceSpanLink:
    link_id: str
    owner_id: str
    span_id: str
    role: SpanLinkRole
    position: int
    created_at: datetime
    schema_version: str

    def __post_init__(self) -> None:
        _link_common(
            link_id=self.link_id,
            owner_name="owner_id",
            owner_id=self.owner_id,
            target_name="span_id",
            target_id=self.span_id,
            role=self.role,
            allowed_roles=SPAN_LINK_ROLES,
            position=self.position,
            created_at=self.created_at,
            schema_version=self.schema_version,
        )


@dataclass(frozen=True)
class ClaimConflictLink:
    link_id: str
    claim_id: str
    conflict_id: str
    role: ClaimConflictLinkRole
    created_at: datetime
    schema_version: str

    def __post_init__(self) -> None:
        _link_common(
            link_id=self.link_id,
            owner_name="claim_id",
            owner_id=self.claim_id,
            target_name="conflict_id",
            target_id=self.conflict_id,
            role=self.role,
            allowed_roles=CLAIM_CONFLICT_LINK_ROLES,
            position=0,
            created_at=self.created_at,
            schema_version=self.schema_version,
        )


@dataclass(frozen=True)
class ClaimClaimLink:
    link_id: str
    source_claim_id: str
    target_claim_id: str
    relationship_type: ClaimClaimRelationshipType
    created_at: datetime
    schema_version: str

    def __post_init__(self) -> None:
        _link_common(
            link_id=self.link_id,
            owner_name="source_claim_id",
            owner_id=self.source_claim_id,
            target_name="target_claim_id",
            target_id=self.target_claim_id,
            role=self.relationship_type,
            allowed_roles=CLAIM_CLAIM_RELATIONSHIP_TYPES,
            position=0,
            created_at=self.created_at,
            schema_version=self.schema_version,
        )


@dataclass(frozen=True)
class ConflictClaimLink:
    link_id: str
    conflict_id: str
    claim_id: str
    role: ConflictClaimLinkRole
    position: int
    created_at: datetime
    schema_version: str

    def __post_init__(self) -> None:
        _link_common(
            link_id=self.link_id,
            owner_name="conflict_id",
            owner_id=self.conflict_id,
            target_name="claim_id",
            target_id=self.claim_id,
            role=self.role,
            allowed_roles=CONFLICT_CLAIM_LINK_ROLES,
            position=self.position,
            created_at=self.created_at,
            schema_version=self.schema_version,
        )


@dataclass(frozen=True)
class EntityIdentifierLink:
    link_id: str
    entity_id: str
    namespace: str
    value: str
    source_evidence_id: str
    confidence: float
    created_at: datetime
    schema_version: str

    def __post_init__(self) -> None:
        for name in ("link_id", "entity_id", "namespace", "value", "source_evidence_id", "schema_version"):
            _text(name, getattr(self, name))
        _range("confidence", self.confidence)
        _set_datetime("created_at", self.created_at)


@dataclass(frozen=True)
class EntityAliasLink:
    link_id: str
    entity_id: str
    alias: str
    alias_type: str
    source_evidence_id: str
    confidence: float
    created_at: datetime
    schema_version: str

    def __post_init__(self) -> None:
        for name in ("link_id", "entity_id", "alias", "alias_type", "source_evidence_id", "schema_version"):
            _text(name, getattr(self, name))
        _range("confidence", self.confidence)
        _set_datetime("created_at", self.created_at)


@dataclass(frozen=True)
class EvidenceProcessingRun:
    run_id: str
    run_type: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    schema_version: str

    def __post_init__(self) -> None:
        for name in ("run_id", "run_type", "status", "schema_version"):
            _text(name, getattr(self, name))
        _set_datetime("started_at", self.started_at)
        _set_datetime("finished_at", self.finished_at)


@dataclass(frozen=True)
class AIProviderArtifact:
    artifact_id: str
    processing_run_id: str
    provider_name: str
    provider_version: str
    schema_version: str
    prompt_version: str
    content_hash: str
    status: str
    created_at: datetime

    def __post_init__(self) -> None:
        for name in (
            "artifact_id",
            "processing_run_id",
            "provider_name",
            "provider_version",
            "schema_version",
            "prompt_version",
            "content_hash",
            "status",
        ):
            _text(name, getattr(self, name))
        _set_datetime("created_at", self.created_at)


@dataclass(frozen=True)
class AIProviderHealth:
    health_id: str
    provider_name: str
    provider_version: str
    status: ProviderHealthStatus
    checked_at: datetime
    latency_ms: int | None
    failure_type: str
    unavailable_reason: str
    schema_version: str

    def __post_init__(self) -> None:
        for name in (
            "health_id",
            "provider_name",
            "provider_version",
            "failure_type",
            "unavailable_reason",
            "schema_version",
        ):
            _optional_text(name, getattr(self, name))
        _text("health_id", self.health_id)
        _text("provider_name", self.provider_name)
        _text("provider_version", self.provider_version)
        _member("status", self.status, PROVIDER_HEALTH_STATUSES)
        _set_datetime("checked_at", self.checked_at)
        if self.latency_ms is not None and self.latency_ms < 0:
            msg = "latency_ms must be non-negative"
            raise ValueError(msg)


@dataclass(frozen=True)
class ExtractionSchema:
    schema_id: str
    name: str
    purpose: str
    schema_version: str
    output_contract: str
    content_hash: str
    created_at: datetime
    deprecated_at: datetime | None = None

    def __post_init__(self) -> None:
        for name in ("schema_id", "name", "purpose", "schema_version", "output_contract", "content_hash"):
            _text(name, getattr(self, name))
        _set_datetime("created_at", self.created_at)
        _set_datetime("deprecated_at", self.deprecated_at)


@dataclass(frozen=True)
class ExtractionProposal:
    proposal_id: str
    artifact_id: str
    document_id: str
    schema_id: str
    schema_version: str
    provider_name: str
    provider_version: str
    status: ExtractionProposalStatus
    proposed_payload_hash: str
    created_at: datetime
    unavailable_reason: str = ""
    rejection_reason: str = ""

    def __post_init__(self) -> None:
        for name in (
            "proposal_id",
            "artifact_id",
            "document_id",
            "schema_id",
            "schema_version",
            "provider_name",
            "provider_version",
            "proposed_payload_hash",
        ):
            _text(name, getattr(self, name))
        _member("status", self.status, EXTRACTION_PROPOSAL_STATUSES)
        _optional_text("unavailable_reason", self.unavailable_reason)
        _optional_text("rejection_reason", self.rejection_reason)
        _set_datetime("created_at", self.created_at)


@dataclass(frozen=True)
class KnowledgeCheckpoint:
    checkpoint_id: str
    processor_name: str
    target_id: str
    cursor: str
    updated_at: datetime
    schema_version: str

    def __post_init__(self) -> None:
        for name in ("checkpoint_id", "processor_name", "target_id", "cursor", "schema_version"):
            _text(name, getattr(self, name))
        _set_datetime("updated_at", self.updated_at)


@dataclass(frozen=True)
class SecurityAuditEvent:
    event_id: str
    document_id: str
    event_type: str
    detected_at: datetime
    severity: str
    reason: str
    schema_version: str

    def __post_init__(self) -> None:
        for name in ("event_id", "document_id", "event_type", "severity", "reason", "schema_version"):
            _text(name, getattr(self, name))
        _member("severity", self.severity, SECURITY_SEVERITIES)
        _set_datetime("detected_at", self.detected_at)


def _text(name: str, value: str) -> None:
    if not str(value).strip():
        msg = f"{name} is required"
        raise ValueError(msg)


def _optional_text(name: str, value: str) -> None:
    if value and not str(value).strip():
        msg = f"{name} must not be blank"
        raise ValueError(msg)


def _member(name: str, value: str, allowed: frozenset[str]) -> None:
    if value not in allowed:
        msg = f"{name} must be one of: {', '.join(sorted(allowed))}"
        raise ValueError(msg)


def set_as_frozenset(values: tuple[str, ...]) -> frozenset[str]:
    return frozenset(values)


def _set_datetime(name: str, value: datetime | None) -> None:
    if value is None:
        return
    if value.tzinfo is None:
        msg = f"{name} must be timezone-aware"
        raise ValueError(msg)


def _range(name: str, value: float) -> None:
    if not 0.0 <= float(value) <= 1.0:
        msg = f"{name} must be between 0 and 1"
        raise ValueError(msg)


def _clamped(value: float) -> float:
    _range("confidence component", value)
    return round(float(value), 4)


def _frozen_metadata(value: dict[str, Any]) -> MappingProxyType[str, Any]:
    return MappingProxyType({str(key): item for key, item in value.items()})


def _lifecycle_event(
    *,
    event_id: str,
    owner_name: str,
    owner_id: str,
    event_type_name: str,
    event_type: str,
    valid_event_types: frozenset[str],
    effective_at: datetime,
    recorded_at: datetime,
    source_evidence_id: str,
    reason: str,
    previous_status: str | None,
    new_status: str,
    valid_statuses: frozenset[str],
    processing_run_id: str,
    schema_version: str,
) -> None:
    for name, value in (
        ("event_id", event_id),
        (owner_name, owner_id),
        ("source_evidence_id", source_evidence_id),
        ("reason", reason),
        ("processing_run_id", processing_run_id),
        ("schema_version", schema_version),
    ):
        _text(name, value)
    _member(event_type_name, event_type, valid_event_types)
    if previous_status is not None:
        _member("previous_status", previous_status, valid_statuses)
    _member("new_status", new_status, valid_statuses)
    _set_datetime("effective_at", effective_at)
    _set_datetime("recorded_at", recorded_at)


def _link_common(
    *,
    link_id: str,
    owner_name: str,
    owner_id: str,
    target_name: str,
    target_id: str,
    role: str,
    allowed_roles: frozenset[str],
    position: int,
    created_at: datetime,
    schema_version: str,
) -> None:
    for name, value in (
        ("link_id", link_id),
        (owner_name, owner_id),
        (target_name, target_id),
        ("schema_version", schema_version),
    ):
        _text(name, value)
    _member("role", role, allowed_roles)
    if position < 0:
        msg = "position must be non-negative"
        raise ValueError(msg)
    _set_datetime("created_at", created_at)
