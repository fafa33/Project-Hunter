from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
from typing import Any, Literal

AvailabilityState = Literal["available", "stale", "partial", "unavailable"]
Directness = Literal["direct_observation", "proxy_signal", "derived_from_direct", "unavailable"]
SufficiencyState = Literal[
    "sufficient",
    "sufficient_with_limitations",
    "degraded",
    "insufficient",
    "unavailable",
]
DegradedModeOutcome = Literal[
    "normal",
    "degraded_non_blocking",
    "degraded_material_limitation",
    "blocked_insufficient_evidence",
    "unavailable",
]
BlockingLevel = Literal[
    "required_for_output",
    "required_for_high_confidence",
    "required_for_full_report",
    "optional_context",
]
RequirementKind = Literal[
    "direct_observation",
    "derived_observation",
    "source_quality",
    "freshness",
    "lineage",
    "conflict_state",
    "proxy_context",
]
EvidenceDomain = Literal[
    "candidate_identity",
    "market",
    "onchain",
    "developer",
    "social",
    "macro",
    "evidence_intelligence",
    "competitive",
    "historical",
    "protocol",
    "unknown",
]
ProxySignalType = Literal[
    "market_proxy",
    "liquidity_proxy",
    "developer_proxy",
    "usage_proxy",
    "revenue_proxy",
    "competitive_proxy",
    "social_proxy",
    "none",
]
DisagreementState = Literal[
    "agreement",
    "disagreement",
    "insufficient_sources",
    "incompatible_scope",
    "unavailable",
]
FreshnessState = Literal["fresh", "stale", "unavailable"]
SourceQualityState = Literal["high", "medium", "low", "conflicted", "unavailable"]
LineageState = Literal["complete", "partial", "missing"]
ConflictState = Literal["none", "disputed", "conflicted", "unavailable"]
ReplayMode = Literal["current", "historical_strict_known_by_hunter", "reconstructed_after_cutoff"]
SourceValidationStatus = Literal[
    "agreement",
    "disagreement",
    "missing_provider",
    "stale_source",
    "conflict",
    "incompatible_scope",
    "unavailable",
]
LineageOwnerType = Literal["availability", "assessment", "disagreement", "validation"]

AVAILABILITY_STATES: frozenset[str] = frozenset(AvailabilityState.__args__)  # type: ignore[attr-defined]
DIRECTNESS_VALUES: frozenset[str] = frozenset(Directness.__args__)  # type: ignore[attr-defined]
SUFFICIENCY_STATES: frozenset[str] = frozenset(SufficiencyState.__args__)  # type: ignore[attr-defined]
DEGRADED_MODE_OUTCOMES: frozenset[str] = frozenset(DegradedModeOutcome.__args__)  # type: ignore[attr-defined]
BLOCKING_LEVELS: frozenset[str] = frozenset(BlockingLevel.__args__)  # type: ignore[attr-defined]
REQUIREMENT_KINDS: frozenset[str] = frozenset(RequirementKind.__args__)  # type: ignore[attr-defined]
EVIDENCE_DOMAINS: frozenset[str] = frozenset(EvidenceDomain.__args__)  # type: ignore[attr-defined]
PROXY_SIGNAL_TYPES: frozenset[str] = frozenset(ProxySignalType.__args__)  # type: ignore[attr-defined]
DISAGREEMENT_STATES: frozenset[str] = frozenset(DisagreementState.__args__)  # type: ignore[attr-defined]
FRESHNESS_STATES: frozenset[str] = frozenset(FreshnessState.__args__)  # type: ignore[attr-defined]
SOURCE_QUALITY_STATES: frozenset[str] = frozenset(SourceQualityState.__args__)  # type: ignore[attr-defined]
LINEAGE_STATES: frozenset[str] = frozenset(LineageState.__args__)  # type: ignore[attr-defined]
CONFLICT_STATES: frozenset[str] = frozenset(ConflictState.__args__)  # type: ignore[attr-defined]
REPLAY_MODES: frozenset[str] = frozenset(ReplayMode.__args__)  # type: ignore[attr-defined]
SOURCE_VALIDATION_STATUSES: frozenset[str] = frozenset(SourceValidationStatus.__args__)  # type: ignore[attr-defined]
LINEAGE_OWNER_TYPES: frozenset[str] = frozenset(LineageOwnerType.__args__)  # type: ignore[attr-defined]


@dataclass(frozen=True)
class DataRequirement:
    requirement_id: str
    engine_id: str
    analysis_purpose: str
    output_field: str
    requirement_kind: RequirementKind
    evidence_domain: EvidenceDomain
    required_entity_type: str
    required_source_types: tuple[str, ...]
    direct_observation_required: bool
    proxy_allowed: bool
    accepted_proxy_types: tuple[ProxySignalType, ...]
    minimum_freshness_seconds: int
    minimum_source_authority: str
    minimum_lineage_depth: int
    minimum_confidence: float
    historical_required: bool
    blocking_level: BlockingLevel
    policy_id: str
    policy_version: str
    effective_at: datetime
    recorded_at: datetime
    schema_version: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in (
            "requirement_id",
            "engine_id",
            "analysis_purpose",
            "output_field",
            "required_entity_type",
            "minimum_source_authority",
            "policy_id",
            "policy_version",
            "schema_version",
        ):
            _text(name, getattr(self, name))
        _member("requirement_kind", self.requirement_kind, REQUIREMENT_KINDS)
        _member("evidence_domain", self.evidence_domain, EVIDENCE_DOMAINS)
        _member("blocking_level", self.blocking_level, BLOCKING_LEVELS)
        _text_tuple("required_source_types", self.required_source_types)
        _proxy_tuple("accepted_proxy_types", self.accepted_proxy_types)
        if self.proxy_allowed and not self.accepted_proxy_types:
            msg = "proxy-allowed requirements must define accepted_proxy_types"
            raise ValueError(msg)
        if not self.proxy_allowed and self.accepted_proxy_types:
            msg = "accepted_proxy_types require proxy_allowed"
            raise ValueError(msg)
        if self.direct_observation_required and self.requirement_kind == "proxy_context":
            msg = "proxy_context requirements cannot require direct observations"
            raise ValueError(msg)
        if self.minimum_freshness_seconds < 0:
            msg = "minimum_freshness_seconds must be non-negative"
            raise ValueError(msg)
        if self.minimum_lineage_depth < 0:
            msg = "minimum_lineage_depth must be non-negative"
            raise ValueError(msg)
        _range("minimum_confidence", self.minimum_confidence)
        _aware("effective_at", self.effective_at)
        _aware("recorded_at", self.recorded_at)
        object.__setattr__(self, "minimum_confidence", round(float(self.minimum_confidence), 4))
        object.__setattr__(self, "metadata", _metadata(self.metadata))

    def directness_satisfies_requirement(self, directness: Directness) -> bool:
        if self.direct_observation_required:
            return directness == "direct_observation"
        if directness == "proxy_signal":
            return self.proxy_allowed
        return directness in {"direct_observation", "derived_from_direct"}


@dataclass(frozen=True)
class DataAvailability:
    availability_id: str
    requirement_id: str
    candidate_id: str
    engine_id: str
    analysis_purpose: str
    availability_state: AvailabilityState
    directness: Directness
    proxy_type: ProxySignalType | None
    freshness_seconds: int | None
    source_quality: SourceQualityState
    lineage_complete: bool
    conflict_state: ConflictState
    evidence_count: int
    missing_reason: str
    effective_at: datetime
    recorded_at: datetime
    cutoff_at: datetime | None
    replay_mode: ReplayMode
    processing_run_id: str
    schema_version: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in (
            "availability_id",
            "requirement_id",
            "candidate_id",
            "engine_id",
            "analysis_purpose",
            "processing_run_id",
            "schema_version",
        ):
            _text(name, getattr(self, name))
        _member("availability_state", self.availability_state, AVAILABILITY_STATES)
        _member("directness", self.directness, DIRECTNESS_VALUES)
        _member("source_quality", self.source_quality, SOURCE_QUALITY_STATES)
        _member("conflict_state", self.conflict_state, CONFLICT_STATES)
        _member("replay_mode", self.replay_mode, REPLAY_MODES)
        if self.proxy_type is not None:
            _member("proxy_type", self.proxy_type, PROXY_SIGNAL_TYPES)
            if self.proxy_type == "none":
                msg = "proxy_type cannot be none when present"
                raise ValueError(msg)
        if self.directness == "proxy_signal" and self.proxy_type is None:
            msg = "proxy_signal directness requires proxy_type"
            raise ValueError(msg)
        if self.directness != "proxy_signal" and self.proxy_type is not None:
            msg = "proxy_type is only allowed for proxy_signal directness"
            raise ValueError(msg)
        if self.availability_state == "unavailable":
            _text("missing_reason", self.missing_reason)
            if self.directness != "unavailable":
                msg = "unavailable state requires unavailable directness"
                raise ValueError(msg)
        if self.directness == "unavailable" and self.availability_state != "unavailable":
            msg = "unavailable directness requires unavailable state"
            raise ValueError(msg)
        if self.freshness_seconds is not None and self.freshness_seconds < 0:
            msg = "freshness_seconds must be non-negative"
            raise ValueError(msg)
        if self.evidence_count < 0:
            msg = "evidence_count must be non-negative"
            raise ValueError(msg)
        _aware("effective_at", self.effective_at)
        _aware("recorded_at", self.recorded_at)
        _aware("cutoff_at", self.cutoff_at)
        object.__setattr__(self, "metadata", _metadata(self.metadata))


@dataclass(frozen=True)
class DataSufficiencyAssessment:
    assessment_id: str
    candidate_id: str
    engine_id: str
    analysis_purpose: str
    assessment_scope: str
    sufficiency_state: SufficiencyState
    degraded_mode: DegradedModeOutcome
    coverage_score: float
    freshness_state: FreshnessState
    source_quality_state: SourceQualityState
    lineage_state: LineageState
    conflict_state: ConflictState
    direct_observation_coverage: float
    proxy_signal_coverage: float
    material_missing_count: int
    limitations_summary: str
    policy_id: str
    policy_version: str
    effective_at: datetime
    recorded_at: datetime
    cutoff_at: datetime | None
    replay_mode: ReplayMode
    processing_run_id: str
    schema_version: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in (
            "assessment_id",
            "candidate_id",
            "engine_id",
            "analysis_purpose",
            "assessment_scope",
            "policy_id",
            "policy_version",
            "processing_run_id",
            "schema_version",
        ):
            _text(name, getattr(self, name))
        _member("sufficiency_state", self.sufficiency_state, SUFFICIENCY_STATES)
        _member("degraded_mode", self.degraded_mode, DEGRADED_MODE_OUTCOMES)
        _member("freshness_state", self.freshness_state, FRESHNESS_STATES)
        _member("source_quality_state", self.source_quality_state, SOURCE_QUALITY_STATES)
        _member("lineage_state", self.lineage_state, LINEAGE_STATES)
        _member("conflict_state", self.conflict_state, CONFLICT_STATES)
        _member("replay_mode", self.replay_mode, REPLAY_MODES)
        for name in ("coverage_score", "direct_observation_coverage", "proxy_signal_coverage"):
            _range(name, getattr(self, name))
        if self.material_missing_count < 0:
            msg = "material_missing_count must be non-negative"
            raise ValueError(msg)
        if self.sufficiency_state in {"degraded", "insufficient", "unavailable"}:
            _text("limitations_summary", self.limitations_summary)
        _aware("effective_at", self.effective_at)
        _aware("recorded_at", self.recorded_at)
        _aware("cutoff_at", self.cutoff_at)
        object.__setattr__(self, "coverage_score", round(float(self.coverage_score), 4))
        object.__setattr__(self, "direct_observation_coverage", round(float(self.direct_observation_coverage), 4))
        object.__setattr__(self, "proxy_signal_coverage", round(float(self.proxy_signal_coverage), 4))
        object.__setattr__(self, "metadata", _metadata(self.metadata))


@dataclass(frozen=True)
class SourceDisagreement:
    disagreement_id: str
    candidate_id: str
    requirement_id: str
    engine_id: str
    analysis_purpose: str
    disagreement_state: DisagreementState
    compared_source_count: int
    compatible_scope: bool
    reason: str
    effective_at: datetime
    recorded_at: datetime
    replay_mode: ReplayMode
    processing_run_id: str
    schema_version: str

    def __post_init__(self) -> None:
        for name in (
            "disagreement_id",
            "candidate_id",
            "requirement_id",
            "engine_id",
            "analysis_purpose",
            "reason",
            "processing_run_id",
            "schema_version",
        ):
            _text(name, getattr(self, name))
        _member("disagreement_state", self.disagreement_state, DISAGREEMENT_STATES)
        _member("replay_mode", self.replay_mode, REPLAY_MODES)
        if self.compared_source_count < 0:
            msg = "compared_source_count must be non-negative"
            raise ValueError(msg)
        _aware("effective_at", self.effective_at)
        _aware("recorded_at", self.recorded_at)


@dataclass(frozen=True)
class SourceValidationResult:
    validation_id: str
    candidate_id: str
    requirement_id: str
    engine_id: str
    analysis_purpose: str
    source_a: str
    source_b: str
    validation_status: SourceValidationStatus
    compatible_scope: bool
    source_authority_state: SourceQualityState
    freshness_state: FreshnessState
    reason: str
    effective_at: datetime
    recorded_at: datetime
    cutoff_at: datetime | None
    replay_mode: ReplayMode
    processing_run_id: str
    schema_version: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in (
            "validation_id",
            "candidate_id",
            "requirement_id",
            "engine_id",
            "analysis_purpose",
            "source_a",
            "source_b",
            "reason",
            "processing_run_id",
            "schema_version",
        ):
            _text(name, getattr(self, name))
        _member("validation_status", self.validation_status, SOURCE_VALIDATION_STATUSES)
        _member("source_authority_state", self.source_authority_state, SOURCE_QUALITY_STATES)
        _member("freshness_state", self.freshness_state, FRESHNESS_STATES)
        _member("replay_mode", self.replay_mode, REPLAY_MODES)
        _aware("effective_at", self.effective_at)
        _aware("recorded_at", self.recorded_at)
        _aware("cutoff_at", self.cutoff_at)
        object.__setattr__(self, "metadata", _metadata(self.metadata))


@dataclass(frozen=True)
class DataSufficiencyEvidenceLink:
    link_id: str
    owner_type: LineageOwnerType
    owner_id: str
    source_evidence_id: str
    role: str
    position: int
    created_at: datetime
    schema_version: str

    def __post_init__(self) -> None:
        _lineage(self, "source_evidence_id")


@dataclass(frozen=True)
class DataSufficiencySpanLink:
    link_id: str
    owner_type: LineageOwnerType
    owner_id: str
    span_id: str
    role: str
    position: int
    created_at: datetime
    schema_version: str

    def __post_init__(self) -> None:
        _lineage(self, "span_id")


@dataclass(frozen=True)
class DataSufficiencyClaimLink:
    link_id: str
    owner_type: LineageOwnerType
    owner_id: str
    claim_id: str
    role: str
    position: int
    created_at: datetime
    schema_version: str

    def __post_init__(self) -> None:
        _lineage(self, "claim_id")


@dataclass(frozen=True)
class DataSufficiencyConflictLink:
    link_id: str
    owner_type: LineageOwnerType
    owner_id: str
    conflict_id: str
    role: str
    position: int
    created_at: datetime
    schema_version: str

    def __post_init__(self) -> None:
        _lineage(self, "conflict_id")


@dataclass(frozen=True)
class DataSufficiencyProcessingRun:
    run_id: str
    run_type: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    replay_mode: ReplayMode
    cutoff_at: datetime | None
    schema_version: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("run_id", "run_type", "status", "schema_version"):
            _text(name, getattr(self, name))
        _member("replay_mode", self.replay_mode, REPLAY_MODES)
        _aware("started_at", self.started_at)
        _aware("finished_at", self.finished_at)
        _aware("cutoff_at", self.cutoff_at)
        object.__setattr__(self, "metadata", _metadata(self.metadata))


@dataclass(frozen=True)
class DataSufficiencyCheckpoint:
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


def _lineage(value: Any, lineage_field: str) -> None:
    for name in ("link_id", "owner_id", lineage_field, "role", "schema_version"):
        _text(name, getattr(value, name))
    _member("owner_type", value.owner_type, LINEAGE_OWNER_TYPES)
    if value.position < 0:
        msg = "position must be non-negative"
        raise ValueError(msg)
    _aware("created_at", value.created_at)


def _text_tuple(name: str, values: tuple[str, ...]) -> None:
    if not values:
        msg = f"{name} is required"
        raise ValueError(msg)
    for value in values:
        _text(name, value)


def _proxy_tuple(name: str, values: tuple[str, ...]) -> None:
    for value in values:
        _member(name, value, PROXY_SIGNAL_TYPES)
        if value == "none":
            msg = "accepted_proxy_types cannot include none"
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
