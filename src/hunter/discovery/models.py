from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, TypeAlias

CandidateLifecycleStatus = Literal[
    "discovered",
    "identified",
    "evidence_pending",
    "screenable",
    "analyzable",
    "ranked",
    "deep_research",
    "rejected",
    "archived",
]

CandidateType = Literal["project", "protocol", "token", "network", "infrastructure", "unknown"]
IdentityResolutionOutcome = Literal["exact", "probable", "ambiguous", "conflict", "rejected", "unresolved"]
CandidatePriority = Literal["critical", "high", "medium", "low", "defer"]


@dataclass(frozen=True)
class CandidateIdentifier:
    candidate_id: str
    namespace: str
    value: str
    source: str
    confidence: float = 1.0
    first_seen_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))
    last_seen_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))

    def __post_init__(self) -> None:
        if not self.candidate_id.strip() or not self.namespace.strip() or not self.value.strip():
            msg = "candidate identifier requires candidate_id, namespace, and value"
            raise ValueError(msg)
        _validate_confidence(self.confidence)


@dataclass(frozen=True)
class CandidateAlias:
    candidate_id: str
    alias: str
    alias_type: str
    source: str
    confidence: float = 1.0

    def __post_init__(self) -> None:
        if not self.candidate_id.strip() or not self.alias.strip() or not self.alias_type.strip():
            msg = "candidate alias requires candidate_id, alias, and alias_type"
            raise ValueError(msg)
        _validate_confidence(self.confidence)


@dataclass(frozen=True)
class CandidateSource:
    source_id: str
    candidate_id: str
    provider: str
    source_type: str
    source_url: str | None
    source_ref: str
    observed_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))
    confidence: float = 1.0

    def __post_init__(self) -> None:
        if not self.source_id.strip() or not self.candidate_id.strip() or not self.provider.strip():
            msg = "candidate source requires source_id, candidate_id, and provider"
            raise ValueError(msg)
        if not self.source_type.strip() or not self.source_ref.strip():
            msg = "candidate source requires source_type and source_ref"
            raise ValueError(msg)
        _validate_confidence(self.confidence)


@dataclass(frozen=True)
class DiscoverySource:
    name: str
    source_type: str
    base_url: str | None
    enabled: bool
    confidence: float

    def __post_init__(self) -> None:
        if not self.name.strip() or not self.source_type.strip():
            msg = "discovery source requires name and source_type"
            raise ValueError(msg)
        _validate_confidence(self.confidence)


@dataclass(frozen=True)
class SourceAssetRecord:
    provider: str
    provider_id: str
    slug: str
    name: str
    observed_at: datetime
    symbol: str | None = None
    sector: str | None = None
    primary_chain: str | None = None
    candidate_type: CandidateType = "unknown"
    source_url: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CandidateIdentity:
    candidate_id: str
    outcome: IdentityResolutionOutcome
    confidence: float
    evidence_ids: tuple[str, ...]
    reason: str

    def __post_init__(self) -> None:
        _validate_confidence(self.confidence)
        object.__setattr__(self, "evidence_ids", tuple(sorted({str(item) for item in self.evidence_ids})))


@dataclass(frozen=True)
class CandidateContract:
    candidate_id: str
    chain_id: str
    address: str
    role: str
    source_id: str
    confidence: float = 1.0


@dataclass(frozen=True)
class CandidateRepository:
    candidate_id: str
    repository: str
    source_id: str
    confidence: float = 1.0


@dataclass(frozen=True)
class CandidateCategory:
    candidate_id: str
    category: str
    source: str
    confidence: float = 1.0


@dataclass(frozen=True)
class CandidateEvidenceReference:
    candidate_id: str
    evidence_id: str
    source_id: str
    reference_type: str


@dataclass(frozen=True)
class CandidateLifecycleTransition:
    transition_id: str
    candidate_id: str
    previous_state: CandidateLifecycleStatus | None
    new_state: CandidateLifecycleStatus
    transitioned_at: datetime
    reason: str
    supporting_evidence_ids: tuple[str, ...]
    discovery_run_id: str

    def __post_init__(self) -> None:
        if not self.transition_id.strip() or not self.candidate_id.strip() or not self.reason.strip():
            msg = "candidate lifecycle transition requires id, candidate_id, and reason"
            raise ValueError(msg)
        object.__setattr__(
            self,
            "supporting_evidence_ids",
            tuple(sorted({str(item) for item in self.supporting_evidence_ids if str(item)})),
        )


@dataclass(frozen=True)
class DiscoveryConflict:
    conflict_id: str
    candidate_id: str
    conflict_type: str
    description: str
    detected_at: datetime
    source_ids: tuple[str, ...]
    status: str = "unresolved"


@dataclass(frozen=True)
class CandidateScreeningResult:
    screening_id: str
    candidate_id: str
    screened_at: datetime
    status: str
    score: float
    advanced: bool
    reasons: tuple[str, ...]
    missing_evidence: tuple[str, ...]
    confidence: float
    coverage: float

    def __post_init__(self) -> None:
        _validate_confidence(self.confidence)
        _validate_confidence(self.coverage)
        object.__setattr__(self, "reasons", tuple(str(item) for item in self.reasons))
        object.__setattr__(self, "missing_evidence", tuple(str(item) for item in self.missing_evidence))


@dataclass(frozen=True)
class CandidateQueueEntry:
    queue_entry_id: str
    candidate_id: str
    priority_score: float
    priority: CandidatePriority
    priority_reasons: tuple[str, ...]
    missing_evidence: tuple[str, ...]
    lifecycle_state: CandidateLifecycleStatus
    created_at: datetime
    updated_at: datetime
    source_run_id: str
    eligible_for_deep_analysis: bool


@dataclass(frozen=True)
class DiscoveryCheckpoint:
    checkpoint_id: str
    provider: str
    cursor: str
    updated_at: datetime
    status: str


@dataclass(frozen=True)
class DiscoveryCoverageReport:
    generated_at: datetime
    source_discovery_coverage: float
    canonical_identity_coverage: float
    contract_identity_coverage: float
    official_link_verification_coverage: float
    screening_coverage: float
    analyzable_coverage: float
    deep_analysis_coverage: float
    historical_point_in_time_coverage: float


@dataclass(frozen=True)
class CandidateRecord:
    candidate_id: str
    slug: str
    name: str
    symbol: str | None
    sector: str | None
    primary_chain: str | None
    candidate_type: CandidateType
    lifecycle_status: CandidateLifecycleStatus
    discovery_source: str
    first_seen_at: datetime
    last_seen_at: datetime
    confidence: float
    identifiers: tuple[CandidateIdentifier, ...] = ()
    aliases: tuple[CandidateAlias, ...] = ()
    sources: tuple[CandidateSource, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    source_ids: tuple[str, ...] = ()
    identity_resolution_status: str = "not_resolved"
    queue_status: str = "not_queued"
    screening_status: str = "not_screened"
    intrinsic_value_status: str = "not_modeled"
    competition_status: str = "not_modeled"
    network_effect_status: str = "not_modeled"
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.candidate_id.strip() or not self.slug.strip() or not self.name.strip():
            msg = "candidate requires candidate_id, slug, and name"
            raise ValueError(msg)
        if self.first_seen_at.tzinfo is None or self.last_seen_at.tzinfo is None:
            msg = "candidate timestamps must be timezone-aware"
            raise ValueError(msg)
        _validate_confidence(self.confidence)
        object.__setattr__(self, "identifiers", tuple(self.identifiers))
        object.__setattr__(self, "aliases", tuple(self.aliases))
        object.__setattr__(self, "sources", tuple(self.sources))
        object.__setattr__(self, "evidence_ids", tuple(sorted({str(item) for item in self.evidence_ids if str(item)})))
        object.__setattr__(self, "source_ids", tuple(sorted({str(item) for item in self.source_ids if str(item)})))
        object.__setattr__(self, "metadata", {str(key): value for key, value in self.metadata.items()})


@dataclass(frozen=True)
class DiscoveryRun:
    run_id: str
    provider: str
    started_at: datetime
    finished_at: datetime
    candidates_seen: int
    candidates_created: int
    candidates_updated: int
    status: str
    message: str = ""

    def __post_init__(self) -> None:
        if not self.run_id.strip() or not self.provider.strip() or not self.status.strip():
            msg = "discovery run requires run_id, provider, and status"
            raise ValueError(msg)


@dataclass(frozen=True)
class CandidateRegistryStats:
    total_candidates: int
    configured_candidates: int
    by_status: dict[str, int]
    by_source: dict[str, int]
    identifier_count: int
    alias_count: int
    source_count: int
    screenable_candidates: int
    future_identity_ready_candidates: int
    last_run_at: datetime | None

    @property
    def registry_coverage(self) -> float:
        if self.configured_candidates == 0:
            return 0.0
        return round(self.configured_candidates / self.configured_candidates, 4)

    @property
    def screenable_ratio(self) -> float:
        if self.total_candidates == 0:
            return 0.0
        return round(self.screenable_candidates / self.total_candidates, 4)


def _validate_confidence(value: float) -> None:
    if value < 0.0 or value > 1.0:
        msg = "confidence must be between 0 and 1"
        raise ValueError(msg)


CandidateRegistryEntry: TypeAlias = CandidateRecord
