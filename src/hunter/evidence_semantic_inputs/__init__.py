from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from hunter.persistence.models import QuerySpec
from hunter.persistence.records import SnapshotRecord
from hunter.persistence.sql import RepositoryFactory, SessionFactory, create_schema, create_sqlite_engine
from hunter.persistence.sql.exceptions import PersistenceIdentityConflictError
from hunter.value_capture.models import FundamentalEvidenceRecord
from hunter.value_capture.repository import SupplyAndValueCaptureRepository

DEFAULT_EVIDENCE_SEMANTIC_INPUTS_DB = Path("data/data_ops.sqlite")
EVIDENCE_SEMANTIC_INPUTS_MIGRATION_ID = "generic-sql-evidence-semantic-inputs-snapshot-v1"
EVIDENCE_SEMANTIC_INPUT_POLICY_SCHEMA_VERSION = "evidence-semantic-input-policy-v1"
EVIDENCE_SEMANTIC_INPUT_RECORD_SCHEMA_VERSION = "evidence-semantic-input-record-v1"

_POLICY_SNAPSHOT_TYPE = "evidence-semantic-input-policy-snapshot"
_RECORD_SNAPSHOT_TYPE = "evidence-semantic-input-record-snapshot"
_APPLICATION_ROOT_ENV = "HUNTER_APPLICATION_ROOT"
CANONICAL_SEMANTIC_INPUT_AUTHORITY_ID = "canonical-evidence-semantic-input-authority-v1"
AUTHORIZING_ADR_REFERENCE = "ADR-0028"
POLICY_LOGICAL_ID = "canonical-evidence-semantic-input-policy"


class EvidenceSemanticInputError(ValueError):
    pass


class EvidenceSemanticInputIntegrityError(ValueError):
    pass


class CanonicalEvidenceSemanticInputAuthorityError(ValueError):
    """Raised when an unattested or unauthorized semantic input operation is attempted."""


@dataclass(frozen=True)
class EvidenceSemanticInputRule:
    rule_id: str
    match_evidence_type: str | None = None
    match_source_methodology: str | None = None
    match_attribution_rule_id: str | None = None
    match_unit: str | None = None
    match_source_id: str | None = None
    shape_id: str = ""
    accounting_meaning: Literal["period_specific", "cumulative", "event"] = "period_specific"
    supply_basis_id: str = ""
    pathway_id: str = ""
    asserts_representation_continuity: bool = False
    currency: str = ""
    raw_unit: str = ""
    semantic_dimensions: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        _required_text(self, "rule_id", "shape_id", "supply_basis_id", "pathway_id", "currency", "raw_unit")
        if self.accounting_meaning not in {"period_specific", "cumulative", "event"}:
            raise EvidenceSemanticInputError("unknown accounting meaning in rule")


@dataclass(frozen=True)
class EvidenceSemanticInputPolicySnapshot:
    record_id: str
    logical_id: str
    schema_version: str
    semantic_version: str
    version: str
    rules: tuple[EvidenceSemanticInputRule, ...]
    effective_at: datetime
    recorded_at: datetime
    known_at: datetime
    quality_state: Literal["accepted"]
    conflict_state: Literal["none", "resolved"]
    authorizing_adr_reference: str
    authorized_by: str
    content_hash: str
    supersedes_version: str | None = None
    supersedes_record_id: str | None = None
    correction_reason: str = ""

    def __post_init__(self) -> None:
        _required_text(
            self,
            "record_id",
            "logical_id",
            "schema_version",
            "semantic_version",
            "version",
            "authorizing_adr_reference",
            "authorized_by",
            "content_hash",
        )
        if self.quality_state != "accepted" or self.conflict_state not in {"none", "resolved"}:
            raise EvidenceSemanticInputError("policy snapshot is not authoritative")
        if self.logical_id != POLICY_LOGICAL_ID:
            raise EvidenceSemanticInputError(f"logical_id must equal {POLICY_LOGICAL_ID!r}")
        if self.authorizing_adr_reference != AUTHORIZING_ADR_REFERENCE:
            raise EvidenceSemanticInputError(f"authorizing_adr_reference must equal {AUTHORIZING_ADR_REFERENCE!r}")
        if self.authorized_by != CANONICAL_SEMANTIC_INPUT_AUTHORITY_ID:
            raise EvidenceSemanticInputError(f"authorized_by must equal {CANONICAL_SEMANTIC_INPUT_AUTHORITY_ID!r}")
        _normalize_chronology(self)
        _validate_correction(self)


@dataclass(frozen=True)
class EvidenceSemanticInputRecord:
    record_id: str
    logical_id: str
    schema_version: str
    semantic_version: str
    evidence_record_id: str
    evidence_record_version: str
    evidence_record_content_hash: str
    policy_record_id: str
    policy_logical_id: str
    policy_semantic_version: str
    policy_content_hash: str
    shape_id: str
    accounting_meaning: Literal["period_specific", "cumulative", "event"]
    supply_basis_id: str
    pathway_id: str
    representation_continuity_asserted: bool
    representation_continuity_proof_id: str
    representation_continuity_proof_version: str
    representation_continuity_proof_content_hash: str
    currency: str
    raw_unit: str
    semantic_dimensions: dict[str, Any]
    matched_rule_id: str
    evaluation_fingerprint: str
    authorizing_adr_reference: str
    authorized_by: str
    effective_at: datetime
    recorded_at: datetime
    known_at: datetime
    quality_state: Literal["accepted"]
    conflict_state: Literal["none", "resolved"]
    content_hash: str
    supersedes_record_id: str | None = None
    correction_reason: str = ""

    def __post_init__(self) -> None:
        _required_text(
            self,
            "record_id",
            "logical_id",
            "schema_version",
            "semantic_version",
            "evidence_record_id",
            "evidence_record_version",
            "evidence_record_content_hash",
            "policy_record_id",
            "policy_logical_id",
            "policy_semantic_version",
            "policy_content_hash",
            "shape_id",
            "supply_basis_id",
            "pathway_id",
            "currency",
            "raw_unit",
            "matched_rule_id",
            "evaluation_fingerprint",
            "authorizing_adr_reference",
            "authorized_by",
            "content_hash",
        )
        if self.representation_continuity_asserted:
            _required_text(
                self,
                "representation_continuity_proof_id",
                "representation_continuity_proof_version",
                "representation_continuity_proof_content_hash",
            )
        if self.accounting_meaning not in {"period_specific", "cumulative", "event"}:
            raise EvidenceSemanticInputError("unknown accounting meaning in record")
        if self.quality_state != "accepted" or self.conflict_state not in {"none", "resolved"}:
            raise EvidenceSemanticInputError("semantic input record is not authoritative")
        if self.authorizing_adr_reference != AUTHORIZING_ADR_REFERENCE:
            raise EvidenceSemanticInputError(f"authorizing_adr_reference must equal {AUTHORIZING_ADR_REFERENCE!r}")
        if self.authorized_by != CANONICAL_SEMANTIC_INPUT_AUTHORITY_ID:
            raise EvidenceSemanticInputError(f"authorized_by must equal {CANONICAL_SEMANTIC_INPUT_AUTHORITY_ID!r}")
        _normalize_chronology(self)
        _validate_record_correction(self)


class EvidenceSemanticInputRepository:
    """Read boundary for EvidenceSemanticInput records stored in generic SQL persistence."""

    def __init__(self, path: str | Path = DEFAULT_EVIDENCE_SEMANTIC_INPUTS_DB) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        engine = create_sqlite_engine(self.path)
        try:
            create_schema(engine)
        finally:
            engine.dispose()

    def get_policy(self, record_id: str) -> EvidenceSemanticInputPolicySnapshot | None:
        snapshot = self._load_policy(record_id)
        return _policy_from_payload(snapshot.payload) if snapshot is not None else None

    def get_policy_version(self, version: str) -> EvidenceSemanticInputPolicySnapshot | None:
        snapshot = self._load_policy_version(version)
        return _policy_from_payload(snapshot.payload) if snapshot is not None else None

    def get_input_record(self, record_id: str) -> EvidenceSemanticInputRecord | None:
        snapshot = self._load_record(record_id)
        return _record_from_payload(snapshot.payload) if snapshot is not None else None

    def policy_history(self, logical_id: str = POLICY_LOGICAL_ID) -> tuple[EvidenceSemanticInputPolicySnapshot, ...]:
        if not logical_id.strip():
            raise ValueError("logical_id must not be blank")
        records = [record for record in self._policies_skipping_malformed() if record.logical_id == logical_id]
        records.sort(
            key=lambda item: (item.effective_at, item.recorded_at, item.known_at, item.record_id),
        )
        return tuple(records)

    def policy_records(self) -> tuple[EvidenceSemanticInputPolicySnapshot, ...]:
        return tuple(
            sorted(
                self._policies_skipping_malformed(),
                key=lambda item: (item.logical_id, item.effective_at, item.recorded_at, item.known_at, item.record_id),
            )
        )

    def input_records(self) -> tuple[EvidenceSemanticInputRecord, ...]:
        return tuple(
            sorted(
                self._records_skipping_malformed(),
                key=lambda item: (item.logical_id, item.effective_at, item.recorded_at, item.known_at, item.record_id),
            )
        )

    def _policies_skipping_malformed(self) -> tuple[EvidenceSemanticInputPolicySnapshot, ...]:
        records = []
        for snapshot in self._snapshots(_POLICY_SNAPSHOT_TYPE):
            try:
                records.append(_policy_from_payload(snapshot.payload))
            except (EvidenceSemanticInputIntegrityError, ValueError, KeyError, TypeError, AttributeError):
                continue
        return tuple(records)

    def _records_skipping_malformed(self) -> tuple[EvidenceSemanticInputRecord, ...]:
        records = []
        for snapshot in self._snapshots(_RECORD_SNAPSHOT_TYPE):
            try:
                records.append(_record_from_payload(snapshot.payload))
            except (EvidenceSemanticInputIntegrityError, ValueError, KeyError, TypeError, AttributeError):
                continue
        return tuple(records)

    def _load_policy(self, record_id: str) -> SnapshotRecord | None:
        engine = create_sqlite_engine(self.path)
        session = SessionFactory(engine).create()
        try:
            snapshots = RepositoryFactory(session).snapshots().query(QuerySpec(record_kind="snapshot"))
            for snapshot in snapshots:
                if snapshot.snapshot_type == _POLICY_SNAPSHOT_TYPE and snapshot.payload.get("record_id") == record_id:
                    return snapshot
            return None
        finally:
            session.close()
            engine.dispose()

    def _load_policy_version(self, version: str) -> SnapshotRecord | None:
        target_id = f"{POLICY_LOGICAL_ID}:{version}"
        engine = create_sqlite_engine(self.path)
        session = SessionFactory(engine).create()
        try:
            snapshot = RepositoryFactory(session).snapshots().load(target_id)
            if snapshot is None or snapshot.snapshot_type != _POLICY_SNAPSHOT_TYPE:
                return None
            return snapshot
        finally:
            session.close()
            engine.dispose()

    def _load_record(self, record_id: str) -> SnapshotRecord | None:
        target_id = f"input-record:{record_id}"
        engine = create_sqlite_engine(self.path)
        session = SessionFactory(engine).create()
        try:
            snapshot = RepositoryFactory(session).snapshots().load(target_id)
            if snapshot is None or snapshot.snapshot_type != _RECORD_SNAPSHOT_TYPE:
                return None
            return snapshot
        finally:
            session.close()
            engine.dispose()

    def _snapshots(self, snapshot_type: str) -> tuple[SnapshotRecord, ...]:
        engine = create_sqlite_engine(self.path)
        session = SessionFactory(engine).create()
        try:
            records = RepositoryFactory(session).snapshots().query(QuerySpec(record_kind="snapshot"))
            return tuple(item for item in records if item.snapshot_type == snapshot_type)
        finally:
            session.close()
            engine.dispose()


class CanonicalEvidenceSemanticInputAuthority:
    """Sole canonical owner of semantic-classification inputs."""

    def __init__(
        self,
        *,
        repository: EvidenceSemanticInputRepository,
        value_capture_repository: SupplyAndValueCaptureRepository | None = None,
        application_root: Path | None = None,
    ) -> None:
        self.repository = repository
        self.value_capture_repository = value_capture_repository or SupplyAndValueCaptureRepository(repository.path)
        self._application_root = _authorized_application_root(application_root)

    def persist_policy(
        self,
        *,
        version: str,
        rules: tuple[EvidenceSemanticInputRule, ...],
        effective_at: datetime,
        recorded_at: datetime,
        known_at: datetime,
        quality_state: Literal["accepted"] = "accepted",
        conflict_state: Literal["none", "resolved"] = "none",
        supersedes_version: str | None = None,
        supersedes_record_id: str | None = None,
        correction_reason: str = "",
    ) -> EvidenceSemanticInputPolicySnapshot:
        record = EvidenceSemanticInputPolicySnapshot(
            record_id="pending",
            logical_id=POLICY_LOGICAL_ID,
            schema_version=EVIDENCE_SEMANTIC_INPUT_POLICY_SCHEMA_VERSION,
            semantic_version="1.0.0",
            version=version,
            rules=rules,
            effective_at=effective_at,
            recorded_at=recorded_at,
            known_at=known_at,
            quality_state=quality_state,
            conflict_state=conflict_state,
            authorizing_adr_reference=AUTHORIZING_ADR_REFERENCE,
            authorized_by=CANONICAL_SEMANTIC_INPUT_AUTHORITY_ID,
            content_hash="pending",
            supersedes_version=supersedes_version,
            supersedes_record_id=supersedes_record_id,
            correction_reason=correction_reason,
        )
        record = _normalize_policy(record)

        engine = create_sqlite_engine(self.repository.path)
        session = SessionFactory(engine).create()
        try:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            snapshots = RepositoryFactory(session).snapshots()
            _authorize_policy_correction(snapshots, record)
            snapshots.save(policy_snapshot(record))
            session.commit()
        except PersistenceIdentityConflictError as exc:
            session.rollback()
            raise EvidenceSemanticInputIntegrityError(str(exc)) from exc
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
            engine.dispose()
        return record

    def strict_known_policy(
        self,
        *,
        policy_logical_id: str = POLICY_LOGICAL_ID,
        effective_as_of: datetime,
        known_by: datetime,
    ) -> EvidenceSemanticInputPolicySnapshot | None:
        records = self.repository.policy_records()
        return _strict_known_policy(
            records, policy_logical_id=policy_logical_id, effective_as_of=effective_as_of, known_by=known_by
        )

    def derive_and_persist_input(
        self,
        *,
        evidence_record: FundamentalEvidenceRecord,
        policy_snapshot: EvidenceSemanticInputPolicySnapshot,
        recorded_at: datetime,
        known_at: datetime,
        supersedes_record_id: str | None = None,
        correction_reason: str = "",
    ) -> EvidenceSemanticInputRecord:
        recorded_at = _aware(recorded_at)
        known_at = _aware(known_at)

        # 1. Load exact persisted native evidence record and verify agreement
        persisted_evidence = self.value_capture_repository.evidence(evidence_record.record_id)
        if persisted_evidence is None:
            raise EvidenceSemanticInputError(
                f"unpersisted evidence record: {evidence_record.record_id!r} does not exist in value capture repository"
            )
        if (
            persisted_evidence.record_id != evidence_record.record_id
            or persisted_evidence.semantic_version != evidence_record.semantic_version
            or persisted_evidence.content_hash != evidence_record.content_hash
            or persisted_evidence != evidence_record
        ):
            raise EvidenceSemanticInputError("caller-supplied evidence record does not match persisted record")
        if (
            persisted_evidence.effective_at > known_at
            or persisted_evidence.recorded_at > known_at
            or persisted_evidence.known_at > known_at
        ):
            raise EvidenceSemanticInputError("evidence record is not strict-known at derivation cutoff")

        # 2. Load exact persisted policy snapshot and verify agreement
        persisted_policy = self.repository.get_policy_version(policy_snapshot.version)
        if persisted_policy is None or persisted_policy.record_id != policy_snapshot.record_id:
            persisted_policy = self.repository.get_policy(policy_snapshot.record_id)
        if persisted_policy is None:
            raise EvidenceSemanticInputError(
                f"unpersisted policy snapshot: {policy_snapshot.record_id!r} version {policy_snapshot.version!r} does not exist"
            )
        if (
            persisted_policy.record_id != policy_snapshot.record_id
            or persisted_policy.version != policy_snapshot.version
            or persisted_policy.content_hash != policy_snapshot.content_hash
            or persisted_policy != policy_snapshot
        ):
            raise EvidenceSemanticInputError("caller-supplied policy snapshot does not match persisted record")
        if (
            persisted_policy.effective_at > known_at
            or persisted_policy.recorded_at > known_at
            or persisted_policy.known_at > known_at
        ):
            raise EvidenceSemanticInputError("policy snapshot is not strict-known at derivation cutoff")

        # Verify policy snapshot is strict-known tip at cutoff
        strict_policy = self.strict_known_policy(
            policy_logical_id=policy_snapshot.logical_id,
            effective_as_of=evidence_record.effective_at,
            known_by=known_at,
        )
        if strict_policy is None or strict_policy.record_id != policy_snapshot.record_id:
            raise EvidenceSemanticInputError(
                "specified policy snapshot is not the strict-known tip at requested cutoff"
            )

        if evidence_record.quality_state != "accepted" or evidence_record.conflict_state not in {"none", "resolved"}:
            raise EvidenceSemanticInputError("evidence record is not authoritative")
        if policy_snapshot.quality_state != "accepted" or policy_snapshot.conflict_state not in {"none", "resolved"}:
            raise EvidenceSemanticInputError("policy snapshot is not authoritative")

        matched_rules = []
        for rule in policy_snapshot.rules:
            if _rule_matches(rule, evidence_record):
                matched_rules.append(rule)

        if not matched_rules:
            raise EvidenceSemanticInputError(f"no policy rule matched evidence_record {evidence_record.record_id!r}")
        if len(matched_rules) > 1:
            # Check if all matched rules yield identical outputs
            outputs = [
                (
                    r.shape_id,
                    r.accounting_meaning,
                    r.supply_basis_id,
                    r.pathway_id,
                    r.asserts_representation_continuity,
                    r.currency,
                    r.raw_unit,
                    json.dumps(r.semantic_dimensions or {}, sort_keys=True),
                )
                for r in matched_rules
            ]
            if len(set(outputs)) > 1:
                raise EvidenceSemanticInputError(
                    f"conflict: multiple non-equivalent policy rules matched evidence_record {evidence_record.record_id!r}"
                )

        selected_rule = matched_rules[0]

        logical_id = f"semantic-input:{evidence_record.record_id}:{evidence_record.semantic_version}:{policy_snapshot.logical_id}"
        effective_at = evidence_record.effective_at

        rep_continuity_proof_id = policy_snapshot.record_id if selected_rule.asserts_representation_continuity else ""
        rep_continuity_proof_version = (
            policy_snapshot.version if selected_rule.asserts_representation_continuity else ""
        )
        rep_continuity_proof_hash = (
            policy_snapshot.content_hash if selected_rule.asserts_representation_continuity else ""
        )

        semantic_dims = dict(selected_rule.semantic_dimensions or {})
        eval_fingerprint = _evaluation_fingerprint(evidence_record, policy_snapshot, selected_rule)

        rec = EvidenceSemanticInputRecord(
            record_id="pending",
            logical_id=logical_id,
            schema_version=EVIDENCE_SEMANTIC_INPUT_RECORD_SCHEMA_VERSION,
            semantic_version="1.0.0",
            evidence_record_id=evidence_record.record_id,
            evidence_record_version=evidence_record.semantic_version,
            evidence_record_content_hash=evidence_record.content_hash,
            policy_record_id=policy_snapshot.record_id,
            policy_logical_id=policy_snapshot.logical_id,
            policy_semantic_version=policy_snapshot.semantic_version,
            policy_content_hash=policy_snapshot.content_hash,
            shape_id=selected_rule.shape_id,
            accounting_meaning=selected_rule.accounting_meaning,
            supply_basis_id=selected_rule.supply_basis_id,
            pathway_id=selected_rule.pathway_id,
            representation_continuity_asserted=selected_rule.asserts_representation_continuity,
            representation_continuity_proof_id=rep_continuity_proof_id,
            representation_continuity_proof_version=rep_continuity_proof_version,
            representation_continuity_proof_content_hash=rep_continuity_proof_hash,
            currency=selected_rule.currency,
            raw_unit=selected_rule.raw_unit,
            semantic_dimensions=semantic_dims,
            matched_rule_id=selected_rule.rule_id,
            evaluation_fingerprint=eval_fingerprint,
            authorizing_adr_reference=AUTHORIZING_ADR_REFERENCE,
            authorized_by=CANONICAL_SEMANTIC_INPUT_AUTHORITY_ID,
            effective_at=effective_at,
            recorded_at=recorded_at,
            known_at=known_at,
            quality_state="accepted",
            conflict_state="none",
            content_hash="pending",
            supersedes_record_id=supersedes_record_id,
            correction_reason=correction_reason,
        )
        rec = _normalize_input_record(rec)

        engine = create_sqlite_engine(self.repository.path)
        session = SessionFactory(engine).create()
        try:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            snapshots = RepositoryFactory(session).snapshots()
            _authorize_record_correction(snapshots, rec)
            snapshots.save(record_snapshot(rec))
            session.commit()
        except PersistenceIdentityConflictError as exc:
            session.rollback()
            raise EvidenceSemanticInputIntegrityError(str(exc)) from exc
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
            engine.dispose()
        return rec

    def strict_known_input(
        self,
        *,
        evidence_record_id: str,
        evidence_record_version: str,
        effective_as_of: datetime,
        known_by: datetime,
    ) -> EvidenceSemanticInputRecord | None:
        records = self.repository.input_records()
        policies = self.repository.policy_records()
        return _strict_known_input(
            records,
            policies=policies,
            evidence_record_id=evidence_record_id,
            evidence_record_version=evidence_record_version,
            effective_as_of=effective_as_of,
            known_by=known_by,
        )


def policy_snapshot(record: EvidenceSemanticInputPolicySnapshot) -> SnapshotRecord:
    target_id = f"{record.logical_id}:{record.version}"
    return SnapshotRecord(
        id=target_id,
        created_at=record.recorded_at,
        effective_at=record.effective_at,
        snapshot_type=_POLICY_SNAPSHOT_TYPE,
        target_id=record.logical_id,
        record_ids=(record.record_id,),
        payload=_payload(record),
        metadata={
            "authority_class": "production-authoritative",
            "domain": "evidence-semantic-inputs",
            "logical_id": record.logical_id,
            "version": record.version,
            "known_at": record.known_at.isoformat(),
        },
    )


def record_snapshot(record: EvidenceSemanticInputRecord) -> SnapshotRecord:
    target_id = f"input-record:{record.record_id}"
    return SnapshotRecord(
        id=target_id,
        created_at=record.recorded_at,
        effective_at=record.effective_at,
        snapshot_type=_RECORD_SNAPSHOT_TYPE,
        target_id=record.logical_id,
        record_ids=(record.record_id,),
        payload=_payload(record),
        metadata={
            "authority_class": "production-authoritative",
            "domain": "evidence-semantic-inputs",
            "logical_id": record.logical_id,
            "evidence_record_id": record.evidence_record_id,
            "known_at": record.known_at.isoformat(),
        },
    )


def _authorize_policy_correction(snapshots: Any, record: EvidenceSemanticInputPolicySnapshot) -> None:
    predecessor_id = record.supersedes_record_id
    predecessor_version = record.supersedes_version
    target_id = f"{record.logical_id}:{record.version}"

    if predecessor_id is None and predecessor_version is None:
        existing_root = next(
            (
                item
                for item in snapshots.query(QuerySpec(record_kind="snapshot"))
                if item.snapshot_type == _POLICY_SNAPSHOT_TYPE
                and item.payload.get("logical_id") == record.logical_id
                and item.id != target_id
            ),
            None,
        )
        if existing_root is not None:
            raise EvidenceSemanticInputIntegrityError(
                f"a root policy record already exists for logical_id {record.logical_id!r}; "
                "use a correction (supersedes_version and supersedes_record_id) instead of a second independent root"
            )

        existing_version = snapshots.load(target_id)
        if existing_version is not None and existing_version.snapshot_type == _POLICY_SNAPSHOT_TYPE:
            raise EvidenceSemanticInputIntegrityError(
                f"policy version {record.version!r} already exists; use a correction instead"
            )
        return

    if predecessor_id is None or predecessor_version is None:
        raise EvidenceSemanticInputIntegrityError(
            "supersedes_version and supersedes_record_id must be provided together"
        )

    predecessor_target_id = f"{record.logical_id}:{predecessor_version}"
    prior_snapshot = snapshots.load(predecessor_target_id)
    if prior_snapshot is None or prior_snapshot.snapshot_type != _POLICY_SNAPSHOT_TYPE:
        raise EvidenceSemanticInputIntegrityError("correction predecessor does not exist")

    prior_payload = prior_snapshot.payload
    if prior_payload.get("record_id") != predecessor_id:
        raise EvidenceSemanticInputIntegrityError("correction predecessor record_id mismatch")
    if str(prior_payload.get("logical_id")) != record.logical_id:
        raise EvidenceSemanticInputIntegrityError("correction must preserve logical_id")
    if datetime.fromisoformat(str(prior_payload["recorded_at"])) >= record.recorded_at:
        raise EvidenceSemanticInputIntegrityError("correction recorded_at must follow predecessor")
    if datetime.fromisoformat(str(prior_payload["known_at"])) >= record.known_at:
        raise EvidenceSemanticInputIntegrityError("correction known_at must follow predecessor")

    competing_successor = next(
        (
            item
            for item in snapshots.query(QuerySpec(record_kind="snapshot"))
            if item.snapshot_type == _POLICY_SNAPSHOT_TYPE
            and item.payload.get("supersedes_version") == predecessor_version
            and item.id != target_id
        ),
        None,
    )
    if competing_successor is not None:
        raise EvidenceSemanticInputIntegrityError("branching correction lineage is prohibited")


def _authorize_record_correction(snapshots: Any, record: EvidenceSemanticInputRecord) -> None:
    predecessor_id = record.supersedes_record_id
    target_id = f"input-record:{record.record_id}"

    if predecessor_id is None:
        existing_root = next(
            (
                item
                for item in snapshots.query(QuerySpec(record_kind="snapshot"))
                if item.snapshot_type == _RECORD_SNAPSHOT_TYPE
                and item.payload.get("logical_id") == record.logical_id
                and item.id != target_id
            ),
            None,
        )
        if existing_root is not None:
            raise EvidenceSemanticInputIntegrityError(
                f"a root input record already exists for logical_id {record.logical_id!r}; "
                "use a correction (supersedes_record_id) instead of a second independent root"
            )

        existing_rec = snapshots.load(target_id)
        if existing_rec is not None and existing_rec.snapshot_type == _RECORD_SNAPSHOT_TYPE:
            raise EvidenceSemanticInputIntegrityError(f"input record {record.record_id!r} already exists")
        return

    predecessor_target_id = f"input-record:{predecessor_id}"
    prior_snapshot = snapshots.load(predecessor_target_id)
    if prior_snapshot is None or prior_snapshot.snapshot_type != _RECORD_SNAPSHOT_TYPE:
        raise EvidenceSemanticInputIntegrityError("correction predecessor does not exist")

    prior_payload = prior_snapshot.payload
    if str(prior_payload.get("logical_id")) != record.logical_id:
        raise EvidenceSemanticInputIntegrityError("correction must preserve logical_id")
    if datetime.fromisoformat(str(prior_payload["recorded_at"])) >= record.recorded_at:
        raise EvidenceSemanticInputIntegrityError("correction recorded_at must follow predecessor")
    if datetime.fromisoformat(str(prior_payload["known_at"])) >= record.known_at:
        raise EvidenceSemanticInputIntegrityError("correction known_at must follow predecessor")

    competing_successor = next(
        (
            item
            for item in snapshots.query(QuerySpec(record_kind="snapshot"))
            if item.snapshot_type == _RECORD_SNAPSHOT_TYPE
            and item.payload.get("supersedes_record_id") == predecessor_id
            and item.id != target_id
        ),
        None,
    )
    if competing_successor is not None:
        raise EvidenceSemanticInputIntegrityError("branching correction lineage is prohibited")


def _strict_known_policy(
    records: tuple[EvidenceSemanticInputPolicySnapshot, ...],
    *,
    policy_logical_id: str,
    effective_as_of: datetime,
    known_by: datetime,
) -> EvidenceSemanticInputPolicySnapshot | None:
    effective_as_of = _aware(effective_as_of)
    known_by = _aware(known_by)
    eligible = [
        item
        for item in records
        if item.logical_id == policy_logical_id
        and item.effective_at <= effective_as_of
        and item.recorded_at <= known_by
        and item.known_at <= known_by
        and item.quality_state == "accepted"
        and item.conflict_state in {"none", "resolved"}
    ]
    if not eligible:
        return None

    superseded_versions = {item.supersedes_version for item in eligible if item.supersedes_version is not None}
    superseded_records = {item.supersedes_record_id for item in eligible if item.supersedes_record_id is not None}

    current = [
        item
        for item in eligible
        if item.version not in superseded_versions and item.record_id not in superseded_records
    ]
    current.sort(
        key=lambda item: (item.effective_at, item.known_at, item.recorded_at, item.version, item.record_id),
        reverse=True,
    )
    return current[0] if current else None


def _strict_known_input(
    records: tuple[EvidenceSemanticInputRecord, ...],
    *,
    policies: tuple[EvidenceSemanticInputPolicySnapshot, ...],
    evidence_record_id: str,
    evidence_record_version: str,
    effective_as_of: datetime,
    known_by: datetime,
) -> EvidenceSemanticInputRecord | None:
    effective_as_of = _aware(effective_as_of)
    known_by = _aware(known_by)
    eligible = [
        item
        for item in records
        if item.evidence_record_id == evidence_record_id
        and item.evidence_record_version == evidence_record_version
        and item.effective_at <= effective_as_of
        and item.recorded_at <= known_by
        and item.known_at <= known_by
        and item.quality_state == "accepted"
        and item.conflict_state in {"none", "resolved"}
    ]
    if not eligible:
        return None

    # Check that governing policy is also strict-known at same coordinates
    valid_inputs = []
    for item in eligible:
        governing_policy = _strict_known_policy(
            policies,
            policy_logical_id=item.policy_logical_id,
            effective_as_of=effective_as_of,
            known_by=known_by,
        )
        if governing_policy is not None and governing_policy.record_id == item.policy_record_id:
            valid_inputs.append(item)

    if not valid_inputs:
        return None

    superseded_records = {item.supersedes_record_id for item in valid_inputs if item.supersedes_record_id is not None}
    current = [item for item in valid_inputs if item.record_id not in superseded_records]

    current.sort(
        key=lambda item: (item.effective_at, item.known_at, item.recorded_at, item.record_id),
        reverse=True,
    )
    return current[0] if current else None


def _rule_matches(rule: EvidenceSemanticInputRule, rec: FundamentalEvidenceRecord) -> bool:
    if rule.match_evidence_type is not None and rule.match_evidence_type != rec.evidence_type:
        return False
    if rule.match_source_methodology is not None and rule.match_source_methodology != rec.source_methodology:
        return False
    if rule.match_attribution_rule_id is not None and rule.match_attribution_rule_id != rec.attribution_rule_id:
        return False
    if rule.match_unit is not None and rule.match_unit != rec.unit:
        return False
    if rule.match_source_id is not None and rule.match_source_id != rec.source_id:
        return False
    return True


def _evaluation_fingerprint(
    rec: FundamentalEvidenceRecord,
    policy: EvidenceSemanticInputPolicySnapshot,
    rule: EvidenceSemanticInputRule,
) -> str:
    raw = {
        "evidence_record_id": rec.record_id,
        "evidence_record_content_hash": rec.content_hash,
        "policy_record_id": policy.record_id,
        "policy_content_hash": policy.content_hash,
        "matched_rule_id": rule.rule_id,
        "shape_id": rule.shape_id,
        "accounting_meaning": rule.accounting_meaning,
        "supply_basis_id": rule.supply_basis_id,
        "pathway_id": rule.pathway_id,
        "asserts_representation_continuity": rule.asserts_representation_continuity,
        "currency": rule.currency,
        "raw_unit": rule.raw_unit,
        "semantic_dimensions": rule.semantic_dimensions or {},
    }
    encoded = json.dumps(raw, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _normalize_policy(record: EvidenceSemanticInputPolicySnapshot) -> EvidenceSemanticInputPolicySnapshot:
    content_hash = _policy_content_hash(record)
    record_id = hashlib.sha256(f"{record.logical_id}:{record.version}:{content_hash}".encode()).hexdigest()
    normalized = replace(record, content_hash=content_hash, record_id=record_id)
    _normalize_chronology(normalized)
    return normalized


def _normalize_input_record(record: EvidenceSemanticInputRecord) -> EvidenceSemanticInputRecord:
    content_hash = _record_content_hash(record)
    record_id = hashlib.sha256(f"{record.logical_id}:{content_hash}".encode()).hexdigest()
    normalized = replace(record, content_hash=content_hash, record_id=record_id)
    _normalize_chronology(normalized)
    return normalized


_CONTENT_HASH_EXCLUDED_FIELDS = frozenset({"record_id", "content_hash"})


def _policy_content_hash(record: EvidenceSemanticInputPolicySnapshot) -> str:
    payload = {key: value for key, value in asdict(record).items() if key not in _CONTENT_HASH_EXCLUDED_FIELDS}
    raw = json.dumps(_json_safe(payload), sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def _record_content_hash(record: EvidenceSemanticInputRecord) -> str:
    payload = {key: value for key, value in asdict(record).items() if key not in _CONTENT_HASH_EXCLUDED_FIELDS}
    raw = json.dumps(_json_safe(payload), sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def _policy_from_payload(payload: dict[str, Any]) -> EvidenceSemanticInputPolicySnapshot:
    if not isinstance(payload, dict):
        raise EvidenceSemanticInputIntegrityError("policy payload must be a mapping")
    required_fields = (
        "record_id",
        "logical_id",
        "schema_version",
        "semantic_version",
        "version",
        "rules",
        "effective_at",
        "recorded_at",
        "known_at",
        "quality_state",
        "conflict_state",
        "authorizing_adr_reference",
        "authorized_by",
        "content_hash",
    )
    missing = tuple(name for name in required_fields if name not in payload)
    if missing:
        raise EvidenceSemanticInputIntegrityError(
            "legacy policy snapshot is missing required fields: " + ",".join(missing)
        )
    result = dict(payload)
    try:
        rules_raw = result.get("rules")
        if not isinstance(rules_raw, (list, tuple)):
            raise EvidenceSemanticInputIntegrityError("rules must be an iterable sequence")
        rules_list = []
        for r in rules_raw:
            if isinstance(r, dict):
                rules_list.append(EvidenceSemanticInputRule(**r))
            elif isinstance(r, EvidenceSemanticInputRule):
                rules_list.append(r)
            else:
                raise EvidenceSemanticInputIntegrityError(f"invalid rule item: {r!r}")
        result["rules"] = tuple(rules_list)
        for name in ("effective_at", "recorded_at", "known_at"):
            result[name] = datetime.fromisoformat(str(result[name])).astimezone(UTC)

        deserialized = EvidenceSemanticInputPolicySnapshot(**result)
    except (TypeError, AttributeError, ValueError) as exc:
        raise EvidenceSemanticInputIntegrityError(f"malformed policy payload: {exc}") from exc

    expected_content_hash = _policy_content_hash(deserialized)
    if deserialized.content_hash != expected_content_hash:
        raise EvidenceSemanticInputIntegrityError(
            f"persisted policy content hash mismatch: expected {expected_content_hash!r}, got {deserialized.content_hash!r}"
        )
    expected_record_id = hashlib.sha256(
        f"{deserialized.logical_id}:{deserialized.version}:{expected_content_hash}".encode()
    ).hexdigest()
    if deserialized.record_id != expected_record_id:
        raise EvidenceSemanticInputIntegrityError(
            f"persisted policy record_id mismatch: expected {expected_record_id!r}, got {deserialized.record_id!r}"
        )

    return deserialized


def _record_from_payload(payload: dict[str, Any]) -> EvidenceSemanticInputRecord:
    if not isinstance(payload, dict):
        raise EvidenceSemanticInputIntegrityError("record payload must be a mapping")
    required_fields = (
        "record_id",
        "logical_id",
        "schema_version",
        "semantic_version",
        "evidence_record_id",
        "evidence_record_version",
        "evidence_record_content_hash",
        "policy_record_id",
        "policy_logical_id",
        "policy_semantic_version",
        "policy_content_hash",
        "shape_id",
        "accounting_meaning",
        "supply_basis_id",
        "pathway_id",
        "representation_continuity_asserted",
        "representation_continuity_proof_id",
        "representation_continuity_proof_version",
        "representation_continuity_proof_content_hash",
        "currency",
        "raw_unit",
        "semantic_dimensions",
        "matched_rule_id",
        "evaluation_fingerprint",
        "authorizing_adr_reference",
        "authorized_by",
        "effective_at",
        "recorded_at",
        "known_at",
        "quality_state",
        "conflict_state",
        "content_hash",
    )
    missing = tuple(name for name in required_fields if name not in payload)
    if missing:
        raise EvidenceSemanticInputIntegrityError(
            "legacy record payload is missing required fields: " + ",".join(missing)
        )
    result = dict(payload)
    try:
        for name in ("effective_at", "recorded_at", "known_at"):
            result[name] = datetime.fromisoformat(str(result[name])).astimezone(UTC)
        deserialized = EvidenceSemanticInputRecord(**result)
    except (TypeError, AttributeError, ValueError) as exc:
        raise EvidenceSemanticInputIntegrityError(f"malformed input record payload: {exc}") from exc

    expected_content_hash = _record_content_hash(deserialized)
    if deserialized.content_hash != expected_content_hash:
        raise EvidenceSemanticInputIntegrityError(
            f"persisted input record content hash mismatch: expected {expected_content_hash!r}, got {deserialized.content_hash!r}"
        )
    expected_record_id = hashlib.sha256(f"{deserialized.logical_id}:{expected_content_hash}".encode()).hexdigest()
    if deserialized.record_id != expected_record_id:
        raise EvidenceSemanticInputIntegrityError(
            f"persisted input record record_id mismatch: expected {expected_record_id!r}, got {deserialized.record_id!r}"
        )

    return deserialized


def _payload(value: Any) -> dict[str, Any]:
    return _json_safe(asdict(value))


def _json_safe(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _required_text(instance: object, *fields: str) -> None:
    for field in fields:
        value = getattr(instance, field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} must not be blank")


def _normalize_chronology(instance: Any) -> None:
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


def _validate_correction(instance: EvidenceSemanticInputPolicySnapshot) -> None:
    predecessor_v = instance.supersedes_version
    predecessor_id = instance.supersedes_record_id
    reason = instance.correction_reason
    if bool(predecessor_v) != bool(predecessor_id):
        raise ValueError("supersedes_version and supersedes_record_id must be provided together")
    if bool(predecessor_v) != bool(reason.strip()):
        raise ValueError("supersedes fields and correction_reason must be provided together")


def _validate_record_correction(instance: EvidenceSemanticInputRecord) -> None:
    predecessor_id = instance.supersedes_record_id
    reason = instance.correction_reason
    if bool(predecessor_id) != bool(reason.strip()):
        raise ValueError("supersedes_record_id and correction_reason must be provided together")


def _utc(name: str, value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(UTC)


def _authorized_application_root(application_root: Path | None) -> Path:
    if application_root is None:
        configured = os.environ.get(_APPLICATION_ROOT_ENV, "").strip()
        if not configured:
            raise CanonicalEvidenceSemanticInputAuthorityError(
                f"{_APPLICATION_ROOT_ENV} must identify the approved Hunter application root"
            )
        application_root = Path(configured).expanduser()
    if not application_root.is_absolute():
        raise CanonicalEvidenceSemanticInputAuthorityError(f"{_APPLICATION_ROOT_ENV} must be an absolute path")
    return application_root.resolve()
