"""Canonical Comparative Valuation foundation (ADR 0026, Issue #159).

This package implements the authoritative evidence, persistence, replay,
correction, validation, and query foundations for `comparative_valuation` as
defined by ADR 0026 and ADPR-0003's Option 2C (versioned point-in-time candidate
universe plus service-owned eligibility decisions). It is a *foundation*: it does
not wire any scheduler, Dashboard field, or Market Validation adapter, and it
never produces a `[0,1]` normalized Market Validation input (no calibrated
normalization exists). A standalone operator-facing production entry point
(`hunter comparative-valuation-authority`, `hunter.comparative_valuation.command`,
Issue #181) exposes this foundation's existing write and read-only status
operations without adding any new authority, validation, or Market Validation
wiring.

The five immutable record families are:
    - PeerUniversePolicyRecord
    - PeerUniverseSnapshot
    - PeerEligibilityDecisionRecord
    - ComparativeMetricObservationRecord
    - ComparativeValuationAssessmentRecord

Downstream boundaries are closed: this package cannot calculate Mispricing,
Asymmetry, Opportunity Assessment, Market Validation composition, scoring,
weighting, recommendation, or ranking (ADR 0026 Prohibited Comparisons;
ADR 0021 authority matrix).
"""

from hunter.comparative_valuation.models import (
    AUTHORIZING_ADR_REFERENCE,
    CANDIDATE_SOURCE_AUTHORITY,
    CANONICAL_AUTHORITY_ID,
    COMPARATIVE_VALUATION_SCHEMA_VERSION,
    DENOMINATOR_EVIDENCE_TYPE,
    FULLY_DILUTED_MARKET_FACT_TYPE,
    LIMITED_PEER_SET_STATE,
    MINIMUM_ELIGIBLE_PEERS,
    NORMALIZATION_UNAVAILABLE,
    NUMERATOR_METRIC,
    REFERENCE_STATISTIC,
    REQUIRED_CORRELATION_GROUP,
    ComparativeMetricObservationRecord,
    ComparativeValuationAssessmentRecord,
    DimensionDecision,
    PeerCandidate,
    PeerEligibilityDecisionRecord,
    PeerUniversePolicyRecord,
    PeerUniverseSnapshot,
)
from hunter.comparative_valuation.repository import (
    COMPARATIVE_VALUATION_MIGRATION_ID,
    DEFAULT_COMPARATIVE_VALUATION_DB,
)
from hunter.comparative_valuation.repository import (
    ComparativeValuationIntegrityError as CanonicalComparativeValuationIntegrityError,
)
from hunter.comparative_valuation.repository import (
    ComparativeValuationRepository as CanonicalComparativeValuationRepository,
)
from hunter.comparative_valuation.service import (
    CanonicalComparativeValuationAuthorityError,
    CanonicalComparativeValuationService,
)

__all__ = [
    "AUTHORIZING_ADR_REFERENCE",
    "CANDIDATE_SOURCE_AUTHORITY",
    "CANONICAL_AUTHORITY_ID",
    "COMPARATIVE_VALUATION_MIGRATION_ID",
    "COMPARATIVE_VALUATION_SCHEMA_VERSION",
    "DEFAULT_COMPARATIVE_VALUATION_DB",
    "DENOMINATOR_EVIDENCE_TYPE",
    "FULLY_DILUTED_MARKET_FACT_TYPE",
    "LIMITED_PEER_SET_STATE",
    "MINIMUM_ELIGIBLE_PEERS",
    "NORMALIZATION_UNAVAILABLE",
    "NUMERATOR_METRIC",
    "REFERENCE_STATISTIC",
    "REQUIRED_CORRELATION_GROUP",
    "CanonicalComparativeValuationAuthorityError",
    "CanonicalComparativeValuationIntegrityError",
    "CanonicalComparativeValuationRepository",
    "CanonicalComparativeValuationService",
    "ComparativeMetricObservationRecord",
    "ComparativeValuationAssessmentRecord",
    "DimensionDecision",
    "PeerCandidate",
    "PeerEligibilityDecisionRecord",
    "PeerUniversePolicyRecord",
    "PeerUniverseSnapshot",
]
