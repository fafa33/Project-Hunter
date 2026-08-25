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

ASYMMETRY_SCHEMA_VERSION = "canonical-asymmetry-v1"

AvailabilityState = Literal[
    "AVAILABLE",
    "UNAVAILABLE_UNCALIBRATED_NORMALIZATION",
    "UNAVAILABLE_NO_MARKET_OBSERVATION",
    "UNAVAILABLE_NO_SCENARIO_SET",
    "UNAVAILABLE_NO_PROBABILITIES",
    "UNAVAILABLE_NO_PAYOFFS",
    "UNAVAILABLE_INCOMPLETE_SCENARIO_COVERAGE",
    "UNAVAILABLE_MISSING_MATERIAL_DOWNSIDE",
    "UNAVAILABLE_UNDEFINED_DENOMINATOR",
    "UNAVAILABLE_UNSUPPORTED_PROBABILITIES",
    "UNAVAILABLE_UNSUPPORTED_METHODOLOGY",
    "UNAVAILABLE_IDENTITY_MISMATCH",
    "UNAVAILABLE_UNIT_MISMATCH",
    "UNAVAILABLE_CONFLICTED_INPUTS",
    "UNAVAILABLE_INCOMPATIBLE_TIMES",
    "UNAVAILABLE_STALE_INPUTS",
]
QualityState = Literal["accepted", "unavailable"]
ConflictState = Literal["none", "open", "contested", "resolved"]
NormalizationStatus = Literal["unavailable"]
ScenarioType = Literal["upside", "downside", "downside_tail"]
PayoffClass = Literal["positive", "negative", "neutral"]

AVAILABILITY_STATES = frozenset(AvailabilityState.__args__)  # type: ignore[attr-defined]
QUALITY_STATES = frozenset(QualityState.__args__)  # type: ignore[attr-defined]
CONFLICT_STATES = frozenset(ConflictState.__args__)  # type: ignore[attr-defined]
SCENARIO_TYPES = frozenset(ScenarioType.__args__)  # type: ignore[attr-defined]
PAYOFF_CLASSES = frozenset(PayoffClass.__args__)  # type: ignore[attr-defined]

# ADR 0021 asymmetry contract: the probability-weighted balance of favorable and adverse
# payoff for the same asset representation across an immutable, predeclared scenario set.
# Raw upside is the probability-weighted positive terminal return; raw downside is the
# absolute probability-weighted negative terminal return; the raw asymmetry ratio is
# expected_positive_payoff / expected_negative_payoff with range [0, +inf). Fixed, never
# caller-parameterized.
RAW_ASYMMETRY_FORMULA = "expected_positive_payoff / expected_negative_payoff"
RAW_SIGN_CONVENTION = "higher-more-favorable"
NEUTRAL_RATIO = "1"
RAW_RANGE = "[0, +inf)"

# ADR 0021 asymmetry contract: "Zero downside does not imply infinity: the methodology
# must declare a finite cap or mark the result insufficient." The first foundation
# methodology fixes the insufficient-marker treatment: an undefined denominator keeps the
# input explicitly missing (it is never emitted as an infinite ratio).
ZERO_DOWNSIDE_TREATMENT = "insufficient-marker"

# ADR 0021 asymmetry contract: "Scenario horizon, baseline price, quote currency,
# entity/representation, terminal valuation rule, probability policy, and payoff units are
# fixed before calculation." The first foundation methodology fixes a 365-day scenario
# horizon (mirroring the valuation family's first-horizon decision).
SCENARIO_HORIZON_DAYS = 365

# ADR 0021 asymmetry contract: payoff units are fixed before calculation. The foundation
# fixes terminal returns expressed in quote-currency-relative decimal units.
REQUIRED_PAYOFF_UNIT = "terminal_return"

# ADR 0021 asymmetry contract: the baseline price is a separately observed market value
# for the identical representation and quote currency.
BASELINE_MARKET_OBSERVATION_FACT_TYPE = "spot_price"

# ADR 0021 asymmetry contract: "Probabilities must be declared before payoff evaluation,
# sum under a versioned policy, and expose uncertainty." The first foundation policy fixes
# a deterministic exact-decimal sum-to-one rule.
PROBABILITY_SUM_POLICY_ID = "asymmetry-probability-sum-v1"
PROBABILITY_SUM_POLICY_VERSION = "1.0.0"

# ADR 0021 asymmetry contract: the terminal valuation rule is fixed before calculation.
# The foundation accepts only the predeclared terminal-return rule; payoff estimates are
# declared before evaluation, never re-derived from current/latest prices at assessment.
SUPPORTED_TERMINAL_VALUATION_RULE = "predeclared-terminal-return-v1"
SUPPORTED_PAYOFF_MODEL_CONFIG_VERSION = "asymmetry-payoff-model-v1"

# ADR 0021 Anti-double-counting and correlation policy: asymmetry evidence is assigned to
# one contribution group, and the same fair-value delta cannot count as both mispricing
# upside and scenario upside. This foundation never consumes a fair-value estimate as a
# scenario payoff, so its correlation group is distinct from the valuation-mispricing
# group and no duplicated delta can be counted twice.
REQUIRED_CORRELATION_GROUP = "asymmetry-scenario"

# ADR 0021 Persistence requirements: a [0,1] normalized value requires a predeclared,
# versioned, historically calibrated monotonic transform and a declared neutral ratio. No
# calibration exists in this foundation, so the only legal value is "unavailable".
NORMALIZATION_UNAVAILABLE: NormalizationStatus = "unavailable"

# The only ADR currently authorizing this record family. Fixed; extending authorization
# to another ADR is a future governance decision.
AUTHORIZING_ADR_REFERENCE = "ADR-0021"

# Identifies the sole canonical authority permitted to produce this record family.
CANONICAL_AUTHORITY_ID = "canonical-asymmetry-authority-v1"

# Fixed, versioned confidence policy for this foundation's decomposed confidence
# computation (ADR 0021 confidence rules: scenario coverage, probability quality, payoff
# uncertainty, dependency/correlation structure, tail coverage, price quality, and model
# dispersion).
ASYMMETRY_CONFIDENCE_POLICY_ID = "asymmetry-confidence-v1"
ASYMMETRY_CONFIDENCE_POLICY_VERSION = "1.0.0"

# The only formula version this foundation implements. A persisted methodology declaring
# any other formula version is unsupported and keeps the assessment explicitly
# unavailable (ADR 0020 "Unsupported methodology must remain explicit unavailable").
SUPPORTED_FORMULA_VERSION = "asymmetry-raw-ratio-v1"


@dataclass(frozen=True)
class AsymmetryMethodologySnapshot:
    """Immutable reference methodology for the Canonical Asymmetry authority
    (ADR 0021 asymmetry contract; ADR 0020 input contract). Fixes the raw payoff
    formula, sign convention, neutral ratio, raw range, scenario horizon, baseline
    observation type, probability-sum policy, terminal valuation rule, payoff units,
    zero-downside treatment, required prerequisites, correlation group, and
    normalization/calibration unavailability. Never a price, a payoff, or an
    assessment result."""

    record_id: str
    logical_id: str
    schema_version: str
    semantic_version: str
    methodology_id: str
    formula_version: str
    raw_formula: str
    sign_convention: str
    neutral_ratio: str
    raw_range: str
    scenario_horizon_days: int
    required_quote_currency: str
    required_payoff_unit: str
    baseline_market_observation_fact_type: str
    probability_sum_policy_id: str
    probability_sum_policy_version: str
    terminal_valuation_rule: str
    supported_payoff_model_config_version: str
    zero_downside_treatment: str
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
            "neutral_ratio",
            "raw_range",
            "required_quote_currency",
            "required_payoff_unit",
            "baseline_market_observation_fact_type",
            "probability_sum_policy_id",
            "probability_sum_policy_version",
            "terminal_valuation_rule",
            "supported_payoff_model_config_version",
            "zero_downside_treatment",
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
        if self.raw_formula != RAW_ASYMMETRY_FORMULA:
            raise ValueError(f"raw_formula must be {RAW_ASYMMETRY_FORMULA!r} per ADR 0021")
        if self.sign_convention != RAW_SIGN_CONVENTION:
            raise ValueError(f"sign_convention must be {RAW_SIGN_CONVENTION!r} per ADR 0021")
        if self.neutral_ratio != NEUTRAL_RATIO:
            raise ValueError(f"neutral_ratio must be {NEUTRAL_RATIO!r} per ADR 0021")
        if self.raw_range != RAW_RANGE:
            raise ValueError(f"raw_range must be {RAW_RANGE!r} per ADR 0021")
        if self.scenario_horizon_days != SCENARIO_HORIZON_DAYS:
            raise ValueError(f"scenario_horizon_days must be {SCENARIO_HORIZON_DAYS} per ADR 0021")
        if self.required_payoff_unit != REQUIRED_PAYOFF_UNIT:
            raise ValueError(f"required_payoff_unit must be {REQUIRED_PAYOFF_UNIT!r} per ADR 0021")
        if self.baseline_market_observation_fact_type != BASELINE_MARKET_OBSERVATION_FACT_TYPE:
            raise ValueError(
                f"baseline_market_observation_fact_type must be {BASELINE_MARKET_OBSERVATION_FACT_TYPE!r} "
                "per ADR 0021"
            )
        if self.probability_sum_policy_id != PROBABILITY_SUM_POLICY_ID:
            raise ValueError(f"probability_sum_policy_id must be {PROBABILITY_SUM_POLICY_ID!r} per ADR 0021")
        if self.probability_sum_policy_version != PROBABILITY_SUM_POLICY_VERSION:
            raise ValueError(f"probability_sum_policy_version must be {PROBABILITY_SUM_POLICY_VERSION!r} per ADR 0021")
        if self.terminal_valuation_rule != SUPPORTED_TERMINAL_VALUATION_RULE:
            raise ValueError(f"terminal_valuation_rule must be {SUPPORTED_TERMINAL_VALUATION_RULE!r} per ADR 0021")
        if self.supported_payoff_model_config_version != SUPPORTED_PAYOFF_MODEL_CONFIG_VERSION:
            raise ValueError(
                "supported_payoff_model_config_version must be "
                f"{SUPPORTED_PAYOFF_MODEL_CONFIG_VERSION!r} per ADR 0021"
            )
        if self.zero_downside_treatment != ZERO_DOWNSIDE_TREATMENT:
            raise ValueError(f"zero_downside_treatment must be {ZERO_DOWNSIDE_TREATMENT!r} per ADR 0021")
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
class ScenarioSetSnapshot:
    """Immutable, predeclared scenario set for one asset representation (ADR 0021
    asymmetry contract: "All scenarios, probabilities, baselines, dependencies, and
    payoff estimates must be immutable and strict-known"). Fixes the scenario ids and
    types, the strict-known baseline market observation reference, the fixed horizon,
    quote currency, probability-sum policy, terminal valuation rule, payoff units, the
    material omitted-scenario policy, dependency/correlation pairs, evidence and source
    versions, and declared uncertainty. Never a probability, payoff, or assessment."""

    record_id: str
    logical_id: str
    schema_version: str
    semantic_version: str
    identity: EconomicClaimIdentity
    methodology_record_id: str
    methodology_record_version: str
    scenario_set_id: str
    scenario_ids: tuple[str, ...]
    scenario_types: tuple[str, ...]
    baseline_market_fact_record_id: str
    baseline_market_fact_record_version: str
    horizon_days: int
    quote_currency: str
    probability_sum_policy_id: str
    probability_sum_policy_version: str
    terminal_valuation_rule: str
    payoff_unit: str
    material_omitted_scenario_policy: str
    uncertainty: str
    evidence_record_ids: tuple[str, ...]
    source_versions: tuple[str, ...]
    dependency_pairs: tuple[tuple[str, str], ...]
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
            "methodology_record_id",
            "methodology_record_version",
            "scenario_set_id",
            "baseline_market_fact_record_id",
            "baseline_market_fact_record_version",
            "quote_currency",
            "probability_sum_policy_id",
            "probability_sum_policy_version",
            "terminal_valuation_rule",
            "payoff_unit",
            "material_omitted_scenario_policy",
            "uncertainty",
            "content_hash",
        )
        _member("quality_state", self.quality_state, QUALITY_STATES)
        _member("conflict_state", self.conflict_state, CONFLICT_STATES)
        if not self.scenario_ids:
            raise ValueError("scenario_ids must not be empty")
        if len(self.scenario_ids) != len(set(self.scenario_ids)):
            raise ValueError("scenario_ids must be unique")
        for scenario_id in self.scenario_ids:
            if not scenario_id.strip():
                raise ValueError("scenario_ids must not contain blank entries")
        if len(self.scenario_types) != len(self.scenario_ids):
            raise ValueError("scenario_types must parallel scenario_ids")
        for scenario_type in self.scenario_types:
            if scenario_type not in SCENARIO_TYPES:
                raise ValueError(f"unsupported scenario_type: {scenario_type}")
        # The methodology's tail and missing-scenario policy requires explicit downside
        # tail coverage in the predeclared set (ADR 0021: confidence reflects tail
        # coverage; material downside evidence must exist).
        if "downside_tail" not in self.scenario_types:
            raise ValueError("scenario_types must declare at least one 'downside_tail' scenario per ADR 0021")
        if self.horizon_days != SCENARIO_HORIZON_DAYS:
            raise ValueError(f"horizon_days must be {SCENARIO_HORIZON_DAYS} per ADR 0021")
        if self.probability_sum_policy_id != PROBABILITY_SUM_POLICY_ID:
            raise ValueError(f"probability_sum_policy_id must be {PROBABILITY_SUM_POLICY_ID!r} per ADR 0021")
        if self.probability_sum_policy_version != PROBABILITY_SUM_POLICY_VERSION:
            raise ValueError(f"probability_sum_policy_version must be {PROBABILITY_SUM_POLICY_VERSION!r} per ADR 0021")
        if self.terminal_valuation_rule != SUPPORTED_TERMINAL_VALUATION_RULE:
            raise ValueError(f"terminal_valuation_rule must be {SUPPORTED_TERMINAL_VALUATION_RULE!r} per ADR 0021")
        if self.payoff_unit != REQUIRED_PAYOFF_UNIT:
            raise ValueError(f"payoff_unit must be {REQUIRED_PAYOFF_UNIT!r} per ADR 0021")
        _bounded_decimal("uncertainty", self.uncertainty)
        if not self.evidence_record_ids or not self.source_versions:
            raise ValueError("evidence_record_ids and source_versions must not be empty")
        if len(self.evidence_record_ids) != len(self.source_versions):
            raise ValueError("evidence_record_ids and source_versions must be parallel")
        allowed = set(self.scenario_ids)
        for first, second in self.dependency_pairs:
            if first not in allowed or second not in allowed:
                raise ValueError("dependency_pairs must reference declared scenario ids")
            if first == second:
                raise ValueError("dependency_pairs must not contain self-pairs")
        if len(self.dependency_pairs) != len(set(self.dependency_pairs)):
            raise ValueError("dependency_pairs must be unique")
        _normalize_chronology(self)
        _validate_correction(self)


@dataclass(frozen=True)
class ScenarioProbabilityRecord:
    """Immutable declared probability for one scenario of a predeclared scenario set
    (ADR 0021 asymmetry contract). Probabilities are declared before payoff evaluation,
    carry explicit uncertainty and a declared probability authority/method, and must sum
    to one under the versioned policy."""

    record_id: str
    logical_id: str
    schema_version: str
    semantic_version: str
    scenario_set_record_id: str
    scenario_set_record_version: str
    scenario_id: str
    probability: str
    probability_uncertainty: str
    probability_authority: str
    probability_method: str
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
            "scenario_set_record_id",
            "scenario_set_record_version",
            "scenario_id",
            "probability",
            "probability_uncertainty",
            "probability_authority",
            "probability_method",
            "content_hash",
        )
        _member("quality_state", self.quality_state, QUALITY_STATES)
        _member("conflict_state", self.conflict_state, CONFLICT_STATES)
        probability = _decimal(self.probability)
        if not probability.is_finite() or probability <= 0 or probability > 1:
            raise ValueError("probability must be a finite decimal in (0, 1]")
        _bounded_decimal("probability_uncertainty", self.probability_uncertainty)
        _normalize_chronology(self)
        _validate_correction(self)


@dataclass(frozen=True)
class ScenarioPayoffEstimateRecord:
    """Immutable declared terminal payoff for one scenario of a predeclared scenario set
    (ADR 0021 asymmetry contract). Carries the fixed payoff unit, the deterministic
    positive/negative/neutral classification derived from the signed terminal payoff, the
    supported model configuration version, explicit payoff uncertainty, and evidence
    lineage."""

    record_id: str
    logical_id: str
    schema_version: str
    semantic_version: str
    scenario_set_record_id: str
    scenario_set_record_version: str
    scenario_id: str
    terminal_payoff: str
    payoff_class: PayoffClass
    payoff_unit: str
    model_config_version: str
    payoff_uncertainty: str
    evidence_record_ids: tuple[str, ...]
    source_record_id: str
    source_record_version: str
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
            "scenario_set_record_id",
            "scenario_set_record_version",
            "scenario_id",
            "terminal_payoff",
            "payoff_unit",
            "model_config_version",
            "payoff_uncertainty",
            "source_record_id",
            "source_record_version",
            "content_hash",
        )
        _member("quality_state", self.quality_state, QUALITY_STATES)
        _member("conflict_state", self.conflict_state, CONFLICT_STATES)
        _member("payoff_class", self.payoff_class, PAYOFF_CLASSES)
        payoff = _decimal(self.terminal_payoff)
        if not payoff.is_finite():
            raise ValueError("terminal_payoff must be a finite decimal")
        if self.payoff_unit != REQUIRED_PAYOFF_UNIT:
            raise ValueError(f"payoff_unit must be {REQUIRED_PAYOFF_UNIT!r} per ADR 0021")
        if self.model_config_version != SUPPORTED_PAYOFF_MODEL_CONFIG_VERSION:
            raise ValueError("model_config_version must be " f"{SUPPORTED_PAYOFF_MODEL_CONFIG_VERSION!r} per ADR 0021")
        _bounded_decimal("payoff_uncertainty", self.payoff_uncertainty)
        # Deterministic classification: the positive/negative/neutral class must equal
        # the sign of the declared signed terminal payoff (no relabeling).
        expected_class = "positive" if payoff > 0 else "negative" if payoff < 0 else "neutral"
        if self.payoff_class != expected_class:
            raise ValueError("payoff_class does not match the sign of terminal_payoff")
        _normalize_chronology(self)
        _validate_correction(self)


@dataclass(frozen=True)
class AsymmetryAssessmentRecord:
    """The only canonical output of this authority (ADR 0021 asymmetry contract). Binds
    the exact-version methodology, scenario set, scenario probability and payoff records,
    and baseline market observation it consumed; the expected positive and negative
    probability-weighted payoffs; the raw asymmetry ratio; the neutral ratio, sign
    convention, zero-downside treatment, tail coverage, normalization state, confidence
    decomposition, availability state, correlation group, provenance, and correction
    lineage. References rather than re-counts scenario evidence (ADR 0021
    anti-double-counting)."""

    record_id: str
    logical_id: str
    schema_version: str
    semantic_version: str
    identity: EconomicClaimIdentity
    methodology_record_id: str
    methodology_record_version: str
    scenario_set_record_id: str
    scenario_set_record_version: str
    scenario_probability_record_ids: tuple[str, ...]
    scenario_payoff_record_ids: tuple[str, ...]
    baseline_market_fact_record_id: str
    baseline_market_fact_record_version: str
    expected_positive_payoff: str
    expected_negative_payoff: str
    raw_asymmetry_ratio: str | None
    neutral_ratio: str
    sign_convention: str
    zero_downside_treatment: str
    tail_coverage: str
    normalization_status: NormalizationStatus
    normalized_value: None
    confidence_scenario_coverage: str
    confidence_probability_quality: str
    confidence_payoff_uncertainty: str
    confidence_tail_coverage: str
    confidence_price_quality: str
    confidence_dependency: str
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
            "neutral_ratio",
            "sign_convention",
            "zero_downside_treatment",
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
        if self.neutral_ratio != NEUTRAL_RATIO:
            raise ValueError(f"neutral_ratio must be {NEUTRAL_RATIO!r} per ADR 0021")
        if self.sign_convention != RAW_SIGN_CONVENTION:
            raise ValueError(f"sign_convention must be {RAW_SIGN_CONVENTION!r} per ADR 0021")
        if self.zero_downside_treatment != ZERO_DOWNSIDE_TREATMENT:
            raise ValueError(f"zero_downside_treatment must be {ZERO_DOWNSIDE_TREATMENT!r} per ADR 0021")
        if self.correlation_group != REQUIRED_CORRELATION_GROUP:
            raise ValueError(f"correlation_group must be {REQUIRED_CORRELATION_GROUP!r} per ADR 0021")
        _confidence_fields = (
            "confidence_scenario_coverage",
            "confidence_probability_quality",
            "confidence_payoff_uncertainty",
            "confidence_tail_coverage",
            "confidence_price_quality",
            "confidence_dependency",
            "confidence",
        )
        has_raw_values = self.availability_state in ("AVAILABLE", "UNAVAILABLE_UNCALIBRATED_NORMALIZATION")
        if has_raw_values:
            # Confidence is only meaningful when the assessment persists raw payoff values;
            # it is then required, bounded, and capped by its weakest mandatory component.
            for name in _confidence_fields:
                _bounded_decimal(name, getattr(self, name))
            components = [Decimal(getattr(self, name)) for name in _confidence_fields[:-1]]
            if Decimal(self.confidence) > min(components):
                raise ValueError("overall confidence cannot exceed the weakest mandatory component")
            # A raw assessment binds the exact-version methodology, scenario set, scenario
            # probability/payoff records, and baseline market observation (ADR 0021 record
            # contract), and persists the expected probability-weighted payoffs and the raw
            # asymmetry ratio.
            _required_text(
                self,
                "methodology_record_id",
                "methodology_record_version",
                "scenario_set_record_id",
                "scenario_set_record_version",
                "baseline_market_fact_record_id",
                "baseline_market_fact_record_version",
                "expected_positive_payoff",
                "expected_negative_payoff",
                "tail_coverage",
            )
            if not self.scenario_probability_record_ids or not self.scenario_payoff_record_ids:
                raise ValueError("a raw assessment must reference its scenario probability and payoff records")
            expected_positive = _decimal(self.expected_positive_payoff)
            expected_negative = _decimal(self.expected_negative_payoff)
            if not expected_positive.is_finite() or expected_positive < 0:
                raise ValueError("expected_positive_payoff must be a finite, non-negative decimal")
            if not expected_negative.is_finite() or expected_negative <= 0:
                raise ValueError("expected_negative_payoff must be a finite, strictly positive decimal")
            if self.raw_asymmetry_ratio is None:
                raise ValueError("a raw assessment must persist the raw asymmetry ratio")
            ratio = _decimal(self.raw_asymmetry_ratio)
            if not ratio.is_finite() or ratio < 0:
                raise ValueError("raw_asymmetry_ratio must be finite and within the declared raw range [0, +inf)")
            # Deterministic integrity: the raw ratio is recomputed exactly from the persisted
            # expected payoffs (no averaging, no fabricated evidence).
            expected_ratio = expected_positive / expected_negative
            if ratio != expected_ratio:
                raise ValueError("raw_asymmetry_ratio does not equal expected_positive / expected_negative")
            _bounded_decimal("tail_coverage", self.tail_coverage)
        else:
            # Explicit missingness (ADR 0020 "Confidence is unavailable, not zero"): an
            # unavailable assessment must never encode unavailable as a zero confidence.
            for name in _confidence_fields:
                if getattr(self, name).strip():
                    raise ValueError(
                        f"{name} must be blank for an unavailable assessment "
                        "(explicit missingness; never encode unavailable as zero confidence)"
                    )
            for name in ("expected_positive_payoff", "expected_negative_payoff", "tail_coverage"):
                if getattr(self, name).strip():
                    raise ValueError(f"{name} must be blank for an unavailable assessment")
            if self.raw_asymmetry_ratio is not None:
                raise ValueError("raw_asymmetry_ratio must be unavailable for an unavailable assessment")
            if self.scenario_probability_record_ids or self.scenario_payoff_record_ids:
                raise ValueError("scenario record references must be unavailable for an unavailable assessment")
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
