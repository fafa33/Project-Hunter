from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Literal

from hunter.value_capture.models import EconomicClaimIdentity

VALUATION_SCHEMA_VERSION = "canonical-valuation-v1"

QualityState = Literal["accepted", "unavailable"]
ConflictState = Literal["none", "open", "contested", "resolved"]
NormalizationStatus = Literal["unavailable"]

QUALITY_STATES = frozenset(QualityState.__args__)  # type: ignore[attr-defined]
CONFLICT_STATES = frozenset(ConflictState.__args__)  # type: ignore[attr-defined]

# ADR 0022 Scope condition 2 allow-list. A rule outside this set is out of scope for
# canonical valuation regardless of what strict-known lookup key a caller supplies.
PERMITTED_VALUE_CAPTURE_RULE_TYPES = frozenset(
    {
        "fee_distribution",
        "revenue_distribution",
        "buyback_and_burn",
        "staking_distribution",
        "redemption_entitlement",
        "collateral_entitlement",
    }
)

# ADR 0021's correlation group requirement, reaffirmed unmodified by ADR 0022 and by
# hunter.valuation_methodology.models.REQUIRED_CORRELATION_GROUP. Fixed, never
# caller-overridable, exposed on every ValuationAssessmentRecord.
REQUIRED_CORRELATION_GROUP = "valuation-mispricing"

# ADR 0022 Calibration requirements: "valuation remains unavailable in Market Validation
# regardless of whether CanonicalValuationService itself is implemented" until a
# calibration set exists and passes leakage testing (Milestone 4/5). The only legal value
# for Milestone 3 is "unavailable" -- never a computed [0,1] normalized score.
NORMALIZATION_UNAVAILABLE: NormalizationStatus = "unavailable"


@dataclass(frozen=True)
class FairValueEstimateRecord:
    record_id: str
    logical_id: str
    schema_version: str
    semantic_version: str
    identity: EconomicClaimIdentity
    effective_at: datetime
    recorded_at: datetime
    known_at: datetime
    quality_state: QualityState
    conflict_state: ConflictState
    currency: str
    horizon_days: int
    supply_basis_selection_rule: str
    discount_rate_policy_id: str
    discount_rate_policy_version: str
    discount_rate_applied: str
    fair_value_per_diluted_unit_p10: str
    fair_value_per_diluted_unit_p50: str
    fair_value_per_diluted_unit_p90: str
    total_diluted_value_p10: str
    total_diluted_value_p50: str
    total_diluted_value_p90: str
    model_dispersion_ratio: str
    confidence: str
    confidence_base: str
    confidence_dispersion_factor: str
    confidence_freshness_factor: str
    methodology_record_id: str
    methodology_record_version: str
    evidence_record_id: str
    evidence_record_version: str
    rule_record_id: str
    rule_record_version: str
    supply_record_id: str
    supply_record_version: str
    market_fact_record_ids: tuple[str, ...]
    market_fact_record_versions: tuple[str, ...]
    content_hash: str
    supersedes_record_id: str | None = None
    correction_reason: str = ""

    def __post_init__(self) -> None:
        _required_text(
            self,
            "record_id",
            "logical_id",
            "schema_version",
            "semantic_version",
            "currency",
            "supply_basis_selection_rule",
            "discount_rate_policy_id",
            "discount_rate_policy_version",
            "discount_rate_applied",
            "fair_value_per_diluted_unit_p10",
            "fair_value_per_diluted_unit_p50",
            "fair_value_per_diluted_unit_p90",
            "total_diluted_value_p10",
            "total_diluted_value_p50",
            "total_diluted_value_p90",
            "model_dispersion_ratio",
            "confidence",
            "confidence_base",
            "confidence_dispersion_factor",
            "confidence_freshness_factor",
            "methodology_record_id",
            "methodology_record_version",
            "evidence_record_id",
            "evidence_record_version",
            "rule_record_id",
            "rule_record_version",
            "supply_record_id",
            "supply_record_version",
            "content_hash",
        )
        _member("quality_state", self.quality_state, QUALITY_STATES)
        _member("conflict_state", self.conflict_state, CONFLICT_STATES)
        if self.horizon_days != 365:
            raise ValueError("horizon_days must be 365 per ADR 0021/ADR 0022")
        _bounded_decimal("discount_rate_applied", self.discount_rate_applied)
        _bounded_decimal("confidence", self.confidence)
        _bounded_decimal("confidence_base", self.confidence_base)
        _bounded_decimal("confidence_dispersion_factor", self.confidence_dispersion_factor)
        _bounded_decimal("confidence_freshness_factor", self.confidence_freshness_factor)
        # Confidence can never exceed the minimum confidence of any single required input
        # (ADR 0022 Confidence rules): confidence_base already is that minimum, and the two
        # factors are themselves bounded [0,1] multipliers, so this is enforced by
        # construction in the service -- this is a defense-in-depth restatement.
        if _decimal(self.confidence) > _decimal(self.confidence_base):
            raise ValueError("confidence must not exceed confidence_base")
        p10 = _non_negative_decimal("fair_value_per_diluted_unit_p10", self.fair_value_per_diluted_unit_p10)
        p50 = _non_negative_decimal("fair_value_per_diluted_unit_p50", self.fair_value_per_diluted_unit_p50)
        p90 = _non_negative_decimal("fair_value_per_diluted_unit_p90", self.fair_value_per_diluted_unit_p90)
        if not (p10 <= p50 <= p90):
            raise ValueError("fair_value_per_diluted_unit p10 <= p50 <= p90 must hold")
        t10 = _non_negative_decimal("total_diluted_value_p10", self.total_diluted_value_p10)
        t50 = _non_negative_decimal("total_diluted_value_p50", self.total_diluted_value_p50)
        t90 = _non_negative_decimal("total_diluted_value_p90", self.total_diluted_value_p90)
        if not (t10 <= t50 <= t90):
            raise ValueError("total_diluted_value p10 <= p50 <= p90 must hold")
        if len(self.market_fact_record_ids) != len(self.market_fact_record_versions):
            raise ValueError("market_fact_record_ids and versions must have equal length")
        if any(not isinstance(v, str) or not v.strip() for v in self.market_fact_record_ids):
            raise ValueError("market_fact_record_ids must contain nonblank strings")
        if any(not isinstance(v, str) or not v.strip() for v in self.market_fact_record_versions):
            raise ValueError("market_fact_record_versions must contain nonblank strings")
        object.__setattr__(self, "market_fact_record_ids", tuple(self.market_fact_record_ids))
        object.__setattr__(self, "market_fact_record_versions", tuple(self.market_fact_record_versions))
        _normalize_chronology(self)
        _validate_correction(self)


@dataclass(frozen=True)
class ValuationAssessmentRecord:
    record_id: str
    logical_id: str
    schema_version: str
    semantic_version: str
    identity: EconomicClaimIdentity
    effective_at: datetime
    recorded_at: datetime
    known_at: datetime
    quality_state: QualityState
    conflict_state: ConflictState
    fair_value_estimate_record_id: str
    fair_value_estimate_record_version: str
    confidence: str
    normalization_status: NormalizationStatus
    normalized_value: None
    correlation_group: str
    content_hash: str
    supersedes_record_id: str | None = None
    correction_reason: str = ""

    def __post_init__(self) -> None:
        _required_text(
            self,
            "record_id",
            "logical_id",
            "schema_version",
            "semantic_version",
            "fair_value_estimate_record_id",
            "fair_value_estimate_record_version",
            "confidence",
            "content_hash",
        )
        _member("quality_state", self.quality_state, QUALITY_STATES)
        _member("conflict_state", self.conflict_state, CONFLICT_STATES)
        _bounded_decimal("confidence", self.confidence)
        if self.normalization_status != NORMALIZATION_UNAVAILABLE:
            raise ValueError(
                "normalization_status must remain 'unavailable' until Milestone 4 is independently "
                "audited, per ADR 0022's Persistence requirements and Calibration requirements sections"
            )
        if self.normalized_value is not None:
            raise ValueError(
                "normalized_value must remain None (never zero/neutral) while normalization_status "
                "is 'unavailable', per ADR 0021's explicit missingness policy"
            )
        if self.correlation_group != REQUIRED_CORRELATION_GROUP:
            raise ValueError(f"correlation_group must be {REQUIRED_CORRELATION_GROUP!r} per ADR 0021")
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


def _non_negative_decimal(name: str, value: str) -> Decimal:
    number = _decimal(value)
    if not number.is_finite() or number < 0:
        raise ValueError(f"{name} must be non-negative")
    return number
