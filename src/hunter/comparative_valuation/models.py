from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
try:
    from datetime import UTC
except ImportError:
    from datetime import timezone as _tz
    UTC = _tz.utc
from decimal import Decimal, InvalidOperation
from typing import Literal

from hunter.value_capture.models import EconomicClaimIdentity

COMPARATIVE_VALUATION_SCHEMA_VERSION = "comparative-valuation-v1"

AvailabilityState = Literal[
    "AVAILABLE",
    "LIMITED_PEER_SET",
    "UNAVAILABLE_NO_CANDIDATES",
    "UNAVAILABLE_INSUFFICIENT_ELIGIBLE_PEERS",
    "UNAVAILABLE_INCOMPATIBLE_COORDINATES",
    "UNAVAILABLE_STALE_INPUTS",
    "UNAVAILABLE_CONFLICTED_INPUTS",
    "UNAVAILABLE_UNSUPPORTED_ENTITY_CLASS",
    "UNAVAILABLE_UNCALIBRATED_NORMALIZATION",
]
EligibilityDecision = Literal["included", "excluded", "indeterminate"]
QualityState = Literal["accepted", "unavailable"]
ConflictState = Literal["none", "open", "contested", "resolved"]
NormalizationStatus = Literal["unavailable"]

AVAILABILITY_STATES = frozenset(AvailabilityState.__args__)  # type: ignore[attr-defined]
ELIGIBILITY_DECISIONS = frozenset(EligibilityDecision.__args__)  # type: ignore[attr-defined]
QUALITY_STATES = frozenset(QualityState.__args__)  # type: ignore[attr-defined]
CONFLICT_STATES = frozenset(ConflictState.__args__)  # type: ignore[attr-defined]

# ADR 0026 Reference and Residual Method: at least three eligible economic-entity peers
# are required. With fewer, no canonical reference or residual is available. Fixed, never
# caller-parameterized.
MINIMUM_ELIGIBLE_PEERS = 3

# ADR 0026 Comparability Requirements: the single denominator is native attributable
# value-capture flow; the numerator is the fully diluted observed market value. Fixed.
NUMERATOR_METRIC = "fully_diluted_observed_market_value"
DENOMINATOR_EVIDENCE_TYPE = "native_attributable_value_capture_flow"
FULLY_DILUTED_MARKET_FACT_TYPE = "fully_diluted_valuation"

# ADR 0026 Reference and Residual Method: equal peer weighting and the unweighted median
# reference statistic. No alternative weighting policy is authorized by this methodology.
REFERENCE_STATISTIC = "unweighted_median"

# ADR 0026 Missingness: `LIMITED_PEER_SET` is descriptive metadata only; below three
# eligible peers the canonical assessment and residual are unavailable.
LIMITED_PEER_SET_STATE: AvailabilityState = "LIMITED_PEER_SET"

# ADR 0026 Persistence requirements: normalization requires a predeclared, versioned,
# monotonic transform calibrated on strict-known historical evidence. No calibration
# exists in this foundation, so the only legal value is "unavailable".
NORMALIZATION_UNAVAILABLE: NormalizationStatus = "unavailable"

# ADR 0021 Anti-double-counting and correlation policy: comparative valuation joins the
# valuation/fair-value correlation group when it shares the same fundamental denominator
# evidence, as this methodology does. Fixed, never caller-parameterized.
REQUIRED_CORRELATION_GROUP = "valuation-mispricing"

# ADR 0026 Peer Universe Authority: one immutable, versioned, point-in-time candidate
# universe; no Registry + Discovery union and no current-list peer universe (Option 2C of
# ADPR-0003).
CANDIDATE_SOURCE_AUTHORITY = "option-2c-versioned-point-in-time-candidate-universe"

# The only ADR currently authorizing this record family. Fixed; extending authorization
# to another ADR is a future governance decision.
AUTHORIZING_ADR_REFERENCE = "ADR-0026"

# Identifies the sole canonical authority permitted to produce this record family.
CANONICAL_AUTHORITY_ID = "canonical-comparative-valuation-authority-v1"


@dataclass(frozen=True)
class PeerCandidate:
    """One candidate economic-entity representation declared by the versioned
    point-in-time candidate universe (Option 2C). Candidate membership and declared
    comparability coordinates are input evidence, never peer eligibility."""

    identity: EconomicClaimIdentity
    source_record_id: str
    source_record_version: str
    lifecycle_state: str
    sector_classification: str
    value_capture_mechanism_type: str
    revenue_accounting_meaning: str
    native_evidence_type: str
    quote_currency: str
    supply_basis: str

    def __post_init__(self) -> None:
        _required_text(
            self,
            "source_record_id",
            "source_record_version",
            "lifecycle_state",
            "sector_classification",
            "value_capture_mechanism_type",
            "revenue_accounting_meaning",
            "native_evidence_type",
            "quote_currency",
            "supply_basis",
        )
        if self.lifecycle_state == "unavailable" or self.sector_classification == "unavailable":
            raise ValueError("a candidate may not declare unavailable comparability coordinates")
        if self.supply_basis != "fully_diluted_supply":
            raise ValueError("the first methodology requires the fully_diluted_supply basis")


@dataclass(frozen=True)
class DimensionDecision:
    """One dimension-level eligibility decision preserved on a
    PeerEligibilityDecisionRecord (ADR 0026 Peer Eligibility)."""

    coordinate: str
    passed: bool | None
    reason: str
    evidence_reference: str = ""

    def __post_init__(self) -> None:
        _required_text(self, "coordinate", "reason")


@dataclass(frozen=True)
class PeerUniversePolicyRecord:
    """Reference policy record that owns the complete predeclared peer-policy
    (ADR 0026 Peer Universe Authority). Never a candidate or assessment result."""

    record_id: str
    logical_id: str
    schema_version: str
    semantic_version: str
    effective_at: datetime
    recorded_at: datetime
    known_at: datetime
    content_hash: str
    quality_state: QualityState
    conflict_state: ConflictState
    policy_id: str
    supported_entity_class: str
    entity_class_criteria_id: str
    entity_class_criteria_version: str
    taxonomy: str
    taxonomy_version: str
    required_lifecycle_state: str
    required_sector_classification: str
    required_value_capture_mechanism_types: tuple[str, ...]
    required_revenue_accounting_meaning: str
    required_native_evidence_types: tuple[str, ...]
    required_quote_currency: str
    required_supply_basis: str
    accounting_horizon_days: int
    observation_window_days: int
    freshness_limit_days: int
    minimum_eligible_peers: int
    equal_peer_weighting: bool
    reference_statistic: str
    minimum_decision_coverage_percent: int
    minimum_observation_coverage_percent: int
    deterministic_ordering: str
    tie_breaking: str
    outlier_treatment: str
    residual_sign_convention: str
    normalization_policy_id: None
    calibration_policy_id: None
    correlation_group: str
    maximum_candidate_universe_size: int
    confidence_policy_id: str
    confidence_policy_version: str
    candidate_source_authority: str
    authorizing_adr_reference: str
    authorized_by: str
    supersedes_record_id: str | None = None
    correction_reason: str = ""

    def __post_init__(self) -> None:
        _required_text(
            self,
            "record_id",
            "logical_id",
            "schema_version",
            "semantic_version",
            "content_hash",
            "policy_id",
            "supported_entity_class",
            "entity_class_criteria_id",
            "entity_class_criteria_version",
            "taxonomy",
            "taxonomy_version",
            "required_lifecycle_state",
            "required_sector_classification",
            "required_revenue_accounting_meaning",
            "required_quote_currency",
            "required_supply_basis",
            "deterministic_ordering",
            "tie_breaking",
            "outlier_treatment",
            "residual_sign_convention",
            "correlation_group",
            "confidence_policy_id",
            "confidence_policy_version",
            "candidate_source_authority",
            "authorizing_adr_reference",
            "authorized_by",
        )
        _member("quality_state", self.quality_state, QUALITY_STATES)
        _member("conflict_state", self.conflict_state, CONFLICT_STATES)
        if not self.required_value_capture_mechanism_types:
            raise ValueError("required_value_capture_mechanism_types must not be empty")
        if not self.required_native_evidence_types:
            raise ValueError("required_native_evidence_types must not be empty")
        if self.required_supply_basis != "fully_diluted_supply":
            raise ValueError("the first methodology requires the fully_diluted_supply basis")
        if self.accounting_horizon_days != 365:
            raise ValueError("accounting_horizon_days must be 365 per ADR 0021/ADR 0022/ADR 0026")
        if self.minimum_eligible_peers < MINIMUM_ELIGIBLE_PEERS:
            raise ValueError(f"minimum_eligible_peers must be at least {MINIMUM_ELIGIBLE_PEERS}")
        if self.minimum_decision_coverage_percent != 100 or self.minimum_observation_coverage_percent != 100:
            raise ValueError("the first methodology requires 100 percent decision and observation coverage")
        if self.equal_peer_weighting is not True:
            raise ValueError("equal_peer_weighting must remain True per ADR 0026")
        if self.reference_statistic != REFERENCE_STATISTIC:
            raise ValueError(f"reference_statistic must be {REFERENCE_STATISTIC!r} per ADR 0026")
        if self.outlier_treatment != "none":
            raise ValueError("the first methodology retains every eligible observation without trimming")
        if self.residual_sign_convention != "positive-cheaper":
            raise ValueError("residual_sign_convention must be 'positive-cheaper' per ADR 0026")
        if self.normalization_policy_id is not None or self.calibration_policy_id is not None:
            raise ValueError(
                "normalization_policy_id and calibration_policy_id must remain unavailable (None) until a "
                "predeclared, versioned, historically calibrated monotonic transform is accepted"
            )
        if self.correlation_group != REQUIRED_CORRELATION_GROUP:
            raise ValueError(f"correlation_group must be {REQUIRED_CORRELATION_GROUP!r} per ADR 0021")
        if self.maximum_candidate_universe_size < 1:
            raise ValueError("maximum_candidate_universe_size must be positive")
        if self.candidate_source_authority != CANDIDATE_SOURCE_AUTHORITY:
            raise ValueError(
                f"candidate_source_authority must be {CANDIDATE_SOURCE_AUTHORITY!r} per ADPR-0003 Option 2C"
            )
        if self.authorizing_adr_reference != AUTHORIZING_ADR_REFERENCE:
            raise ValueError(f"authorizing_adr_reference must be {AUTHORIZING_ADR_REFERENCE!r} for this foundation")
        _normalize_chronology(self)
        _validate_correction(self)


@dataclass(frozen=True)
class PeerUniverseSnapshot:
    """Immutable point-in-time candidate universe snapshot (ADR 0026 Peer Universe
    Authority; ADPR-0003 Option 2C). Binds the target, the policy version, the ordered
    candidate set with source provenance, and the deterministic construction
    fingerprint."""

    record_id: str
    logical_id: str
    schema_version: str
    semantic_version: str
    identity: EconomicClaimIdentity
    policy_record_id: str
    policy_record_version: str
    cutoff: datetime
    candidates: tuple[PeerCandidate, ...]
    deterministic_construction_fingerprint: str
    effective_at: datetime
    recorded_at: datetime
    known_at: datetime
    content_hash: str
    quality_state: QualityState
    conflict_state: ConflictState
    supersedes_record_id: str | None = None
    correction_reason: str = ""

    def __post_init__(self) -> None:
        _required_text(
            self,
            "record_id",
            "logical_id",
            "schema_version",
            "semantic_version",
            "policy_record_id",
            "policy_record_version",
            "deterministic_construction_fingerprint",
            "content_hash",
        )
        _member("quality_state", self.quality_state, QUALITY_STATES)
        _member("conflict_state", self.conflict_state, CONFLICT_STATES)
        cutoff = _utc("cutoff", self.cutoff)
        if self.effective_at != cutoff:
            raise ValueError("effective_at must equal the declared point-in-time cutoff")
        if len(self.candidates) > 1:
            entity_ids = [candidate.identity.entity_id for candidate in self.candidates]
            if len(set(entity_ids)) != len(entity_ids):
                raise ValueError("one economic entity may appear at most once in a peer cohort")
        if len({candidate.identity.entity_id for candidate in self.candidates}) != len(self.candidates):
            raise ValueError("candidate identities must be distinct")
        if any(candidate.identity.entity_id == self.identity.entity_id for candidate in self.candidates):
            raise ValueError("a target cannot be its own peer")
        if self.cutoff > self.recorded_at or self.cutoff > self.known_at:
            raise ValueError("cutoff must not follow recorded_at or known_at")
        _normalize_chronology(self)
        _validate_correction(self)


@dataclass(frozen=True)
class PeerEligibilityDecisionRecord:
    """One immutable service-owned eligibility decision for one target/candidate pair
    (ADR 0026 Peer Eligibility). Preserves every dimension-level decision, reason,
    exact evidence reference, confidence component, and conflict/missingness state."""

    record_id: str
    logical_id: str
    schema_version: str
    semantic_version: str
    target_identity: EconomicClaimIdentity
    candidate_identity: EconomicClaimIdentity
    policy_record_id: str
    policy_record_version: str
    universe_snapshot_id: str
    decision: EligibilityDecision
    dimension_decisions: tuple[DimensionDecision, ...]
    reason: str
    rule_record_id: str
    rule_record_version: str
    supply_record_id: str
    supply_record_version: str
    evidence_record_id: str
    evidence_record_version: str
    market_fact_record_id: str
    market_fact_record_version: str
    confidence_component: str
    missingness_state: str
    effective_at: datetime
    recorded_at: datetime
    known_at: datetime
    content_hash: str
    quality_state: QualityState
    conflict_state: ConflictState
    supersedes_record_id: str | None = None
    correction_reason: str = ""

    def __post_init__(self) -> None:
        _required_text(
            self,
            "record_id",
            "logical_id",
            "schema_version",
            "semantic_version",
            "policy_record_id",
            "policy_record_version",
            "universe_snapshot_id",
            "reason",
            "missingness_state",
            "content_hash",
        )
        _member("decision", self.decision, ELIGIBILITY_DECISIONS)
        _member("quality_state", self.quality_state, QUALITY_STATES)
        _member("conflict_state", self.conflict_state, CONFLICT_STATES)
        if not self.dimension_decisions:
            raise ValueError("dimension_decisions must not be empty")
        if self.confidence_component and not _bounded_decimal_ok(self.confidence_component):
            raise ValueError("confidence_component must be between 0 and 1")
        if self.candidate_identity.entity_id == self.target_identity.entity_id:
            raise ValueError("a target cannot be its own peer")
        _normalize_chronology(self)
        _validate_correction(self)


@dataclass(frozen=True)
class ComparativeMetricObservationRecord:
    """One immutable metric observation binding one target or peer to the exact
    numerator and denominator records, the multiple, and its availability/conflict
    state (ADR 0026 Persistence model)."""

    record_id: str
    logical_id: str
    schema_version: str
    semantic_version: str
    identity: EconomicClaimIdentity
    policy_record_id: str
    policy_record_version: str
    universe_snapshot_id: str
    numerator_record_id: str
    numerator_record_version: str
    numerator_value: str
    numerator_currency: str
    denominator_record_id: str
    denominator_record_version: str
    denominator_value: str
    denominator_currency: str
    rule_record_id: str
    rule_record_version: str
    value_capture_pathway_id: str
    comparative_multiple: str
    currency: str
    horizon_days: int
    supply_basis: str
    availability_state: AvailabilityState
    normalization_status: NormalizationStatus
    conflict_state: ConflictState
    calculation_fingerprint: str
    effective_at: datetime
    recorded_at: datetime
    known_at: datetime
    content_hash: str
    quality_state: QualityState
    supersedes_record_id: str | None = None
    correction_reason: str = ""

    def __post_init__(self) -> None:
        _required_text(
            self,
            "record_id",
            "logical_id",
            "schema_version",
            "semantic_version",
            "policy_record_id",
            "policy_record_version",
            "universe_snapshot_id",
            "numerator_record_id",
            "numerator_record_version",
            "numerator_value",
            "numerator_currency",
            "denominator_record_id",
            "denominator_record_version",
            "denominator_value",
            "denominator_currency",
            "rule_record_id",
            "rule_record_version",
            "value_capture_pathway_id",
            "comparative_multiple",
            "currency",
            "supply_basis",
            "calculation_fingerprint",
            "content_hash",
        )
        _member("availability_state", self.availability_state, AVAILABILITY_STATES)
        _member("conflict_state", self.conflict_state, CONFLICT_STATES)
        _member("quality_state", self.quality_state, QUALITY_STATES)
        if self.horizon_days != 365:
            raise ValueError("horizon_days must be 365 per ADR 0021/ADR 0022/ADR 0026")
        if self.supply_basis != "fully_diluted_supply":
            raise ValueError("the first methodology requires the fully_diluted_supply basis")
        if not _positive_decimal_ok(self.numerator_value):
            raise ValueError("numerator_value must be a strictly positive decimal")
        if not _positive_decimal_ok(self.denominator_value):
            raise ValueError("denominator_value must be a strictly positive decimal")
        if not _positive_decimal_ok(self.comparative_multiple):
            raise ValueError("comparative_multiple must be a strictly positive decimal")
        if self.normalization_status != NORMALIZATION_UNAVAILABLE:
            raise ValueError(
                "normalization_status must remain 'unavailable' until a predeclared, versioned, "
                "historically calibrated monotonic transform exists (ADR 0026)"
            )
        _normalize_chronology(self)
        _validate_correction(self)


@dataclass(frozen=True)
class ComparativeValuationAssessmentRecord:
    """The only canonical output of this authority (ADR 0026 Downstream Boundaries).
    Binds the target, policy, universe snapshot, included decision IDs, target and peer
    observation IDs, the complete ordered peer distribution, the median, the raw log
    residual, normalization state, cohort coverage, confidence decomposition,
    availability state, correlation group, provenance, and correction lineage."""

    record_id: str
    logical_id: str
    schema_version: str
    semantic_version: str
    identity: EconomicClaimIdentity
    policy_record_id: str
    policy_record_version: str
    universe_snapshot_id: str
    included_decision_record_ids: tuple[str, ...]
    target_observation_record_id: str
    peer_observation_record_ids: tuple[str, ...]
    peer_multiple_distribution: tuple[str, ...]
    peer_median_multiple: str | None
    target_multiple: str | None
    raw_log_residual: str | None
    residual_sign_convention: str
    normalization_policy_id: None
    normalization_status: NormalizationStatus
    normalized_value: None
    cohort_decision_coverage: str
    cohort_observation_coverage: str
    confidence_universe_coverage: str
    confidence_eligibility_evidence: str
    confidence_metric_evidence: str
    confidence_coordinate_compatibility: str
    confidence_peer_set_cardinality: str
    confidence_methodology_calibration: str
    confidence: str
    availability_state: AvailabilityState
    correlation_group: str
    effective_at: datetime
    recorded_at: datetime
    known_at: datetime
    content_hash: str
    quality_state: QualityState
    conflict_state: ConflictState
    supersedes_record_id: str | None = None
    correction_reason: str = ""

    def __post_init__(self) -> None:
        _required_text(
            self,
            "record_id",
            "logical_id",
            "schema_version",
            "semantic_version",
            "policy_record_id",
            "policy_record_version",
            "universe_snapshot_id",
            "residual_sign_convention",
            "cohort_decision_coverage",
            "cohort_observation_coverage",
            "content_hash",
        )
        _member("availability_state", self.availability_state, AVAILABILITY_STATES)
        _member("quality_state", self.quality_state, QUALITY_STATES)
        _member("conflict_state", self.conflict_state, CONFLICT_STATES)
        if self.normalization_status != NORMALIZATION_UNAVAILABLE:
            raise ValueError(
                "normalization_status must remain 'unavailable' until a predeclared, versioned, "
                "historically calibrated monotonic transform exists (ADR 0026)"
            )
        if self.normalized_value is not None:
            raise ValueError(
                "normalized_value must remain None (never zero/neutral) while normalization_status "
                "is 'unavailable', per ADR 0026 Missingness"
            )
        if self.normalization_policy_id is not None or self.normalized_value is not None:
            raise ValueError("no calibrated normalization exists in this foundation")
        if self.correlation_group != REQUIRED_CORRELATION_GROUP:
            raise ValueError(f"correlation_group must be {REQUIRED_CORRELATION_GROUP!r} per ADR 0021")
        _confidence_fields = (
            "confidence_universe_coverage",
            "confidence_eligibility_evidence",
            "confidence_metric_evidence",
            "confidence_coordinate_compatibility",
            "confidence_peer_set_cardinality",
            "confidence_methodology_calibration",
            "confidence",
        )
        has_raw_values = self.availability_state in ("AVAILABLE", "UNAVAILABLE_UNCALIBRATED_NORMALIZATION")
        if has_raw_values:
            # Confidence is only meaningful when the assessment persists raw values; it is
            # then required, bounded, and capped by its weakest mandatory component.
            for name in _confidence_fields:
                _bounded_decimal(name, getattr(self, name))
            components = [Decimal(getattr(self, name)) for name in _confidence_fields[:-1]]
            if Decimal(self.confidence) > min(components):
                raise ValueError("overall confidence cannot exceed the weakest mandatory component")
        else:
            # Explicit missingness (ADR 0020 "Confidence is unavailable, not zero"): an
            # unavailable assessment must never encode unavailable as a zero confidence.
            for name in _confidence_fields:
                if getattr(self, name).strip():
                    raise ValueError(
                        f"{name} must be blank for an unavailable assessment "
                        "(explicit missingness; never encode unavailable as zero confidence)"
                    )
        if self.availability_state in ("AVAILABLE", "UNAVAILABLE_UNCALIBRATED_NORMALIZATION"):
            if (
                not self.target_observation_record_id.strip()
                or self.peer_median_multiple is None
                or self.target_multiple is None
                or self.raw_log_residual is None
                or not self.peer_multiple_distribution
            ):
                raise ValueError("an assessment with raw values must persist the full raw distribution")
            for value in (self.peer_median_multiple, self.target_multiple):
                if not _positive_decimal_ok(value):
                    raise ValueError("peer_median_multiple and target_multiple must be strictly positive")
        if self.availability_state == "AVAILABLE":
            raise ValueError(
                "AVAILABLE requires a historically calibrated normalization accepted through "
                "independent review; this foundation never emits AVAILABLE"
            )
        if len(self.included_decision_record_ids) != len(self.peer_observation_record_ids):
            raise ValueError("included decision IDs and peer observation IDs must correspond one-to-one")
        _normalize_chronology(self)
        _validate_correction(self)


def _required_text(instance: object, *fields: str) -> None:
    for field in fields:
        value = getattr(instance, field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} must not be blank")


def _member(name: str, value: str, allowed: frozenset[str]) -> None:
    if value not in allowed:
        raise ValueError(f"unsupported {name}: {value}")


def _normalize_chronology(instance: object) -> None:
    effective = _utc("effective_at", instance.effective_at)
    recorded = _utc("recorded_at", instance.recorded_at)
    known = _utc("known_at", instance.known_at)
    if effective > recorded:
        raise ValueError("effective_at must be <= recorded_at")
    if recorded > known:
        raise ValueError("recorded_at must be <= known_at")
    object.__setattr__(instance, "effective_at", effective)
    object.__setattr__(instance, "recorded_at", recorded)
    object.__setattr__(instance, "known_at", known)


def _validate_correction(instance: object) -> None:
    predecessor = instance.supersedes_record_id
    reason = instance.correction_reason
    if bool(predecessor) != bool(reason.strip()):
        raise ValueError("supersedes_record_id and correction_reason must be provided together")


def _utc(name: str, value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


def _decimal(value: str) -> Decimal:
    try:
        return Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"invalid decimal value: {value}") from exc


def _bounded_decimal(name: str, value: str) -> None:
    number = _decimal(value)
    if not number.is_finite() or number < 0 or number > 1:
        raise ValueError(f"{name} must be between 0 and 1")


def _bounded_decimal_ok(value: str) -> bool:
    try:
        _bounded_decimal("confidence_component", value)
    except ValueError:
        return False
    return True


def _positive_decimal_ok(value: str) -> bool:
    try:
        number = _decimal(value)
    except ValueError:
        return False
    return number.is_finite() and number > 0
