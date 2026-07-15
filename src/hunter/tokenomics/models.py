from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

EvidenceLifecycleStatus = Literal[
    "active",
    "corrected",
    "retracted",
    "superseded",
    "contested",
    "unavailable",
    "ambiguous",
    "proxy",
]
ReportMode = Literal["known_by_hunter", "reconstructed"]
SupplyMetric = Literal[
    "circulating_supply",
    "total_supply",
    "max_supply",
    "fully_diluted_supply",
    "unlocked_supply",
    "locked_supply",
    "vested_supply",
    "burned_supply",
    "staked_supply",
    "treasury_supply",
    "unknown",
]
SupplyDefinitionState = EvidenceLifecycleStatus
AllocationCategory = Literal[
    "community",
    "ecosystem",
    "treasury",
    "team",
    "investor",
    "advisor",
    "foundation",
    "liquidity",
    "market_maker",
    "exchange",
    "staking",
    "burn",
    "public_sale",
    "private_sale",
    "airdrop",
    "rewards",
    "unknown",
    "other_declared",
]
VestingScheduleState = EvidenceLifecycleStatus
VestingSegmentState = Literal[
    "planned",
    "active",
    "completed",
    "corrected",
    "retracted",
    "superseded",
    "contested",
    "unavailable",
    "ambiguous",
    "proxy",
]
UnlockEventState = Literal[
    "scheduled",
    "completed",
    "delayed",
    "cancelled",
    "corrected",
    "retracted",
    "superseded",
    "contested",
    "unavailable",
    "ambiguous",
    "proxy",
]
AddressCategory = Literal[
    "treasury",
    "team",
    "investor",
    "market_maker",
    "exchange",
    "bridge",
    "contract",
    "burn",
    "staking",
    "unknown",
    "other_declared",
]
AttributionBasis = Literal[
    "official_disclosure",
    "validated_claim",
    "source_document",
    "provider_label",
    "onchain_contract_role",
    "balance_only",
    "manual_review",
    "unknown",
    "proxy",
]
VerificationState = Literal["verified", "unverified", "disputed", "unavailable", "ambiguous", "proxy"]
ConfidenceState = Literal["high", "medium", "low", "unknown", "contested", "unavailable", "proxy"]
AvailabilityState = Literal["available", "stale", "partial", "unavailable", "ambiguous", "proxy"]
CoverageState = Literal["complete", "partial", "gap", "unavailable", "unknown", "proxy"]
MarketObservationCoverageState = Literal["complete", "partial", "thin", "stale", "unavailable", "unknown", "proxy"]
ExchangeFlowCoverageState = Literal["complete", "partial", "estimated", "stale", "unavailable", "unknown", "proxy"]
ConflictState = Literal["open", "contested", "resolved", "retracted", "unavailable", "ambiguous"]
ResolutionState = Literal["unresolved", "accepted", "rejected", "corrected", "superseded", "withdrawn"]

EVIDENCE_LIFECYCLE_STATUSES: frozenset[str] = frozenset(EvidenceLifecycleStatus.__args__)  # type: ignore[attr-defined]
REPORT_MODES: frozenset[str] = frozenset(ReportMode.__args__)  # type: ignore[attr-defined]
SUPPLY_METRICS: frozenset[str] = frozenset(SupplyMetric.__args__)  # type: ignore[attr-defined]
SUPPLY_DEFINITION_STATES: frozenset[str] = EVIDENCE_LIFECYCLE_STATUSES
ALLOCATION_CATEGORIES: frozenset[str] = frozenset(AllocationCategory.__args__)  # type: ignore[attr-defined]
VESTING_SCHEDULE_STATES: frozenset[str] = EVIDENCE_LIFECYCLE_STATUSES
VESTING_SEGMENT_STATES: frozenset[str] = frozenset(VestingSegmentState.__args__)  # type: ignore[attr-defined]
UNLOCK_EVENT_STATES: frozenset[str] = frozenset(UnlockEventState.__args__)  # type: ignore[attr-defined]
ADDRESS_CATEGORIES: frozenset[str] = frozenset(AddressCategory.__args__)  # type: ignore[attr-defined]
ATTRIBUTION_BASES: frozenset[str] = frozenset(AttributionBasis.__args__)  # type: ignore[attr-defined]
VERIFICATION_STATES: frozenset[str] = frozenset(VerificationState.__args__)  # type: ignore[attr-defined]
CONFIDENCE_STATES: frozenset[str] = frozenset(ConfidenceState.__args__)  # type: ignore[attr-defined]
AVAILABILITY_STATES: frozenset[str] = frozenset(AvailabilityState.__args__)  # type: ignore[attr-defined]
COVERAGE_STATES: frozenset[str] = frozenset(CoverageState.__args__)  # type: ignore[attr-defined]
MARKET_OBSERVATION_COVERAGE_STATES: frozenset[str] = frozenset(MarketObservationCoverageState.__args__)  # type: ignore[attr-defined]
EXCHANGE_FLOW_COVERAGE_STATES: frozenset[str] = frozenset(ExchangeFlowCoverageState.__args__)  # type: ignore[attr-defined]
CONFLICT_STATES: frozenset[str] = frozenset(ConflictState.__args__)  # type: ignore[attr-defined]
RESOLUTION_STATES: frozenset[str] = frozenset(ResolutionState.__args__)  # type: ignore[attr-defined]

TOKENOMICS_SCHEMA_VERSION = "tokenomics-v3.3.0-phase-a"


@dataclass(frozen=True)
class TokenAsset:
    asset_id: str
    candidate_id: str
    symbol: str
    name: str
    effective_at: datetime
    recorded_at: datetime
    schema_version: str = TOKENOMICS_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _texts(self, "asset_id", "candidate_id", "symbol", "name", "schema_version")
        _aware("effective_at", self.effective_at)
        _aware("recorded_at", self.recorded_at)


@dataclass(frozen=True)
class TokenRepresentation:
    representation_id: str
    asset_id: str
    chain: str
    contract_address: str
    decimals: int
    effective_at: datetime
    recorded_at: datetime
    schema_version: str = TOKENOMICS_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _texts(self, "representation_id", "asset_id", "chain", "contract_address", "schema_version")
        if self.decimals < 0:
            raise ValueError("decimals must be non-negative")
        _aware("effective_at", self.effective_at)
        _aware("recorded_at", self.recorded_at)


@dataclass(frozen=True)
class TokenomicsEvidenceArtifact:
    artifact_id: str
    source_type: str
    source_uri: str
    content_hash: str
    observed_at: datetime
    recorded_at: datetime
    lifecycle_status: EvidenceLifecycleStatus
    schema_version: str = TOKENOMICS_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _texts(self, "artifact_id", "source_type", "source_uri", "content_hash", "schema_version")
        _member("lifecycle_status", self.lifecycle_status, EVIDENCE_LIFECYCLE_STATUSES)
        _aware("observed_at", self.observed_at)
        _aware("recorded_at", self.recorded_at)


@dataclass(frozen=True)
class TokenomicsEvidenceClaim:
    claim_id: str
    asset_id: str
    subject: str
    predicate: str
    value: str
    unit: str
    evidence_status: EvidenceLifecycleStatus
    confidence_state: ConfidenceState
    effective_at: datetime
    recorded_at: datetime
    schema_version: str = TOKENOMICS_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _texts(self, "claim_id", "asset_id", "subject", "predicate", "value", "unit", "schema_version")
        _member("evidence_status", self.evidence_status, EVIDENCE_LIFECYCLE_STATUSES)
        _member("confidence_state", self.confidence_state, CONFIDENCE_STATES)
        _aware("effective_at", self.effective_at)
        _aware("recorded_at", self.recorded_at)


@dataclass(frozen=True)
class EvidenceLifecycleEvent:
    event_id: str
    claim_id: str
    lifecycle_status: EvidenceLifecycleStatus
    effective_at: datetime
    recorded_at: datetime
    predecessor_event_id: str | None = None
    predecessor_claim_id: str | None = None
    reason: str = ""
    schema_version: str = TOKENOMICS_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _texts(self, "event_id", "claim_id", "schema_version")
        _optional_text("predecessor_event_id", self.predecessor_event_id)
        _optional_text("predecessor_claim_id", self.predecessor_claim_id)
        _member("lifecycle_status", self.lifecycle_status, EVIDENCE_LIFECYCLE_STATUSES)
        _aware("effective_at", self.effective_at)
        _aware("recorded_at", self.recorded_at)


@dataclass(frozen=True)
class ClaimArtifactLink:
    link_id: str
    claim_id: str
    artifact_id: str
    role: str
    position: int
    schema_version: str = TOKENOMICS_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _texts(self, "link_id", "claim_id", "artifact_id", "role", "schema_version")
        if self.position < 0:
            raise ValueError("position must be non-negative")


@dataclass(frozen=True)
class SupplyObservation:
    observation_id: str
    representation_id: str
    supply_metric: SupplyMetric
    amount: str
    unit: str
    effective_at: datetime
    observed_at: datetime
    recorded_at: datetime
    availability_state: AvailabilityState
    coverage_state: CoverageState
    schema_version: str = TOKENOMICS_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _texts(self, "observation_id", "representation_id", "amount", "unit", "schema_version")
        _member("supply_metric", self.supply_metric, SUPPLY_METRICS)
        _member("availability_state", self.availability_state, AVAILABILITY_STATES)
        _member("coverage_state", self.coverage_state, COVERAGE_STATES)
        _aware("effective_at", self.effective_at)
        _aware("observed_at", self.observed_at)
        _aware("recorded_at", self.recorded_at)


@dataclass(frozen=True)
class SupplyDefinitionReconciliation:
    reconciliation_id: str
    asset_id: str
    supply_metric: SupplyMetric
    definition_state: SupplyDefinitionState
    effective_at: datetime
    recorded_at: datetime
    schema_version: str = TOKENOMICS_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _texts(self, "reconciliation_id", "asset_id", "schema_version")
        _member("supply_metric", self.supply_metric, SUPPLY_METRICS)
        _member("definition_state", self.definition_state, SUPPLY_DEFINITION_STATES)
        _aware("effective_at", self.effective_at)
        _aware("recorded_at", self.recorded_at)


@dataclass(frozen=True)
class AllocationDefinition:
    allocation_id: str
    asset_id: str
    category: AllocationCategory
    percentage: float | None
    amount: str | None
    unit: str
    effective_start_at: datetime
    effective_end_at: datetime | None
    recorded_at: datetime
    availability_state: AvailabilityState
    schema_version: str = TOKENOMICS_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _texts(self, "allocation_id", "asset_id", "unit", "schema_version")
        _member("category", self.category, ALLOCATION_CATEGORIES)
        _member("availability_state", self.availability_state, AVAILABILITY_STATES)
        if self.percentage is not None and not 0 <= self.percentage <= 1:
            raise ValueError("percentage must be between 0 and 1")
        _optional_text("amount", self.amount)
        _aware("effective_start_at", self.effective_start_at)
        _optional_aware("effective_end_at", self.effective_end_at)
        _aware("recorded_at", self.recorded_at)


@dataclass(frozen=True)
class VestingSchedule:
    schedule_id: str
    asset_id: str
    allocation_id: str
    schedule_state: VestingScheduleState
    effective_start_at: datetime
    effective_end_at: datetime | None
    recorded_at: datetime
    schema_version: str = TOKENOMICS_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _texts(self, "schedule_id", "asset_id", "allocation_id", "schema_version")
        _member("schedule_state", self.schedule_state, VESTING_SCHEDULE_STATES)
        _aware("effective_start_at", self.effective_start_at)
        _optional_aware("effective_end_at", self.effective_end_at)
        _aware("recorded_at", self.recorded_at)


@dataclass(frozen=True)
class VestingScheduleSegment:
    segment_id: str
    schedule_id: str
    segment_state: VestingSegmentState
    start_at: datetime
    end_at: datetime
    amount: str | None
    percentage: float | None
    recorded_at: datetime
    schema_version: str = TOKENOMICS_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _texts(self, "segment_id", "schedule_id", "schema_version")
        _member("segment_state", self.segment_state, VESTING_SEGMENT_STATES)
        _optional_text("amount", self.amount)
        if self.percentage is not None and not 0 <= self.percentage <= 1:
            raise ValueError("percentage must be between 0 and 1")
        _aware("start_at", self.start_at)
        _aware("end_at", self.end_at)
        _aware("recorded_at", self.recorded_at)


@dataclass(frozen=True)
class UnlockEvent:
    unlock_event_id: str
    schedule_id: str
    unlock_state: UnlockEventState
    unlock_at: datetime
    amount: str | None
    percentage: float | None
    recorded_at: datetime
    schema_version: str = TOKENOMICS_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _texts(self, "unlock_event_id", "schedule_id", "schema_version")
        _member("unlock_state", self.unlock_state, UNLOCK_EVENT_STATES)
        _optional_text("amount", self.amount)
        if self.percentage is not None and not 0 <= self.percentage <= 1:
            raise ValueError("percentage must be between 0 and 1")
        _aware("unlock_at", self.unlock_at)
        _aware("recorded_at", self.recorded_at)


@dataclass(frozen=True)
class HolderBalanceSnapshot:
    snapshot_id: str
    representation_id: str
    observed_at: datetime
    recorded_at: datetime
    coverage_state: CoverageState
    schema_version: str = TOKENOMICS_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _texts(self, "snapshot_id", "representation_id", "schema_version")
        _member("coverage_state", self.coverage_state, COVERAGE_STATES)
        _aware("observed_at", self.observed_at)
        _aware("recorded_at", self.recorded_at)


@dataclass(frozen=True)
class HolderEntry:
    entry_id: str
    snapshot_id: str
    address: str
    balance: str
    unit: str
    attribution_basis: AttributionBasis
    schema_version: str = TOKENOMICS_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _texts(self, "entry_id", "snapshot_id", "address", "balance", "unit", "schema_version")
        _member("attribution_basis", self.attribution_basis, ATTRIBUTION_BASES)


@dataclass(frozen=True)
class AddressClassification:
    classification_id: str
    representation_id: str
    address: str
    category: AddressCategory
    attribution_basis: AttributionBasis
    verification_state: VerificationState
    confidence_state: ConfidenceState
    valid_from: datetime
    valid_to: datetime | None
    recorded_at: datetime
    schema_version: str = TOKENOMICS_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _texts(self, "classification_id", "representation_id", "address", "schema_version")
        _member("category", self.category, ADDRESS_CATEGORIES)
        _member("attribution_basis", self.attribution_basis, ATTRIBUTION_BASES)
        _member("verification_state", self.verification_state, VERIFICATION_STATES)
        _member("confidence_state", self.confidence_state, CONFIDENCE_STATES)
        if self.attribution_basis == "balance_only" and self.category != "unknown":
            raise ValueError("balance_only attribution cannot assign an address identity category")
        _aware("valid_from", self.valid_from)
        _optional_aware("valid_to", self.valid_to)
        _aware("recorded_at", self.recorded_at)


@dataclass(frozen=True)
class ClassificationEvidenceLink:
    link_id: str
    classification_id: str
    artifact_id: str
    role: str
    position: int
    schema_version: str = TOKENOMICS_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _texts(self, "link_id", "classification_id", "artifact_id", "role", "schema_version")
        if self.position < 0:
            raise ValueError("position must be non-negative")


@dataclass(frozen=True)
class VenueMarketObservation:
    observation_id: str
    representation_id: str
    venue: str
    pair: str
    price: str
    volume_24h: str | None
    window_start: datetime
    window_end: datetime
    observed_at: datetime
    recorded_at: datetime
    coverage_state: MarketObservationCoverageState
    schema_version: str = TOKENOMICS_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _texts(self, "observation_id", "representation_id", "venue", "pair", "price", "schema_version")
        _optional_text("volume_24h", self.volume_24h)
        _member("coverage_state", self.coverage_state, MARKET_OBSERVATION_COVERAGE_STATES)
        _aware("window_start", self.window_start)
        _aware("window_end", self.window_end)
        _aware("observed_at", self.observed_at)
        _aware("recorded_at", self.recorded_at)


@dataclass(frozen=True)
class TransferObservation:
    observation_id: str
    representation_id: str
    tx_hash: str
    from_address: str
    to_address: str
    amount: str
    observed_at: datetime
    recorded_at: datetime
    coverage_state: CoverageState
    schema_version: str = TOKENOMICS_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _texts(
            self,
            "observation_id",
            "representation_id",
            "tx_hash",
            "from_address",
            "to_address",
            "amount",
            "schema_version",
        )
        _member("coverage_state", self.coverage_state, COVERAGE_STATES)
        _aware("observed_at", self.observed_at)
        _aware("recorded_at", self.recorded_at)


@dataclass(frozen=True)
class ExchangeFlowWindow:
    window_id: str
    representation_id: str
    venue: str
    window_start: datetime
    window_end: datetime
    inflow: str | None
    outflow: str | None
    coverage_state: ExchangeFlowCoverageState
    recorded_at: datetime
    schema_version: str = TOKENOMICS_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _texts(self, "window_id", "representation_id", "venue", "schema_version")
        _optional_text("inflow", self.inflow)
        _optional_text("outflow", self.outflow)
        _member("coverage_state", self.coverage_state, EXCHANGE_FLOW_COVERAGE_STATES)
        _aware("window_start", self.window_start)
        _aware("window_end", self.window_end)
        _aware("recorded_at", self.recorded_at)


@dataclass(frozen=True)
class ObservationConflict:
    conflict_id: str
    asset_id: str
    conflict_state: ConflictState
    detected_at: datetime
    recorded_at: datetime
    schema_version: str = TOKENOMICS_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _texts(self, "conflict_id", "asset_id", "schema_version")
        _member("conflict_state", self.conflict_state, CONFLICT_STATES)
        _aware("detected_at", self.detected_at)
        _aware("recorded_at", self.recorded_at)


@dataclass(frozen=True)
class ObservationConflictMember:
    member_id: str
    conflict_id: str
    observation_table: str
    observation_id: str
    role: str
    schema_version: str = TOKENOMICS_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _texts(self, "member_id", "conflict_id", "observation_table", "observation_id", "role", "schema_version")


@dataclass(frozen=True)
class ConflictResolutionEvent:
    event_id: str
    conflict_id: str
    resolution_state: ResolutionState
    effective_at: datetime
    recorded_at: datetime
    predecessor_event_id: str | None = None
    reason: str = ""
    schema_version: str = TOKENOMICS_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _texts(self, "event_id", "conflict_id", "schema_version")
        _optional_text("predecessor_event_id", self.predecessor_event_id)
        _member("resolution_state", self.resolution_state, RESOLUTION_STATES)
        _aware("effective_at", self.effective_at)
        _aware("recorded_at", self.recorded_at)


@dataclass(frozen=True)
class TokenomicsReportRun:
    run_id: str
    execution_identity: str
    report_mode: ReportMode
    cutoff_at: datetime | None
    started_at: datetime
    recorded_at: datetime
    schema_version: str = TOKENOMICS_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _texts(self, "run_id", "execution_identity", "schema_version")
        _member("report_mode", self.report_mode, REPORT_MODES)
        _optional_aware("cutoff_at", self.cutoff_at)
        _aware("started_at", self.started_at)
        _aware("recorded_at", self.recorded_at)


@dataclass(frozen=True)
class TokenomicsReportObservationLink:
    link_id: str
    report_run_id: str
    observation_table: str
    observation_id: str
    role: str
    position: int
    schema_version: str = TOKENOMICS_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _texts(self, "link_id", "report_run_id", "observation_table", "observation_id", "role", "schema_version")
        if self.position < 0:
            raise ValueError("position must be non-negative")


@dataclass(frozen=True)
class TokenomicsSufficiencyAssessmentRecord:
    assessment_id: str
    report_run_id: str
    asset_id: str
    assessment_scope: str
    availability_state: AvailabilityState
    confidence_state: ConfidenceState
    limitation: str
    effective_at: datetime
    recorded_at: datetime
    schema_version: str = TOKENOMICS_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _texts(self, "assessment_id", "report_run_id", "asset_id", "assessment_scope", "limitation", "schema_version")
        _member("availability_state", self.availability_state, AVAILABILITY_STATES)
        _member("confidence_state", self.confidence_state, CONFIDENCE_STATES)
        _aware("effective_at", self.effective_at)
        _aware("recorded_at", self.recorded_at)


@dataclass(frozen=True)
class TokenomicsReportSufficiencyLink:
    link_id: str
    report_run_id: str
    assessment_id: str
    role: str
    position: int
    schema_version: str = TOKENOMICS_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _texts(self, "link_id", "report_run_id", "assessment_id", "role", "schema_version")
        if self.position < 0:
            raise ValueError("position must be non-negative")


def _texts(instance: object, *names: str) -> None:
    for name in names:
        _text(name, getattr(instance, name))


def _text(name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")


def _optional_text(name: str, value: str | None) -> None:
    if value is not None:
        _text(name, value)


def _member(name: str, value: str, values: frozenset[str]) -> None:
    if value not in values:
        allowed = ", ".join(sorted(values))
        raise ValueError(f"{name} must be one of: {allowed}")


def _aware(name: str, value: datetime) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def _optional_aware(name: str, value: datetime | None) -> None:
    if value is not None:
        _aware(name, value)
