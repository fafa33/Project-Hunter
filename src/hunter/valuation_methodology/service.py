from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol

from hunter.persistence.models import QuerySpec
from hunter.persistence.sql import RepositoryFactory, SessionFactory, create_sqlite_engine
from hunter.persistence.sql.exceptions import PersistenceIdentityConflictError
from hunter.valuation_methodology.models import (
    AUTHORIZING_ADR_REFERENCE,
    CANONICAL_AUTHORITY_ID,
    PERMITTED_MODEL_IDENTIFIER,
    REQUIRED_CORRELATION_GROUP,
    REQUIRED_HORIZON_DAYS,
    VALUATION_METHODOLOGY_SCHEMA_VERSION,
    MethodologyEvidenceInputContract,
    ValuationMethodologySnapshot,
)
from hunter.valuation_methodology.repository import (
    ValuationMethodologyIntegrityError,
    ValuationMethodologyRepository,
    methodology_contract_snapshot,
    methodology_snapshot,
)

_APPLICATION_ROOT_ENV = "HUNTER_APPLICATION_ROOT"


class MethodologyContractAuthority(Protocol):
    def strict_known_contract(
        self,
        *,
        contract_id: str,
        contract_version: str,
        effective_as_of: datetime,
        known_by: datetime,
    ) -> MethodologyEvidenceInputContract | None: ...


class CanonicalValuationMethodologyAuthorityError(ValueError):
    """Raised when an unattested or otherwise unauthorized methodology write is attempted."""


class CanonicalValuationMethodologyAuthority:
    """Service-owned authority boundary for ValuationMethodologySnapshot.

    Authority model (see PR body / architecture note for the full pre-implementation
    analysis): this reuses the operator-attested, HUNTER_APPLICATION_ROOT-gated pattern
    already accepted for hunter.committee.command and hunter.valuation_evidence.command,
    rather than hunter.value_capture's provider-signing-key model. A
    ValuationMethodologySnapshot is a governance-authored configuration object fixed by
    an accepted ADR, not a fact acquired from an external, untrusted-by-default source --
    there is no endpoint, host, or provider to authenticate against, so a signing-key
    scheme would falsely model it as provider-acquired evidence. Instead:

    - construction requires a validated, operator-approved application root (mirroring
      the identical `_application_root()` check already used, unmodified, in both
      existing CLIs) -- this is the sole "is this caller attested" gate;
    - every ADR-fixed parameter (permitted_model_identifier, horizon_days,
      correlation_group, normalization_policy_id, authorizing_adr_reference,
      authorized_by) is injected here as a fixed constant and is never accepted as a
      caller-supplied argument to persist_methodology -- there is no code path through
      this service's public API that can override them, exactly mirroring how
      ObservedMarketFactService.semantic_version and market_validation/runner.py's
      four-input hard gate are non-parameterized.

    The repository remains purely mechanical; all validation lives here, exactly as in
    hunter.value_capture.service.SupplyAndValueCaptureService.
    """

    def __init__(
        self,
        *,
        repository: ValuationMethodologyRepository,
        application_root: Path | None = None,
    ) -> None:
        self.repository = repository
        self._application_root = _authorized_application_root(application_root)

    def persist_methodology(
        self,
        *,
        entity_class_criteria_id: str,
        entity_class_criteria_version: str,
        currency: str,
        discount_rate_policy_id: str,
        discount_rate_policy_version: str,
        sensitivity_policy_id: str,
        sensitivity_policy_version: str,
        supply_basis_selection_rule: str,
        effective_at: datetime,
        recorded_at: datetime,
        known_at: datetime,
        accepts_assembled_evidence: bool = False,
        supersedes_record_id: str | None = None,
        correction_reason: str = "",
    ) -> ValuationMethodologySnapshot:
        record = ValuationMethodologySnapshot(
            record_id="pending",
            logical_id="pending",
            schema_version=VALUATION_METHODOLOGY_SCHEMA_VERSION,
            semantic_version="1.0.0",
            effective_at=effective_at,
            recorded_at=recorded_at,
            known_at=known_at,
            content_hash="pending",
            quality_state="accepted",
            conflict_state="none",
            entity_class_criteria_id=entity_class_criteria_id,
            entity_class_criteria_version=entity_class_criteria_version,
            permitted_model_identifier=PERMITTED_MODEL_IDENTIFIER,
            horizon_days=REQUIRED_HORIZON_DAYS,
            currency=currency,
            discount_rate_policy_id=discount_rate_policy_id,
            discount_rate_policy_version=discount_rate_policy_version,
            sensitivity_policy_id=sensitivity_policy_id,
            sensitivity_policy_version=sensitivity_policy_version,
            supply_basis_selection_rule=supply_basis_selection_rule,
            normalization_policy_id=None,
            correlation_group=REQUIRED_CORRELATION_GROUP,
            authorizing_adr_reference=AUTHORIZING_ADR_REFERENCE,
            authorized_by=CANONICAL_AUTHORITY_ID,
            accepts_assembled_evidence=accepts_assembled_evidence,
            supersedes_record_id=supersedes_record_id,
            correction_reason=correction_reason,
        )
        record = _normalize(record)

        engine = create_sqlite_engine(self.repository.path)
        session = SessionFactory(engine).create()
        try:
            # BEGIN IMMEDIATE before validating, exactly the hardened transaction pattern
            # from audit finding F1 (hunter.market_facts.repository.apply /
            # hunter.value_capture.service.persist_capability): two concurrent corrections
            # of the same predecessor cannot both observe "no existing successor".
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            snapshots = RepositoryFactory(session).snapshots()
            _authorize_correction(snapshots, record)
            snapshots.save(methodology_snapshot(record))
            session.commit()
        except PersistenceIdentityConflictError as exc:
            session.rollback()
            raise ValuationMethodologyIntegrityError(str(exc)) from exc
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
            engine.dispose()
        return record

    def persist_contract(
        self,
        *,
        contract_id: str,
        contract_version: str,
        methodology_logical_id: str,
        accepts_assembled_evidence: bool,
        accepted_shape_ids: tuple[str, ...],
        accepted_assembly_rule_versions: tuple[str, ...],
        accounting_window_start: datetime,
        accounting_window_end: datetime,
        entity_id: str,
        representation_id: str,
        value_capture_pathway_id: str,
        currency: str,
        unit: str,
        effective_at: datetime,
        recorded_at: datetime,
        known_at: datetime,
        exact_gap_free_non_overlapping_coverage_required: bool = True,
        allow_representation_boundary_crossing: bool = False,
        allow_pathway_boundary_crossing: bool = False,
        allow_supply_basis_boundary_crossing: bool = False,
        provenance_content_hash_required: bool = True,
        conflict_policy: Literal["reject"] = "reject",
        minimum_quality_state: Literal["accepted"] = "accepted",
        missingness_behavior: Literal["unavailable"] = "unavailable",
        strict_known_required: bool = True,
        supersedes_contract_version: str | None = None,
        correction_reason: str = "",
    ) -> MethodologyEvidenceInputContract:
        contract = MethodologyEvidenceInputContract(
            contract_id=contract_id,
            contract_version=contract_version,
            methodology_logical_id=methodology_logical_id,
            accepts_assembled_evidence=accepts_assembled_evidence,
            accepted_shape_ids=accepted_shape_ids,
            accepted_assembly_rule_versions=accepted_assembly_rule_versions,
            accounting_window_start=accounting_window_start,
            accounting_window_end=accounting_window_end,
            exact_gap_free_non_overlapping_coverage_required=exact_gap_free_non_overlapping_coverage_required,
            allow_representation_boundary_crossing=allow_representation_boundary_crossing,
            allow_pathway_boundary_crossing=allow_pathway_boundary_crossing,
            allow_supply_basis_boundary_crossing=allow_supply_basis_boundary_crossing,
            provenance_content_hash_required=provenance_content_hash_required,
            conflict_policy=conflict_policy,
            minimum_quality_state=minimum_quality_state,
            entity_id=entity_id,
            representation_id=representation_id,
            value_capture_pathway_id=value_capture_pathway_id,
            currency=currency,
            unit=unit,
            missingness_behavior=missingness_behavior,
            strict_known_required=strict_known_required,
            effective_at=effective_at,
            recorded_at=recorded_at,
            known_at=known_at,
            quality_state="accepted",
            conflict_state="none",
            content_hash="pending",
            supersedes_contract_version=supersedes_contract_version,
            correction_reason=correction_reason,
        )
        contract = _normalize_contract(contract)

        engine = create_sqlite_engine(self.repository.path)
        session = SessionFactory(engine).create()
        try:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            snapshots = RepositoryFactory(session).snapshots()
            _authorize_contract_correction(snapshots, contract)
            snapshots.save(methodology_contract_snapshot(contract))
            session.commit()
        except PersistenceIdentityConflictError as exc:
            session.rollback()
            raise ValuationMethodologyIntegrityError(str(exc)) from exc
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
            engine.dispose()
        return contract

    def get(self, record_id: str) -> ValuationMethodologySnapshot | None:
        return self.repository.get(record_id)

    def get_contract(self, contract_id: str, contract_version: str) -> MethodologyEvidenceInputContract | None:
        return self.repository.get_contract(contract_id, contract_version)

    def methodology_history(self, logical_id: str) -> tuple[ValuationMethodologySnapshot, ...]:
        return self.repository.methodology_history(logical_id)

    def contract_history(self, contract_id: str) -> tuple[MethodologyEvidenceInputContract, ...]:
        return self.repository.contract_history(contract_id)

    def strict_known_methodology(
        self,
        *,
        effective_as_of: datetime,
        known_by: datetime,
    ) -> ValuationMethodologySnapshot | None:
        return _strict_known(self.repository.records(), effective_as_of=effective_as_of, known_by=known_by)

    def strict_known_contract(
        self,
        *,
        contract_id: str,
        contract_version: str,
        effective_as_of: datetime,
        known_by: datetime,
    ) -> MethodologyEvidenceInputContract | None:
        contract = _strict_known_contract(
            self.repository.contracts(),
            contract_id=contract_id,
            contract_version=contract_version,
            effective_as_of=effective_as_of,
            known_by=known_by,
        )
        if contract is None:
            return None

        # ADR 0028 rule: strict_known_contract requires the exact governing snapshot
        # bound to THIS contract's methodology_logical_id in force at those
        # coordinates to have accepts_assembled_evidence == True.
        governing_snapshot = _strict_known_methodology_for_logical_id(
            self.repository.records(),
            logical_id=contract.methodology_logical_id,
            effective_as_of=effective_as_of,
            known_by=known_by,
        )
        if governing_snapshot is None or not governing_snapshot.accepts_assembled_evidence:
            return None

        return contract

    def unresolved_conflicts(self) -> tuple[ValuationMethodologySnapshot, ...]:
        return _unresolved_conflicts(self.repository.records())


def _authorize_contract_correction(snapshots: Any, record: MethodologyEvidenceInputContract) -> None:
    predecessor_version = record.supersedes_contract_version
    target_id = f"{record.contract_id}:{record.contract_version}"
    if predecessor_version is None:
        existing_root = next(
            (
                item
                for item in snapshots.query(QuerySpec(record_kind="snapshot"))
                if item.snapshot_type == "methodology-evidence-input-contract"
                and item.payload.get("contract_id") == record.contract_id
                and item.id != target_id
            ),
            None,
        )
        if existing_root is not None:
            raise ValuationMethodologyIntegrityError(
                "a root contract already exists for this contract_id; use a correction "
                "(supersedes_contract_version) instead of a second independent root contract"
            )
        return
    predecessor_target_id = f"{record.contract_id}:{predecessor_version}"
    prior_snapshot = snapshots.load(predecessor_target_id)
    if prior_snapshot is None or prior_snapshot.snapshot_type != "methodology-evidence-input-contract":
        raise ValuationMethodologyIntegrityError("contract correction predecessor does not exist")
    prior_payload = prior_snapshot.payload
    if str(prior_payload.get("contract_id")) != record.contract_id:
        raise ValuationMethodologyIntegrityError("contract correction must preserve contract_id")
    if datetime.fromisoformat(str(prior_payload["recorded_at"])) >= record.recorded_at:
        raise ValuationMethodologyIntegrityError("contract correction recorded_at must follow predecessor")
    if datetime.fromisoformat(str(prior_payload["known_at"])) >= record.known_at:
        raise ValuationMethodologyIntegrityError("contract correction known_at must follow predecessor")
    competing_successor = next(
        (
            item
            for item in snapshots.query(QuerySpec(record_kind="snapshot"))
            if item.snapshot_type == "methodology-evidence-input-contract"
            and item.payload.get("supersedes_contract_version") == predecessor_version
            and item.payload.get("contract_id") == record.contract_id
            and item.id != target_id
        ),
        None,
    )
    if competing_successor is not None:
        raise ValuationMethodologyIntegrityError("branching contract correction lineage is prohibited")


def _authorize_correction(snapshots: Any, record: ValuationMethodologySnapshot) -> None:
    predecessor_id = record.supersedes_record_id
    if predecessor_id is None:
        # A second, independent "root" record for the same logical_id would bypass the
        # correction mechanism entirely (branching without ever setting
        # supersedes_record_id). ADR 0022 authorizes exactly one methodology for the
        # first supported entity class, so unlike hunter.value_capture's provider-fact
        # conflict-flagging (appropriate for genuinely divergent external observations),
        # a second root record here is rejected outright rather than merely flagged --
        # any amendment must go through supersedes_record_id.
        existing_root = next(
            (
                item
                for item in snapshots.query(QuerySpec(record_kind="snapshot"))
                if item.snapshot_type == "valuation-methodology-snapshot"
                and item.payload.get("logical_id") == record.logical_id
                and item.id != record.record_id
            ),
            None,
        )
        if existing_root is not None:
            raise ValuationMethodologyIntegrityError(
                "a root methodology record already exists for this logical_id; use a correction "
                "(supersedes_record_id) instead of a second independent root record"
            )
        return
    prior_snapshot = snapshots.load(predecessor_id)
    if prior_snapshot is None or prior_snapshot.snapshot_type != "valuation-methodology-snapshot":
        raise ValuationMethodologyIntegrityError("correction predecessor does not exist")
    prior_payload = prior_snapshot.payload
    if str(prior_payload.get("logical_id")) != record.logical_id:
        raise ValuationMethodologyIntegrityError("correction must preserve logical_id")
    if datetime.fromisoformat(str(prior_payload["recorded_at"])) >= record.recorded_at:
        raise ValuationMethodologyIntegrityError("correction recorded_at must follow predecessor")
    if datetime.fromisoformat(str(prior_payload["known_at"])) >= record.known_at:
        raise ValuationMethodologyIntegrityError("correction known_at must follow predecessor")
    # A retried, byte-identical correction (same content-addressed record_id) is an
    # idempotent no-op, not a branch -- excluded here and handled by the ordinary
    # save()-time canonical-hash upsert below. Only a *different* record claiming the
    # same predecessor is a genuine branching correction.
    competing_successor = next(
        (
            item
            for item in snapshots.query(QuerySpec(record_kind="snapshot"))
            if item.snapshot_type == "valuation-methodology-snapshot"
            and item.payload.get("supersedes_record_id") == predecessor_id
            and item.id != record.record_id
        ),
        None,
    )
    if competing_successor is not None:
        raise ValuationMethodologyIntegrityError("branching correction lineage is prohibited")


def _strict_known_methodology_for_logical_id(
    records: tuple[ValuationMethodologySnapshot, ...],
    *,
    logical_id: str,
    effective_as_of: datetime,
    known_by: datetime,
) -> ValuationMethodologySnapshot | None:
    matching = [r for r in records if r.logical_id == logical_id]
    return _strict_known(tuple(matching), effective_as_of=effective_as_of, known_by=known_by)


def _strict_known(
    records: tuple[ValuationMethodologySnapshot, ...], *, effective_as_of: datetime, known_by: datetime
) -> ValuationMethodologySnapshot | None:
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
    current.sort(key=lambda item: (item.effective_at, item.recorded_at, item.known_at, item.record_id), reverse=True)
    return current[0] if current else None


def _strict_known_contract(
    contracts: tuple[MethodologyEvidenceInputContract, ...],
    *,
    contract_id: str,
    contract_version: str,
    effective_as_of: datetime,
    known_by: datetime,
) -> MethodologyEvidenceInputContract | None:
    effective_as_of = _aware(effective_as_of)
    known_by = _aware(known_by)
    eligible = [
        item
        for item in contracts
        if item.contract_id == contract_id
        and item.contract_version == contract_version
        and item.effective_at <= effective_as_of
        and item.recorded_at <= known_by
        and item.known_at <= known_by
        and item.quality_state == "accepted"
        and item.conflict_state in {"none", "resolved"}
    ]
    eligible_all_versions = [
        item
        for item in contracts
        if item.contract_id == contract_id
        and item.effective_at <= effective_as_of
        and item.recorded_at <= known_by
        and item.known_at <= known_by
        and item.quality_state == "accepted"
        and item.conflict_state in {"none", "resolved"}
    ]
    superseded = {
        item.supersedes_contract_version
        for item in eligible_all_versions
        if item.supersedes_contract_version is not None
    }
    current = [item for item in eligible if item.contract_version not in superseded]
    current.sort(
        key=lambda item: (item.effective_at, item.recorded_at, item.known_at, item.contract_version),
        reverse=True,
    )
    return current[0] if current else None


def _unresolved_conflicts(
    records: tuple[ValuationMethodologySnapshot, ...],
) -> tuple[ValuationMethodologySnapshot, ...]:
    superseded = {item.supersedes_record_id for item in records if item.supersedes_record_id is not None}
    unresolved = [
        item for item in records if item.record_id not in superseded and item.conflict_state in {"open", "contested"}
    ]
    return tuple(sorted(unresolved, key=lambda item: (item.logical_id, item.effective_at, item.record_id)))


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(UTC)


def _normalize(record: ValuationMethodologySnapshot) -> ValuationMethodologySnapshot:
    logical_id = _logical_id(record)
    content_hash = _content_hash(record, logical_id=logical_id)
    record_id = hashlib.sha256(f"{logical_id}:{content_hash}".encode()).hexdigest()
    return replace(record, logical_id=logical_id, content_hash=content_hash, record_id=record_id)


def _normalize_contract(contract: MethodologyEvidenceInputContract) -> MethodologyEvidenceInputContract:
    content_hash = _contract_content_hash(contract)
    return replace(contract, content_hash=content_hash)


def _logical_id(record: ValuationMethodologySnapshot) -> str:
    # A correction's logical identity is the (model, entity-class-criteria) pair it
    # governs, not the specific parameter values -- a discount-rate-policy revision is a
    # correction of the *same* methodology; a different model or entity-class-criteria
    # id is a materially different methodology family (a new logical_id), never a
    # correction chain of this one.
    raw = "|".join((record.permitted_model_identifier, record.entity_class_criteria_id))
    return hashlib.sha256(raw.encode()).hexdigest()


_CONTENT_HASH_EXCLUDED_FIELDS = frozenset({"record_id", "logical_id", "content_hash"})


def _content_hash(record: ValuationMethodologySnapshot, *, logical_id: str) -> str:
    payload = {key: value for key, value in asdict(record).items() if key not in _CONTENT_HASH_EXCLUDED_FIELDS}
    payload["logical_id"] = logical_id
    raw = json.dumps(_json_safe(payload), sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def _contract_content_hash(contract: MethodologyEvidenceInputContract) -> str:
    payload = {key: value for key, value in asdict(contract).items() if key != "content_hash"}
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
    hunter.committee.command and hunter.valuation_evidence.command. Enforced here at
    service-construction time (rather than only at a CLI entry point) so that
    unauthorized/unattested construction fails closed regardless of caller."""
    if application_root is None:
        configured = os.environ.get(_APPLICATION_ROOT_ENV, "").strip()
        if not configured:
            raise CanonicalValuationMethodologyAuthorityError(
                f"{_APPLICATION_ROOT_ENV} must identify the approved Hunter application root"
            )
        application_root = Path(configured).expanduser()
    if not application_root.is_absolute():
        raise CanonicalValuationMethodologyAuthorityError(f"{_APPLICATION_ROOT_ENV} must be an absolute path")
    return application_root.resolve()
