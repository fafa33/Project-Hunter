from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Literal

from hunter.value_capture.models import EconomicClaimIdentity

MISPRICING_SCHEMA_VERSION = "mispricing-v1"

AvailabilityState = Literal[
    "AVAILABLE",
    "UNAVAILABLE_UNCALIBRATED_NORMALIZATION",
    "UNAVAILABLE_NO_FAIR_VALUE",
    "UNAVAILABLE_NO_MARKET_OBSERVATION",
    "UNAVAILABLE_IDENTITY_MISMATCH",
    "UNAVAILABLE_UNIT_MISMATCH",
    "UNAVAILABLE_SUPPLY_BASIS_MISMATCH",
    "UNAVAILABLE_ZERO_OR_NEGATIVE_PRICE",
    "UNAVAILABLE_INCOMPATIBLE_TIMES",
    "UNAVAILABLE_STALE_INPUTS",
    "UNAVAILABLE_CONFLICTED_INPUTS",
    "UNAVAILABLE_UNSUPPORTED_METHODOLOGY",
]
QualityState = Literal["accepted", "unavailable"]
ConflictState = Literal["none", "open", "contested", "resolved"]
NormalizationStatus = Literal["unavailable"]

AVAILABILITY_STATES = frozenset(AvailabilityState.__args__)  # type: ignore[attr-defined]
QUALITY_STATES = frozenset(QualityState.__args__)  # type: ignore[attr-defined]
CONFLICT_STATES = frozenset(ConflictState.__args__)  # type: ignore[attr-defined]

# ADR 0021 mispricing contract: the signed divergence between one authorized canonical
# fair-value estimate and a separately observed market price for the identical asset
# claim, representation, quote currency, and supply basis. The raw unit is the decimal
# return-like ratio (fair_value_p50 - observed_market_price) / observed_market_price;
# positive means estimated undervaluation, zero means equality, negative means estimated
# overvaluation. Raw range is [-1, +inf) when both inputs are positive. Fixed, never
# caller-parameterized.
RAW_MISPRICING_FORMULA = "(fair_value_p50 - observed_market_price) / observed_market_price"
RAW_SIGN_CONVENTION = "positive-undervaluation"
NEUTRAL_POINT = "0"
RAW_RANGE = "[-1, +inf)"

# ADR 0021 mispricing contract: "market observation tolerance is fixed by methodology."
# The first production methodology fixes a 7-day observation tolerance between the
# fair-value effective time and the observed market price effective time.
TIMESTAMP_TOLERANCE_DAYS = 7

# ADR 0021 mispricing contract: the observed market price is a separately observed
# spot price for the identical representation, quote currency, and supply basis.
MARKET_OBSERVATION_FACT_TYPE = "spot_price"

# ADR 0021 mispricing contract: identical supply basis to the referenced fair-value
# estimate, which the first valuation methodology fixes as fully diluted supply.
REQUIRED_SUPPLY_BASIS = "fully_diluted_supply"

# ADR 0021 Anti-double-counting and correlation policy: valuation and mispricing are one
# valuation/fair-value correlation group because mispricing depends on valuation. Fixed,
# never caller-parameterized.
REQUIRED_CORRELATION_GROUP = "valuation-mispricing"

# ADR 0021 Persistence requirements: a [0,1] normalized value requires a predeclared,
# versioned, historically calibrated monotonic transform that preserves sign around a
# declared neutral point. No calibration exists in this foundation, so the only legal
# value is "unavailable".
NORMALIZATION_UNAVAILABLE: NormalizationStatus = "unavailable"

# The only ADR currently authorizing this record family. Fixed; extending authorization
# to another ADR is a future governance decision.
AUTHORIZING_ADR_REFERENCE = "ADR-0021"

# Identifies the sole canonical authority permitted to produce this record family.
CANONICAL_AUTHORITY_ID = "canonical-mispricing-authority-v1"

# Fixed, versioned confidence policy for this foundation's decomposed confidence
# computation (ADR 0021 confidence rules). Confidence cannot exceed either prerequisite
# and is further reduced by valuation dispersion, price-source conflicts,
# liquidity/market-quality limitations, timestamp distance, representation uncertainty,
# and stale evidence.
MISPRICING_CONFIDENCE_POLICY_ID = "mispricing-confidence-v1"
MISPRICING_CONFIDENCE_POLICY_VERSION = "1.0.0"

# The only formula version this foundation implements. A persisted methodology declaring
# any other formula version is unsupported and keeps the assessment explicitly
# unavailable (ADR 0020 "Unsupported methodology must remain explicit unavailable").
SUPPORTED_FORMULA_VERSION = "mispricing-raw-ratio-v1"


@dataclass(frozen=True)
class MispricingMethodologySnapshot:
    """Immutable reference methodology for the Canonical Mispricing authority
    (ADR 0021 mispricing contract; ADR 0020 input contract). Fixes the raw formula,
    sign convention, neutral point, raw range, observation tolerance, required
    prerequisites, correlation group, and normalization/calibration unavailability.
    Never a price, a fair value, or an assessment result."""

    record_id: str
    logical_id: str
    schema_version: str
    semantic_version: str
    methodology_id: str
    formula_version: str
    raw_formula: str
    sign_convention: str
    neutral_point: str
    raw_range: str
    timestamp_tolerance_days: int
    required_quote_currency: str
    required_supply_basis: str
    market_observation_fact_type: str
    supported_entity_class: str
    entity_class_criteria_id: str
    entity_class_criteria_version: str
    correlation_group: str
    confidence_policy_id: str
    confidence_policy_version: str
    normalization_policy_id: None
    calibration_policy_id: None
    authorizing_adr_reference: str
    authorized_by: str
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
            "methodology_id",
            "formula_version",
            "raw_formula",
            "sign_convention",
            "neutral_point",
            "raw_range",
            "required_quote_currency",
            "required_supply_basis",
            "market_observation_fact_type",
            "supported_entity_class",
            "entity_class_criteria_id",
            "entity_class_criteria_version",
            "correlation_group",
            "confidence_policy_id",
            "confidence_policy_version",
            "authorizing_adr_reference",
            "authorized_by",
            "content_hash",
        )
        _member("quality_state", self.quality_state, QUALITY_STATES)
        _member("conflict_state", self.conflict_state, CONFLICT_STATES)
        if self.raw_formula != RAW_MISPRICING_FORMULA:
            raise ValueError(f"raw_formula must be {RAW_MISPRICING_FORMULA!r} per ADR 0021")
        if self.sign_convention != RAW_SIGN_CONVENTION:
            raise ValueError(f"sign_convention must be {RAW_SIGN_CONVENTION!r} per ADR 0021")
        if self.neutral_point != NEUTRAL_POINT:
            raise ValueError(f"neutral_point must be {NEUTRAL_POINT!r} per ADR 0021")
        if self.raw_range != RAW_RANGE:
            raise ValueError(f"raw_range must be {RAW_RANGE!r} per ADR 0021")
        if self.timestamp_tolerance_days != TIMESTAMP_TOLERANCE_DAYS:
            raise ValueError(f"timestamp_tolerance_days must be {TIMESTAMP_TOLERANCE_DAYS} per ADR 0021")
        if self.required_supply_basis != REQUIRED_SUPPLY_BASIS:
            raise ValueError(f"required_supply_basis must be {REQUIRED_SUPPLY_BASIS!r} per ADR 0021")
        if self.market_observation_fact_type != MARKET_OBSERVATION_FACT_TYPE:
            raise ValueError(f"market_observation_fact_type must be {MARKET_OBSERVATION_FACT_TYPE!r} per ADR 0021")
        if self.correlation_group != REQUIRED_CORRELATION_GROUP:
            raise ValueError(f"correlation_group must be {REQUIRED_CORRELATION_GROUP!r} per ADR 0021")
        if self.normalization_policy_id is not None or self.calibration_policy_id is not None:
            raise ValueError(
                "normalization_policy_id and calibration_policy_id must remain unavailable (None) until a "
                "predeclared, versioned, historically calibrated monotonic transform is accepted"
            )
        if self.authorizing_adr_reference != AUTHORIZING_ADR_REFERENCE:
            raise ValueError(f"authorizing_adr_reference must be {AUTHORIZING_ADR_REFERENCE!r} for this foundation")
        _normalize_chronology(self)
        _validate_correction(self)


@dataclass(frozen=True)
class MispricingAssessmentRecord:
    """The only canonical output of this authority (ADR 0021 mispricing contract).
    Binds the exact-version fair-value estimate and observed market fact it consumed,
    the exact entity/representation match, the raw signed ratio, the attached
    fair-value uncertainty interval, the neutral point, normalization state,
    confidence decomposition, availability state, correlation group, provenance, and
    correction lineage. References rather than re-counts valuation evidence
    (ADR 0021 anti-double-counting)."""

    record_id: str
    logical_id: str
    schema_version: str
    semantic_version: str
    identity: EconomicClaimIdentity
    methodology_record_id: str
    methodology_record_version: str
    fair_value_record_id: str
    fair_value_record_version: str
    market_fact_record_id: str
    market_fact_record_version: str
    fair_value_p10: str
    fair_value_p50: str
    fair_value_p90: str
    observed_market_price: str
    raw_signed_ratio: str | None
    uncertainty_interval_lower: str | None
    uncertainty_interval_upper: str | None
    neutral_point: str
    sign_convention: str
    normalization_status: NormalizationStatus
    normalized_value: None
    confidence_fair_value: str
    confidence_market_quality: str
    confidence_dispersion: str
    confidence_timestamp: str
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
            "neutral_point",
            "sign_convention",
            "content_hash",
        )
        _member("availability_state", self.availability_state, AVAILABILITY_STATES)
        _member("quality_state", self.quality_state, QUALITY_STATES)
        _member("conflict_state", self.conflict_state, CONFLICT_STATES)
        if self.normalization_status != NORMALIZATION_UNAVAILABLE:
            raise ValueError(
                "normalization_status must remain 'unavailable' until a predeclared, versioned, "
                "historically calibrated monotonic transform exists (ADR 0021)"
            )
        if self.normalized_value is not None:
            raise ValueError(
                "normalized_value must remain None (never zero/neutral) while normalization_status "
                "is 'unavailable', per ADR 0021"
            )
        if self.neutral_point != NEUTRAL_POINT:
            raise ValueError(f"neutral_point must be {NEUTRAL_POINT!r} per ADR 0021")
        if self.sign_convention != RAW_SIGN_CONVENTION:
            raise ValueError(f"sign_convention must be {RAW_SIGN_CONVENTION!r} per ADR 0021")
        if self.correlation_group != REQUIRED_CORRELATION_GROUP:
            raise ValueError(f"correlation_group must be {REQUIRED_CORRELATION_GROUP!r} per ADR 0021")
        _confidence_fields = (
            "confidence_fair_value",
            "confidence_market_quality",
            "confidence_dispersion",
            "confidence_timestamp",
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
            # A raw assessment binds the exact-version methodology, fair-value estimate,
            # and observed market fact it consumed (ADR 0021 record contract).
            _required_text(
                self,
                "methodology_record_id",
                "methodology_record_version",
                "fair_value_record_id",
                "fair_value_record_version",
                "market_fact_record_id",
                "market_fact_record_version",
            )
            if not self.observed_market_price.strip() or self.raw_signed_ratio is None:
                raise ValueError("an assessment with raw values must persist the observed market price and ratio")
            price = _decimal(self.observed_market_price)
            if not price.is_finite() or price <= 0:
                raise ValueError("observed_market_price must be strictly positive per ADR 0021")
            p10 = _decimal(self.fair_value_p10)
            p50 = _decimal(self.fair_value_p50)
            p90 = _decimal(self.fair_value_p90)
            for label, value in (("fair_value_p10", p10), ("fair_value_p50", p50), ("fair_value_p90", p90)):
                if not value.is_finite() or value < 0:
                    raise ValueError(f"{label} must be a finite, non-negative decimal")
            ratio = _decimal(self.raw_signed_ratio)
            if not ratio.is_finite() or ratio < -1:
                raise ValueError("raw_signed_ratio must be finite and within the declared raw range [-1, +inf)")
            # Deterministic integrity: the raw ratio and the attached uncertainty interval
            # are recomputed exactly from the persisted references (no averaging, no
            # fabricated evidence). The raw ratio uses the p50 reference only.
            expected_ratio = (p50 - price) / price
            if ratio != expected_ratio:
                raise ValueError("raw_signed_ratio does not equal (fair_value_p50 - price) / price")
            if self.uncertainty_interval_lower is None or self.uncertainty_interval_upper is None:
                raise ValueError("an assessment with raw values must persist the uncertainty interval")
            expected_lower = (p10 - price) / price
            expected_upper = (p90 - price) / price
            if _decimal(self.uncertainty_interval_lower) != expected_lower:
                raise ValueError("uncertainty_interval_lower does not equal (fair_value_p10 - price) / price")
            if _decimal(self.uncertainty_interval_upper) != expected_upper:
                raise ValueError("uncertainty_interval_upper does not equal (fair_value_p90 - price) / price")
        else:
            for name in ("fair_value_p10", "fair_value_p50", "fair_value_p90", "observed_market_price"):
                if getattr(self, name).strip():
                    raise ValueError(f"{name} must be blank for an unavailable assessment")
            if (
                self.raw_signed_ratio is not None
                or self.uncertainty_interval_lower is not None
                or self.uncertainty_interval_upper is not None
            ):
                raise ValueError("raw values must be unavailable for an unavailable assessment")
        if self.availability_state == "AVAILABLE":
            raise ValueError(
                "AVAILABLE requires a historically calibrated normalization accepted through "
                "independent review; this foundation never emits AVAILABLE"
            )
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
