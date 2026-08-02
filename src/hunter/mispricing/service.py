from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from hunter.market_facts.repository import ObservedMarketFactRepository
from hunter.mispricing.models import (
    AUTHORIZING_ADR_REFERENCE,
    AVAILABILITY_STATES,
    CANONICAL_AUTHORITY_ID,
    MARKET_OBSERVATION_FACT_TYPE,
    MISPRICING_CONFIDENCE_POLICY_ID,
    MISPRICING_CONFIDENCE_POLICY_VERSION,
    MISPRICING_SCHEMA_VERSION,
    NEUTRAL_POINT,
    NORMALIZATION_UNAVAILABLE,
    RAW_MISPRICING_FORMULA,
    RAW_RANGE,
    RAW_SIGN_CONVENTION,
    REQUIRED_CORRELATION_GROUP,
    REQUIRED_SUPPLY_BASIS,
    SUPPORTED_FORMULA_VERSION,
    TIMESTAMP_TOLERANCE_DAYS,
    MispricingAssessmentRecord,
    MispricingMethodologySnapshot,
)
from hunter.mispricing.repository import (
    MispricingIntegrityError,
    MispricingRepository,
    assessment_snapshot,
    methodology_snapshot,
)
from hunter.persistence.models import QuerySpec
from hunter.persistence.sql import RepositoryFactory, SessionFactory, create_sqlite_engine
from hunter.persistence.sql.exceptions import PersistenceIdentityConflictError
from hunter.valuation.repository import CanonicalValuationRepository
from hunter.valuation.service import CanonicalValuationService
from hunter.value_capture.models import EconomicClaimIdentity

_APPLICATION_ROOT_ENV = "HUNTER_APPLICATION_ROOT"

# Snapshot types are fixed by the repository module and reused for correction
# authorization and reads.
_METHODOLOGY_SNAPSHOT_TYPE = "mispricing-methodology-snapshot"
_ASSESSMENT_SNAPSHOT_TYPE = "mispricing-assessment-record"

# ADR 0021 mispricing contract: the raw signed ratio is the only authorized raw output.
# Positive means estimated undervaluation; zero means equality; negative means estimated
# overvaluation. The ratio uses the fair-value p50 reference only -- no averaging of the
# p10/p50/p90 distribution into the raw ratio (ADR 0021 no-averaging requirement).
_RAW_RATIO_FORMULA = RAW_MISPRICING_FORMULA

# ADR 0021 mispricing contract: the attached uncertainty interval is derived from the
# fair-value p10 and p90 references against the same observed market price, preserving
# the full fair-value distribution rather than a single point.
_UNCERTAINTY_INTERVAL = "(fair_value_p10/p90 - observed_market_price) / observed_market_price"

# ADR 0021 confidence rules: "Confidence cannot exceed either prerequisite and is further
# reduced by valuation dispersion, price-source conflicts, liquidity/market-quality
# limitations, timestamp distance, representation uncertainty, and stale evidence."
# This foundation decomposes confidence into the fair-value confidence, the observed
# market price quality confidence, a valuation-dispersion factor, and a timestamp-distance
# factor, and caps overall confidence at the weakest component.
_CONFIDENCE_POLICY_ID = MISPRICING_CONFIDENCE_POLICY_ID
_CONFIDENCE_POLICY_VERSION = MISPRICING_CONFIDENCE_POLICY_VERSION


class CanonicalMispricingAuthorityError(ValueError):
    """Raised for unauthorized or unattested construction and for contradictory
    evidence references. Missing, incompatible, disputed, unknown-time, or unsupported
    prerequisites never raise here: they produce an explicitly unavailable assessment
    (ADR 0020 missingness -- never a partial or degraded-confidence result)."""


class CanonicalMispricingService:
    """Service-owned authority boundary for the two Mispricing record families
    (ADR 0021 mispricing contract; ADR 0020 input contract).

    Authority model is identical to hunter.valuation_methodology, hunter.valuation, and
    hunter.comparative_valuation: HUNTER_APPLICATION_ROOT-gated, operator-attested
    construction; the service computes deterministic outputs from already-persisted,
    already-validated records. Mispricing is never inferred from market providers: the
    observed market price is a provider-owned ObservedMarketFactRecord consumed as an
    exact-version prerequisite, and the mispricing assessment is Hunter-owned
    (ADR 0021 Source-provider eligibility: providers never declare mispricing).

    Methodology parameters that ADR 0021 fixes -- the raw ratio formula, the positive-
    undervaluation sign convention, the zero neutral point, the [-1, +inf) raw range,
    the 7-day observation tolerance, the fully-diluted supply basis, the spot-price
    market observation, the valuation-mispricing correlation group, and no calibrated
    normalization -- are enforced as fixed model invariants (see mispricing.models) and
    by the fixed constants used below; they are never caller-parameterized.

    The two immutable record families are produced by exactly these methods:

    - ``persist_mispricing_methodology`` -> MispricingMethodologySnapshot
    - ``assess``                        -> MispricingAssessmentRecord

    Downstream boundaries are closed: this service cannot calculate Asymmetry,
    Opportunity Assessment, Market Validation composition, scoring, weighting,
    recommendation, or ranking (ADR 0021 authority matrix). The assessment references
    rather than re-counts valuation evidence (ADR 0021 anti-double-counting).
    """

    def __init__(
        self,
        *,
        repository: MispricingRepository,
        valuation_repository: CanonicalValuationRepository,
        market_fact_repository: ObservedMarketFactRepository,
        application_root: Path | None = None,
    ) -> None:
        self.repository = repository
        self.valuation_repository = valuation_repository
        self.market_fact_repository = market_fact_repository
        self._application_root = _authorized_application_root(application_root)

    # ------------------------------------------------------------------ methodology

    def persist_mispricing_methodology(
        self,
        *,
        methodology_id: str,
        required_quote_currency: str,
        entity_class_criteria_id: str,
        entity_class_criteria_version: str,
        effective_at: datetime,
        recorded_at: datetime,
        known_at: datetime,
        formula_version: str = SUPPORTED_FORMULA_VERSION,
        supersedes_record_id: str | None = None,
        correction_reason: str = "",
    ) -> MispricingMethodologySnapshot:
        record = MispricingMethodologySnapshot(
            record_id="pending",
            logical_id="pending",
            schema_version=MISPRICING_SCHEMA_VERSION,
            semantic_version="1.0.0",
            methodology_id=methodology_id,
            formula_version=formula_version,
            raw_formula=RAW_MISPRICING_FORMULA,
            sign_convention=RAW_SIGN_CONVENTION,
            neutral_point=NEUTRAL_POINT,
            raw_range=RAW_RANGE,
            timestamp_tolerance_days=TIMESTAMP_TOLERANCE_DAYS,
            required_quote_currency=required_quote_currency,
            required_supply_basis=REQUIRED_SUPPLY_BASIS,
            market_observation_fact_type=MARKET_OBSERVATION_FACT_TYPE,
            supported_entity_class="canonical_asset_representation",
            entity_class_criteria_id=entity_class_criteria_id,
            entity_class_criteria_version=entity_class_criteria_version,
            correlation_group=REQUIRED_CORRELATION_GROUP,
            confidence_policy_id=_CONFIDENCE_POLICY_ID,
            confidence_policy_version=_CONFIDENCE_POLICY_VERSION,
            normalization_policy_id=None,
            calibration_policy_id=None,
            authorizing_adr_reference=AUTHORIZING_ADR_REFERENCE,
            authorized_by=CANONICAL_AUTHORITY_ID,
            effective_at=effective_at,
            recorded_at=recorded_at,
            known_at=known_at,
            content_hash="pending",
            quality_state="accepted",
            conflict_state="none",
            supersedes_record_id=supersedes_record_id,
            correction_reason=correction_reason,
        )
        record = _normalize_methodology(record)
        self._persist(_METHODOLOGY_SNAPSHOT_TYPE, methodology_snapshot, record)
        return record

    # ------------------------------------------------------------------ assessment

    def assess(
        self,
        *,
        identity: EconomicClaimIdentity,
        methodology_record_id: str,
        fair_value_logical_id: str,
        effective_at: datetime,
        recorded_at: datetime,
        known_at: datetime,
        supersedes_record_id: str | None = None,
        correction_reason: str = "",
    ) -> MispricingAssessmentRecord:
        methodology = self._authoritative_methodology(
            methodology_record_id, effective_at=effective_at, recorded_at=recorded_at, known_at=known_at
        )
        if methodology is None:
            return self._persist_unavailable_assessment(
                identity=identity,
                methodology_record_id=methodology_record_id,
                methodology_record_version="",
                fair_value_logical_id=fair_value_logical_id,
                availability_state="UNAVAILABLE_UNSUPPORTED_METHODOLOGY",
                effective_at=effective_at,
                recorded_at=recorded_at,
                known_at=known_at,
                supersedes_record_id=supersedes_record_id,
                correction_reason=correction_reason,
            )
        if methodology.formula_version != SUPPORTED_FORMULA_VERSION:
            # Unsupported methodology remains explicitly unavailable (ADR 0020
            # "Unsupported methodology must remain explicit unavailable"); it never
            # falls back to a different formula or a fabricated value.
            return self._persist_unavailable_assessment(
                identity=identity,
                methodology_record_id=methodology.record_id,
                methodology_record_version=methodology.semantic_version,
                fair_value_logical_id=fair_value_logical_id,
                availability_state="UNAVAILABLE_UNSUPPORTED_METHODOLOGY",
                effective_at=effective_at,
                recorded_at=recorded_at,
                known_at=known_at,
                supersedes_record_id=supersedes_record_id,
                correction_reason=correction_reason,
            )

        fair_value = CanonicalValuationService.select_strict_known_fair_value_estimate(
            self.valuation_repository,
            effective_as_of=effective_at,
            known_by=known_at,
            logical_id=fair_value_logical_id,
        )
        if fair_value is None:
            return self._persist_unavailable_assessment(
                identity=identity,
                methodology_record_id=methodology.record_id,
                methodology_record_version=methodology.semantic_version,
                fair_value_logical_id=fair_value_logical_id,
                availability_state="UNAVAILABLE_NO_FAIR_VALUE",
                effective_at=effective_at,
                recorded_at=recorded_at,
                known_at=known_at,
                supersedes_record_id=supersedes_record_id,
                correction_reason=correction_reason,
            )
        if not _identity_matches(fair_value.identity, identity):
            return self._persist_unavailable_assessment(
                identity=identity,
                methodology_record_id=methodology.record_id,
                methodology_record_version=methodology.semantic_version,
                fair_value_logical_id=fair_value.logical_id,
                availability_state="UNAVAILABLE_IDENTITY_MISMATCH",
                effective_at=effective_at,
                recorded_at=recorded_at,
                known_at=known_at,
                supersedes_record_id=supersedes_record_id,
                correction_reason=correction_reason,
            )
        if fair_value.currency.strip().lower() != methodology.required_quote_currency.strip().lower():
            return self._persist_unavailable_assessment(
                identity=identity,
                methodology_record_id=methodology.record_id,
                methodology_record_version=methodology.semantic_version,
                fair_value_logical_id=fair_value.logical_id,
                availability_state="UNAVAILABLE_UNIT_MISMATCH",
                effective_at=effective_at,
                recorded_at=recorded_at,
                known_at=known_at,
                supersedes_record_id=supersedes_record_id,
                correction_reason=correction_reason,
            )
        if fair_value.supply_basis_selection_rule != methodology.required_supply_basis:
            return self._persist_unavailable_assessment(
                identity=identity,
                methodology_record_id=methodology.record_id,
                methodology_record_version=methodology.semantic_version,
                fair_value_logical_id=fair_value.logical_id,
                availability_state="UNAVAILABLE_SUPPLY_BASIS_MISMATCH",
                effective_at=effective_at,
                recorded_at=recorded_at,
                known_at=known_at,
                supersedes_record_id=supersedes_record_id,
                correction_reason=correction_reason,
            )
        if fair_value.conflict_state in {"open", "contested"}:
            return self._persist_unavailable_assessment(
                identity=identity,
                methodology_record_id=methodology.record_id,
                methodology_record_version=methodology.semantic_version,
                fair_value_logical_id=fair_value.logical_id,
                availability_state="UNAVAILABLE_CONFLICTED_INPUTS",
                effective_at=effective_at,
                recorded_at=recorded_at,
                known_at=known_at,
                supersedes_record_id=supersedes_record_id,
                correction_reason=correction_reason,
            )

        market_fact = self.market_fact_repository.strict_known_fact(
            entity_id=identity.entity_id,
            representation_id=identity.representation_id,
            fact_type=methodology.market_observation_fact_type,
            effective_as_of=effective_at,
            known_by=known_at,
            quote_currency=methodology.required_quote_currency,
        )
        if market_fact is None:
            return self._persist_unavailable_assessment(
                identity=identity,
                methodology_record_id=methodology.record_id,
                methodology_record_version=methodology.semantic_version,
                fair_value_logical_id=fair_value.logical_id,
                availability_state="UNAVAILABLE_NO_MARKET_OBSERVATION",
                effective_at=effective_at,
                recorded_at=recorded_at,
                known_at=known_at,
                supersedes_record_id=supersedes_record_id,
                correction_reason=correction_reason,
            )
        if (
            market_fact.identity.entity_id != identity.entity_id
            or market_fact.identity.representation_id != identity.representation_id
        ):
            return self._persist_unavailable_assessment(
                identity=identity,
                methodology_record_id=methodology.record_id,
                methodology_record_version=methodology.semantic_version,
                fair_value_logical_id=fair_value.logical_id,
                availability_state="UNAVAILABLE_IDENTITY_MISMATCH",
                effective_at=effective_at,
                recorded_at=recorded_at,
                known_at=known_at,
                supersedes_record_id=supersedes_record_id,
                correction_reason=correction_reason,
            )
        if (market_fact.quote_currency or "").strip().lower() != methodology.required_quote_currency.strip().lower():
            return self._persist_unavailable_assessment(
                identity=identity,
                methodology_record_id=methodology.record_id,
                methodology_record_version=methodology.semantic_version,
                fair_value_logical_id=fair_value.logical_id,
                availability_state="UNAVAILABLE_UNIT_MISMATCH",
                effective_at=effective_at,
                recorded_at=recorded_at,
                known_at=known_at,
                supersedes_record_id=supersedes_record_id,
                correction_reason=correction_reason,
            )
        if market_fact.conflict_state in {"open", "contested"}:
            return self._persist_unavailable_assessment(
                identity=identity,
                methodology_record_id=methodology.record_id,
                methodology_record_version=methodology.semantic_version,
                fair_value_logical_id=fair_value.logical_id,
                availability_state="UNAVAILABLE_CONFLICTED_INPUTS",
                effective_at=effective_at,
                recorded_at=recorded_at,
                known_at=known_at,
                supersedes_record_id=supersedes_record_id,
                correction_reason=correction_reason,
            )

        price = Decimal(market_fact.value)
        if not price.is_finite() or price <= 0:
            # Zero/negative market price makes the input missing (ADR 0021).
            return self._persist_unavailable_assessment(
                identity=identity,
                methodology_record_id=methodology.record_id,
                methodology_record_version=methodology.semantic_version,
                fair_value_logical_id=fair_value.logical_id,
                availability_state="UNAVAILABLE_ZERO_OR_NEGATIVE_PRICE",
                effective_at=effective_at,
                recorded_at=recorded_at,
                known_at=known_at,
                supersedes_record_id=supersedes_record_id,
                correction_reason=correction_reason,
            )

        timestamp_distance = abs((fair_value.effective_at - market_fact.effective_at).days)
        if timestamp_distance > methodology.timestamp_tolerance_days:
            # Fair value and market observation must be temporally compatible
            # (ADR 0021 "compatible effective window").
            return self._persist_unavailable_assessment(
                identity=identity,
                methodology_record_id=methodology.record_id,
                methodology_record_version=methodology.semantic_version,
                fair_value_logical_id=fair_value.logical_id,
                availability_state="UNAVAILABLE_INCOMPATIBLE_TIMES",
                effective_at=effective_at,
                recorded_at=recorded_at,
                known_at=known_at,
                supersedes_record_id=supersedes_record_id,
                correction_reason=correction_reason,
            )

        p10 = Decimal(fair_value.fair_value_per_diluted_unit_p10)
        p50 = Decimal(fair_value.fair_value_per_diluted_unit_p50)
        p90 = Decimal(fair_value.fair_value_per_diluted_unit_p90)
        raw_signed_ratio = (p50 - price) / price
        interval_lower = (p10 - price) / price
        interval_upper = (p90 - price) / price

        confidence = self._confidence_components(
            fair_value=fair_value,
            market_fact=market_fact,
            timestamp_distance=timestamp_distance,
            methodology=methodology,
        )

        record = MispricingAssessmentRecord(
            record_id="pending",
            logical_id="pending",
            schema_version=MISPRICING_SCHEMA_VERSION,
            semantic_version="1.0.0",
            identity=identity,
            methodology_record_id=methodology.record_id,
            methodology_record_version=methodology.semantic_version,
            fair_value_record_id=fair_value.record_id,
            fair_value_record_version=fair_value.semantic_version,
            market_fact_record_id=market_fact.record_id,
            market_fact_record_version=market_fact.semantic_version,
            fair_value_p10=fair_value.fair_value_per_diluted_unit_p10,
            fair_value_p50=fair_value.fair_value_per_diluted_unit_p50,
            fair_value_p90=fair_value.fair_value_per_diluted_unit_p90,
            observed_market_price=market_fact.value,
            raw_signed_ratio=raw_signed_ratio.to_eng_string(),
            uncertainty_interval_lower=interval_lower.to_eng_string(),
            uncertainty_interval_upper=interval_upper.to_eng_string(),
            neutral_point=NEUTRAL_POINT,
            sign_convention=RAW_SIGN_CONVENTION,
            normalization_status=NORMALIZATION_UNAVAILABLE,
            normalized_value=None,
            confidence_fair_value=confidence["fair_value"],
            confidence_market_quality=confidence["market_quality"],
            confidence_dispersion=confidence["dispersion"],
            confidence_timestamp=confidence["timestamp"],
            confidence=confidence["overall"],
            availability_state="UNAVAILABLE_UNCALIBRATED_NORMALIZATION",
            correlation_group=REQUIRED_CORRELATION_GROUP,
            effective_at=effective_at,
            recorded_at=recorded_at,
            known_at=known_at,
            content_hash="pending",
            quality_state="accepted",
            conflict_state="none",
            supersedes_record_id=supersedes_record_id,
            correction_reason=correction_reason,
        )
        record = _normalize_assessment(record, fair_value_logical_id=fair_value.logical_id)
        self._persist(_ASSESSMENT_SNAPSHOT_TYPE, assessment_snapshot, record)
        return record

    # ------------------------------------------------------------- read helpers

    def get_mispricing_methodology(self, record_id: str) -> MispricingMethodologySnapshot | None:
        return self.repository.get_mispricing_methodology(record_id)

    def get_assessment(self, record_id: str) -> MispricingAssessmentRecord | None:
        return self.repository.get_assessment(record_id)

    def methodology_history(self, logical_id: str) -> tuple[MispricingMethodologySnapshot, ...]:
        return self.repository.methodology_history(logical_id)

    def assessment_history(self, logical_id: str) -> tuple[MispricingAssessmentRecord, ...]:
        return self.repository.assessment_history(logical_id)

    # Strict-known replay is owned by the service (ADR 0009: the repository performs no
    # replay selection -- it only stores and mechanically retrieves). Each method reads
    # the full mechanical history for the logical identity and applies the deterministic
    # replay eligibility: cutoff-safe (effective_at <= effective_as_of), known-safe
    # (recorded_at/known_at <= known_by), accepted quality, non-conflicted, and never a
    # superseded lineage predecessor.

    def strict_known_methodology(
        self, *, effective_as_of: datetime, known_by: datetime, logical_id: str
    ) -> MispricingMethodologySnapshot | None:
        return _strict_known(
            self.repository.methodology_history(logical_id),
            effective_as_of=effective_as_of,
            known_by=known_by,
        )

    def strict_known_assessment(
        self, *, effective_as_of: datetime, known_by: datetime, logical_id: str
    ) -> MispricingAssessmentRecord | None:
        return _strict_known(
            self.repository.assessment_history(logical_id),
            effective_as_of=effective_as_of,
            known_by=known_by,
        )

    def unresolved_methodology_conflicts(self) -> tuple[MispricingMethodologySnapshot, ...]:
        return _unresolved_conflicts(self.repository.methodology_records())

    def unresolved_assessment_conflicts(self) -> tuple[MispricingAssessmentRecord, ...]:
        return _unresolved_conflicts(self.repository.assessment_records())

    # ------------------------------------------------------------- internals

    def _authoritative_methodology(
        self,
        methodology_record_id: str,
        *,
        effective_at: datetime,
        recorded_at: datetime,
        known_at: datetime,
    ) -> MispricingMethodologySnapshot | None:
        methodology = self.repository.get_mispricing_methodology(methodology_record_id)
        if methodology is None:
            return None
        if methodology.quality_state != "accepted" or methodology.conflict_state not in {"none", "resolved"}:
            return None
        if methodology.recorded_at > recorded_at or methodology.known_at > known_at:
            return None
        if methodology.effective_at > effective_at:
            return None
        return methodology

    def _confidence_components(
        self,
        *,
        fair_value: Any,
        market_fact: Any,
        timestamp_distance: int,
        methodology: MispricingMethodologySnapshot,
    ) -> dict[str, str]:
        # ADR 0021 confidence rules: confidence cannot exceed either prerequisite and is
        # further reduced by valuation dispersion, price-source conflicts,
        # liquidity/market-quality limitations, timestamp distance, representation
        # uncertainty, and stale evidence.
        fair_value_conf = Decimal(fair_value.confidence)
        if not fair_value_conf.is_finite() or fair_value_conf < 0 or fair_value_conf > 1:
            raise CanonicalMispricingAuthorityError("fair value confidence must be between 0 and 1")
        market_quality = Decimal(market_fact.confidence)
        if not market_quality.is_finite() or market_quality < 0 or market_quality > 1:
            raise CanonicalMispricingAuthorityError("observed market price confidence must be between 0 and 1")
        dispersion_ratio = Decimal(fair_value.model_dispersion_ratio)
        if not dispersion_ratio.is_finite() or dispersion_ratio < 0:
            raise CanonicalMispricingAuthorityError("model dispersion ratio must be a finite non-negative decimal")
        dispersion = max(Decimal(0), Decimal(1) - min(dispersion_ratio, Decimal(1)))
        timestamp = max(
            Decimal(0),
            Decimal(1)
            - min(Decimal(timestamp_distance), Decimal(methodology.timestamp_tolerance_days))
            / Decimal(methodology.timestamp_tolerance_days),
        )
        overall = min(fair_value_conf, market_quality, dispersion, timestamp)
        return {
            "fair_value": fair_value_conf.to_eng_string(),
            "market_quality": market_quality.to_eng_string(),
            "dispersion": dispersion.to_eng_string(),
            "timestamp": timestamp.to_eng_string(),
            "overall": overall.to_eng_string(),
        }

    def _persist_unavailable_assessment(
        self,
        *,
        identity: EconomicClaimIdentity,
        methodology_record_id: str,
        methodology_record_version: str,
        fair_value_logical_id: str,
        availability_state: str,
        effective_at: datetime,
        recorded_at: datetime,
        known_at: datetime,
        supersedes_record_id: str | None,
        correction_reason: str,
    ) -> MispricingAssessmentRecord:
        if availability_state not in AVAILABILITY_STATES:
            raise CanonicalMispricingAuthorityError(f"unsupported availability state: {availability_state}")
        record = MispricingAssessmentRecord(
            record_id="pending",
            logical_id="pending",
            schema_version=MISPRICING_SCHEMA_VERSION,
            semantic_version="1.0.0",
            identity=identity,
            methodology_record_id=methodology_record_id,
            methodology_record_version=methodology_record_version,
            fair_value_record_id="",
            fair_value_record_version="",
            market_fact_record_id="",
            market_fact_record_version="",
            fair_value_p10="",
            fair_value_p50="",
            fair_value_p90="",
            observed_market_price="",
            raw_signed_ratio=None,
            uncertainty_interval_lower=None,
            uncertainty_interval_upper=None,
            neutral_point=NEUTRAL_POINT,
            sign_convention=RAW_SIGN_CONVENTION,
            normalization_status=NORMALIZATION_UNAVAILABLE,
            normalized_value=None,
            # Fix 4 (from the Issue #159 review, applied to this family): explicit
            # missingness -- an unavailable assessment must never encode unavailable as a
            # zero confidence (ADR 0020 "Confidence is unavailable, not zero").
            confidence_fair_value="",
            confidence_market_quality="",
            confidence_dispersion="",
            confidence_timestamp="",
            confidence="",
            availability_state=availability_state,
            correlation_group=REQUIRED_CORRELATION_GROUP,
            effective_at=effective_at,
            recorded_at=recorded_at,
            known_at=known_at,
            content_hash="pending",
            quality_state="accepted",
            conflict_state="none",
            supersedes_record_id=supersedes_record_id,
            correction_reason=correction_reason,
        )
        record = _normalize_assessment(record, fair_value_logical_id=fair_value_logical_id)
        self._persist(_ASSESSMENT_SNAPSHOT_TYPE, assessment_snapshot, record)
        return record

    def _persist(self, snapshot_type: str, snapshot_fn: Any, record: Any) -> None:
        engine = create_sqlite_engine(self.repository.path)
        session = SessionFactory(engine).create()
        try:
            # BEGIN IMMEDIATE before validating -- the hardened transaction pattern from
            # audit finding F1, reused unmodified from hunter.valuation_methodology,
            # hunter.valuation, and hunter.comparative_valuation.
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            snapshots = RepositoryFactory(session).snapshots()
            _authorize_correction(
                snapshots,
                snapshot_type=snapshot_type,
                record=record,
            )
            snapshots.save(snapshot_fn(record))
            session.commit()
        except PersistenceIdentityConflictError as exc:
            session.rollback()
            raise MispricingIntegrityError(str(exc)) from exc
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
            engine.dispose()


def _authorize_correction(snapshots: Any, *, snapshot_type: str, record: Any) -> None:
    """Mirrors hunter.valuation.service._authorize_correction,
    hunter.valuation_methodology.service._authorize_correction, and
    hunter.comparative_valuation.service._authorize_correction unmodified in substance:
    one root record per logical_id, strictly-later correction chronology, and no
    branching successors."""
    record_id = record.record_id
    logical_id = record.logical_id
    supersedes_record_id = record.supersedes_record_id
    recorded_at = record.recorded_at
    known_at = record.known_at
    if supersedes_record_id is None:
        existing_root = next(
            (
                item
                for item in snapshots.query(QuerySpec(record_kind="snapshot"))
                if item.snapshot_type == snapshot_type
                and item.payload.get("logical_id") == logical_id
                and item.id != record_id
            ),
            None,
        )
        if existing_root is not None:
            raise MispricingIntegrityError(
                "a root record already exists for this logical_id; use a correction "
                "(supersedes_record_id) instead of a second independent root record"
            )
        return
    prior_snapshot = snapshots.load(supersedes_record_id)
    if prior_snapshot is None or prior_snapshot.snapshot_type != snapshot_type:
        raise MispricingIntegrityError("correction predecessor does not exist")
    prior_payload = prior_snapshot.payload
    if str(prior_payload.get("logical_id")) != logical_id:
        raise MispricingIntegrityError("correction must preserve logical_id")
    if _aware(prior_payload["recorded_at"]) >= recorded_at:
        raise MispricingIntegrityError("correction recorded_at must follow predecessor")
    if _aware(prior_payload["known_at"]) >= known_at:
        raise MispricingIntegrityError("correction known_at must follow predecessor")
    competing_successor = next(
        (
            item
            for item in snapshots.query(QuerySpec(record_kind="snapshot"))
            if item.snapshot_type == snapshot_type
            and item.payload.get("supersedes_record_id") == supersedes_record_id
            and item.id != record_id
        ),
        None,
    )
    if competing_successor is not None:
        raise MispricingIntegrityError("branching correction lineage is prohibited")


def _normalize_methodology(record: MispricingMethodologySnapshot) -> MispricingMethodologySnapshot:
    logical_id = hashlib.sha256(f"mispricing-methodology|{record.methodology_id}".encode()).hexdigest()
    content_hash = _content_hash(record, logical_id=logical_id)
    record_id = hashlib.sha256(f"{logical_id}:{content_hash}".encode()).hexdigest()
    return replace(record, logical_id=logical_id, content_hash=content_hash, record_id=record_id)


def _normalize_assessment(
    record: MispricingAssessmentRecord, *, fair_value_logical_id: str
) -> MispricingAssessmentRecord:
    logical_id = hashlib.sha256(
        (
            "mispricing-assessment|"
            + _identity_key(record.identity)
            + "|"
            + record.methodology_record_id
            + "|"
            + fair_value_logical_id
        ).encode()
    ).hexdigest()
    content_hash = _content_hash(record, logical_id=logical_id)
    record_id = hashlib.sha256(f"{logical_id}:{content_hash}".encode()).hexdigest()
    return replace(record, logical_id=logical_id, content_hash=content_hash, record_id=record_id)


_CONTENT_HASH_EXCLUDED_FIELDS = frozenset({"record_id", "logical_id", "content_hash"})


def _content_hash(record: Any, *, logical_id: str) -> str:
    payload = {key: value for key, value in asdict(record).items() if key not in _CONTENT_HASH_EXCLUDED_FIELDS}
    payload["logical_id"] = logical_id
    raw = json.dumps(_json_safe(payload), sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def _json_safe(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _authorized_application_root(application_root: Path | None) -> Path:
    """The sole authorization boundary for this service: mirrors, unmodified in
    substance, the identical `_application_root()` check accepted and running in
    hunter.committee.command, hunter.valuation_evidence.command,
    hunter.valuation_methodology.service, hunter.valuation.service, and
    hunter.comparative_valuation.service."""
    if application_root is None:
        configured = os.environ.get(_APPLICATION_ROOT_ENV, "").strip()
        if not configured:
            raise CanonicalMispricingAuthorityError(
                f"{_APPLICATION_ROOT_ENV} must identify the approved Hunter application root"
            )
        application_root = Path(configured).expanduser()
    if not application_root.is_absolute():
        raise CanonicalMispricingAuthorityError(f"{_APPLICATION_ROOT_ENV} must be an absolute path")
    return application_root.resolve()


def _replay_eligible(
    records: Any,
    *,
    effective_as_of: datetime,
    known_by: datetime,
) -> tuple[Any, ...]:
    """Service-owned replay eligibility (ADR 0009: the repository performs no replay
    selection). Returns the cutoff-safe (effective_at <= effective_as_of),
    known-safe (recorded_at/known_at <= known_by), accepted, non-conflicted,
    non-superseded records, deterministically ordered."""
    eligible = [
        item
        for item in records
        if item.effective_at <= effective_as_of
        and item.recorded_at <= known_by
        and item.known_at <= known_by
        and item.quality_state == "accepted"
        and item.conflict_state in {"none", "resolved"}
    ]
    superseded = {item.supersedes_record_id for item in eligible if item.supersedes_record_id is not None}
    current = [item for item in eligible if item.record_id not in superseded]
    current.sort(
        key=lambda item: (item.effective_at, item.recorded_at, item.known_at, item.record_id),
    )
    return tuple(current)


def _strict_known(
    records: Any,
    *,
    effective_as_of: datetime,
    known_by: datetime,
) -> Any | None:
    """Service-owned strict-known selection: the latest replay-eligible record for a
    logical identity, or None when no record is replay-eligible at the cutoff."""
    current = _replay_eligible(records, effective_as_of=effective_as_of, known_by=known_by)
    return current[-1] if current else None


def _unresolved_conflicts(records: tuple[Any, ...]) -> tuple[Any, ...]:
    superseded = {item.supersedes_record_id for item in records if item.supersedes_record_id is not None}
    unresolved = [
        item for item in records if item.record_id not in superseded and item.conflict_state in {"open", "contested"}
    ]
    return tuple(sorted(unresolved, key=lambda item: (item.logical_id, item.effective_at, item.record_id)))


def _identity_matches(estimate_identity: EconomicClaimIdentity, target: EconomicClaimIdentity) -> bool:
    # ADR 0021 mispricing: exact entity/representation match between the fair-value
    # estimate and the mispricing target (identical asset claim, representation).
    return (
        estimate_identity.entity_id == target.entity_id
        and estimate_identity.asset_id == target.asset_id
        and estimate_identity.representation_id == target.representation_id
    )


def _identity_key(identity: EconomicClaimIdentity) -> str:
    return "|".join(
        (
            identity.entity_id,
            identity.economic_claim_id,
            identity.asset_id,
            identity.representation_id,
            identity.token_id,
            identity.chain,
            identity.contract_address,
        )
    )


def _aware(value: Any) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return parsed.astimezone(UTC)
