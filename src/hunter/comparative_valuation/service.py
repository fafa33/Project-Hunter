from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from hunter.comparative_valuation.models import (
    AUTHORIZING_ADR_REFERENCE,
    AVAILABILITY_STATES,
    CANDIDATE_SOURCE_AUTHORITY,
    CANONICAL_AUTHORITY_ID,
    COMPARATIVE_VALUATION_SCHEMA_VERSION,
    FULLY_DILUTED_MARKET_FACT_TYPE,
    MINIMUM_ELIGIBLE_PEERS,
    NORMALIZATION_UNAVAILABLE,
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
    ComparativeValuationIntegrityError,
    ComparativeValuationRepository,
    assessment_snapshot,
    decision_snapshot,
    observation_snapshot,
    policy_snapshot,
    universe_snapshot,
)
from hunter.market_facts.repository import ObservedMarketFactRepository
from hunter.market_facts.service import ObservedMarketFactService
from hunter.persistence.models import QuerySpec
from hunter.persistence.sql import RepositoryFactory, SessionFactory, create_sqlite_engine
from hunter.persistence.sql.exceptions import PersistenceIdentityConflictError
from hunter.value_capture.models import EconomicClaimIdentity
from hunter.value_capture.repository import SupplyAndValueCaptureRepository
from hunter.value_capture.service import SupplyAndValueCaptureService

_APPLICATION_ROOT_ENV = "HUNTER_APPLICATION_ROOT"

# Snapshot types are fixed by the repository module and reused for correction
# authorization and reads.
_POLICY_SNAPSHOT_TYPE = "peer-universe-policy"
_UNIVERSE_SNAPSHOT_TYPE = "peer-universe-snapshot"
_DECISION_SNAPSHOT_TYPE = "peer-eligibility-decision"
_OBSERVATION_SNAPSHOT_TYPE = "comparative-metric-observation"
_ASSESSMENT_SNAPSHOT_TYPE = "comparative-valuation-assessment"

# ADR 0026 Reference and Residual Method: the peer reference is the unweighted median
# of the eligible peer multiples; for an even cohort it is the arithmetic mean of the
# two central ordered values. This is the only authorized reference statistic.
_EVEN_COHORT_MEDIAN = "arithmetic-mean-of-two-central"

# ADR 0026 Reference and Residual Method: raw_log_residual = ln(median / target).
_RESIDUAL_FORMULA = "ln(peer_median_multiple / target_multiple)"

# Deterministic peer ordering per ADR 0026: canonical economic-entity ID, representation
# ID, then source-record identity.
_DETERMINISTIC_ORDERING = "entity-id|representation-id|source-record-id"

# Deterministic tie-break for equal keys: the immutable record ID.
_TIE_BREAKING = "record-id"

# Fixed, versioned confidence policy for this foundation's decomposed confidence
# computation (ADR 0026 Confidence).
_CONFIDENCE_POLICY_ID = "comparative-valuation-confidence-v1"
_CONFIDENCE_POLICY_VERSION = "1.0.0"


class CanonicalComparativeValuationAuthorityError(ValueError):
    """Raised when required evidence is missing, incompatible, or otherwise makes a
    comparative multiple or assessment explicitly unavailable (ADR 0026 Missingness) --
    never a partial or degraded-confidence result. Also raised for unauthorized or
    unattested construction."""


class CanonicalComparativeValuationService:
    """Service-owned authority boundary for the five Comparative Valuation record
    families (ADR 0026; ADPR-0003 Option 2C).

    Authority model is identical to hunter.valuation_methodology and
    hunter.valuation: HUNTER_APPLICATION_ROOT-gated, operator-attested construction; the
    service computes deterministic outputs from already-persisted, already-validated
    native evidence. It never re-implements provider signing (there is no external source
    to authenticate here).

    Methodology parameters that ADR 0026 fixes -- 365-day horizon, fully-diluted supply
    basis, equal peer weighting, unweighted-median reference, no trimming, the
    positive-cheaper residual sign convention, no calibrated normalization, and the
    ADR 0021 `valuation-mispricing` correlation group -- are enforced as fixed model
    invariants (see comparative_valuation.models) and by the fixed constants used below;
    they are never caller-parameterized.

    The five immutable record families are produced by exactly these methods:

    - ``persist_peer_policy``         -> PeerUniversePolicyRecord
    - ``persist_peer_universe``       -> PeerUniverseSnapshot (Option 2C)
    - ``persist_eligibility_decision``-> PeerEligibilityDecisionRecord
    - ``persist_metric_observation``  -> ComparativeMetricObservationRecord
    - ``assess``                      -> ComparativeValuationAssessmentRecord

    Downstream boundaries are closed: this service cannot calculate Mispricing,
    Asymmetry, Opportunity Assessment, Market Validation composition, scoring, weighting,
    recommendation, or ranking (ADR 0026 Prohibited Comparisons).
    """

    def __init__(
        self,
        *,
        repository: ComparativeValuationRepository,
        value_capture_repository: SupplyAndValueCaptureRepository,
        market_fact_repository: ObservedMarketFactRepository,
        application_root: Path | None = None,
    ) -> None:
        self.repository = repository
        self.value_capture_repository = value_capture_repository
        self.market_fact_repository = market_fact_repository
        self._application_root = _authorized_application_root(application_root)

    # ------------------------------------------------------------------ policy

    def persist_peer_policy(
        self,
        *,
        policy_id: str,
        supported_entity_class: str,
        entity_class_criteria_id: str,
        entity_class_criteria_version: str,
        taxonomy: str,
        taxonomy_version: str,
        required_lifecycle_state: str,
        required_sector_classification: str,
        required_value_capture_mechanism_types: tuple[str, ...],
        required_revenue_accounting_meaning: str,
        required_native_evidence_types: tuple[str, ...],
        required_quote_currency: str,
        freshness_limit_days: int,
        observation_window_days: int,
        maximum_candidate_universe_size: int,
        effective_at: datetime,
        recorded_at: datetime,
        known_at: datetime,
        supersedes_record_id: str | None = None,
        correction_reason: str = "",
    ) -> PeerUniversePolicyRecord:
        record = PeerUniversePolicyRecord(
            record_id="pending",
            logical_id="pending",
            schema_version=COMPARATIVE_VALUATION_SCHEMA_VERSION,
            semantic_version="1.0.0",
            effective_at=effective_at,
            recorded_at=recorded_at,
            known_at=known_at,
            content_hash="pending",
            quality_state="accepted",
            conflict_state="none",
            policy_id=policy_id,
            supported_entity_class=supported_entity_class,
            entity_class_criteria_id=entity_class_criteria_id,
            entity_class_criteria_version=entity_class_criteria_version,
            taxonomy=taxonomy,
            taxonomy_version=taxonomy_version,
            required_lifecycle_state=required_lifecycle_state,
            required_sector_classification=required_sector_classification,
            required_value_capture_mechanism_types=tuple(required_value_capture_mechanism_types),
            required_revenue_accounting_meaning=required_revenue_accounting_meaning,
            required_native_evidence_types=tuple(required_native_evidence_types),
            required_quote_currency=required_quote_currency,
            required_supply_basis="fully_diluted_supply",
            accounting_horizon_days=365,
            observation_window_days=observation_window_days,
            freshness_limit_days=freshness_limit_days,
            minimum_eligible_peers=MINIMUM_ELIGIBLE_PEERS,
            equal_peer_weighting=True,
            reference_statistic=REFERENCE_STATISTIC,
            minimum_decision_coverage_percent=100,
            minimum_observation_coverage_percent=100,
            deterministic_ordering=_DETERMINISTIC_ORDERING,
            tie_breaking=_TIE_BREAKING,
            outlier_treatment="none",
            residual_sign_convention="positive-cheaper",
            normalization_policy_id=None,
            calibration_policy_id=None,
            correlation_group=REQUIRED_CORRELATION_GROUP,
            maximum_candidate_universe_size=maximum_candidate_universe_size,
            confidence_policy_id=_CONFIDENCE_POLICY_ID,
            confidence_policy_version=_CONFIDENCE_POLICY_VERSION,
            candidate_source_authority=CANDIDATE_SOURCE_AUTHORITY,
            authorizing_adr_reference=AUTHORIZING_ADR_REFERENCE,
            authorized_by=CANONICAL_AUTHORITY_ID,
            supersedes_record_id=supersedes_record_id,
            correction_reason=correction_reason,
        )
        record = _normalize_policy(record)
        self._persist(_POLICY_SNAPSHOT_TYPE, policy_snapshot, record)
        return record

    # ------------------------------------------------------------- peer universe

    def persist_peer_universe(
        self,
        *,
        identity: EconomicClaimIdentity,
        policy_record_id: str,
        cutoff: datetime,
        candidates: tuple[PeerCandidate, ...],
        recorded_at: datetime,
        known_at: datetime,
        supersedes_record_id: str | None = None,
        correction_reason: str = "",
    ) -> PeerUniverseSnapshot:
        policy = self.repository.get_peer_policy(policy_record_id)
        if policy is None:
            raise CanonicalComparativeValuationAuthorityError(
                f"referenced peer universe policy does not exist: {policy_record_id}"
            )
        if policy.quality_state != "accepted" or policy.conflict_state not in {"none", "resolved"}:
            raise CanonicalComparativeValuationAuthorityError(
                "non-authoritative peer universe policy cannot support a peer universe"
            )
        if len(candidates) > policy.maximum_candidate_universe_size:
            raise CanonicalComparativeValuationAuthorityError(
                "candidate set exceeds the predeclared maximum candidate universe size"
            )
        ordered_candidates = tuple(sorted(candidates, key=_candidate_sort_key))
        fingerprint = _construction_fingerprint(identity, policy.record_id, cutoff, ordered_candidates)
        record = PeerUniverseSnapshot(
            record_id="pending",
            logical_id="pending",
            schema_version=COMPARATIVE_VALUATION_SCHEMA_VERSION,
            semantic_version="1.0.0",
            identity=identity,
            policy_record_id=policy.record_id,
            policy_record_version=policy.semantic_version,
            cutoff=cutoff,
            candidates=ordered_candidates,
            deterministic_construction_fingerprint=fingerprint,
            effective_at=cutoff,
            recorded_at=recorded_at,
            known_at=known_at,
            content_hash="pending",
            quality_state="accepted",
            conflict_state="none",
            supersedes_record_id=supersedes_record_id,
            correction_reason=correction_reason,
        )
        record = _normalize_universe(record)
        self._persist(_UNIVERSE_SNAPSHOT_TYPE, universe_snapshot, record)
        return record

    # -------------------------------------------------------- eligibility decisions

    def persist_eligibility_decision(
        self,
        *,
        target_identity: EconomicClaimIdentity,
        candidate_identity: EconomicClaimIdentity,
        policy_record_id: str,
        universe_snapshot_id: str,
        effective_at: datetime,
        recorded_at: datetime,
        known_at: datetime,
        supersedes_record_id: str | None = None,
        correction_reason: str = "",
    ) -> PeerEligibilityDecisionRecord:
        policy = self._authoritative_policy(
            policy_record_id, effective_at=effective_at, recorded_at=recorded_at, known_at=known_at
        )
        universe = self.repository.get_peer_universe(universe_snapshot_id)
        if universe is None or universe.identity != target_identity:
            raise CanonicalComparativeValuationAuthorityError(
                "referenced peer universe does not exist or does not bind this target"
            )
        if universe.policy_record_id != policy.record_id:
            raise CanonicalComparativeValuationAuthorityError(
                "peer universe does not bind the referenced policy version"
            )
        candidate = _candidate_in_universe(universe, candidate_identity)
        if candidate is None:
            raise CanonicalComparativeValuationAuthorityError(
                "candidate is not a member of the referenced peer universe snapshot"
            )

        dimensions, decision, missingness = self._eligibility_dimensions(
            policy=policy,
            universe=universe,
            candidate=candidate,
            target_identity=target_identity,
            effective_at=effective_at,
            known_at=known_at,
        )
        included = decision == "included"
        evidence_refs = (
            self._evidence_references_for_candidate(
                policy=policy,
                candidate=candidate,
                effective_at=effective_at,
                known_at=known_at,
            )
            if included
            else _empty_evidence_refs()
        )
        confidence = evidence_refs["confidence"] if included else ""
        record = PeerEligibilityDecisionRecord(
            record_id="pending",
            logical_id="pending",
            schema_version=COMPARATIVE_VALUATION_SCHEMA_VERSION,
            semantic_version="1.0.0",
            target_identity=target_identity,
            candidate_identity=candidate_identity,
            policy_record_id=policy.record_id,
            policy_record_version=policy.semantic_version,
            universe_snapshot_id=universe.record_id,
            decision=decision,
            dimension_decisions=tuple(dimensions),
            reason=_decision_reason(decision, dimensions, missingness),
            rule_record_id=evidence_refs["rule_record_id"],
            rule_record_version=evidence_refs["rule_record_version"],
            supply_record_id=evidence_refs["supply_record_id"],
            supply_record_version=evidence_refs["supply_record_version"],
            evidence_record_id=evidence_refs["evidence_record_id"],
            evidence_record_version=evidence_refs["evidence_record_version"],
            market_fact_record_id=evidence_refs["market_fact_record_id"],
            market_fact_record_version=evidence_refs["market_fact_record_version"],
            confidence_component=confidence,
            missingness_state=missingness,
            effective_at=effective_at,
            recorded_at=recorded_at,
            known_at=known_at,
            content_hash="pending",
            quality_state="accepted",
            conflict_state="none",
            supersedes_record_id=supersedes_record_id,
            correction_reason=correction_reason,
        )
        record = _normalize_decision(record)
        self._persist(_DECISION_SNAPSHOT_TYPE, decision_snapshot, record)
        return record

    # ------------------------------------------------------- metric observations

    def persist_metric_observation(
        self,
        *,
        identity: EconomicClaimIdentity,
        policy_record_id: str,
        universe_snapshot_id: str,
        evidence_type: str,
        rule_type: str,
        effective_at: datetime,
        recorded_at: datetime,
        known_at: datetime,
        supersedes_record_id: str | None = None,
        correction_reason: str = "",
    ) -> ComparativeMetricObservationRecord:
        policy = self._authoritative_policy(
            policy_record_id, effective_at=effective_at, recorded_at=recorded_at, known_at=known_at
        )
        universe = self.repository.get_peer_universe(universe_snapshot_id)
        if universe is None:
            raise CanonicalComparativeValuationAuthorityError("referenced peer universe does not exist")
        # The metric observation may be recorded for the universe's bound target identity
        # or for any candidate economic entity that is a member of the universe snapshot
        # (ADR 0026 Metric Observations: the complete ordered distribution requires
        # observations for the target and every eligible peer).
        if universe.identity != identity and _candidate_in_universe(universe, identity) is None:
            raise CanonicalComparativeValuationAuthorityError(
                "metric observation identity is not the target or a member of the referenced peer universe"
            )
        if universe.policy_record_id != policy.record_id:
            raise CanonicalComparativeValuationAuthorityError(
                "peer universe does not bind the referenced policy version"
            )
        if evidence_type not in policy.required_native_evidence_types:
            raise CanonicalComparativeValuationAuthorityError(
                "evidence_type is not a required native evidence type declared by the peer policy"
            )
        if rule_type not in policy.required_value_capture_mechanism_types:
            raise CanonicalComparativeValuationAuthorityError(
                "rule_type is not a required value-capture mechanism type declared by the peer policy"
            )

        numerator = self._strict_known_market_fact(
            policy=policy,
            identity=identity,
            effective_at=effective_at,
            known_at=known_at,
        )
        if numerator is None:
            raise CanonicalComparativeValuationAuthorityError(
                "no strict-known fully-diluted observed market value is available for this identity"
            )
        flow_evidence = self._strict_known_flow_evidence(
            policy=policy,
            identity=identity,
            evidence_type=evidence_type,
            rule_type=rule_type,
            effective_at=effective_at,
            known_at=known_at,
        )
        if flow_evidence is None:
            raise CanonicalComparativeValuationAuthorityError(
                "no strict-known native attributable value-capture evidence is available for this identity"
            )
        evidence, rule = flow_evidence
        if evidence.unit is None or evidence.unit.strip().lower() != policy.required_quote_currency:
            raise CanonicalComparativeValuationAuthorityError(
                "evidence unit does not match the peer policy quote currency; currency conversion is not authorized"
            )
        horizon_start = effective_at - timedelta(days=policy.accounting_horizon_days)
        if not (horizon_start <= evidence.accounting_period_start and evidence.accounting_period_end <= effective_at):
            raise CanonicalComparativeValuationAuthorityError(
                "evidence accounting period is not fully contained within the 365-day lookback window"
            )
        accounting_period_days = (evidence.accounting_period_end - evidence.accounting_period_start).days
        if accounting_period_days != policy.accounting_horizon_days:
            raise CanonicalComparativeValuationAuthorityError(
                "evidence accounting period length does not match the 365-day horizon; no annualization is authorized"
            )
        if rule.rate_or_proportion is None:
            raise CanonicalComparativeValuationAuthorityError(
                "value-capture rule does not declare rate_or_proportion; attributable flow cannot be determined "
                "with certainty (ADR 0026 Missingness)"
            )
        raw_flow = Decimal(evidence.amount or "0")
        if not raw_flow.is_finite() or raw_flow <= 0:
            raise CanonicalComparativeValuationAuthorityError(
                "native attributable value-capture flow must be strictly positive"
            )
        attributable_flow = raw_flow * Decimal(rule.rate_or_proportion)
        if not attributable_flow.is_finite() or attributable_flow <= 0:
            raise CanonicalComparativeValuationAuthorityError(
                "native attributable value-capture flow must be strictly positive"
            )
        fully_diluted_value = Decimal(numerator.value)
        if not fully_diluted_value.is_finite() or fully_diluted_value <= 0:
            raise CanonicalComparativeValuationAuthorityError(
                "fully diluted observed market value must be strictly positive"
            )
        multiple = fully_diluted_value / attributable_flow
        pathway = rule.mechanism_policy_id
        fingerprint = _calculation_fingerprint(
            numerator=numerator,
            evidence=evidence,
            rule=rule,
            multiple=multiple,
            policy=policy,
            pathway=pathway,
        )
        record = ComparativeMetricObservationRecord(
            record_id="pending",
            logical_id="pending",
            schema_version=COMPARATIVE_VALUATION_SCHEMA_VERSION,
            semantic_version="1.0.0",
            identity=identity,
            policy_record_id=policy.record_id,
            policy_record_version=policy.semantic_version,
            universe_snapshot_id=universe.record_id,
            numerator_record_id=numerator.record_id,
            numerator_record_version=numerator.semantic_version,
            numerator_value=numerator.value,
            numerator_currency=numerator.quote_currency or policy.required_quote_currency,
            denominator_record_id=evidence.record_id,
            denominator_record_version=evidence.semantic_version,
            denominator_value=attributable_flow.to_eng_string(),
            denominator_currency=policy.required_quote_currency,
            rule_record_id=rule.record_id,
            rule_record_version=rule.semantic_version,
            value_capture_pathway_id=pathway,
            comparative_multiple=multiple.to_eng_string(),
            currency=policy.required_quote_currency,
            horizon_days=policy.accounting_horizon_days,
            supply_basis="fully_diluted_supply",
            # Fix 5: raw-observation availability is separate from normalization
            # availability. A valid raw observation is AVAILABLE even though the
            # normalized output is unavailable; the model enforces
            # normalization_status == "unavailable".
            availability_state="AVAILABLE",
            normalization_status=NORMALIZATION_UNAVAILABLE,
            conflict_state="none",
            calculation_fingerprint=fingerprint,
            effective_at=effective_at,
            recorded_at=recorded_at,
            known_at=known_at,
            content_hash="pending",
            quality_state="accepted",
            supersedes_record_id=supersedes_record_id,
            correction_reason=correction_reason,
        )
        record = _normalize_observation(record)
        self._persist(_OBSERVATION_SNAPSHOT_TYPE, observation_snapshot, record)
        return record

    # ------------------------------------------------------------- assessments

    def assess(
        self,
        *,
        identity: EconomicClaimIdentity,
        policy_record_id: str,
        universe_snapshot_id: str,
        evidence_type: str,
        rule_type: str,
        effective_at: datetime,
        recorded_at: datetime,
        known_at: datetime,
        supersedes_record_id: str | None = None,
        correction_reason: str = "",
    ) -> ComparativeValuationAssessmentRecord:
        policy = self._authoritative_policy(
            policy_record_id, effective_at=effective_at, recorded_at=recorded_at, known_at=known_at
        )
        universe = self.repository.get_peer_universe(universe_snapshot_id)
        if universe is None or universe.identity != identity:
            raise CanonicalComparativeValuationAuthorityError(
                "referenced peer universe does not exist or does not bind this target"
            )
        if universe.policy_record_id != policy.record_id:
            raise CanonicalComparativeValuationAuthorityError(
                "peer universe does not bind the referenced policy version"
            )
        if not universe.candidates:
            return self._persist_unavailable_assessment(
                identity=identity,
                policy=policy,
                universe=universe,
                availability_state="UNAVAILABLE_NO_CANDIDATES",
                recorded_at=recorded_at,
                known_at=known_at,
                supersedes_record_id=supersedes_record_id,
                correction_reason=correction_reason,
            )

        decisions = _replay_eligible(
            self.repository.decisions_for_universe(universe.record_id),
            effective_as_of=effective_at,
            known_by=known_at,
        )
        candidate_representations = {candidate.identity.representation_id for candidate in universe.candidates}
        decision_representations = {item.candidate_identity.representation_id for item in decisions}
        if decision_representations != candidate_representations or len(decisions) != len(decision_representations):
            # Complete, unique decision coverage: every candidate in the universe snapshot
            # has exactly one current, replay-eligible eligibility decision, and no
            # decision exists for a foreign or duplicated candidate (ADR 0026 coverage;
            # peer uniqueness enforced by set equality, never count-only).
            return self._persist_unavailable_assessment(
                identity=identity,
                policy=policy,
                universe=universe,
                availability_state="UNAVAILABLE_INSUFFICIENT_ELIGIBLE_PEERS",
                recorded_at=recorded_at,
                known_at=known_at,
                supersedes_record_id=supersedes_record_id,
                correction_reason=correction_reason,
            )
        indeterminate = [item for item in decisions if item.decision == "indeterminate"]
        if indeterminate:
            # An indeterminate decision fails the complete-decision coverage gate.
            return self._persist_unavailable_assessment(
                identity=identity,
                policy=policy,
                universe=universe,
                availability_state="UNAVAILABLE_INSUFFICIENT_ELIGIBLE_PEERS",
                recorded_at=recorded_at,
                known_at=known_at,
                supersedes_record_id=supersedes_record_id,
                correction_reason=correction_reason,
            )
        included = [item for item in decisions if item.decision == "included"]
        eligible_peers = tuple(sorted(included, key=lambda item: _decision_sort_key(item)))
        if len(eligible_peers) < MINIMUM_ELIGIBLE_PEERS:
            return self._persist_unavailable_assessment(
                identity=identity,
                policy=policy,
                universe=universe,
                availability_state="UNAVAILABLE_INSUFFICIENT_ELIGIBLE_PEERS",
                recorded_at=recorded_at,
                known_at=known_at,
                supersedes_record_id=supersedes_record_id,
                correction_reason=correction_reason,
            )

        observations = _replay_eligible(
            self.repository.observations_for_universe(universe.record_id),
            effective_as_of=effective_at,
            known_by=known_at,
        )
        target_observations = [item for item in observations if item.identity == identity]
        if len(target_observations) != 1:
            return self._persist_unavailable_assessment(
                identity=identity,
                policy=policy,
                universe=universe,
                availability_state="UNAVAILABLE_INCOMPATIBLE_COORDINATES",
                recorded_at=recorded_at,
                known_at=known_at,
                supersedes_record_id=supersedes_record_id,
                correction_reason=correction_reason,
            )
        target_observation = target_observations[0]
        eligible_peer_representations = {item.candidate_identity.representation_id for item in eligible_peers}
        peer_observations = tuple(
            sorted(
                (
                    observation
                    for observation in observations
                    if observation.identity.representation_id in eligible_peer_representations
                ),
                key=lambda item: _observation_sort_key(item),
            )
        )
        peer_observation_representations = {item.identity.representation_id for item in peer_observations}
        if peer_observation_representations != eligible_peer_representations or len(peer_observations) != len(
            peer_observation_representations
        ):
            # Complete, unique observation coverage: every included eligible peer has
            # exactly one current, replay-eligible, compatible metric observation
            # (ADR 0026 coverage; peer uniqueness enforced by set equality).
            return self._persist_unavailable_assessment(
                identity=identity,
                policy=policy,
                universe=universe,
                availability_state="UNAVAILABLE_INSUFFICIENT_ELIGIBLE_PEERS",
                recorded_at=recorded_at,
                known_at=known_at,
                supersedes_record_id=supersedes_record_id,
                correction_reason=correction_reason,
            )
        conflicted = [item for item in peer_observations if item.conflict_state in {"open", "contested"}]
        if conflicted or target_observation.conflict_state in {"open", "contested"}:
            return self._persist_unavailable_assessment(
                identity=identity,
                policy=policy,
                universe=universe,
                availability_state="UNAVAILABLE_CONFLICTED_INPUTS",
                recorded_at=recorded_at,
                known_at=known_at,
                supersedes_record_id=supersedes_record_id,
                correction_reason=correction_reason,
            )

        peer_distribution = tuple(_ordered_peer_multiple_distribution(peer_observations))
        peer_median = _unweighted_median(peer_distribution)
        target_multiple = Decimal(target_observation.comparative_multiple)
        residual = _raw_log_residual(peer_median, target_multiple)

        confidence = self._confidence_components(
            policy=policy,
            universe=universe,
            decisions=tuple(eligible_peers),
            observations=tuple((target_observation,) + peer_observations),
            peer_median=peer_median,
            target_multiple=target_multiple,
            effective_at=effective_at,
            known_at=known_at,
        )

        record = ComparativeValuationAssessmentRecord(
            record_id="pending",
            logical_id="pending",
            schema_version=COMPARATIVE_VALUATION_SCHEMA_VERSION,
            semantic_version="1.0.0",
            identity=identity,
            policy_record_id=policy.record_id,
            policy_record_version=policy.semantic_version,
            universe_snapshot_id=universe.record_id,
            included_decision_record_ids=tuple(item.record_id for item in eligible_peers),
            target_observation_record_id=target_observation.record_id,
            peer_observation_record_ids=tuple(item.record_id for item in peer_observations),
            peer_multiple_distribution=tuple(value.to_eng_string() for value in peer_distribution),
            peer_median_multiple=peer_median.to_eng_string(),
            target_multiple=target_multiple.to_eng_string(),
            raw_log_residual=residual.to_eng_string(),
            residual_sign_convention="positive-cheaper",
            normalization_policy_id=None,
            normalization_status=NORMALIZATION_UNAVAILABLE,
            normalized_value=None,
            cohort_decision_coverage=_coverage_string(len(decisions), len(universe.candidates)),
            cohort_observation_coverage=_coverage_string(len(peer_observations), len(eligible_peers)),
            confidence_universe_coverage=confidence["universe"],
            confidence_eligibility_evidence=confidence["eligibility"],
            confidence_metric_evidence=confidence["metric"],
            confidence_coordinate_compatibility=confidence["coordinates"],
            confidence_peer_set_cardinality=confidence["cardinality"],
            confidence_methodology_calibration=confidence["calibration"],
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

    def get_peer_policy(self, record_id: str) -> PeerUniversePolicyRecord | None:
        return self.repository.get_peer_policy(record_id)

    def get_peer_universe(self, record_id: str) -> PeerUniverseSnapshot | None:
        return self.repository.get_peer_universe(record_id)

    def get_eligibility_decision(self, record_id: str) -> PeerEligibilityDecisionRecord | None:
        return self.repository.get_eligibility_decision(record_id)

    def get_metric_observation(self, record_id: str) -> ComparativeMetricObservationRecord | None:
        return self.repository.get_metric_observation(record_id)

    def get_assessment(self, record_id: str) -> ComparativeValuationAssessmentRecord | None:
        return self.repository.get_assessment(record_id)

    def policy_history(self, logical_id: str) -> tuple[PeerUniversePolicyRecord, ...]:
        return self.repository.policy_history(logical_id)

    def universe_history(self, logical_id: str) -> tuple[PeerUniverseSnapshot, ...]:
        return self.repository.universe_history(logical_id)

    def decision_history(self, logical_id: str) -> tuple[PeerEligibilityDecisionRecord, ...]:
        return self.repository.decision_history(logical_id)

    def observation_history(self, logical_id: str) -> tuple[ComparativeMetricObservationRecord, ...]:
        return self.repository.observation_history(logical_id)

    def assessment_history(self, logical_id: str) -> tuple[ComparativeValuationAssessmentRecord, ...]:
        return self.repository.assessment_history(logical_id)

    # Strict-known replay is owned by the service (ADR 0009: the repository performs no
    # replay selection -- it only stores and mechanically retrieves). Each method reads
    # the full mechanical history for the logical identity and applies the deterministic
    # replay eligibility: cutoff-safe (effective_at <= effective_as_of), known-safe
    # (recorded_at/known_at <= known_by), accepted quality, non-conflicted, and never a
    # superseded lineage predecessor.

    def strict_known_policy(
        self, *, effective_as_of: datetime, known_by: datetime, logical_id: str
    ) -> PeerUniversePolicyRecord | None:
        return _strict_known(
            self.repository.policy_history(logical_id),
            effective_as_of=effective_as_of,
            known_by=known_by,
        )

    def strict_known_universe(
        self, *, effective_as_of: datetime, known_by: datetime, logical_id: str
    ) -> PeerUniverseSnapshot | None:
        return _strict_known(
            self.repository.universe_history(logical_id),
            effective_as_of=effective_as_of,
            known_by=known_by,
        )

    def strict_known_decision(
        self, *, effective_as_of: datetime, known_by: datetime, logical_id: str
    ) -> PeerEligibilityDecisionRecord | None:
        return _strict_known(
            self.repository.decision_history(logical_id),
            effective_as_of=effective_as_of,
            known_by=known_by,
        )

    def strict_known_observation(
        self, *, effective_as_of: datetime, known_by: datetime, logical_id: str
    ) -> ComparativeMetricObservationRecord | None:
        return _strict_known(
            self.repository.observation_history(logical_id),
            effective_as_of=effective_as_of,
            known_by=known_by,
        )

    def strict_known_assessment(
        self, *, effective_as_of: datetime, known_by: datetime, logical_id: str
    ) -> ComparativeValuationAssessmentRecord | None:
        return _strict_known(
            self.repository.assessment_history(logical_id),
            effective_as_of=effective_as_of,
            known_by=known_by,
        )

    def unresolved_policy_conflicts(self) -> tuple[PeerUniversePolicyRecord, ...]:
        return _unresolved_conflicts(self.repository.policy_records())

    def unresolved_universe_conflicts(self) -> tuple[PeerUniverseSnapshot, ...]:
        return _unresolved_conflicts(self.repository.universe_records())

    def unresolved_decision_conflicts(self) -> tuple[PeerEligibilityDecisionRecord, ...]:
        return _unresolved_conflicts(self.repository.decision_records())

    def unresolved_observation_conflicts(self) -> tuple[ComparativeMetricObservationRecord, ...]:
        return _unresolved_conflicts(self.repository.observation_records())

    def unresolved_assessment_conflicts(self) -> tuple[ComparativeValuationAssessmentRecord, ...]:
        return _unresolved_conflicts(self.repository.assessment_records())

    # ------------------------------------------------------------- internals

    def _authoritative_policy(
        self,
        policy_record_id: str,
        *,
        effective_at: datetime,
        recorded_at: datetime,
        known_at: datetime,
    ) -> PeerUniversePolicyRecord:
        policy = self.repository.get_peer_policy(policy_record_id)
        if policy is None:
            raise CanonicalComparativeValuationAuthorityError(
                f"referenced peer universe policy does not exist: {policy_record_id}"
            )
        if policy.quality_state != "accepted" or policy.conflict_state not in {"none", "resolved"}:
            raise CanonicalComparativeValuationAuthorityError(
                "non-authoritative peer universe policy cannot support comparative valuation"
            )
        if policy.recorded_at > recorded_at or policy.known_at > known_at or policy.effective_at > effective_at:
            raise CanonicalComparativeValuationAuthorityError(
                "peer universe policy is not strict-known at the requested cutoff"
            )
        return policy

    def _eligibility_dimensions(
        self,
        *,
        policy: PeerUniversePolicyRecord,
        universe: PeerUniverseSnapshot,
        candidate: PeerCandidate,
        target_identity: EconomicClaimIdentity,
        effective_at: datetime,
        known_at: datetime,
    ) -> tuple[tuple[DimensionDecision, ...], str, str]:
        dimensions: list[DimensionDecision] = []

        def coordinate(name: str, candidate_value: str, required: str) -> None:
            passed = candidate_value == required
            dimensions.append(
                DimensionDecision(
                    coordinate=name,
                    passed=passed,
                    reason=(
                        f"candidate {candidate_value!r} matches required {required!r}"
                        if passed
                        else f"candidate {candidate_value!r} does not match required {required!r}"
                    ),
                    evidence_reference=(policy.authorizing_adr_reference),
                )
            )

        def coordinate_member(name: str, candidate_value: str, required_values: tuple[str, ...]) -> None:
            # Fix 7: deterministic authority-compliant selection -- the candidate matches
            # when its declared value is a member of the policy's required set. Set
            # membership is order-independent, so no "_single_or_first" shortcut is
            # needed and multi-value policies are handled deterministically.
            passed = candidate_value in required_values
            dimensions.append(
                DimensionDecision(
                    coordinate=name,
                    passed=passed,
                    reason=(
                        f"candidate {candidate_value!r} is a member of required set {required_values!r}"
                        if passed
                        else f"candidate {candidate_value!r} is not a member of required set {required_values!r}"
                    ),
                    evidence_reference=(policy.authorizing_adr_reference),
                )
            )

        coordinate("lifecycle_state", candidate.lifecycle_state, policy.required_lifecycle_state)
        coordinate("sector_classification", candidate.sector_classification, policy.required_sector_classification)
        coordinate_member(
            "value_capture_mechanism_type",
            candidate.value_capture_mechanism_type,
            policy.required_value_capture_mechanism_types,
        )
        coordinate(
            "revenue_accounting_meaning",
            candidate.revenue_accounting_meaning,
            policy.required_revenue_accounting_meaning,
        )
        coordinate_member(
            "native_evidence_type",
            candidate.native_evidence_type,
            policy.required_native_evidence_types,
        )
        coordinate("quote_currency", candidate.quote_currency, policy.required_quote_currency)
        coordinate("supply_basis", candidate.supply_basis, policy.required_supply_basis)

        missingness: list[str] = []
        evidence = self._strict_known_flow_evidence(
            policy=policy,
            identity=candidate.identity,
            evidence_type=candidate.native_evidence_type,
            rule_type=candidate.value_capture_mechanism_type,
            effective_at=effective_at,
            known_at=known_at,
            missingness=missingness,
        )
        dimensions.append(
            DimensionDecision(
                coordinate="native_fundamental_evidence",
                passed=True if evidence is not None else None,
                reason=(
                    "strict-known native FundamentalEvidenceRecord available for the 365-day window"
                    if evidence is not None
                    else "missing strict-known native FundamentalEvidenceRecord"
                ),
                evidence_reference=(evidence[0].record_id if evidence is not None else ""),
            )
        )
        supply = self._strict_known_supply(
            policy=policy,
            identity=candidate.identity,
            effective_at=effective_at,
            known_at=known_at,
            missingness=missingness,
        )
        supply_available = supply is not None
        dimensions.append(
            DimensionDecision(
                coordinate="supply_basis",
                passed=True if supply_available else None,
                reason=(
                    "strict-known fully-diluted SupplyBasisSnapshot available"
                    if supply_available
                    else "missing strict-known fully-diluted SupplyBasisSnapshot"
                ),
                evidence_reference=(supply.record_id if supply_available else ""),
            )
        )
        market_fact = self._strict_known_market_fact(
            policy=policy,
            identity=candidate.identity,
            effective_at=effective_at,
            known_at=known_at,
            missingness=missingness,
        )
        market_fact_available = market_fact is not None
        dimensions.append(
            DimensionDecision(
                coordinate="fully_diluted_market_value",
                passed=True if market_fact_available else None,
                reason=(
                    "strict-known fully_diluted_valuation ObservedMarketFactRecord available"
                    if market_fact_available
                    else "missing strict-known fully_diluted_valuation ObservedMarketFactRecord"
                ),
                evidence_reference=(market_fact.record_id if market_fact_available else ""),
            )
        )

        hard_failures = [item for item in dimensions if item.passed is False]
        missing = [item for item in dimensions if item.passed is None]
        if hard_failures:
            decision = "excluded"
            missingness_state = "excluded:" + "|".join(item.coordinate for item in hard_failures)
        elif missing:
            decision = "indeterminate"
            missingness_state = "evidence-missing:" + "|".join(item.coordinate for item in missing)
        else:
            decision = "included"
            missingness_state = "complete"
        if missingness:
            missingness_state = missingness_state + ";" + ";".join(sorted(set(missingness)))
        return tuple(dimensions), decision, missingness_state

    def _evidence_references_for_candidate(
        self,
        *,
        policy: PeerUniversePolicyRecord,
        candidate: PeerCandidate,
        effective_at: datetime,
        known_at: datetime,
    ) -> dict[str, str]:
        evidence = self._strict_known_flow_evidence(
            policy=policy,
            identity=candidate.identity,
            evidence_type=candidate.native_evidence_type,
            rule_type=candidate.value_capture_mechanism_type,
            effective_at=effective_at,
            known_at=known_at,
        )
        supply = self._strict_known_supply(
            policy=policy, identity=candidate.identity, effective_at=effective_at, known_at=known_at
        )
        market_fact = self._strict_known_market_fact(
            policy=policy, identity=candidate.identity, effective_at=effective_at, known_at=known_at
        )
        if evidence is None or supply is None or market_fact is None:
            return _empty_evidence_refs()
        confidence = min(
            (
                Decimal(evidence[0].evidence_confidence),
                Decimal(evidence[1].confidence),
                Decimal(supply.confidence),
                Decimal(market_fact.confidence),
            )
        )
        return {
            "rule_record_id": evidence[1].record_id,
            "rule_record_version": evidence[1].semantic_version,
            "supply_record_id": supply.record_id,
            "supply_record_version": supply.semantic_version,
            "evidence_record_id": evidence[0].record_id,
            "evidence_record_version": evidence[0].semantic_version,
            "market_fact_record_id": market_fact.record_id,
            "market_fact_record_version": market_fact.semantic_version,
            "confidence": confidence.to_eng_string(),
        }

    def _strict_known_flow_evidence(
        self,
        *,
        policy: PeerUniversePolicyRecord,
        identity: EconomicClaimIdentity,
        evidence_type: str,
        rule_type: str,
        effective_at: datetime,
        known_at: datetime,
        missingness: list[str] | None = None,
    ) -> tuple[Any, Any] | None:
        evidence = SupplyAndValueCaptureService.select_strict_known_evidence(
            self.value_capture_repository,
            entity_id=identity.entity_id,
            economic_claim_id=identity.economic_claim_id,
            representation_id=identity.representation_id,
            effective_as_of=effective_at,
            known_by=known_at,
            evidence_type=evidence_type,
        )
        rule = SupplyAndValueCaptureService.select_strict_known_rule(
            self.value_capture_repository,
            entity_id=identity.entity_id,
            economic_claim_id=identity.economic_claim_id,
            representation_id=identity.representation_id,
            effective_as_of=effective_at,
            known_by=known_at,
            rule_type=rule_type,
        )
        if evidence is None or rule is None:
            if missingness is not None:
                missingness.append("native_attributable_flow_evidence")
            return None
        if evidence.identity != identity or rule.identity != identity:
            if missingness is not None:
                missingness.append("identity_mismatch")
            return None
        if evidence.conflict_state in {"open", "contested"} or rule.conflict_state in {"open", "contested"}:
            if missingness is not None:
                missingness.append("conflicted_flow_evidence")
            return None
        if evidence.attribution_rule_id != rule.mechanism_policy_id:
            if missingness is not None:
                missingness.append("attribution_mismatch")
            return None
        if evidence.amount is None:
            if missingness is not None:
                missingness.append("missing_flow_amount")
            return None
        return evidence, rule

    def _strict_known_supply(
        self,
        *,
        policy: PeerUniversePolicyRecord,
        identity: EconomicClaimIdentity,
        effective_at: datetime,
        known_at: datetime,
        missingness: list[str] | None = None,
    ) -> Any | None:
        supply = SupplyAndValueCaptureService.select_strict_known_supply(
            self.value_capture_repository,
            entity_id=identity.entity_id,
            economic_claim_id=identity.economic_claim_id,
            representation_id=identity.representation_id,
            effective_as_of=effective_at,
            known_by=known_at,
            supply_basis_type="fully_diluted_supply",
        )
        if supply is None:
            if missingness is not None:
                missingness.append("supply_basis")
            return None
        if supply.identity != identity:
            if missingness is not None:
                missingness.append("supply_identity_mismatch")
            return None
        if supply.conflict_state in {"open", "contested"}:
            if missingness is not None:
                missingness.append("conflicted_supply")
            return None
        return supply

    def _strict_known_market_fact(
        self,
        *,
        policy: PeerUniversePolicyRecord,
        identity: EconomicClaimIdentity,
        effective_at: datetime,
        known_at: datetime,
        missingness: list[str] | None = None,
    ) -> Any | None:
        fact = ObservedMarketFactService.select_strict_known_fact(
            self.market_fact_repository,
            entity_id=identity.entity_id,
            representation_id=identity.representation_id,
            fact_type=FULLY_DILUTED_MARKET_FACT_TYPE,
            effective_as_of=effective_at,
            known_by=known_at,
            quote_currency=policy.required_quote_currency,
        )
        if fact is None:
            if missingness is not None:
                missingness.append("fully_diluted_market_value")
            return None
        if fact.conflict_state in {"open", "contested"}:
            if missingness is not None:
                missingness.append("conflicted_market_value")
            return None
        freshness_days = (effective_at - fact.effective_at).days
        if freshness_days < 0 or freshness_days > policy.freshness_limit_days:
            if missingness is not None:
                missingness.append("stale_market_value")
            return None
        return fact

    def _confidence_components(
        self,
        *,
        policy: PeerUniversePolicyRecord,
        universe: PeerUniverseSnapshot,
        decisions: tuple[PeerEligibilityDecisionRecord, ...],
        observations: tuple[ComparativeMetricObservationRecord, ...],
        peer_median: Decimal,
        target_multiple: Decimal,
        effective_at: datetime,
        known_at: datetime,
    ) -> dict[str, str]:
        # Peer-universe coverage confidence: complete decision coverage over the bounded
        # point-in-time candidate universe.
        universe_coverage = Decimal(1) if len(decisions) == len(universe.candidates) else Decimal(0)
        # Eligibility-evidence confidence: the weakest persisted eligibility confidence
        # component among the included peer decisions.
        eligibility_values = [
            Decimal(decision.confidence_component) for decision in decisions if decision.confidence_component
        ]
        eligibility = min(eligibility_values) if eligibility_values else Decimal(0)
        # Metric-evidence confidence: recomputed from the exact strict-known native
        # evidence each observation consumed, at the same cutoff (deterministic).
        metric_confidences: list[Decimal] = []
        for observation in observations:
            evidence = SupplyAndValueCaptureService.select_strict_known_evidence(
                self.value_capture_repository,
                entity_id=observation.identity.entity_id,
                economic_claim_id=observation.identity.economic_claim_id,
                representation_id=observation.identity.representation_id,
                effective_as_of=effective_at,
                known_by=known_at,
                evidence_type=policy.required_native_evidence_types[0],
            )
            rule = SupplyAndValueCaptureService.select_strict_known_rule(
                self.value_capture_repository,
                entity_id=observation.identity.entity_id,
                economic_claim_id=observation.identity.economic_claim_id,
                representation_id=observation.identity.representation_id,
                effective_as_of=effective_at,
                known_by=known_at,
                rule_type=policy.required_value_capture_mechanism_types[0],
            )
            if evidence is not None and rule is not None:
                metric_confidences.extend(
                    (
                        Decimal(evidence.evidence_confidence),
                        Decimal(rule.confidence),
                    )
                )
        metric = min(metric_confidences) if metric_confidences else Decimal(0)
        # Coordinate-compatibility confidence: every included peer passed every mandatory
        # coordinate dimension (the eligibility gate already guaranteed this).
        coordinates = Decimal(1) if decisions else Decimal(0)
        # Peer-set cardinality confidence: grows with cohort size above the three-peer
        # minimum, never exceeding 1.
        cardinality = Decimal(1) - Decimal(1) / Decimal(len(decisions) + 1) if decisions else Decimal(0)
        # Methodology-calibration confidence: no historically calibrated, leakage-tested
        # monotonic transform has been accepted through independent review, so it is 0.
        calibration = Decimal(0)
        overall = min(universe_coverage, eligibility, metric, coordinates, cardinality, calibration)
        return {
            "universe": universe_coverage.to_eng_string(),
            "eligibility": eligibility.to_eng_string(),
            "metric": metric.to_eng_string(),
            "coordinates": coordinates.to_eng_string(),
            "cardinality": cardinality.to_eng_string(),
            "calibration": calibration.to_eng_string(),
            "overall": overall.to_eng_string(),
        }

    def _persist_unavailable_assessment(
        self,
        *,
        identity: EconomicClaimIdentity,
        policy: PeerUniversePolicyRecord,
        universe: PeerUniverseSnapshot,
        availability_state: str,
        recorded_at: datetime,
        known_at: datetime,
        supersedes_record_id: str | None,
        correction_reason: str,
    ) -> ComparativeValuationAssessmentRecord:
        if availability_state not in AVAILABILITY_STATES:
            raise CanonicalComparativeValuationAuthorityError(f"unsupported availability state: {availability_state}")
        # Coverage strings reflect only replay-eligible records at the assessment cutoff
        # (Fix 3: assess() selects only replay-eligible, non-superseded records).
        decisions = _replay_eligible(
            self.repository.decisions_for_universe(universe.record_id),
            effective_as_of=universe.cutoff,
            known_by=known_at,
        )
        observations = _replay_eligible(
            self.repository.observations_for_universe(universe.record_id),
            effective_as_of=universe.cutoff,
            known_by=known_at,
        )
        record = ComparativeValuationAssessmentRecord(
            record_id="pending",
            logical_id="pending",
            schema_version=COMPARATIVE_VALUATION_SCHEMA_VERSION,
            semantic_version="1.0.0",
            identity=identity,
            policy_record_id=policy.record_id,
            policy_record_version=policy.semantic_version,
            universe_snapshot_id=universe.record_id,
            included_decision_record_ids=(),
            target_observation_record_id="",
            peer_observation_record_ids=(),
            peer_multiple_distribution=(),
            peer_median_multiple=None,
            target_multiple=None,
            raw_log_residual=None,
            residual_sign_convention="positive-cheaper",
            normalization_policy_id=None,
            normalization_status=NORMALIZATION_UNAVAILABLE,
            normalized_value=None,
            cohort_decision_coverage=_coverage_string(len(decisions), len(universe.candidates)),
            cohort_observation_coverage=_coverage_string(len(observations), len(universe.candidates) + 1),
            # Fix 4: explicit missingness -- an unavailable assessment must never encode
            # unavailable as a zero confidence (ADR 0020 "Confidence is unavailable, not
            # zero"). All confidence fields are blank and the model enforces this.
            confidence_universe_coverage="",
            confidence_eligibility_evidence="",
            confidence_metric_evidence="",
            confidence_coordinate_compatibility="",
            confidence_peer_set_cardinality="",
            confidence_methodology_calibration="",
            confidence="",
            availability_state=availability_state,
            correlation_group=REQUIRED_CORRELATION_GROUP,
            effective_at=universe.cutoff,
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
            # audit finding F1, reused unmodified from hunter.valuation_methodology and
            # hunter.valuation.
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
            raise ComparativeValuationIntegrityError(str(exc)) from exc
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
            engine.dispose()


def _authorize_correction(snapshots: Any, *, snapshot_type: str, record: Any) -> None:
    """Mirrors hunter.valuation.service._authorize_correction and
    hunter.valuation_methodology.service._authorize_correction unmodified in substance:
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
            raise ComparativeValuationIntegrityError(
                "a root record already exists for this logical_id; use a correction "
                "(supersedes_record_id) instead of a second independent root record"
            )
        return
    prior_snapshot = snapshots.load(supersedes_record_id)
    if prior_snapshot is None or prior_snapshot.snapshot_type != snapshot_type:
        raise ComparativeValuationIntegrityError("correction predecessor does not exist")
    prior_payload = prior_snapshot.payload
    if str(prior_payload.get("logical_id")) != logical_id:
        raise ComparativeValuationIntegrityError("correction must preserve logical_id")
    if _aware(prior_payload["recorded_at"]) >= recorded_at:
        raise ComparativeValuationIntegrityError("correction recorded_at must follow predecessor")
    if _aware(prior_payload["known_at"]) >= known_at:
        raise ComparativeValuationIntegrityError("correction known_at must follow predecessor")
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
        raise ComparativeValuationIntegrityError("branching correction lineage is prohibited")


def _normalize_policy(record: PeerUniversePolicyRecord) -> PeerUniversePolicyRecord:
    logical_id = hashlib.sha256(f"peer-policy|{record.policy_id}".encode()).hexdigest()
    content_hash = _content_hash(record, logical_id=logical_id)
    record_id = hashlib.sha256(f"{logical_id}:{content_hash}".encode()).hexdigest()
    return replace(record, logical_id=logical_id, content_hash=content_hash, record_id=record_id)


def _normalize_universe(record: PeerUniverseSnapshot) -> PeerUniverseSnapshot:
    logical_id = hashlib.sha256(
        (
            "peer-universe|"
            + _identity_key(record.identity)
            + "|"
            + record.policy_record_id
            + "|"
            + record.cutoff.isoformat()
        ).encode()
    ).hexdigest()
    content_hash = _content_hash(record, logical_id=logical_id)
    record_id = hashlib.sha256(f"{logical_id}:{content_hash}".encode()).hexdigest()
    return replace(record, logical_id=logical_id, content_hash=content_hash, record_id=record_id)


def _normalize_decision(record: PeerEligibilityDecisionRecord) -> PeerEligibilityDecisionRecord:
    logical_id = hashlib.sha256(
        (
            "peer-eligibility|"
            + _identity_key(record.target_identity)
            + "|"
            + _identity_key(record.candidate_identity)
            + "|"
            + record.policy_record_id
            + "|"
            + record.universe_snapshot_id
        ).encode()
    ).hexdigest()
    content_hash = _content_hash(record, logical_id=logical_id)
    record_id = hashlib.sha256(f"{logical_id}:{content_hash}".encode()).hexdigest()
    return replace(record, logical_id=logical_id, content_hash=content_hash, record_id=record_id)


def _normalize_observation(record: ComparativeMetricObservationRecord) -> ComparativeMetricObservationRecord:
    logical_id = hashlib.sha256(
        (
            "comparative-observation|"
            + _identity_key(record.identity)
            + "|"
            + record.policy_record_id
            + "|"
            + record.universe_snapshot_id
        ).encode()
    ).hexdigest()
    content_hash = _content_hash(record, logical_id=logical_id)
    record_id = hashlib.sha256(f"{logical_id}:{content_hash}".encode()).hexdigest()
    return replace(record, logical_id=logical_id, content_hash=content_hash, record_id=record_id)


def _normalize_assessment(record: ComparativeValuationAssessmentRecord) -> ComparativeValuationAssessmentRecord:
    logical_id = hashlib.sha256(
        (
            "comparative-assessment|"
            + _identity_key(record.identity)
            + "|"
            + record.policy_record_id
            + "|"
            + record.universe_snapshot_id
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
    hunter.valuation_methodology.service, and hunter.valuation.service."""
    if application_root is None:
        configured = os.environ.get(_APPLICATION_ROOT_ENV, "").strip()
        if not configured:
            raise CanonicalComparativeValuationAuthorityError(
                f"{_APPLICATION_ROOT_ENV} must identify the approved Hunter application root"
            )
        application_root = Path(configured).expanduser()
    if not application_root.is_absolute():
        raise CanonicalComparativeValuationAuthorityError(f"{_APPLICATION_ROOT_ENV} must be an absolute path")
    return application_root.resolve()


def _construction_fingerprint(
    identity: EconomicClaimIdentity,
    policy_record_id: str,
    cutoff: datetime,
    candidates: tuple[PeerCandidate, ...],
) -> str:
    ordered = tuple(sorted(candidates, key=lambda item: _candidate_sort_key(item)))
    raw = json.dumps(
        {
            "identity": _identity_key(identity),
            "policy_record_id": policy_record_id,
            "cutoff": cutoff.isoformat(),
            "candidates": [
                {
                    "identity": _identity_key(item.identity),
                    "source_record_id": item.source_record_id,
                    "source_record_version": item.source_record_version,
                }
                for item in ordered
            ],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(raw).hexdigest()


def _calculation_fingerprint(
    *,
    numerator: Any,
    evidence: Any,
    rule: Any,
    multiple: Decimal,
    policy: PeerUniversePolicyRecord,
    pathway: str,
) -> str:
    raw = json.dumps(
        {
            "numerator": (numerator.record_id, numerator.semantic_version),
            "denominator": (evidence.record_id, evidence.semantic_version),
            "rule": (rule.record_id, rule.semantic_version),
            "pathway": pathway,
            "multiple": multiple.to_eng_string(),
            "currency": policy.required_quote_currency,
            "horizon_days": policy.accounting_horizon_days,
            "supply_basis": "fully_diluted_supply",
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(raw).hexdigest()


def _candidate_in_universe(universe: PeerUniverseSnapshot, identity: EconomicClaimIdentity) -> PeerCandidate | None:
    return next(
        (candidate for candidate in universe.candidates if candidate.identity == identity),
        None,
    )


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


def _decision_reason(decision: str, dimensions: tuple[DimensionDecision, ...], missingness: str) -> str:
    failed = [item.coordinate for item in dimensions if item.passed is False]
    missing = [item.coordinate for item in dimensions if item.passed is None]
    return (
        f"decision={decision}; missingness={missingness}; "
        f"excluded_coordinates={','.join(failed) or 'none'}; "
        f"missing_evidence={','.join(missing) or 'none'}"
    )


def _empty_evidence_refs() -> dict[str, str]:
    return {
        "rule_record_id": "",
        "rule_record_version": "",
        "supply_record_id": "",
        "supply_record_version": "",
        "evidence_record_id": "",
        "evidence_record_version": "",
        "market_fact_record_id": "",
        "market_fact_record_version": "",
        "confidence": "",
    }


def _unweighted_median(values: tuple[Decimal, ...]) -> Decimal:
    if not values:
        raise CanonicalComparativeValuationAuthorityError("peer multiple distribution is empty")
    ordered = sorted(values)
    count = len(ordered)
    midpoint = count // 2
    if count % 2 == 1:
        return ordered[midpoint]
    return (ordered[midpoint - 1] + ordered[midpoint]) / Decimal(2)


def _raw_log_residual(median: Decimal, target_multiple: Decimal) -> Decimal:
    if median <= 0 or target_multiple <= 0:
        raise CanonicalComparativeValuationAuthorityError(
            "raw log residual requires strictly positive median and target multiples"
        )
    try:
        return (median / target_multiple).ln()
    except Exception as exc:  # pragma: no cover - defensive
        raise CanonicalComparativeValuationAuthorityError("raw log residual is undefined") from exc


def _ordered_peer_multiple_distribution(
    peer_observations: tuple[ComparativeMetricObservationRecord, ...],
) -> tuple[Decimal, ...]:
    ordered = sorted(peer_observations, key=lambda item: _observation_sort_key(item))
    return tuple(Decimal(item.comparative_multiple) for item in ordered)


def _observation_for(
    observations: tuple[ComparativeMetricObservationRecord, ...], identity: EconomicClaimIdentity
) -> ComparativeMetricObservationRecord | None:
    return next(
        (item for item in observations if item.identity == identity),
        None,
    )


def _coverage_string(covered: int, total: int) -> str:
    return f"{covered}/{total}"


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


def _candidate_sort_key(candidate: PeerCandidate) -> tuple[str, str, str, str, str]:
    return (
        candidate.identity.entity_id,
        candidate.identity.representation_id,
        candidate.source_record_id,
        candidate.source_record_version,
        candidate.identity.token_id,
    )


def _decision_sort_key(record: PeerEligibilityDecisionRecord) -> tuple[str, str, str]:
    return (
        record.candidate_identity.entity_id,
        record.candidate_identity.representation_id,
        record.record_id,
    )


def _observation_sort_key(record: ComparativeMetricObservationRecord) -> tuple[str, str, str]:
    return (
        record.identity.entity_id,
        record.identity.representation_id,
        record.record_id,
    )


def _aware(value: Any) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return parsed.astimezone(UTC)
