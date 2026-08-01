"""Canonical Asymmetry foundation (ADR 0021, ADR 0020).

This package implements the authoritative evidence, persistence, replay,
correction, validation, and query foundations for `asymmetry` as defined by
ADR 0021 (Canonical Valuation Evidence Authority) and ADR 0020 (Canonical Market
Validation Input Authority and Strict-Known Replay). It is a *foundation*: it does
not wire any CLI, scheduler, Dashboard field, Market Validation adapter, or
production entry point, and it never produces a `[0,1]` normalized Market
Validation input (no calibrated normalization exists).

The immutable record families are:
    - AsymmetryMethodologySnapshot
    - ScenarioSetSnapshot
    - ScenarioProbabilityRecord
    - ScenarioPayoffEstimateRecord
    - AsymmetryAssessmentRecord

The service is the sole Hunter-owned producer of asymmetry assessments and of the
predeclared scenario, probability, and payoff records. Market providers own only
observed facts (price, quote currency, supply measures, market capitalization,
volume, provider identifiers, timestamps, raw metadata); they never assign scenario
probabilities, calculate asymmetry, or normalize inputs (ADR 0021
Source-provider eligibility).

Downstream boundaries are closed: this package cannot calculate Mispricing,
Opportunity Assessment, Market Validation composition, scoring, weighting,
recommendation, or ranking (ADR 0021 authority matrix; ADR 0021 correlation group
`asymmetry-scenario`; ADR 0021 anti-double-counting: the same fair-value delta
cannot count as both mispricing upside and scenario upside).
"""

from hunter.asymmetry.models import (
    ASYMMETRY_CONFIDENCE_POLICY_ID,
    ASYMMETRY_CONFIDENCE_POLICY_VERSION,
    ASYMMETRY_SCHEMA_VERSION,
    AUTHORIZING_ADR_REFERENCE,
    BASELINE_MARKET_OBSERVATION_FACT_TYPE,
    CANONICAL_AUTHORITY_ID,
    NEUTRAL_RATIO,
    NORMALIZATION_UNAVAILABLE,
    PROBABILITY_SUM_POLICY_ID,
    PROBABILITY_SUM_POLICY_VERSION,
    RAW_ASYMMETRY_FORMULA,
    RAW_RANGE,
    RAW_SIGN_CONVENTION,
    REQUIRED_CORRELATION_GROUP,
    REQUIRED_PAYOFF_UNIT,
    SCENARIO_HORIZON_DAYS,
    SUPPORTED_FORMULA_VERSION,
    SUPPORTED_PAYOFF_MODEL_CONFIG_VERSION,
    SUPPORTED_TERMINAL_VALUATION_RULE,
    ZERO_DOWNSIDE_TREATMENT,
    AsymmetryAssessmentRecord,
    AsymmetryMethodologySnapshot,
    ScenarioPayoffEstimateRecord,
    ScenarioProbabilityRecord,
    ScenarioSetSnapshot,
)
from hunter.asymmetry.repository import (
    ASYMMETRY_MIGRATION_ID,
    DEFAULT_ASYMMETRY_DB,
)
from hunter.asymmetry.repository import (
    AsymmetryIntegrityError as CanonicalAsymmetryIntegrityError,
)
from hunter.asymmetry.repository import (
    AsymmetryRepository as CanonicalAsymmetryRepository,
)
from hunter.asymmetry.service import (
    CanonicalAsymmetryAuthorityError,
    CanonicalAsymmetryService,
)

__all__ = [
    "ASYMMETRY_CONFIDENCE_POLICY_ID",
    "ASYMMETRY_CONFIDENCE_POLICY_VERSION",
    "ASYMMETRY_MIGRATION_ID",
    "ASYMMETRY_SCHEMA_VERSION",
    "AUTHORIZING_ADR_REFERENCE",
    "BASELINE_MARKET_OBSERVATION_FACT_TYPE",
    "CANONICAL_AUTHORITY_ID",
    "DEFAULT_ASYMMETRY_DB",
    "NEUTRAL_RATIO",
    "NORMALIZATION_UNAVAILABLE",
    "PROBABILITY_SUM_POLICY_ID",
    "PROBABILITY_SUM_POLICY_VERSION",
    "RAW_ASYMMETRY_FORMULA",
    "RAW_RANGE",
    "RAW_SIGN_CONVENTION",
    "REQUIRED_CORRELATION_GROUP",
    "REQUIRED_PAYOFF_UNIT",
    "SCENARIO_HORIZON_DAYS",
    "SUPPORTED_FORMULA_VERSION",
    "SUPPORTED_PAYOFF_MODEL_CONFIG_VERSION",
    "SUPPORTED_TERMINAL_VALUATION_RULE",
    "ZERO_DOWNSIDE_TREATMENT",
    "CanonicalAsymmetryAuthorityError",
    "CanonicalAsymmetryIntegrityError",
    "CanonicalAsymmetryRepository",
    "CanonicalAsymmetryService",
    "AsymmetryAssessmentRecord",
    "AsymmetryMethodologySnapshot",
    "ScenarioPayoffEstimateRecord",
    "ScenarioProbabilityRecord",
    "ScenarioSetSnapshot",
]
