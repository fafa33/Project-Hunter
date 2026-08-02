from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from hunter.market_facts.repository import ObservedMarketFactRepository
from hunter.persistence.models import QuerySpec
from hunter.persistence.sql import RepositoryFactory, SessionFactory, create_sqlite_engine
from hunter.persistence.sql.exceptions import PersistenceIdentityConflictError
from hunter.valuation.models import (
    NORMALIZATION_UNAVAILABLE,
    PERMITTED_VALUE_CAPTURE_RULE_TYPES,
    REQUIRED_CORRELATION_GROUP,
    VALUATION_SCHEMA_VERSION,
    FairValueEstimateRecord,
    ValuationAssessmentRecord,
)
from hunter.valuation.repository import (
    CanonicalValuationIntegrityError,
    CanonicalValuationRepository,
    assessment_snapshot,
    estimate_snapshot,
)
from hunter.valuation_methodology.service import CanonicalValuationMethodologyAuthority
from hunter.value_capture.models import EconomicClaimIdentity
from hunter.value_capture.repository import SupplyAndValueCaptureRepository

_APPLICATION_ROOT_ENV = "HUNTER_APPLICATION_ROOT"

# The single, fixed, versioned discount-rate policy this implementation knows how to
# apply. ADR 0022's Prohibited Methodologies section requires any discount rate to be
# "fixed, versioned, and declared inside the ValuationMethodologySnapshot before
# evaluation" -- this constant is that fixed declaration, not a fitted/calibrated value.
# A ValuationMethodologySnapshot declaring any other discount_rate_policy_id/version is
# unsupported by Milestone 3 and fails closed rather than being guessed at.
CANONICAL_DISCOUNT_RATE_POLICY_ID = "canonical-fixed-discount-rate-v1"
CANONICAL_DISCOUNT_RATE_POLICY_VERSION = "1.0.0"
CANONICAL_DISCOUNT_RATE = Decimal("0.15")

# The single, fixed, versioned uncertainty-propagation policy this implementation knows
# how to apply: p10/p50/p90 dispersion is the conservative maximum of the three inputs'
# own declared uncertainty fields (FundamentalEvidenceRecord.uncertainty,
# ValueCaptureRuleSnapshot.uncertainty, SupplyBasisSnapshot.uncertainty), never a fixed
# band unrelated to those declared values (ADR 0022 Uncertainty handling). This is a
# single fixed arithmetic combination, not a sensitivity-analysis engine: no scenario
# variation, no discount-rate stress-testing, no multi-parameter search -- Milestone 4/
# calibration territory that Milestone 3 explicitly does not implement.
CANONICAL_SENSITIVITY_POLICY_ID = "canonical-fixed-uncertainty-propagation-v1"
CANONICAL_SENSITIVITY_POLICY_VERSION = "1.0.0"

# ADR 0022 Permitted methodology's default: "expressed per fully-diluted unit unless the
# methodology snapshot explicitly declares and justifies a different supply basis."
# Milestone 3 implements only the default; any other declared rule fails closed.
CANONICAL_SUPPLY_BASIS_SELECTION_RULE = "fully_diluted_supply"

_ESTIMATE_SNAPSHOT_TYPE = "fair-value-estimate-record"
_ASSESSMENT_SNAPSHOT_TYPE = "valuation-assessment-record"


class CanonicalValuationAuthorityError(ValueError):
    """Raised when required evidence is missing, incompatible, or otherwise makes a
    fair-value estimate explicitly unavailable (ADR 0022 Missingness) -- never a partial
    or degraded-confidence estimate. Also raised for unauthorized/unattested
    construction."""


class CanonicalValuationService:
    """Service-owned authority boundary for FairValueEstimateRecord and
    ValuationAssessmentRecord (ADR 0022, Milestone 3).

    Consumes exactly the five record families ADR 0022's Valuation Inputs section
    authorizes -- ObservedMarketFactRecord (transitively, via the SupplyBasisSnapshot it
    validates), FundamentalEvidenceRecord, ValueCaptureRuleSnapshot, SupplyBasisSnapshot,
    and ValuationMethodologySnapshot -- each resolved by strict-known replay at the exact
    same cutoff. No other record family, provider, repository, configuration file, or
    caller-supplied value may inform an estimate.

    Authority model is identical to hunter.valuation_methodology's (ADR 0022 Milestone 2):
    HUNTER_APPLICATION_ROOT-gated, operator-attested construction, reusing the same
    pattern rather than introducing a second governance model. CanonicalValuationService
    computes a deterministic output from already-persisted, already-validated evidence --
    exactly analogous to hunter.committee's AuthoritativeInvestmentCommitteeService, not to
    hunter.value_capture's provider-signing model (there is no external source to
    authenticate here either).

    The permitted model family (discounted value-capture flow), horizon (365 days),
    discount-rate policy, and uncertainty-propagation policy are each fixed, versioned
    constants enforced here -- never caller-parameterized -- mirroring exactly how
    hunter.valuation_methodology fixes permitted_model_identifier/horizon_days/
    correlation_group. A ValuationMethodologySnapshot declaring a discount-rate or
    sensitivity policy id/version other than the one this implementation registers fails
    closed rather than being computed speculatively.
    """

    def __init__(
        self,
        *,
        repository: CanonicalValuationRepository,
        methodology_authority: CanonicalValuationMethodologyAuthority,
        value_capture_repository: SupplyAndValueCaptureRepository,
        market_fact_repository: ObservedMarketFactRepository,
        application_root: Path | None = None,
    ) -> None:
        self.repository = repository
        self.methodology_authority = methodology_authority
        self.value_capture_repository = value_capture_repository
        self.market_fact_repository = market_fact_repository
        self._application_root = _authorized_application_root(application_root)

    def estimate_fair_value(
        self,
        *,
        identity: EconomicClaimIdentity,
        evidence_type: str,
        rule_type: str,
        effective_at: datetime,
        recorded_at: datetime,
        known_at: datetime,
        supersedes_estimate_record_id: str | None = None,
        supersedes_assessment_record_id: str | None = None,
        correction_reason: str = "",
    ) -> tuple[FairValueEstimateRecord, ValuationAssessmentRecord]:
        methodology = self.methodology_authority.strict_known_methodology(
            effective_as_of=effective_at, known_by=known_at
        )
        if methodology is None:
            raise CanonicalValuationAuthorityError("no strict-known ValuationMethodologySnapshot is available")
        if methodology.recorded_at > recorded_at or methodology.known_at > known_at:
            raise CanonicalValuationAuthorityError(
                "future-known ValuationMethodologySnapshot cannot support this estimate"
            )

        supply = self.value_capture_repository.strict_known_supply(
            entity_id=identity.entity_id,
            economic_claim_id=identity.economic_claim_id,
            representation_id=identity.representation_id,
            effective_as_of=effective_at,
            known_by=known_at,
            supply_basis_type="circulating_supply",
        )
        if supply is None:
            raise CanonicalValuationAuthorityError("no strict-known SupplyBasisSnapshot is available")
        if supply.recorded_at > recorded_at or supply.known_at > known_at:
            raise CanonicalValuationAuthorityError("future-known SupplyBasisSnapshot cannot support this estimate")
        if supply.identity != identity:
            raise CanonicalValuationAuthorityError("supply basis identity does not match requested target identity")
        components = dict(supply.quantity_components)
        for required_component in ("circulating_supply", "total_supply", "fully_diluted_supply"):
            if required_component not in components:
                raise CanonicalValuationAuthorityError(
                    f"SupplyBasisSnapshot is missing required component: {required_component} "
                    "(ADR 0022 Scope condition 3)"
                )

        evidence = self.value_capture_repository.strict_known_evidence(
            entity_id=identity.entity_id,
            economic_claim_id=identity.economic_claim_id,
            representation_id=identity.representation_id,
            effective_as_of=effective_at,
            known_by=known_at,
            evidence_type=evidence_type,
        )
        if evidence is None:
            raise CanonicalValuationAuthorityError("no strict-known FundamentalEvidenceRecord is available")
        if evidence.recorded_at > recorded_at or evidence.known_at > known_at:
            raise CanonicalValuationAuthorityError(
                "future-known FundamentalEvidenceRecord cannot support this estimate"
            )
        if evidence.identity != identity:
            raise CanonicalValuationAuthorityError("evidence identity does not match requested target identity")

        rule = self.value_capture_repository.strict_known_rule(
            entity_id=identity.entity_id,
            economic_claim_id=identity.economic_claim_id,
            representation_id=identity.representation_id,
            effective_as_of=effective_at,
            known_by=known_at,
            rule_type=rule_type,
        )
        if rule is None:
            raise CanonicalValuationAuthorityError("no strict-known ValueCaptureRuleSnapshot is available")
        if rule.recorded_at > recorded_at or rule.known_at > known_at:
            raise CanonicalValuationAuthorityError("future-known ValueCaptureRuleSnapshot cannot support this estimate")
        if rule.identity != identity:
            raise CanonicalValuationAuthorityError("rule identity does not match requested target identity")
        if rule.rule_type not in PERMITTED_VALUE_CAPTURE_RULE_TYPES:
            raise CanonicalValuationAuthorityError(
                f"rule_type {rule.rule_type!r} is out of scope for canonical valuation (ADR 0022 Scope condition 2)"
            )

        if evidence.attribution_rule_id != rule.mechanism_policy_id:
            raise CanonicalValuationAuthorityError(
                "evidence attribution_rule_id does not match the accepted rule's mechanism_policy_id "
                "(ADR 0022 Scope condition 4)"
            )

        horizon_start = effective_at - timedelta(days=methodology.horizon_days)
        if not (horizon_start <= evidence.accounting_period_start and evidence.accounting_period_end <= effective_at):
            raise CanonicalValuationAuthorityError(
                "evidence accounting period is not fully contained within the valuation horizon's "
                "lookback window (ADR 0022 Scope condition 4)"
            )
        accounting_period_days = (evidence.accounting_period_end - evidence.accounting_period_start).days
        if accounting_period_days != methodology.horizon_days:
            raise CanonicalValuationAuthorityError(
                "evidence accounting period length does not match the valuation horizon; an arbitrary-"
                "period amount cannot be treated as a fixed 365-day flow without an authorized "
                "annualization policy (ADR 0022 Prohibited methodologies)"
            )

        market_facts = []
        if not supply.observed_market_fact_ids:
            raise CanonicalValuationAuthorityError("SupplyBasisSnapshot references no observed market facts")
        for fact_id, fact_version in zip(
            supply.observed_market_fact_ids, supply.observed_market_fact_versions, strict=True
        ):
            fact = self.market_fact_repository.record(fact_id)
            if fact is None:
                raise CanonicalValuationAuthorityError(f"referenced observed market fact does not exist: {fact_id}")
            if fact.semantic_version != fact_version:
                raise CanonicalValuationAuthorityError("observed market fact version does not match reference")
            if fact.quality_state != "accepted" or fact.conflict_state not in {"none", "resolved"}:
                raise CanonicalValuationAuthorityError(
                    "non-authoritative observed market fact cannot support valuation"
                )
            if fact.recorded_at > recorded_at or fact.known_at > known_at or fact.effective_at > effective_at:
                raise CanonicalValuationAuthorityError(
                    "observed market fact is not strict-known at the requested cutoff"
                )
            market_facts.append(fact)

        if evidence.unit is None or evidence.unit.strip().lower() != methodology.currency.strip().lower():
            raise CanonicalValuationAuthorityError(
                "evidence unit does not match methodology currency; currency conversion is not authorized "
                "in Milestone 3"
            )
        if evidence.amount is None:
            raise CanonicalValuationAuthorityError("evidence has no attributable flow amount")

        if (
            methodology.discount_rate_policy_id != CANONICAL_DISCOUNT_RATE_POLICY_ID
            or methodology.discount_rate_policy_version != CANONICAL_DISCOUNT_RATE_POLICY_VERSION
        ):
            raise CanonicalValuationAuthorityError(
                "methodology declares a discount-rate policy this implementation does not support"
            )
        if (
            methodology.sensitivity_policy_id != CANONICAL_SENSITIVITY_POLICY_ID
            or methodology.sensitivity_policy_version != CANONICAL_SENSITIVITY_POLICY_VERSION
        ):
            raise CanonicalValuationAuthorityError(
                "methodology declares a sensitivity policy this implementation does not support"
            )
        if methodology.supply_basis_selection_rule != CANONICAL_SUPPLY_BASIS_SELECTION_RULE:
            raise CanonicalValuationAuthorityError(
                "methodology declares a supply-basis selection rule this implementation does not support"
            )

        raw_flow = Decimal(evidence.amount)
        if not raw_flow.is_finite() or raw_flow < 0:
            raise CanonicalValuationAuthorityError("attributable value-capture flow must be finite and non-negative")
        if rule.rate_or_proportion is None:
            raise CanonicalValuationAuthorityError(
                "ValueCaptureRuleSnapshot does not declare rate_or_proportion; attributable value cannot be "
                "determined with certainty (ADR 0022 Missingness)"
            )
        flow = raw_flow * Decimal(rule.rate_or_proportion)
        supply_quantity = Decimal(components["fully_diluted_supply"])
        if not supply_quantity.is_finite() or supply_quantity <= 0:
            raise CanonicalValuationAuthorityError("fully diluted supply must be a positive, finite quantity")

        pv_total = flow / (1 + CANONICAL_DISCOUNT_RATE)
        p50_unit = pv_total / supply_quantity
        uncertainty = max(Decimal(evidence.uncertainty), Decimal(rule.uncertainty), Decimal(supply.uncertainty))
        if uncertainty > 1:
            uncertainty = Decimal(1)
        p10_unit = max(Decimal(0), p50_unit * (1 - uncertainty))
        p90_unit = p50_unit * (1 + uncertainty)
        p10_total = max(Decimal(0), pv_total * (1 - uncertainty))
        p90_total = pv_total * (1 + uncertainty)
        dispersion_ratio = (p90_unit - p10_unit) / p50_unit if p50_unit > 0 else Decimal(1)

        confidence_base = min(
            Decimal(evidence.entity_link_confidence),
            Decimal(evidence.evidence_confidence),
            Decimal(rule.confidence),
            Decimal(supply.confidence),
        )
        dispersion_factor = max(Decimal(0), 1 - min(dispersion_ratio, Decimal(1)))
        age_days = (effective_at - evidence.accounting_period_end).days
        freshness_factor = max(Decimal(0), min(Decimal(1), 1 - Decimal(age_days) / Decimal(methodology.horizon_days)))
        confidence = confidence_base * dispersion_factor * freshness_factor

        estimate = FairValueEstimateRecord(
            record_id="pending",
            logical_id="pending",
            schema_version=VALUATION_SCHEMA_VERSION,
            semantic_version="1.0.0",
            identity=identity,
            effective_at=effective_at,
            recorded_at=recorded_at,
            known_at=known_at,
            quality_state="accepted",
            conflict_state="none",
            currency=methodology.currency,
            horizon_days=methodology.horizon_days,
            supply_basis_selection_rule=methodology.supply_basis_selection_rule,
            discount_rate_policy_id=methodology.discount_rate_policy_id,
            discount_rate_policy_version=methodology.discount_rate_policy_version,
            discount_rate_applied=str(CANONICAL_DISCOUNT_RATE),
            fair_value_per_diluted_unit_p10=str(p10_unit),
            fair_value_per_diluted_unit_p50=str(p50_unit),
            fair_value_per_diluted_unit_p90=str(p90_unit),
            total_diluted_value_p10=str(p10_total),
            total_diluted_value_p50=str(pv_total),
            total_diluted_value_p90=str(p90_total),
            model_dispersion_ratio=str(dispersion_ratio),
            confidence=str(confidence),
            confidence_base=str(confidence_base),
            confidence_dispersion_factor=str(dispersion_factor),
            confidence_freshness_factor=str(freshness_factor),
            methodology_record_id=methodology.record_id,
            methodology_record_version=methodology.semantic_version,
            evidence_record_id=evidence.record_id,
            evidence_record_version=evidence.semantic_version,
            rule_record_id=rule.record_id,
            rule_record_version=rule.semantic_version,
            supply_record_id=supply.record_id,
            supply_record_version=supply.semantic_version,
            market_fact_record_ids=tuple(fact.record_id for fact in market_facts),
            market_fact_record_versions=tuple(fact.semantic_version for fact in market_facts),
            content_hash="pending",
            supersedes_record_id=supersedes_estimate_record_id,
            correction_reason=correction_reason,
        )
        estimate = _normalize_estimate(estimate)

        assessment = ValuationAssessmentRecord(
            record_id="pending",
            logical_id=estimate.logical_id,
            schema_version=VALUATION_SCHEMA_VERSION,
            semantic_version="1.0.0",
            identity=identity,
            effective_at=effective_at,
            recorded_at=recorded_at,
            known_at=known_at,
            quality_state="accepted",
            conflict_state="none",
            fair_value_estimate_record_id=estimate.record_id,
            fair_value_estimate_record_version=estimate.semantic_version,
            confidence=estimate.confidence,
            normalization_status=NORMALIZATION_UNAVAILABLE,
            normalized_value=None,
            correlation_group=REQUIRED_CORRELATION_GROUP,
            content_hash="pending",
            supersedes_record_id=supersedes_assessment_record_id,
            correction_reason=correction_reason,
        )
        assessment = _normalize_assessment(assessment)

        self._persist(estimate, assessment)
        return estimate, assessment

    def get_fair_value_estimate(self, record_id: str) -> FairValueEstimateRecord | None:
        return self.repository.get_fair_value_estimate(record_id)

    def get_valuation_assessment(self, record_id: str) -> ValuationAssessmentRecord | None:
        return self.repository.get_valuation_assessment(record_id)

    def fair_value_history(self, logical_id: str) -> tuple[FairValueEstimateRecord, ...]:
        return self.repository.fair_value_history(logical_id)

    def valuation_assessment_history(self, logical_id: str) -> tuple[ValuationAssessmentRecord, ...]:
        return self.repository.valuation_assessment_history(logical_id)

    def strict_known_fair_value_estimate(
        self, *, effective_as_of: datetime, known_by: datetime, logical_id: str
    ) -> FairValueEstimateRecord | None:
        return self.select_strict_known_fair_value_estimate(
            self.repository, effective_as_of=effective_as_of, known_by=known_by, logical_id=logical_id
        )

    def strict_known_valuation_assessment(
        self, *, effective_as_of: datetime, known_by: datetime, logical_id: str
    ) -> ValuationAssessmentRecord | None:
        return self.select_strict_known_valuation_assessment(
            self.repository, effective_as_of=effective_as_of, known_by=known_by, logical_id=logical_id
        )

    @staticmethod
    def select_strict_known_fair_value_estimate(
        repository: CanonicalValuationRepository,
        *,
        effective_as_of: datetime,
        known_by: datetime,
        logical_id: str,
    ) -> FairValueEstimateRecord | None:
        return _strict_known(
            repository.fair_value_history(logical_id), effective_as_of=effective_as_of, known_by=known_by
        )

    @staticmethod
    def select_strict_known_valuation_assessment(
        repository: CanonicalValuationRepository,
        *,
        effective_as_of: datetime,
        known_by: datetime,
        logical_id: str,
    ) -> ValuationAssessmentRecord | None:
        return _strict_known(
            repository.valuation_assessment_history(logical_id),
            effective_as_of=effective_as_of,
            known_by=known_by,
        )

    def _persist(self, estimate: FairValueEstimateRecord, assessment: ValuationAssessmentRecord) -> None:
        engine = create_sqlite_engine(self.repository.path)
        session = SessionFactory(engine).create()
        try:
            # BEGIN IMMEDIATE before validating -- the hardened transaction pattern from
            # audit finding F1 / hunter.valuation_methodology, reused unmodified.
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            snapshots = RepositoryFactory(session).snapshots()
            _authorize_correction(
                snapshots,
                snapshot_type=_ESTIMATE_SNAPSHOT_TYPE,
                record_id=estimate.record_id,
                logical_id=estimate.logical_id,
                supersedes_record_id=estimate.supersedes_record_id,
                recorded_at=estimate.recorded_at,
                known_at=estimate.known_at,
            )
            _authorize_correction(
                snapshots,
                snapshot_type=_ASSESSMENT_SNAPSHOT_TYPE,
                record_id=assessment.record_id,
                logical_id=assessment.logical_id,
                supersedes_record_id=assessment.supersedes_record_id,
                recorded_at=assessment.recorded_at,
                known_at=assessment.known_at,
            )
            snapshots.save(estimate_snapshot(estimate))
            snapshots.save(assessment_snapshot(assessment))
            session.commit()
        except PersistenceIdentityConflictError as exc:
            session.rollback()
            raise CanonicalValuationIntegrityError(str(exc)) from exc
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
            engine.dispose()


def _authorize_correction(
    snapshots: Any,
    *,
    snapshot_type: str,
    record_id: str,
    logical_id: str,
    supersedes_record_id: str | None,
    recorded_at: datetime,
    known_at: datetime,
) -> None:
    if supersedes_record_id is None:
        # A second, independent root record for the same logical_id would bypass the
        # correction mechanism entirely. Mirrors
        # hunter.valuation_methodology.service._authorize_correction's identical rule.
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
            raise CanonicalValuationIntegrityError(
                "a root record already exists for this logical_id; use a correction "
                "(supersedes_record_id) instead of a second independent root record"
            )
        return
    prior_snapshot = snapshots.load(supersedes_record_id)
    if prior_snapshot is None or prior_snapshot.snapshot_type != snapshot_type:
        raise CanonicalValuationIntegrityError("correction predecessor does not exist")
    prior_payload = prior_snapshot.payload
    if str(prior_payload.get("logical_id")) != logical_id:
        raise CanonicalValuationIntegrityError("correction must preserve logical_id")
    if datetime.fromisoformat(str(prior_payload["recorded_at"])) >= recorded_at:
        raise CanonicalValuationIntegrityError("correction recorded_at must follow predecessor")
    if datetime.fromisoformat(str(prior_payload["known_at"])) >= known_at:
        raise CanonicalValuationIntegrityError("correction known_at must follow predecessor")
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
        raise CanonicalValuationIntegrityError("branching correction lineage is prohibited")


def _strict_known(records: tuple[Any, ...], *, effective_as_of: datetime, known_by: datetime) -> Any | None:
    effective_as_of = _aware(effective_as_of)
    known_by = _aware(known_by)
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
        reverse=True,
    )
    return current[0] if current else None


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(UTC)


def _normalize_estimate(record: FairValueEstimateRecord) -> FairValueEstimateRecord:
    logical_id = _logical_id(record.identity, record.methodology_record_id)
    content_hash = _content_hash(record, logical_id=logical_id)
    record_id = hashlib.sha256(f"{logical_id}:{content_hash}".encode()).hexdigest()
    return replace(record, logical_id=logical_id, content_hash=content_hash, record_id=record_id)


def _normalize_assessment(record: ValuationAssessmentRecord) -> ValuationAssessmentRecord:
    content_hash = _content_hash(record, logical_id=record.logical_id)
    record_id = hashlib.sha256(f"{record.logical_id}:{content_hash}".encode()).hexdigest()
    return replace(record, content_hash=content_hash, record_id=record_id)


def _logical_id(identity: EconomicClaimIdentity, methodology_record_id: str) -> str:
    # A fair-value estimate's logical lineage is scoped to (target identity, methodology
    # record). Re-running the same methodology version against newer evidence is a
    # correction of the same lineage; a different methodology *version* is a materially
    # different, non-comparable population (ADR 0022 Comparability rules), so it starts a
    # new logical_id rather than silently chaining onto the prior one.
    raw = "|".join(
        (
            identity.entity_id,
            identity.economic_claim_id,
            identity.asset_id,
            identity.representation_id,
            identity.token_id,
            identity.chain,
            identity.contract_address,
            methodology_record_id,
        )
    )
    return hashlib.sha256(raw.encode()).hexdigest()


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
    substance, the identical `_application_root()` check already accepted and running in
    hunter.committee.command, hunter.valuation_evidence.command, and
    hunter.valuation_methodology.service. Enforced here at service-construction time so
    that unauthorized/unattested construction fails closed regardless of caller."""
    if application_root is None:
        configured = os.environ.get(_APPLICATION_ROOT_ENV, "").strip()
        if not configured:
            raise CanonicalValuationAuthorityError(
                f"{_APPLICATION_ROOT_ENV} must identify the approved Hunter application root"
            )
        application_root = Path(configured).expanduser()
    if not application_root.is_absolute():
        raise CanonicalValuationAuthorityError(f"{_APPLICATION_ROOT_ENV} must be an absolute path")
    return application_root.resolve()
