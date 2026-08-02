from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, replace
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from hunter.asymmetry.models import (
    ASYMMETRY_CONFIDENCE_POLICY_ID,
    ASYMMETRY_CONFIDENCE_POLICY_VERSION,
    ASYMMETRY_SCHEMA_VERSION,
    AUTHORIZING_ADR_REFERENCE,
    AVAILABILITY_STATES,
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
    AsymmetryIntegrityError,
    AsymmetryRepository,
    assessment_snapshot,
    methodology_snapshot,
    scenario_payoff_snapshot,
    scenario_probability_snapshot,
    scenario_set_snapshot,
)
from hunter.market_facts.repository import ObservedMarketFactRepository
from hunter.market_facts.service import ObservedMarketFactService
from hunter.persistence.models import QuerySpec
from hunter.persistence.sql import RepositoryFactory, SessionFactory, create_sqlite_engine
from hunter.persistence.sql.exceptions import PersistenceIdentityConflictError
from hunter.value_capture.models import EconomicClaimIdentity

_APPLICATION_ROOT_ENV = "HUNTER_APPLICATION_ROOT"

# Snapshot types are fixed by the repository module and reused for correction
# authorization and reads.
_METHODOLOGY_SNAPSHOT_TYPE = "asymmetry-methodology-snapshot"
_SCENARIO_SET_SNAPSHOT_TYPE = "asymmetry-scenario-set-snapshot"
_PROBABILITY_SNAPSHOT_TYPE = "asymmetry-scenario-probability-record"
_PAYOFF_SNAPSHOT_TYPE = "asymmetry-scenario-payoff-estimate-record"
_ASSESSMENT_SNAPSHOT_TYPE = "asymmetry-assessment-record"

# ADR 0021 asymmetry contract: the raw asymmetry ratio is expected_positive_payoff /
# expected_negative_payoff with raw range [0, +inf). Fixed, never caller-parameterized.
_RAW_RATIO_FORMULA = RAW_ASYMMETRY_FORMULA

# ADR 0021 asymmetry contract: "Zero downside does not imply infinity: the methodology
# must declare a finite cap or mark the result insufficient." This foundation fixes the
# insufficient-marker treatment: an undefined denominator keeps the input explicitly
# missing and never emits an infinite ratio.
_ZERO_DOWNSIDE_TREATMENT = ZERO_DOWNSIDE_TREATMENT

# ADR 0021 confidence rules: confidence reflects scenario coverage, probability quality,
# payoff uncertainty, dependency/correlation structure, tail coverage, price quality, and
# model dispersion. This foundation decomposes confidence into a scenario-coverage
# factor, a probability-quality factor, a payoff-uncertainty factor, a tail-coverage
# factor, a baseline price-quality factor, and a declared dependency-structure factor, and
# caps overall confidence at the weakest component. Model dispersion is represented by
# the declared payoff-uncertainty factor under the fixed supported payoff model config.
_CONFIDENCE_POLICY_ID = ASYMMETRY_CONFIDENCE_POLICY_ID
_CONFIDENCE_POLICY_VERSION = ASYMMETRY_CONFIDENCE_POLICY_VERSION


class CanonicalAsymmetryAuthorityError(ValueError):
    """Raised for unauthorized or unattested construction and for contradictory
    evidence references. Missing, incompatible, disputed, unknown-time, or unsupported
    prerequisites never raise here: they produce an explicitly unavailable assessment
    (ADR 0020 missingness -- never a partial or degraded-confidence result)."""


class CanonicalAsymmetryService:
    """Service-owned authority boundary for the Asymmetry record families
    (ADR 0021 asymmetry contract; ADR 0020 input contract).

    Authority model is identical to hunter.valuation_methodology, hunter.valuation,
    hunter.comparative_valuation, and hunter.mispricing: HUNTER_APPLICATION_ROOT-gated,
    operator-attested construction; the service computes deterministic outputs from
    already-persisted, already-validated records. Asymmetry is never inferred from
    market providers: the baseline price is a provider-owned ObservedMarketFactRecord
    consumed as an exact-version prerequisite, and the asymmetry assessment, the
    predeclared scenario set, probabilities, and payoff estimates are Hunter-owned
    (ADR 0021 Source-provider eligibility: providers never assign scenario
    probabilities, calculate asymmetry, or normalize inputs).

    Methodology parameters that ADR 0021 fixes -- the raw payoff formula, the
    higher-more-favorable sign convention, the neutral ratio 1, the [0, +inf) raw range,
    the 365-day scenario horizon, the spot-price baseline observation, the exact-decimal
    probability-sum-to-one policy, the predeclared terminal-return rule, the fixed payoff
    unit, the insufficient-marker zero-downside treatment, the asymmetry-scenario
    correlation group, and no calibrated normalization -- are enforced as fixed model
    invariants (see asymmetry.models) and by the fixed constants used below; they are
    never caller-parameterized.

    The record families are produced by exactly these methods:

    - ``persist_asymmetry_methodology`` -> AsymmetryMethodologySnapshot
    - ``persist_scenario_set``          -> ScenarioSetSnapshot
    - ``persist_scenario_probability``  -> ScenarioProbabilityRecord
    - ``persist_scenario_payoff``       -> ScenarioPayoffEstimateRecord
    - ``assess``                        -> AsymmetryAssessmentRecord

    Downstream boundaries are closed: this service cannot calculate Mispricing,
    Opportunity Assessment, Market Validation composition, scoring, weighting,
    recommendation, or ranking (ADR 0021 authority matrix). The assessment references
    rather than re-counts scenario evidence and never consumes a fair-value delta as a
    scenario payoff (ADR 0021 anti-double-counting: the same fair-value delta cannot
    count as both mispricing upside and scenario upside; this foundation never binds a
    fair-value estimate into a scenario).
    """

    def __init__(
        self,
        *,
        repository: AsymmetryRepository,
        market_fact_repository: ObservedMarketFactRepository,
        application_root: Path | None = None,
    ) -> None:
        self.repository = repository
        self.market_fact_repository = market_fact_repository
        self._application_root = _authorized_application_root(application_root)

    # ------------------------------------------------------------------ methodology

    def persist_asymmetry_methodology(
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
    ) -> AsymmetryMethodologySnapshot:
        record = AsymmetryMethodologySnapshot(
            record_id="pending",
            logical_id="pending",
            schema_version=ASYMMETRY_SCHEMA_VERSION,
            semantic_version="1.0.0",
            methodology_id=methodology_id,
            formula_version=formula_version,
            raw_formula=RAW_ASYMMETRY_FORMULA,
            sign_convention=RAW_SIGN_CONVENTION,
            neutral_ratio=NEUTRAL_RATIO,
            raw_range=RAW_RANGE,
            scenario_horizon_days=SCENARIO_HORIZON_DAYS,
            required_quote_currency=required_quote_currency,
            required_payoff_unit=REQUIRED_PAYOFF_UNIT,
            baseline_market_observation_fact_type=BASELINE_MARKET_OBSERVATION_FACT_TYPE,
            probability_sum_policy_id=PROBABILITY_SUM_POLICY_ID,
            probability_sum_policy_version=PROBABILITY_SUM_POLICY_VERSION,
            terminal_valuation_rule=SUPPORTED_TERMINAL_VALUATION_RULE,
            supported_payoff_model_config_version=SUPPORTED_PAYOFF_MODEL_CONFIG_VERSION,
            zero_downside_treatment=_ZERO_DOWNSIDE_TREATMENT,
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

    # ------------------------------------------------------------------ scenario set

    def persist_scenario_set(
        self,
        *,
        identity: EconomicClaimIdentity,
        methodology_record_id: str,
        scenario_set_id: str,
        scenario_ids: tuple[str, ...],
        scenario_types: tuple[str, ...],
        baseline_market_fact_record_id: str,
        quote_currency: str,
        material_omitted_scenario_policy: str,
        uncertainty: str,
        evidence_record_ids: tuple[str, ...],
        source_versions: tuple[str, ...],
        dependency_pairs: tuple[tuple[str, str], ...],
        effective_at: datetime,
        recorded_at: datetime,
        known_at: datetime,
        supersedes_record_id: str | None = None,
        correction_reason: str = "",
    ) -> ScenarioSetSnapshot:
        methodology = self.repository.get_asymmetry_methodology(methodology_record_id)
        if (
            methodology is None
            or methodology.quality_state != "accepted"
            or methodology.conflict_state
            not in {
                "none",
                "resolved",
            }
        ):
            raise CanonicalAsymmetryAuthorityError("scenario set requires an accepted, non-conflicted methodology")
        if methodology.formula_version != SUPPORTED_FORMULA_VERSION:
            raise CanonicalAsymmetryAuthorityError("scenario set requires the supported methodology formula version")
        if methodology.required_quote_currency.strip().lower() != quote_currency.strip().lower():
            raise CanonicalAsymmetryAuthorityError("scenario set quote currency must match the methodology")
        baseline = self.market_fact_repository.record(baseline_market_fact_record_id)
        if baseline is None:
            raise CanonicalAsymmetryAuthorityError("baseline market observation record does not exist")
        if baseline.fact_type != BASELINE_MARKET_OBSERVATION_FACT_TYPE:
            raise CanonicalAsymmetryAuthorityError("baseline market observation must be a spot price")
        if (
            baseline.identity.entity_id != identity.entity_id
            or baseline.identity.representation_id != identity.representation_id
        ):
            raise CanonicalAsymmetryAuthorityError("baseline market observation identity must match the target")
        if (baseline.quote_currency or "").strip().lower() != quote_currency.strip().lower():
            raise CanonicalAsymmetryAuthorityError("baseline market observation quote currency must match")
        if baseline.quality_state != "accepted":
            raise CanonicalAsymmetryAuthorityError(
                "baseline market observation must be accepted and strict-known at the scenario set cutoff"
            )
        strict_selected = ObservedMarketFactService.select_strict_known_fact(
            self.market_fact_repository,
            entity_id=identity.entity_id,
            representation_id=identity.representation_id,
            fact_type=BASELINE_MARKET_OBSERVATION_FACT_TYPE,
            effective_as_of=effective_at,
            known_by=known_at,
            quote_currency=quote_currency,
        )
        if strict_selected is None or strict_selected.record_id != baseline.record_id:
            raise CanonicalAsymmetryAuthorityError(
                "baseline market observation must be accepted and strict-known at the scenario set cutoff"
            )
        record = ScenarioSetSnapshot(
            record_id="pending",
            logical_id="pending",
            schema_version=ASYMMETRY_SCHEMA_VERSION,
            semantic_version="1.0.0",
            identity=identity,
            methodology_record_id=methodology.record_id,
            methodology_record_version=methodology.semantic_version,
            scenario_set_id=scenario_set_id,
            scenario_ids=scenario_ids,
            scenario_types=scenario_types,
            baseline_market_fact_record_id=baseline.record_id,
            baseline_market_fact_record_version=baseline.semantic_version,
            horizon_days=SCENARIO_HORIZON_DAYS,
            quote_currency=quote_currency,
            probability_sum_policy_id=PROBABILITY_SUM_POLICY_ID,
            probability_sum_policy_version=PROBABILITY_SUM_POLICY_VERSION,
            terminal_valuation_rule=SUPPORTED_TERMINAL_VALUATION_RULE,
            payoff_unit=REQUIRED_PAYOFF_UNIT,
            material_omitted_scenario_policy=material_omitted_scenario_policy,
            uncertainty=uncertainty,
            evidence_record_ids=evidence_record_ids,
            source_versions=source_versions,
            dependency_pairs=dependency_pairs,
            effective_at=effective_at,
            recorded_at=recorded_at,
            known_at=known_at,
            content_hash="pending",
            quality_state="accepted",
            conflict_state="none",
            supersedes_record_id=supersedes_record_id,
            correction_reason=correction_reason,
        )
        record = _normalize_scenario_set(record)
        self._persist(_SCENARIO_SET_SNAPSHOT_TYPE, scenario_set_snapshot, record)
        return record

    # ------------------------------------------------------------------ probability

    def persist_scenario_probability(
        self,
        *,
        scenario_set_record_id: str,
        scenario_id: str,
        probability: str,
        probability_uncertainty: str,
        probability_authority: str,
        probability_method: str,
        effective_at: datetime,
        recorded_at: datetime,
        known_at: datetime,
        supersedes_record_id: str | None = None,
        correction_reason: str = "",
    ) -> ScenarioProbabilityRecord:
        scenario_set = self.repository.get_scenario_set(scenario_set_record_id)
        if (
            scenario_set is None
            or scenario_set.quality_state != "accepted"
            or scenario_set.conflict_state
            not in {
                "none",
                "resolved",
            }
        ):
            raise CanonicalAsymmetryAuthorityError("probability requires an accepted, non-conflicted scenario set")
        if scenario_id not in scenario_set.scenario_ids:
            raise CanonicalAsymmetryAuthorityError("scenario_id is not declared by the scenario set")
        record = ScenarioProbabilityRecord(
            record_id="pending",
            logical_id="pending",
            schema_version=ASYMMETRY_SCHEMA_VERSION,
            semantic_version="1.0.0",
            scenario_set_record_id=scenario_set.record_id,
            scenario_set_record_version=scenario_set.semantic_version,
            scenario_id=scenario_id,
            probability=probability,
            probability_uncertainty=probability_uncertainty,
            probability_authority=probability_authority,
            probability_method=probability_method,
            effective_at=effective_at,
            recorded_at=recorded_at,
            known_at=known_at,
            content_hash="pending",
            quality_state="accepted",
            conflict_state="none",
            supersedes_record_id=supersedes_record_id,
            correction_reason=correction_reason,
        )
        record = _normalize_probability(record)
        self._persist(_PROBABILITY_SNAPSHOT_TYPE, scenario_probability_snapshot, record)
        return record

    # ------------------------------------------------------------------ payoff

    def persist_scenario_payoff(
        self,
        *,
        scenario_set_record_id: str,
        scenario_id: str,
        terminal_payoff: str,
        payoff_uncertainty: str,
        evidence_record_ids: tuple[str, ...],
        source_record_id: str,
        source_record_version: str,
        effective_at: datetime,
        recorded_at: datetime,
        known_at: datetime,
        supersedes_record_id: str | None = None,
        correction_reason: str = "",
    ) -> ScenarioPayoffEstimateRecord:
        scenario_set = self.repository.get_scenario_set(scenario_set_record_id)
        if (
            scenario_set is None
            or scenario_set.quality_state != "accepted"
            or scenario_set.conflict_state
            not in {
                "none",
                "resolved",
            }
        ):
            raise CanonicalAsymmetryAuthorityError("payoff requires an accepted, non-conflicted scenario set")
        if scenario_id not in scenario_set.scenario_ids:
            raise CanonicalAsymmetryAuthorityError("scenario_id is not declared by the scenario set")
        payoff = _decimal(terminal_payoff)
        if not payoff.is_finite():
            raise CanonicalAsymmetryAuthorityError("terminal_payoff must be a finite decimal")
        payoff_class = "positive" if payoff > 0 else "negative" if payoff < 0 else "neutral"
        record = ScenarioPayoffEstimateRecord(
            record_id="pending",
            logical_id="pending",
            schema_version=ASYMMETRY_SCHEMA_VERSION,
            semantic_version="1.0.0",
            scenario_set_record_id=scenario_set.record_id,
            scenario_set_record_version=scenario_set.semantic_version,
            scenario_id=scenario_id,
            terminal_payoff=terminal_payoff,
            payoff_class=payoff_class,
            payoff_unit=REQUIRED_PAYOFF_UNIT,
            model_config_version=SUPPORTED_PAYOFF_MODEL_CONFIG_VERSION,
            payoff_uncertainty=payoff_uncertainty,
            evidence_record_ids=evidence_record_ids,
            source_record_id=source_record_id,
            source_record_version=source_record_version,
            effective_at=effective_at,
            recorded_at=recorded_at,
            known_at=known_at,
            content_hash="pending",
            quality_state="accepted",
            conflict_state="none",
            supersedes_record_id=supersedes_record_id,
            correction_reason=correction_reason,
        )
        record = _normalize_payoff(record)
        self._persist(_PAYOFF_SNAPSHOT_TYPE, scenario_payoff_snapshot, record)
        return record

    # ------------------------------------------------------------------ assessment

    def assess(
        self,
        *,
        identity: EconomicClaimIdentity,
        methodology_record_id: str,
        scenario_set_logical_id: str,
        effective_at: datetime,
        recorded_at: datetime,
        known_at: datetime,
        supersedes_record_id: str | None = None,
        correction_reason: str = "",
    ) -> AsymmetryAssessmentRecord:
        methodology = self._authoritative_methodology(
            methodology_record_id, effective_at=effective_at, recorded_at=recorded_at, known_at=known_at
        )
        if methodology is None:
            return self._persist_unavailable_assessment(
                identity=identity,
                methodology_record_id=methodology_record_id,
                methodology_record_version="",
                availability_state="UNAVAILABLE_UNSUPPORTED_METHODOLOGY",
                recorded_at=recorded_at,
                known_at=known_at,
                supersedes_record_id=supersedes_record_id,
                correction_reason=correction_reason,
            )
        if methodology.formula_version != SUPPORTED_FORMULA_VERSION:
            # Unsupported methodology remains explicitly unavailable (ADR 0020
            # "Unsupported methodology must remain explicit unavailable"); it never falls
            # back to a different formula or a fabricated value.
            return self._persist_unavailable_assessment(
                identity=identity,
                methodology_record_id=methodology.record_id,
                methodology_record_version=methodology.semantic_version,
                availability_state="UNAVAILABLE_UNSUPPORTED_METHODOLOGY",
                recorded_at=recorded_at,
                known_at=known_at,
                supersedes_record_id=supersedes_record_id,
                correction_reason=correction_reason,
            )

        scenario_set = self.strict_known_scenario_set(
            effective_as_of=effective_at, known_by=known_at, logical_id=scenario_set_logical_id
        )
        if scenario_set is None:
            return self._persist_unavailable_assessment(
                identity=identity,
                methodology_record_id=methodology.record_id,
                methodology_record_version=methodology.semantic_version,
                availability_state="UNAVAILABLE_NO_SCENARIO_SET",
                recorded_at=recorded_at,
                known_at=known_at,
                supersedes_record_id=supersedes_record_id,
                correction_reason=correction_reason,
            )
        if not _identity_matches(scenario_set.identity, identity):
            return self._persist_unavailable_assessment(
                identity=identity,
                methodology_record_id=methodology.record_id,
                methodology_record_version=methodology.semantic_version,
                availability_state="UNAVAILABLE_IDENTITY_MISMATCH",
                recorded_at=recorded_at,
                known_at=known_at,
                supersedes_record_id=supersedes_record_id,
                correction_reason=correction_reason,
            )
        if scenario_set.quote_currency.strip().lower() != methodology.required_quote_currency.strip().lower():
            return self._persist_unavailable_assessment(
                identity=identity,
                methodology_record_id=methodology.record_id,
                methodology_record_version=methodology.semantic_version,
                availability_state="UNAVAILABLE_UNIT_MISMATCH",
                recorded_at=recorded_at,
                known_at=known_at,
                supersedes_record_id=supersedes_record_id,
                correction_reason=correction_reason,
            )
        if scenario_set.conflict_state in {"open", "contested"}:
            return self._persist_unavailable_assessment(
                identity=identity,
                methodology_record_id=methodology.record_id,
                methodology_record_version=methodology.semantic_version,
                availability_state="UNAVAILABLE_CONFLICTED_INPUTS",
                recorded_at=recorded_at,
                known_at=known_at,
                supersedes_record_id=supersedes_record_id,
                correction_reason=correction_reason,
            )

        baseline = self.market_fact_repository.record(scenario_set.baseline_market_fact_record_id)
        if baseline is None:
            return self._persist_unavailable_assessment(
                identity=identity,
                methodology_record_id=methodology.record_id,
                methodology_record_version=methodology.semantic_version,
                availability_state="UNAVAILABLE_NO_MARKET_OBSERVATION",
                recorded_at=recorded_at,
                known_at=known_at,
                supersedes_record_id=supersedes_record_id,
                correction_reason=correction_reason,
            )
        if (
            baseline.recorded_at > known_at
            or baseline.known_at > known_at
            or baseline.effective_at > effective_at
            or baseline.quality_state != "accepted"
        ):
            # The bound baseline must be strict-known at the assessment cutoff; a fact
            # recorded after the cutoff cannot be selected for replay (ADR 0020).
            return self._persist_unavailable_assessment(
                identity=identity,
                methodology_record_id=methodology.record_id,
                methodology_record_version=methodology.semantic_version,
                availability_state="UNAVAILABLE_STALE_INPUTS",
                recorded_at=recorded_at,
                known_at=known_at,
                supersedes_record_id=supersedes_record_id,
                correction_reason=correction_reason,
            )
        strict_selected = ObservedMarketFactService.select_strict_known_fact(
            self.market_fact_repository,
            entity_id=identity.entity_id,
            representation_id=identity.representation_id,
            fact_type=BASELINE_MARKET_OBSERVATION_FACT_TYPE,
            effective_as_of=effective_at,
            known_by=known_at,
            quote_currency=methodology.required_quote_currency,
        )
        if strict_selected is None or strict_selected.record_id != baseline.record_id:
            # The exact-version baseline bound by the scenario set must be the strict-known
            # selection at the cutoff; a later fact superseding it keeps the earlier
            # baseline stale (ADR 0020 strict-known, no latest/current fallback).
            return self._persist_unavailable_assessment(
                identity=identity,
                methodology_record_id=methodology.record_id,
                methodology_record_version=methodology.semantic_version,
                availability_state="UNAVAILABLE_STALE_INPUTS",
                recorded_at=recorded_at,
                known_at=known_at,
                supersedes_record_id=supersedes_record_id,
                correction_reason=correction_reason,
            )
        if (
            baseline.identity.entity_id != identity.entity_id
            or baseline.identity.representation_id != identity.representation_id
        ):
            return self._persist_unavailable_assessment(
                identity=identity,
                methodology_record_id=methodology.record_id,
                methodology_record_version=methodology.semantic_version,
                availability_state="UNAVAILABLE_IDENTITY_MISMATCH",
                recorded_at=recorded_at,
                known_at=known_at,
                supersedes_record_id=supersedes_record_id,
                correction_reason=correction_reason,
            )
        if (baseline.quote_currency or "").strip().lower() != methodology.required_quote_currency.strip().lower():
            return self._persist_unavailable_assessment(
                identity=identity,
                methodology_record_id=methodology.record_id,
                methodology_record_version=methodology.semantic_version,
                availability_state="UNAVAILABLE_UNIT_MISMATCH",
                recorded_at=recorded_at,
                known_at=known_at,
                supersedes_record_id=supersedes_record_id,
                correction_reason=correction_reason,
            )
        if baseline.conflict_state in {"open", "contested"}:
            return self._persist_unavailable_assessment(
                identity=identity,
                methodology_record_id=methodology.record_id,
                methodology_record_version=methodology.semantic_version,
                availability_state="UNAVAILABLE_CONFLICTED_INPUTS",
                recorded_at=recorded_at,
                known_at=known_at,
                supersedes_record_id=supersedes_record_id,
                correction_reason=correction_reason,
            )

        probabilities: dict[str, ScenarioProbabilityRecord] = {}
        payoffs: dict[str, ScenarioPayoffEstimateRecord] = {}
        for scenario_id in scenario_set.scenario_ids:
            probability = self.strict_known_scenario_probability(
                effective_as_of=effective_at,
                known_by=known_at,
                logical_id=_probability_logical_id(scenario_set.record_id, scenario_id),
            )
            payoff = self.strict_known_scenario_payoff(
                effective_as_of=effective_at,
                known_by=known_at,
                logical_id=_payoff_logical_id(scenario_set.record_id, scenario_id),
            )
            probabilities[scenario_id] = probability
            payoffs[scenario_id] = payoff
        if all(item is None for item in probabilities.values()):
            return self._persist_unavailable_assessment(
                identity=identity,
                methodology_record_id=methodology.record_id,
                methodology_record_version=methodology.semantic_version,
                availability_state="UNAVAILABLE_NO_PROBABILITIES",
                recorded_at=recorded_at,
                known_at=known_at,
                supersedes_record_id=supersedes_record_id,
                correction_reason=correction_reason,
            )
        if all(item is None for item in payoffs.values()):
            return self._persist_unavailable_assessment(
                identity=identity,
                methodology_record_id=methodology.record_id,
                methodology_record_version=methodology.semantic_version,
                availability_state="UNAVAILABLE_NO_PAYOFFS",
                recorded_at=recorded_at,
                known_at=known_at,
                supersedes_record_id=supersedes_record_id,
                correction_reason=correction_reason,
            )
        if any(item is None for item in probabilities.values()) or any(item is None for item in payoffs.values()):
            # Incomplete scenario coverage: every declared scenario must carry a
            # strict-known probability and payoff at the cutoff (ADR 0021 "incomplete
            # scenario coverage ... makes the input missing").
            return self._persist_unavailable_assessment(
                identity=identity,
                methodology_record_id=methodology.record_id,
                methodology_record_version=methodology.semantic_version,
                availability_state="UNAVAILABLE_INCOMPLETE_SCENARIO_COVERAGE",
                recorded_at=recorded_at,
                known_at=known_at,
                supersedes_record_id=supersedes_record_id,
                correction_reason=correction_reason,
            )
        for record in [*probabilities.values(), *payoffs.values()]:
            if record.conflict_state in {"open", "contested"}:
                return self._persist_unavailable_assessment(
                    identity=identity,
                    methodology_record_id=methodology.record_id,
                    methodology_record_version=methodology.semantic_version,
                    availability_state="UNAVAILABLE_CONFLICTED_INPUTS",
                    recorded_at=recorded_at,
                    known_at=known_at,
                    supersedes_record_id=supersedes_record_id,
                    correction_reason=correction_reason,
                )

        # Probabilities must sum to one under the versioned policy (ADR 0021: "sum under
        # a versioned policy"); an unsupported probability vector keeps the input missing.
        total_probability = sum(
            (Decimal(probabilities[scenario_id].probability) for scenario_id in scenario_set.scenario_ids),
            Decimal(0),
        )
        if total_probability != Decimal(1):
            return self._persist_unavailable_assessment(
                identity=identity,
                methodology_record_id=methodology.record_id,
                methodology_record_version=methodology.semantic_version,
                availability_state="UNAVAILABLE_UNSUPPORTED_PROBABILITIES",
                recorded_at=recorded_at,
                known_at=known_at,
                supersedes_record_id=supersedes_record_id,
                correction_reason=correction_reason,
            )

        # Material downside: the predeclared set must include at least one negative
        # payoff; missing material downside keeps the input missing (ADR 0021).
        if not any(Decimal(payoffs[scenario_id].terminal_payoff) < 0 for scenario_id in scenario_set.scenario_ids):
            return self._persist_unavailable_assessment(
                identity=identity,
                methodology_record_id=methodology.record_id,
                methodology_record_version=methodology.semantic_version,
                availability_state="UNAVAILABLE_MISSING_MATERIAL_DOWNSIDE",
                recorded_at=recorded_at,
                known_at=known_at,
                supersedes_record_id=supersedes_record_id,
                correction_reason=correction_reason,
            )

        expected_positive = Decimal(0)
        expected_negative = Decimal(0)
        for scenario_id in scenario_set.scenario_ids:
            probability = Decimal(probabilities[scenario_id].probability)
            payoff = Decimal(payoffs[scenario_id].terminal_payoff)
            if payoff > 0:
                expected_positive += probability * payoff
            elif payoff < 0:
                expected_negative += probability * abs(payoff)
        if expected_negative == 0:
            # Zero downside does not imply infinity: the methodology marks the result
            # insufficient (ADR 0021); the undefined denominator keeps the input missing.
            return self._persist_unavailable_assessment(
                identity=identity,
                methodology_record_id=methodology.record_id,
                methodology_record_version=methodology.semantic_version,
                availability_state="UNAVAILABLE_UNDEFINED_DENOMINATOR",
                recorded_at=recorded_at,
                known_at=known_at,
                supersedes_record_id=supersedes_record_id,
                correction_reason=correction_reason,
            )
        raw_asymmetry_ratio = expected_positive / expected_negative

        # Tail coverage: the probability mass declared under the methodology's tail and
        # missing-scenario policy (scenarios typed downside_tail).
        tail_coverage = Decimal(0)
        for index, scenario_id in enumerate(scenario_set.scenario_ids):
            if scenario_set.scenario_types[index] == "downside_tail":
                tail_coverage += Decimal(probabilities[scenario_id].probability)

        confidence = self._confidence_components(
            methodology=methodology,
            scenario_set=scenario_set,
            probabilities=probabilities,
            payoffs=payoffs,
            baseline=baseline,
            tail_coverage=tail_coverage,
        )

        record = AsymmetryAssessmentRecord(
            record_id="pending",
            logical_id="pending",
            schema_version=ASYMMETRY_SCHEMA_VERSION,
            semantic_version="1.0.0",
            identity=identity,
            methodology_record_id=methodology.record_id,
            methodology_record_version=methodology.semantic_version,
            scenario_set_record_id=scenario_set.record_id,
            scenario_set_record_version=scenario_set.semantic_version,
            scenario_probability_record_ids=tuple(
                probabilities[scenario_id].record_id for scenario_id in scenario_set.scenario_ids
            ),
            scenario_payoff_record_ids=tuple(
                payoffs[scenario_id].record_id for scenario_id in scenario_set.scenario_ids
            ),
            baseline_market_fact_record_id=baseline.record_id,
            baseline_market_fact_record_version=baseline.semantic_version,
            expected_positive_payoff=expected_positive.to_eng_string(),
            expected_negative_payoff=expected_negative.to_eng_string(),
            raw_asymmetry_ratio=raw_asymmetry_ratio.to_eng_string(),
            neutral_ratio=NEUTRAL_RATIO,
            sign_convention=RAW_SIGN_CONVENTION,
            zero_downside_treatment=_ZERO_DOWNSIDE_TREATMENT,
            tail_coverage=tail_coverage.to_eng_string(),
            normalization_status=NORMALIZATION_UNAVAILABLE,
            normalized_value=None,
            confidence_scenario_coverage=confidence["scenario_coverage"],
            confidence_probability_quality=confidence["probability_quality"],
            confidence_payoff_uncertainty=confidence["payoff_uncertainty"],
            confidence_tail_coverage=confidence["tail_coverage"],
            confidence_price_quality=confidence["price_quality"],
            confidence_dependency=confidence["dependency"],
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
        record = _normalize_assessment(record)
        self._persist(_ASSESSMENT_SNAPSHOT_TYPE, assessment_snapshot, record)
        return record

    # ------------------------------------------------------------- read helpers

    def get_asymmetry_methodology(self, record_id: str) -> AsymmetryMethodologySnapshot | None:
        return self.repository.get_asymmetry_methodology(record_id)

    def get_scenario_set(self, record_id: str) -> ScenarioSetSnapshot | None:
        return self.repository.get_scenario_set(record_id)

    def get_scenario_probability(self, record_id: str) -> ScenarioProbabilityRecord | None:
        return self.repository.get_scenario_probability(record_id)

    def get_scenario_payoff(self, record_id: str) -> ScenarioPayoffEstimateRecord | None:
        return self.repository.get_scenario_payoff(record_id)

    def get_assessment(self, record_id: str) -> AsymmetryAssessmentRecord | None:
        return self.repository.get_assessment(record_id)

    def methodology_history(self, logical_id: str) -> tuple[AsymmetryMethodologySnapshot, ...]:
        return self.repository.methodology_history(logical_id)

    def scenario_set_history(self, logical_id: str) -> tuple[ScenarioSetSnapshot, ...]:
        return self.repository.scenario_set_history(logical_id)

    def scenario_probability_history(self, logical_id: str) -> tuple[ScenarioProbabilityRecord, ...]:
        return self.repository.scenario_probability_history(logical_id)

    def scenario_payoff_history(self, logical_id: str) -> tuple[ScenarioPayoffEstimateRecord, ...]:
        return self.repository.scenario_payoff_history(logical_id)

    def assessment_history(self, logical_id: str) -> tuple[AsymmetryAssessmentRecord, ...]:
        return self.repository.assessment_history(logical_id)

    # Strict-known replay is owned by the service (ADR 0009: the repository performs no
    # replay selection -- it only stores and mechanically retrieves). Each method reads
    # the full mechanical history for the logical identity and applies the deterministic
    # replay eligibility: cutoff-safe (effective_at <= effective_as_of), known-safe
    # (recorded_at/known_at <= known_by), accepted quality, non-conflicted, and never a
    # superseded lineage predecessor.

    def strict_known_methodology(
        self, *, effective_as_of: datetime, known_by: datetime, logical_id: str
    ) -> AsymmetryMethodologySnapshot | None:
        return _strict_known(
            self.repository.methodology_history(logical_id),
            effective_as_of=effective_as_of,
            known_by=known_by,
        )

    def strict_known_scenario_set(
        self, *, effective_as_of: datetime, known_by: datetime, logical_id: str
    ) -> ScenarioSetSnapshot | None:
        return _strict_known(
            self.repository.scenario_set_history(logical_id),
            effective_as_of=effective_as_of,
            known_by=known_by,
        )

    def strict_known_scenario_probability(
        self, *, effective_as_of: datetime, known_by: datetime, logical_id: str
    ) -> ScenarioProbabilityRecord | None:
        return _strict_known(
            self.repository.scenario_probability_history(logical_id),
            effective_as_of=effective_as_of,
            known_by=known_by,
        )

    def strict_known_scenario_payoff(
        self, *, effective_as_of: datetime, known_by: datetime, logical_id: str
    ) -> ScenarioPayoffEstimateRecord | None:
        return _strict_known(
            self.repository.scenario_payoff_history(logical_id),
            effective_as_of=effective_as_of,
            known_by=known_by,
        )

    def strict_known_assessment(
        self, *, effective_as_of: datetime, known_by: datetime, logical_id: str
    ) -> AsymmetryAssessmentRecord | None:
        return _strict_known(
            self.repository.assessment_history(logical_id),
            effective_as_of=effective_as_of,
            known_by=known_by,
        )

    def unresolved_methodology_conflicts(self) -> tuple[AsymmetryMethodologySnapshot, ...]:
        return _unresolved_conflicts(self.repository.methodology_records())

    def unresolved_scenario_set_conflicts(self) -> tuple[ScenarioSetSnapshot, ...]:
        return _unresolved_conflicts(self.repository.scenario_set_records())

    def unresolved_probability_conflicts(self) -> tuple[ScenarioProbabilityRecord, ...]:
        return _unresolved_conflicts(self.repository.probability_records())

    def unresolved_payoff_conflicts(self) -> tuple[ScenarioPayoffEstimateRecord, ...]:
        return _unresolved_conflicts(self.repository.payoff_records())

    def unresolved_assessment_conflicts(self) -> tuple[AsymmetryAssessmentRecord, ...]:
        return _unresolved_conflicts(self.repository.assessment_records())

    # ------------------------------------------------------------- internals

    def _authoritative_methodology(
        self,
        methodology_record_id: str,
        *,
        effective_at: datetime,
        recorded_at: datetime,
        known_at: datetime,
    ) -> AsymmetryMethodologySnapshot | None:
        methodology = self.repository.get_asymmetry_methodology(methodology_record_id)
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
        methodology: AsymmetryMethodologySnapshot,
        scenario_set: ScenarioSetSnapshot,
        probabilities: dict[str, ScenarioProbabilityRecord],
        payoffs: dict[str, ScenarioPayoffEstimateRecord],
        baseline: Any,
        tail_coverage: Decimal,
    ) -> dict[str, str]:
        # ADR 0021 confidence rules: confidence reflects scenario coverage, probability
        # quality, payoff uncertainty, dependency/correlation structure, tail coverage,
        # price quality, and model dispersion. Every declared scenario carries a
        # strict-known probability and payoff at this point, so coverage is complete.
        scenario_coverage = Decimal(1)
        probability_quality = min(
            Decimal(1) - Decimal(probabilities[scenario_id].probability_uncertainty)
            for scenario_id in scenario_set.scenario_ids
        )
        payoff_uncertainty = min(
            Decimal(1) - Decimal(payoffs[scenario_id].payoff_uncertainty) for scenario_id in scenario_set.scenario_ids
        )
        tail_factor = max(Decimal(0), min(tail_coverage, Decimal(1)))
        price_quality = Decimal(baseline.confidence)
        if not price_quality.is_finite() or price_quality < 0 or price_quality > 1:
            raise CanonicalAsymmetryAuthorityError("baseline market observation confidence must be between 0 and 1")
        # Dependency/correlation structure: the declared dependency pairs reduce
        # independence; the factor is the share of declared scenarios that are not
        # connected by a declared dependency pair (deterministic, fixed policy).
        declared = set(scenario_set.scenario_ids)
        dependent = {pair[0] for pair in scenario_set.dependency_pairs} | {
            pair[1] for pair in scenario_set.dependency_pairs
        }
        independent = declared - dependent
        dependency = Decimal(len(independent)) / Decimal(len(declared))
        overall = min(
            scenario_coverage, probability_quality, payoff_uncertainty, tail_factor, price_quality, dependency
        )
        return {
            "scenario_coverage": scenario_coverage.to_eng_string(),
            "probability_quality": probability_quality.to_eng_string(),
            "payoff_uncertainty": payoff_uncertainty.to_eng_string(),
            "tail_coverage": tail_factor.to_eng_string(),
            "price_quality": price_quality.to_eng_string(),
            "dependency": dependency.to_eng_string(),
            "overall": overall.to_eng_string(),
        }

    def _persist_unavailable_assessment(
        self,
        *,
        identity: EconomicClaimIdentity,
        methodology_record_id: str,
        methodology_record_version: str,
        availability_state: str,
        recorded_at: datetime,
        known_at: datetime,
        supersedes_record_id: str | None,
        correction_reason: str,
    ) -> AsymmetryAssessmentRecord:
        if availability_state not in AVAILABILITY_STATES:
            raise CanonicalAsymmetryAuthorityError(f"unsupported availability state: {availability_state}")
        record = AsymmetryAssessmentRecord(
            record_id="pending",
            logical_id="pending",
            schema_version=ASYMMETRY_SCHEMA_VERSION,
            semantic_version="1.0.0",
            identity=identity,
            methodology_record_id=methodology_record_id,
            methodology_record_version=methodology_record_version,
            scenario_set_record_id="",
            scenario_set_record_version="",
            scenario_probability_record_ids=(),
            scenario_payoff_record_ids=(),
            baseline_market_fact_record_id="",
            baseline_market_fact_record_version="",
            expected_positive_payoff="",
            expected_negative_payoff="",
            raw_asymmetry_ratio=None,
            neutral_ratio=NEUTRAL_RATIO,
            sign_convention=RAW_SIGN_CONVENTION,
            zero_downside_treatment=_ZERO_DOWNSIDE_TREATMENT,
            tail_coverage="",
            normalization_status=NORMALIZATION_UNAVAILABLE,
            normalized_value=None,
            # Explicit missingness: an unavailable assessment must never encode
            # unavailable as a zero confidence (ADR 0020 "Confidence is unavailable,
            # not zero").
            confidence_scenario_coverage="",
            confidence_probability_quality="",
            confidence_payoff_uncertainty="",
            confidence_tail_coverage="",
            confidence_price_quality="",
            confidence_dependency="",
            confidence="",
            availability_state=availability_state,
            correlation_group=REQUIRED_CORRELATION_GROUP,
            effective_at=recorded_at,
            recorded_at=recorded_at,
            known_at=known_at,
            content_hash="pending",
            quality_state="accepted",
            conflict_state="none",
            supersedes_record_id=supersedes_record_id,
            correction_reason=correction_reason,
        )
        record = _normalize_assessment(record)
        self._persist(_ASSESSMENT_SNAPSHOT_TYPE, assessment_snapshot, record)
        return record

    def _persist(self, snapshot_type: str, snapshot_fn: Any, record: Any) -> None:
        engine = create_sqlite_engine(self.repository.path)
        session = SessionFactory(engine).create()
        try:
            # BEGIN IMMEDIATE before validating -- the hardened transaction pattern from
            # audit finding F1, reused unmodified from hunter.valuation_methodology,
            # hunter.valuation, hunter.comparative_valuation, and hunter.mispricing.
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
            raise AsymmetryIntegrityError(str(exc)) from exc
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
            engine.dispose()


def _authorize_correction(snapshots: Any, *, snapshot_type: str, record: Any) -> None:
    """Mirrors hunter.valuation.service._authorize_correction,
    hunter.valuation_methodology.service._authorize_correction,
    hunter.comparative_valuation.service._authorize_correction, and
    hunter.mispricing.service._authorize_correction unmodified in substance:
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
            raise AsymmetryIntegrityError(
                "a root record already exists for this logical_id; use a correction "
                "(supersedes_record_id) instead of a second independent root record"
            )
        return
    prior_snapshot = snapshots.load(supersedes_record_id)
    if prior_snapshot is None or prior_snapshot.snapshot_type != snapshot_type:
        raise AsymmetryIntegrityError("correction predecessor does not exist")
    prior_payload = prior_snapshot.payload
    if str(prior_payload.get("logical_id")) != logical_id:
        raise AsymmetryIntegrityError("correction must preserve logical_id")
    if _aware(prior_payload["recorded_at"]) >= recorded_at:
        raise AsymmetryIntegrityError("correction recorded_at must follow predecessor")
    if _aware(prior_payload["known_at"]) >= known_at:
        raise AsymmetryIntegrityError("correction known_at must follow predecessor")
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
        raise AsymmetryIntegrityError("branching correction lineage is prohibited")


def _normalize_methodology(record: AsymmetryMethodologySnapshot) -> AsymmetryMethodologySnapshot:
    logical_id = hashlib.sha256(f"asymmetry-methodology|{record.methodology_id}".encode()).hexdigest()
    content_hash = _content_hash(record, logical_id=logical_id)
    record_id = hashlib.sha256(f"{logical_id}:{content_hash}".encode()).hexdigest()
    return replace(record, logical_id=logical_id, content_hash=content_hash, record_id=record_id)


def _normalize_scenario_set(record: ScenarioSetSnapshot) -> ScenarioSetSnapshot:
    evidence_pairs = tuple(sorted(zip(record.evidence_record_ids, record.source_versions, strict=True)))
    record = replace(
        record,
        evidence_record_ids=tuple(item[0] for item in evidence_pairs),
        source_versions=tuple(item[1] for item in evidence_pairs),
    )
    logical_id = hashlib.sha256(
        (
            "asymmetry-scenario-set|"
            + _identity_key(record.identity)
            + "|"
            + record.methodology_record_id
            + "|"
            + record.scenario_set_id
        ).encode()
    ).hexdigest()
    content_hash = _content_hash(record, logical_id=logical_id)
    record_id = hashlib.sha256(f"{logical_id}:{content_hash}".encode()).hexdigest()
    return replace(record, logical_id=logical_id, content_hash=content_hash, record_id=record_id)


def _normalize_probability(record: ScenarioProbabilityRecord) -> ScenarioProbabilityRecord:
    logical_id = _probability_logical_id(record.scenario_set_record_id, record.scenario_id)
    content_hash = _content_hash(record, logical_id=logical_id)
    record_id = hashlib.sha256(f"{logical_id}:{content_hash}".encode()).hexdigest()
    return replace(record, logical_id=logical_id, content_hash=content_hash, record_id=record_id)


def _normalize_payoff(record: ScenarioPayoffEstimateRecord) -> ScenarioPayoffEstimateRecord:
    record = replace(record, evidence_record_ids=tuple(sorted(record.evidence_record_ids)))
    logical_id = _payoff_logical_id(record.scenario_set_record_id, record.scenario_id)
    content_hash = _content_hash(record, logical_id=logical_id)
    record_id = hashlib.sha256(f"{logical_id}:{content_hash}".encode()).hexdigest()
    return replace(record, logical_id=logical_id, content_hash=content_hash, record_id=record_id)


def _normalize_assessment(record: AsymmetryAssessmentRecord) -> AsymmetryAssessmentRecord:
    logical_id = hashlib.sha256(
        (
            "asymmetry-assessment|"
            + _identity_key(record.identity)
            + "|"
            + record.methodology_record_id
            + "|"
            + record.scenario_set_record_id
        ).encode()
    ).hexdigest()
    content_hash = _content_hash(record, logical_id=logical_id)
    record_id = hashlib.sha256(f"{logical_id}:{content_hash}".encode()).hexdigest()
    return replace(record, logical_id=logical_id, content_hash=content_hash, record_id=record_id)


def _probability_logical_id(scenario_set_record_id: str, scenario_id: str) -> str:
    return hashlib.sha256(f"asymmetry-scenario-probability|{scenario_set_record_id}|{scenario_id}".encode()).hexdigest()


def _payoff_logical_id(scenario_set_record_id: str, scenario_id: str) -> str:
    return hashlib.sha256(f"asymmetry-scenario-payoff|{scenario_set_record_id}|{scenario_id}".encode()).hexdigest()


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
    hunter.valuation_methodology.service, hunter.valuation.service,
    hunter.comparative_valuation.service, and hunter.mispricing.service."""
    if application_root is None:
        configured = os.environ.get(_APPLICATION_ROOT_ENV, "").strip()
        if not configured:
            raise CanonicalAsymmetryAuthorityError(
                f"{_APPLICATION_ROOT_ENV} must identify the approved Hunter application root"
            )
        application_root = Path(configured).expanduser()
    if not application_root.is_absolute():
        raise CanonicalAsymmetryAuthorityError(f"{_APPLICATION_ROOT_ENV} must be an absolute path")
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


def _identity_matches(set_identity: EconomicClaimIdentity, target: EconomicClaimIdentity) -> bool:
    # ADR 0021 asymmetry: exact entity/representation match between the predeclared
    # scenario set and the asymmetry target (identical asset claim, representation).
    return (
        set_identity.entity_id == target.entity_id
        and set_identity.asset_id == target.asset_id
        and set_identity.representation_id == target.representation_id
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


def _decimal(value: str) -> Decimal:
    try:
        return Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"invalid decimal value: {value}") from exc


def _aware(value: Any) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return parsed.astimezone(UTC)
