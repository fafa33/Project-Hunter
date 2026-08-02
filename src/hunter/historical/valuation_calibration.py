from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from hunter.historical.cutoff import timestamp_valid_at
from hunter.valuation.models import FairValueEstimateRecord, ValuationAssessmentRecord
from hunter.valuation.service import CanonicalValuationService
from hunter.value_capture.models import EconomicClaimIdentity
from hunter.value_capture.service import SupplyAndValueCaptureService

# The single fixed supply-basis lookup key CanonicalValuationService.estimate_fair_value
# queries by (ADR 0022 Milestone 3, hunter.valuation.service). The harness re-derives the
# same input independently at the same cutoff -- it must query by the identical fixed key,
# not a caller-parameterized one, or the two lookups would not be comparable.
_SUPPLY_BASIS_TYPE = "circulating_supply"


class ValuationCalibrationLeakageError(ValueError):
    """Raised when a record reconstructed by this harness carries an effective_at,
    recorded_at, or known_at timestamp beyond the calibration case's declared cutoff.

    This is an independent, read-only re-verification of the leakage guarantees
    CanonicalValuationService and its repositories already enforce at write/query time
    (ADR 0022 Milestone 3, hardened by PR #124) -- never the primary enforcement point.
    Fails closed: no averaging, no fallback, no heuristic repair."""


class ValuationCalibrationReconstructionError(ValueError):
    """Raised when independently re-deriving an estimate's input lineage (methodology,
    evidence, rule, supply basis, or observed market facts) at the estimate's own cutoff
    does not reproduce exactly the record ids/versions the estimate itself declares.

    Strict-known replay is defined to be a deterministic function of persisted state and
    a cutoff; any mismatch here means replay is not reproducible and must fail closed
    rather than silently accept a divergent reconstruction."""


@dataclass(frozen=True)
class ValuationCalibrationCase:
    """One point-in-time replay target for the calibration harness.

    Identifies which already-persisted FairValueEstimateRecord/ValuationAssessmentRecord
    lineage to reconstruct (`logical_id`), which strict-known cutoff to reconstruct it at
    (`effective_as_of`, `known_by`), and which input categories
    (`evidence_type`/`rule_type`) originally produced it -- the same parameters
    CanonicalValuationService.estimate_fair_value itself takes. A case performs no
    computation of its own; it only describes what to replay.
    """

    case_id: str
    identity: EconomicClaimIdentity
    evidence_type: str
    rule_type: str
    logical_id: str
    effective_as_of: datetime
    known_by: datetime


@dataclass(frozen=True)
class ValuationInputLineage:
    """The exact input record ids/versions independently re-derived at a case's cutoff,
    for cross-checking against the ones a replayed FairValueEstimateRecord declares."""

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


@dataclass(frozen=True)
class ValuationCalibrationReplay:
    """The deterministic, read-only result of replaying one ValuationCalibrationCase."""

    case_id: str
    logical_id: str
    effective_as_of: datetime
    known_by: datetime
    estimate: FairValueEstimateRecord | None
    assessment: ValuationAssessmentRecord | None
    reconstructed_lineage: ValuationInputLineage | None


@dataclass(frozen=True)
class ValuationCalibrationRun:
    """An ordered, deterministic collection of replays -- the calibration harness's
    complete output for a set of cases. Two runs over identical persisted state and
    identical cases must be equal (and `content_hash` equal), by construction: every
    field this dataclass carries is itself already-persisted, immutable, content-hashed
    data or a pure function of the case's own declared cutoff."""

    run_id: str
    replays: tuple[ValuationCalibrationReplay, ...]


class ValuationCalibrationHarness:
    """Deterministic, side-effect-free replay harness for ADR 0022 Milestone 4.

    Reconstructs historical FairValueEstimateRecord/ValuationAssessmentRecord state
    exclusively through CanonicalValuationService's already-audited, read-only
    strict-known methods (`strict_known_fair_value_estimate`,
    `strict_known_valuation_assessment`) and through the same strict-known repository
    methods `CanonicalValuationService.estimate_fair_value` itself uses internally
    (`methodology_authority.strict_known_methodology`,
    `value_capture_repository.strict_known_evidence/_supply/_rule`,
    `market_fact_repository.record`). It introduces no new lookup, filtering, or
    chronology rule of its own: every strict-known boundary it relies on is the one
    Milestone 2/3 already implements and Milestone 3's test suite already exercises.

    Two things this harness adds that do not already exist:

    1. An independent leakage re-check (`ValuationCalibrationLeakageError`), reusing
       `hunter.historical.cutoff.timestamp_valid_at` -- the same generic, already-audited
       primitive `hunter.historical.replay`/`hunter.historical.bias_controls` use for the
       Market Validation engine pipeline -- applied here to every timestamp on every
       reconstructed valuation record, rather than trusting the write-time checks alone.
    2. A deterministic, content-hashable `ValuationCalibrationRun` output, so that
       reproducibility ("replay this exact set of cases against this exact persisted
       state twice, get byte-identical results") is a directly testable property instead
       of an assumption.

    Never computes a fair-value estimate itself, never persists anything, and never
    reaches into `hunter.market_validation` or any orchestration layer -- this harness is
    valuation-domain-only, matching Milestone 4's explicit non-goals.
    """

    def __init__(self, service: CanonicalValuationService) -> None:
        self.service = service

    def replay(self, case: ValuationCalibrationCase) -> ValuationCalibrationReplay:
        estimate = self.service.strict_known_fair_value_estimate(
            effective_as_of=case.effective_as_of, known_by=case.known_by, logical_id=case.logical_id
        )
        assessment = (
            self.service.strict_known_valuation_assessment(
                effective_as_of=case.effective_as_of, known_by=case.known_by, logical_id=case.logical_id
            )
            if estimate is not None
            else None
        )
        lineage: ValuationInputLineage | None = None
        if estimate is not None:
            _verify_no_leakage(case, "fair_value_estimate", estimate)
            lineage = self._reconstruct_lineage(case, estimate)
        if assessment is not None:
            _verify_no_leakage(case, "valuation_assessment", assessment)
            if assessment.fair_value_estimate_record_id != (estimate.record_id if estimate else None):
                raise ValuationCalibrationReconstructionError(
                    f"{case.case_id}: replayed assessment does not reference the replayed estimate"
                )
        return ValuationCalibrationReplay(
            case_id=case.case_id,
            logical_id=case.logical_id,
            effective_as_of=case.effective_as_of,
            known_by=case.known_by,
            estimate=estimate,
            assessment=assessment,
            reconstructed_lineage=lineage,
        )

    def run(self, cases: tuple[ValuationCalibrationCase, ...]) -> ValuationCalibrationRun:
        replays = tuple(self.replay(case) for case in cases)
        run_id = hashlib.sha256("|".join(f"{case.case_id}:{case.logical_id}" for case in cases).encode()).hexdigest()
        return ValuationCalibrationRun(run_id=run_id, replays=replays)

    def _reconstruct_lineage(
        self, case: ValuationCalibrationCase, estimate: FairValueEstimateRecord
    ) -> ValuationInputLineage:
        methodology = self.service.methodology_authority.strict_known_methodology(
            effective_as_of=case.effective_as_of, known_by=case.known_by
        )
        if methodology is None:
            raise ValuationCalibrationReconstructionError(
                f"{case.case_id}: no strict-known ValuationMethodologySnapshot could be reconstructed "
                "at this cutoff, but the replayed estimate declares one"
            )
        evidence = SupplyAndValueCaptureService.select_strict_known_evidence(
            self.service.value_capture_repository,
            entity_id=case.identity.entity_id,
            economic_claim_id=case.identity.economic_claim_id,
            representation_id=case.identity.representation_id,
            effective_as_of=case.effective_as_of,
            known_by=case.known_by,
            evidence_type=case.evidence_type,
        )
        if evidence is None:
            raise ValuationCalibrationReconstructionError(
                f"{case.case_id}: no strict-known FundamentalEvidenceRecord could be reconstructed "
                "at this cutoff, but the replayed estimate declares one"
            )
        rule = SupplyAndValueCaptureService.select_strict_known_rule(
            self.service.value_capture_repository,
            entity_id=case.identity.entity_id,
            economic_claim_id=case.identity.economic_claim_id,
            representation_id=case.identity.representation_id,
            effective_as_of=case.effective_as_of,
            known_by=case.known_by,
            rule_type=case.rule_type,
        )
        if rule is None:
            raise ValuationCalibrationReconstructionError(
                f"{case.case_id}: no strict-known ValueCaptureRuleSnapshot could be reconstructed "
                "at this cutoff, but the replayed estimate declares one"
            )
        supply = SupplyAndValueCaptureService.select_strict_known_supply(
            self.service.value_capture_repository,
            entity_id=case.identity.entity_id,
            economic_claim_id=case.identity.economic_claim_id,
            representation_id=case.identity.representation_id,
            effective_as_of=case.effective_as_of,
            known_by=case.known_by,
            supply_basis_type=_SUPPLY_BASIS_TYPE,
        )
        if supply is None:
            raise ValuationCalibrationReconstructionError(
                f"{case.case_id}: no strict-known SupplyBasisSnapshot could be reconstructed "
                "at this cutoff, but the replayed estimate declares one"
            )
        market_facts = []
        for fact_id in estimate.market_fact_record_ids:
            fact = self.service.market_fact_repository.record(fact_id)
            if fact is None:
                raise ValuationCalibrationReconstructionError(
                    f"{case.case_id}: referenced observed market fact {fact_id!r} no longer exists"
                )
            _verify_no_leakage(case, f"observed_market_fact:{fact_id}", fact)
            market_facts.append(fact)

        for record, timestamp_field in (
            (methodology, "methodology"),
            (evidence, "evidence"),
            (rule, "rule"),
            (supply, "supply"),
        ):
            _verify_no_leakage(case, timestamp_field, record)

        lineage = ValuationInputLineage(
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
        )
        _cross_check_lineage(case, estimate, lineage)
        return lineage


def _cross_check_lineage(
    case: ValuationCalibrationCase, estimate: FairValueEstimateRecord, lineage: ValuationInputLineage
) -> None:
    expected = ValuationInputLineage(
        methodology_record_id=estimate.methodology_record_id,
        methodology_record_version=estimate.methodology_record_version,
        evidence_record_id=estimate.evidence_record_id,
        evidence_record_version=estimate.evidence_record_version,
        rule_record_id=estimate.rule_record_id,
        rule_record_version=estimate.rule_record_version,
        supply_record_id=estimate.supply_record_id,
        supply_record_version=estimate.supply_record_version,
        market_fact_record_ids=estimate.market_fact_record_ids,
        market_fact_record_versions=estimate.market_fact_record_versions,
    )
    if lineage != expected:
        raise ValuationCalibrationReconstructionError(
            f"{case.case_id}: independently reconstructed input lineage does not match the "
            "lineage the replayed estimate declares; strict-known replay is not reproducible"
        )


def _verify_no_leakage(case: ValuationCalibrationCase, label: str, record: Any) -> None:
    violations = []
    if not timestamp_valid_at(record.effective_at, case.effective_as_of):
        violations.append(f"{label}:effective_at")
    if not timestamp_valid_at(record.recorded_at, case.known_by):
        violations.append(f"{label}:recorded_at")
    if not timestamp_valid_at(record.known_at, case.known_by):
        violations.append(f"{label}:known_at")
    if violations:
        raise ValuationCalibrationLeakageError(
            f"{case.case_id}: reconstructed record violates the case cutoff: {','.join(sorted(violations))}"
        )


def content_hash(run: ValuationCalibrationRun) -> str:
    """A deterministic content hash of an entire calibration run, for asserting
    byte-identical reproducibility across repeated executions against identical
    persisted state. Mirrors the same sorted-key, ISO-8601-normalized json hashing
    pattern `hunter.valuation.service._content_hash` already uses for individual
    records, extended here to a whole run."""
    payload = [_replay_payload(replay) for replay in run.replays]
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def _replay_payload(replay: ValuationCalibrationReplay) -> dict[str, Any]:
    return {
        "case_id": replay.case_id,
        "logical_id": replay.logical_id,
        "effective_as_of": _iso(replay.effective_as_of),
        "known_by": _iso(replay.known_by),
        "estimate": _json_safe(asdict(replay.estimate)) if replay.estimate is not None else None,
        "assessment": _json_safe(asdict(replay.assessment)) if replay.assessment is not None else None,
        "reconstructed_lineage": (
            _json_safe(asdict(replay.reconstructed_lineage)) if replay.reconstructed_lineage is not None else None
        ),
    }


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _json_safe(value: Any) -> Any:
    if isinstance(value, datetime):
        return _iso(value)
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value
