from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
from typing import Any, Literal

CompetitiveRelationshipType = Literal[
    "direct_competitor",
    "indirect_competitor",
    "substitute",
    "centralized_incumbent",
    "open_source_alternative",
    "ecosystem_peer",
    "technology_peer",
    "category_peer",
    "use_case_peer",
]
AlgorithmicPeerRelationshipType = Literal[
    "same_category_similarity",
    "same_chain_similarity",
    "same_use_case_similarity",
    "same_protocol_type_similarity",
    "same_market_segment_similarity",
]
CompetitiveRelationshipStatus = Literal[
    "active",
    "disputed",
    "superseded",
    "retracted",
    "source_removed",
    "historical_only",
    "unavailable",
    "rejected",
]
PeerSetStatus = Literal["active", "partial", "disputed", "historical_only", "unavailable", "rejected"]
PeerSetMemberRole = Literal["evidence_backed_competitor", "algorithmic_peer"]
RelationshipKind = Literal["evidence_backed", "algorithmic_similarity"]
ComparisonDimensionType = Literal[
    "market_category",
    "protocol_category",
    "ecosystem",
    "chain",
    "technology_stack",
    "use_case",
    "user_segment",
    "asset_native_token_distinction",
    "protocol_type",
    "revenue_model",
    "liquidity_venue_coverage",
    "developer_ecosystem",
    "deployment_surface",
]
DimensionMatchStatus = Literal["matched", "different", "missing", "unavailable"]
CompetitiveLinkRole = Literal["supporting", "conflicting", "policy", "lineage", "missing_evidence"]
CompetitiveConflictLinkRole = Literal["participant", "disputed_by", "resolved_by", "superseded_by"]
CompetitiveProcessingRunStatus = Literal["running", "succeeded", "partial", "failed", "unavailable"]

COMPETITIVE_RELATIONSHIP_TYPES: frozenset[str] = frozenset(CompetitiveRelationshipType.__args__)  # type: ignore[attr-defined]
ALGORITHMIC_PEER_RELATIONSHIP_TYPES: frozenset[str] = frozenset(  # type: ignore[attr-defined]
    AlgorithmicPeerRelationshipType.__args__
)
COMPETITIVE_RELATIONSHIP_STATUSES: frozenset[str] = frozenset(  # type: ignore[attr-defined]
    CompetitiveRelationshipStatus.__args__
)
PEER_SET_STATUSES: frozenset[str] = frozenset(PeerSetStatus.__args__)  # type: ignore[attr-defined]
PEER_SET_MEMBER_ROLES: frozenset[str] = frozenset(PeerSetMemberRole.__args__)  # type: ignore[attr-defined]
RELATIONSHIP_KINDS: frozenset[str] = frozenset(RelationshipKind.__args__)  # type: ignore[attr-defined]
COMPARISON_DIMENSION_TYPES: frozenset[str] = frozenset(ComparisonDimensionType.__args__)  # type: ignore[attr-defined]
DIMENSION_MATCH_STATUSES: frozenset[str] = frozenset(DimensionMatchStatus.__args__)  # type: ignore[attr-defined]
COMPETITIVE_LINK_ROLES: frozenset[str] = frozenset(CompetitiveLinkRole.__args__)  # type: ignore[attr-defined]
COMPETITIVE_CONFLICT_LINK_ROLES: frozenset[str] = frozenset(  # type: ignore[attr-defined]
    CompetitiveConflictLinkRole.__args__
)
COMPETITIVE_PROCESSING_RUN_STATUSES: frozenset[str] = frozenset(  # type: ignore[attr-defined]
    CompetitiveProcessingRunStatus.__args__
)


@dataclass(frozen=True)
class CompetitiveRelationship:
    relationship_id: str
    subject_candidate_id: str
    peer_candidate_id: str
    relationship_type: CompetitiveRelationshipType
    status: CompetitiveRelationshipStatus
    predicate_id: str
    predicate_schema_version: str
    claim_id: str
    subject_entity_id: str
    peer_entity_id: str
    scope: str
    modality: str
    polarity: str
    confidence: float
    freshness: float
    effective_at: datetime
    recorded_at: datetime
    schema_version: str
    projection_id: str | None = None
    qualifier: str = ""
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    conflict_status: str = "none"
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in (
            "relationship_id",
            "subject_candidate_id",
            "peer_candidate_id",
            "predicate_id",
            "predicate_schema_version",
            "claim_id",
            "subject_entity_id",
            "peer_entity_id",
            "scope",
            "modality",
            "polarity",
            "schema_version",
        ):
            _text(name, getattr(self, name))
        _member("relationship_type", self.relationship_type, COMPETITIVE_RELATIONSHIP_TYPES)
        _member("status", self.status, COMPETITIVE_RELATIONSHIP_STATUSES)
        _range("confidence", self.confidence)
        _range("freshness", self.freshness)
        _aware("effective_at", self.effective_at)
        _aware("recorded_at", self.recorded_at)
        _aware("valid_from", self.valid_from)
        _aware("valid_to", self.valid_to)
        if self.projection_id is not None:
            _text("projection_id", self.projection_id)
        object.__setattr__(self, "confidence", round(float(self.confidence), 4))
        object.__setattr__(self, "freshness", round(float(self.freshness), 4))
        object.__setattr__(self, "metadata", _metadata(self.metadata))


@dataclass(frozen=True)
class AlgorithmicPeerRelationship:
    relationship_id: str
    subject_candidate_id: str
    peer_candidate_id: str
    relationship_type: AlgorithmicPeerRelationshipType
    status: CompetitiveRelationshipStatus
    policy_id: str
    policy_version: str
    scope: str
    compared_dimension_count: int
    matched_dimension_count: int
    missing_dimension_count: int
    similarity: float
    confidence: float
    freshness: float
    effective_at: datetime
    recorded_at: datetime
    schema_version: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in (
            "relationship_id",
            "subject_candidate_id",
            "peer_candidate_id",
            "policy_id",
            "policy_version",
            "scope",
            "schema_version",
        ):
            _text(name, getattr(self, name))
        _member("relationship_type", self.relationship_type, ALGORITHMIC_PEER_RELATIONSHIP_TYPES)
        _member("status", self.status, COMPETITIVE_RELATIONSHIP_STATUSES)
        for name in ("compared_dimension_count", "matched_dimension_count", "missing_dimension_count"):
            if int(getattr(self, name)) < 0:
                msg = f"{name} must be non-negative"
                raise ValueError(msg)
        if self.matched_dimension_count > self.compared_dimension_count:
            msg = "matched_dimension_count cannot exceed compared_dimension_count"
            raise ValueError(msg)
        if self.missing_dimension_count > self.compared_dimension_count:
            msg = "missing_dimension_count cannot exceed compared_dimension_count"
            raise ValueError(msg)
        _range("similarity", self.similarity)
        _range("confidence", self.confidence)
        _range("freshness", self.freshness)
        _aware("effective_at", self.effective_at)
        _aware("recorded_at", self.recorded_at)
        object.__setattr__(self, "similarity", round(float(self.similarity), 4))
        object.__setattr__(self, "confidence", round(float(self.confidence), 4))
        object.__setattr__(self, "freshness", round(float(self.freshness), 4))
        object.__setattr__(self, "metadata", _metadata(self.metadata))


@dataclass(frozen=True)
class PeerSet:
    peer_set_id: str
    subject_candidate_id: str
    scope: str
    status: PeerSetStatus
    peer_set_version: str
    policy_id: str
    policy_version: str
    evidence_backed_count: int
    algorithmic_peer_count: int
    confidence: float
    coverage: float
    freshness: float
    effective_at: datetime
    recorded_at: datetime
    schema_version: str
    conflict_status: str = "none"
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in (
            "peer_set_id",
            "subject_candidate_id",
            "scope",
            "peer_set_version",
            "policy_id",
            "policy_version",
            "schema_version",
        ):
            _text(name, getattr(self, name))
        _member("status", self.status, PEER_SET_STATUSES)
        for name in ("evidence_backed_count", "algorithmic_peer_count"):
            if int(getattr(self, name)) < 0:
                msg = f"{name} must be non-negative"
                raise ValueError(msg)
        _range("confidence", self.confidence)
        _range("coverage", self.coverage)
        _range("freshness", self.freshness)
        _aware("effective_at", self.effective_at)
        _aware("recorded_at", self.recorded_at)
        object.__setattr__(self, "confidence", round(float(self.confidence), 4))
        object.__setattr__(self, "coverage", round(float(self.coverage), 4))
        object.__setattr__(self, "freshness", round(float(self.freshness), 4))
        object.__setattr__(self, "metadata", _metadata(self.metadata))


@dataclass(frozen=True)
class PeerSetMember:
    member_id: str
    peer_set_id: str
    peer_candidate_id: str
    member_role: PeerSetMemberRole
    relationship_kind: RelationshipKind
    relationship_id: str
    status: PeerSetStatus
    confidence: float
    freshness: float
    position: int
    effective_at: datetime
    recorded_at: datetime
    schema_version: str

    def __post_init__(self) -> None:
        for name in ("member_id", "peer_set_id", "peer_candidate_id", "relationship_id", "schema_version"):
            _text(name, getattr(self, name))
        _member("member_role", self.member_role, PEER_SET_MEMBER_ROLES)
        _member("relationship_kind", self.relationship_kind, RELATIONSHIP_KINDS)
        if self.member_role == "evidence_backed_competitor" and self.relationship_kind != "evidence_backed":
            msg = "evidence-backed members require evidence_backed relationship_kind"
            raise ValueError(msg)
        if self.member_role == "algorithmic_peer" and self.relationship_kind != "algorithmic_similarity":
            msg = "algorithmic members require algorithmic_similarity relationship_kind"
            raise ValueError(msg)
        _member("status", self.status, PEER_SET_STATUSES)
        _range("confidence", self.confidence)
        _range("freshness", self.freshness)
        if self.position < 0:
            msg = "position must be non-negative"
            raise ValueError(msg)
        _aware("effective_at", self.effective_at)
        _aware("recorded_at", self.recorded_at)
        object.__setattr__(self, "confidence", round(float(self.confidence), 4))
        object.__setattr__(self, "freshness", round(float(self.freshness), 4))


@dataclass(frozen=True)
class ComparisonDimension:
    dimension_id: str
    subject_candidate_id: str
    peer_candidate_id: str
    dimension_type: ComparisonDimensionType
    subject_value: str
    peer_value: str
    match_status: DimensionMatchStatus
    relationship_kind: RelationshipKind
    relationship_id: str
    policy_id: str
    policy_version: str
    confidence: float
    effective_at: datetime
    recorded_at: datetime
    schema_version: str

    def __post_init__(self) -> None:
        for name in (
            "dimension_id",
            "subject_candidate_id",
            "peer_candidate_id",
            "subject_value",
            "peer_value",
            "relationship_id",
            "policy_id",
            "policy_version",
            "schema_version",
        ):
            _text(name, getattr(self, name))
        _member("dimension_type", self.dimension_type, COMPARISON_DIMENSION_TYPES)
        _member("match_status", self.match_status, DIMENSION_MATCH_STATUSES)
        _member("relationship_kind", self.relationship_kind, RELATIONSHIP_KINDS)
        _range("confidence", self.confidence)
        _aware("effective_at", self.effective_at)
        _aware("recorded_at", self.recorded_at)
        object.__setattr__(self, "confidence", round(float(self.confidence), 4))


@dataclass(frozen=True)
class CompetitiveAssessment:
    assessment_id: str
    subject_candidate_id: str
    peer_set_id: str
    status: PeerSetStatus
    evidence_backed_competitors: int
    algorithmic_peers: int
    missing_evidence_count: int
    conflict_count: int
    confidence: float
    coverage: float
    freshness: float
    mode: str
    effective_at: datetime
    recorded_at: datetime
    schema_version: str

    def __post_init__(self) -> None:
        for name in ("assessment_id", "subject_candidate_id", "peer_set_id", "mode", "schema_version"):
            _text(name, getattr(self, name))
        _member("status", self.status, PEER_SET_STATUSES)
        for name in ("evidence_backed_competitors", "algorithmic_peers", "missing_evidence_count", "conflict_count"):
            if int(getattr(self, name)) < 0:
                msg = f"{name} must be non-negative"
                raise ValueError(msg)
        _range("confidence", self.confidence)
        _range("coverage", self.coverage)
        _range("freshness", self.freshness)
        _aware("effective_at", self.effective_at)
        _aware("recorded_at", self.recorded_at)
        object.__setattr__(self, "confidence", round(float(self.confidence), 4))
        object.__setattr__(self, "coverage", round(float(self.coverage), 4))
        object.__setattr__(self, "freshness", round(float(self.freshness), 4))


@dataclass(frozen=True)
class CompetitiveRelationshipEvidenceLink:
    link_id: str
    relationship_id: str
    source_evidence_id: str
    role: CompetitiveLinkRole
    position: int
    created_at: datetime
    schema_version: str

    def __post_init__(self) -> None:
        _link_common(
            link_id=self.link_id,
            owner_name="relationship_id",
            owner_id=self.relationship_id,
            target_name="source_evidence_id",
            target_id=self.source_evidence_id,
            role=self.role,
            allowed_roles=COMPETITIVE_LINK_ROLES,
            position=self.position,
            created_at=self.created_at,
            schema_version=self.schema_version,
        )


@dataclass(frozen=True)
class CompetitiveRelationshipSpanLink:
    link_id: str
    relationship_id: str
    span_id: str
    role: CompetitiveLinkRole
    position: int
    created_at: datetime
    schema_version: str

    def __post_init__(self) -> None:
        _link_common(
            link_id=self.link_id,
            owner_name="relationship_id",
            owner_id=self.relationship_id,
            target_name="span_id",
            target_id=self.span_id,
            role=self.role,
            allowed_roles=COMPETITIVE_LINK_ROLES,
            position=self.position,
            created_at=self.created_at,
            schema_version=self.schema_version,
        )


@dataclass(frozen=True)
class PeerSetEvidenceLink:
    link_id: str
    peer_set_id: str
    source_evidence_id: str
    role: CompetitiveLinkRole
    position: int
    created_at: datetime
    schema_version: str

    def __post_init__(self) -> None:
        _link_common(
            link_id=self.link_id,
            owner_name="peer_set_id",
            owner_id=self.peer_set_id,
            target_name="source_evidence_id",
            target_id=self.source_evidence_id,
            role=self.role,
            allowed_roles=COMPETITIVE_LINK_ROLES,
            position=self.position,
            created_at=self.created_at,
            schema_version=self.schema_version,
        )


@dataclass(frozen=True)
class PeerSetSpanLink:
    link_id: str
    peer_set_id: str
    span_id: str
    role: CompetitiveLinkRole
    position: int
    created_at: datetime
    schema_version: str

    def __post_init__(self) -> None:
        _link_common(
            link_id=self.link_id,
            owner_name="peer_set_id",
            owner_id=self.peer_set_id,
            target_name="span_id",
            target_id=self.span_id,
            role=self.role,
            allowed_roles=COMPETITIVE_LINK_ROLES,
            position=self.position,
            created_at=self.created_at,
            schema_version=self.schema_version,
        )


@dataclass(frozen=True)
class CompetitiveConflictLink:
    link_id: str
    relationship_id: str
    conflict_id: str
    role: CompetitiveConflictLinkRole
    created_at: datetime
    schema_version: str

    def __post_init__(self) -> None:
        for name, value in (
            ("link_id", self.link_id),
            ("relationship_id", self.relationship_id),
            ("conflict_id", self.conflict_id),
            ("schema_version", self.schema_version),
        ):
            _text(name, value)
        _member("role", self.role, COMPETITIVE_CONFLICT_LINK_ROLES)
        _aware("created_at", self.created_at)


@dataclass(frozen=True)
class CompetitiveProcessingRun:
    run_id: str
    run_type: str
    status: CompetitiveProcessingRunStatus
    started_at: datetime
    finished_at: datetime | None
    schema_version: str

    def __post_init__(self) -> None:
        _text("run_id", self.run_id)
        _text("run_type", self.run_type)
        _member("status", self.status, COMPETITIVE_PROCESSING_RUN_STATUSES)
        _aware("started_at", self.started_at)
        _aware("finished_at", self.finished_at)
        _text("schema_version", self.schema_version)


@dataclass(frozen=True)
class CompetitiveCheckpoint:
    checkpoint_id: str
    processor_name: str
    target_id: str
    cursor: str
    updated_at: datetime
    schema_version: str

    def __post_init__(self) -> None:
        for name in ("checkpoint_id", "processor_name", "target_id", "cursor", "schema_version"):
            _text(name, getattr(self, name))
        _aware("updated_at", self.updated_at)


def _text(name: str, value: str) -> None:
    if not str(value).strip():
        msg = f"{name} is required"
        raise ValueError(msg)


def _member(name: str, value: str, allowed: frozenset[str]) -> None:
    if value not in allowed:
        msg = f"{name} must be one of {sorted(allowed)}"
        raise ValueError(msg)


def _range(name: str, value: float) -> None:
    if not 0.0 <= float(value) <= 1.0:
        msg = f"{name} must be between 0 and 1"
        raise ValueError(msg)


def _aware(name: str, value: datetime | None) -> None:
    if value is not None and value.tzinfo is None:
        msg = f"{name} must be timezone-aware"
        raise ValueError(msg)


def _metadata(value: dict[str, Any]) -> MappingProxyType[str, Any]:
    return MappingProxyType({str(key): item for key, item in value.items()})


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
    _aware("created_at", created_at)
