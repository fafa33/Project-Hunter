"""Canonical Mispricing foundation (ADR 0021, Issue #162).

This package implements the authoritative evidence, persistence, replay,
correction, validation, and query foundations for `mispricing` as defined by
ADR 0021 (Canonical Valuation Evidence Authority) and ADR 0020 (Canonical Market
Validation Input Authority and Strict-Known Replay). It is a *foundation*: it does
not wire any scheduler, Dashboard field, or Market Validation adapter, and it
never produces a `[0,1]` normalized Market Validation input (no calibrated
normalization exists).

`hunter.mispricing.command` (Issue #183) implements a manifest-driven orchestration
module exposing this foundation's existing write and read-only status operations
without adding any new authority, validation, or Market Validation wiring. It is
deliberately NOT dispatched from `hunter.__main__` and is not reachable through the
`hunter` CLI: dispatching it would create a new cross-component/production boundary
under `docs/ARCHITECTURE_DECISION_PREPARATION_GUIDE.md`'s Scope section and would
require independent Architecture Review before activation. The module is exercised
directly by `tests/test_mispricing_authority_v1.py`, mirroring the identical
precedent established for `hunter.evidence_assembly` and
`hunter.comparative_valuation.command`.

The two immutable record families are:
    - MispricingMethodologySnapshot
    - MispricingAssessmentRecord

The service is the sole Hunter-owned producer of mispricing assessments. Market
providers own only observed facts (price, quote currency, supply measures,
market capitalization, volume, provider identifiers, timestamps, raw metadata);
they never infer mispricing (ADR 0021 Source-provider eligibility).

Downstream boundaries are closed: this package cannot calculate Asymmetry,
Opportunity Assessment, Market Validation composition, scoring, weighting,
recommendation, or ranking (ADR 0021 authority matrix; ADR 0021 correlation
group `valuation-mispricing`).
"""

from hunter.mispricing.models import (
    AUTHORIZING_ADR_REFERENCE,
    CANONICAL_AUTHORITY_ID,
    MARKET_OBSERVATION_FACT_TYPE,
    MISPRICING_SCHEMA_VERSION,
    NEUTRAL_POINT,
    NORMALIZATION_UNAVAILABLE,
    RAW_MISPRICING_FORMULA,
    RAW_RANGE,
    RAW_SIGN_CONVENTION,
    REQUIRED_CORRELATION_GROUP,
    REQUIRED_SUPPLY_BASIS,
    TIMESTAMP_TOLERANCE_DAYS,
    MispricingAssessmentRecord,
    MispricingMethodologySnapshot,
)
from hunter.mispricing.repository import (
    DEFAULT_MISPRICING_DB,
    MISPRICING_MIGRATION_ID,
)
from hunter.mispricing.repository import (
    MispricingIntegrityError as CanonicalMispricingIntegrityError,
)
from hunter.mispricing.repository import (
    MispricingRepository as CanonicalMispricingRepository,
)
from hunter.mispricing.service import (
    CanonicalMispricingAuthorityError,
    CanonicalMispricingService,
)

__all__ = [
    "AUTHORIZING_ADR_REFERENCE",
    "CANONICAL_AUTHORITY_ID",
    "DEFAULT_MISPRICING_DB",
    "MARKET_OBSERVATION_FACT_TYPE",
    "MISPRICING_MIGRATION_ID",
    "MISPRICING_SCHEMA_VERSION",
    "NEUTRAL_POINT",
    "NORMALIZATION_UNAVAILABLE",
    "RAW_MISPRICING_FORMULA",
    "RAW_RANGE",
    "RAW_SIGN_CONVENTION",
    "REQUIRED_CORRELATION_GROUP",
    "REQUIRED_SUPPLY_BASIS",
    "TIMESTAMP_TOLERANCE_DAYS",
    "CanonicalMispricingAuthorityError",
    "CanonicalMispricingIntegrityError",
    "CanonicalMispricingRepository",
    "CanonicalMispricingService",
    "MispricingAssessmentRecord",
    "MispricingMethodologySnapshot",
]
