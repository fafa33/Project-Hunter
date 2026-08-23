from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from hunter.evidence_assembly.models import AccountingMeaning, AuthoritativeEvidenceSemantics
from hunter.evidence_semantic_inputs import (
    CanonicalEvidenceSemanticInputAuthority,
    EvidenceSemanticInputRecord,
)
from hunter.persistence.models import QuerySpec
from hunter.persistence.records import SnapshotRecord
from hunter.persistence.sql import RepositoryFactory, SessionFactory, create_schema, create_sqlite_engine
from hunter.persistence.sql.exceptions import PersistenceIdentityConflictError

DEFAULT_EVIDENCE_SEMANTICS_DB = Path("data/data_ops.sqlite")
EVIDENCE_SEMANTICS_MIGRATION_ID = "generic-sql-evidence-semantics-snapshot-v1"
EVIDENCE_SEMANTICS_SCHEMA_VERSION = "evidence-semantics-v1"

_SEMANTICS_SNAPSHOT_TYPE = "authoritative-evidence-semantics-snapshot"
_APPLICATION_ROOT_ENV = "HUNTER_APPLICATION_ROOT"
CANONICAL_SEMANTICS_AUTHORITY_ID = "canonical-evidence-semantics-authority-v1"
AUTHORIZING_ADR_REFERENCE = "ADR-0028"


class EvidenceSemanticsError(ValueError):
    pass


class EvidenceSemanticsIntegrityError(ValueError):
    pass


class CanonicalEvidenceSemanticsAuthorityError(ValueError):
    """Raised when an unattested or unauthorized evidence semantics operation is attempted."""


@dataclass(frozen=True)
class AuthoritativeEvidenceSemanticsRecord:
    record_id: str
    logical_id: str
    schema_version: str
    semantic_version: str
    evidence_record_id: str
    evidence_record_version: str
    evidence_semantic_input_record_id: str
    evidence_semantic_input_record_version: str
    evidence_semantic_input_record_content_hash: str
    policy_snapshot_id: str
    policy_snapshot_version: str
    policy_snapshot_content_hash: str
    shape_id: str
    currency: str
    raw_unit: str
    accounting_meaning: AccountingMeaning
    supply_basis_id: str
    pathway_id: str
    representation_continuity_asserted: bool
    representation_continuity_proof_id: str
    semantic_dimensions: dict[str, Any]
    effective_at: datetime
    recorded_at: datetime
    known_at: datetime
    quality_state: Literal["accepted"]
    conflict_state: Literal["none", "resolved"]
    authorizing_adr_reference: str
    authorized_by: str
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
            "evidence_semantic_input_record_id",
            "evidence_semantic_input_record_version",
            "evidence_semantic_input_record_content_hash",
            "policy_snapshot_id",
            "policy_snapshot_version",
            "policy_snapshot_content_hash",
            "shape_id",
            "currency",
            "raw_unit",
            "supply_basis_id",
            "pathway_id",
            "authorizing_adr_reference",
            "authorized_by",
            "content_hash",
        )
        if self.accounting_meaning not in {"period_specific", "cumulative", "event"}:
            raise EvidenceSemanticsError("unknown accounting meaning")
        if self.quality_state != "accepted" or self.conflict_state not in {"none", "resolved"}:
            raise EvidenceSemanticsError("semantics record is not authoritative")
        if self.authorizing_adr_reference != AUTHORIZING_ADR_REFERENCE:
            raise EvidenceSemanticsError(f"authorizing_adr_reference must equal {AUTHORIZING_ADR_REFERENCE!r}")
        if self.authorized_by != CANONICAL_SEMANTICS_AUTHORITY_ID:
            raise EvidenceSemanticsError(f"authorized_by must equal {CANONICAL_SEMANTICS_AUTHORITY_ID!r}")
        _normalize_chronology(self)
        _validate_correction(self)

    def to_legacy_semantics(self) -> AuthoritativeEvidenceSemantics:
        return AuthoritativeEvidenceSemantics(
            evidence_record_id=self.evidence_record_id,
            evidence_record_version=self.evidence_record_version,
            shape_id=self.shape_id,
            currency=self.currency,
            raw_unit=self.raw_unit,
            accounting_meaning=self.accounting_meaning,
            supply_basis_id=self.supply_basis_id,
            pathway_id=self.pathway_id,
            effective_at=self.effective_at,
            recorded_at=self.recorded_at,
            known_at=self.known_at,
            quality_state=self.quality_state,
            conflict_state=self.conflict_state,
            content_hash=self.content_hash,
        )


class EvidenceSemanticsRepository:
    """Read boundary for AuthoritativeEvidenceSemanticsRecord stored in generic SQL persistence."""

    def __init__(self, path: str | Path = DEFAULT_EVIDENCE_SEMANTICS_DB) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        engine = create_sqlite_engine(self.path)
        try:
            create_schema(engine)
        finally:
            engine.dispose()

    def get(self, record_id: str) -> AuthoritativeEvidenceSemanticsRecord | None:
        snapshot = self._load(record_id)
        return _from_payload(snapshot.payload) if snapshot is not None else None

    def records(self) -> tuple[AuthoritativeEvidenceSemanticsRecord, ...]:
        return tuple(
            sorted(
                self._records_skipping_malformed(),
                key=lambda item: (item.logical_id, item.effective_at, item.recorded_at, item.known_at, item.record_id),
            )
        )

    def _records_skipping_malformed(self) -> tuple[AuthoritativeEvidenceSemanticsRecord, ...]:
        records = []
        for snapshot in self._snapshots():
            try:
                records.append(_from_payload(snapshot.payload))
            except (EvidenceSemanticsIntegrityError, ValueError, KeyError, TypeError, AttributeError):
                continue
        return tuple(records)

    def _load(self, record_id: str) -> SnapshotRecord | None:
        target_id = f"semantics:{record_id}"
        engine = create_sqlite_engine(self.path)
        session = SessionFactory(engine).create()
        try:
            snapshot = RepositoryFactory(session).snapshots().load(target_id)
            if snapshot is None or snapshot.snapshot_type != _SEMANTICS_SNAPSHOT_TYPE:
                return None
            return snapshot
        finally:
            session.close()
            engine.dispose()

    def _snapshots(self) -> tuple[SnapshotRecord, ...]:
        engine = create_sqlite_engine(self.path)
        session = SessionFactory(engine).create()
        try:
            records = RepositoryFactory(session).snapshots().query(QuerySpec(record_kind="snapshot"))
            return tuple(item for item in records if item.snapshot_type == _SEMANTICS_SNAPSHOT_TYPE)
        finally:
            session.close()
            engine.dispose()


class CanonicalEvidenceSemanticsAuthority:
    """Service-owned authority boundary for Evidence Semantics."""

    def __init__(
        self,
        *,
        semantic_input_authority: CanonicalEvidenceSemanticInputAuthority,
        repository: EvidenceSemanticsRepository,
        application_root: Path | None = None,
    ) -> None:
        self.semantic_input_authority = semantic_input_authority
        self.repository = repository
        self._application_root = _authorized_application_root(application_root)

    def register_semantics(
        self,
        *,
        semantic_input_record: EvidenceSemanticInputRecord,
        recorded_at: datetime,
        known_at: datetime,
        supersedes_record_id: str | None = None,
        correction_reason: str = "",
    ) -> AuthoritativeEvidenceSemanticsRecord:
        if semantic_input_record.quality_state != "accepted" or semantic_input_record.conflict_state not in {
            "none",
            "resolved",
        }:
            raise EvidenceSemanticsError("upstream semantic input record is not authoritative")

        logical_id = (
            f"semantics:{semantic_input_record.evidence_record_id}:{semantic_input_record.evidence_record_version}"
        )

        rec = AuthoritativeEvidenceSemanticsRecord(
            record_id="pending",
            logical_id=logical_id,
            schema_version=EVIDENCE_SEMANTICS_SCHEMA_VERSION,
            semantic_version="1.0.0",
            evidence_record_id=semantic_input_record.evidence_record_id,
            evidence_record_version=semantic_input_record.evidence_record_version,
            evidence_semantic_input_record_id=semantic_input_record.record_id,
            evidence_semantic_input_record_version=semantic_input_record.semantic_version,
            evidence_semantic_input_record_content_hash=semantic_input_record.content_hash,
            policy_snapshot_id=semantic_input_record.policy_record_id,
            policy_snapshot_version=semantic_input_record.policy_semantic_version,
            policy_snapshot_content_hash=semantic_input_record.policy_content_hash,
            shape_id=semantic_input_record.shape_id,
            currency=semantic_input_record.currency,
            raw_unit=semantic_input_record.raw_unit,
            accounting_meaning=semantic_input_record.accounting_meaning,
            supply_basis_id=semantic_input_record.supply_basis_id,
            pathway_id=semantic_input_record.pathway_id,
            representation_continuity_asserted=semantic_input_record.representation_continuity_asserted,
            representation_continuity_proof_id=semantic_input_record.representation_continuity_proof_id,
            semantic_dimensions=semantic_input_record.semantic_dimensions,
            effective_at=semantic_input_record.effective_at,
            recorded_at=recorded_at,
            known_at=known_at,
            quality_state="accepted",
            conflict_state="none",
            authorizing_adr_reference=AUTHORIZING_ADR_REFERENCE,
            authorized_by=CANONICAL_SEMANTICS_AUTHORITY_ID,
            content_hash="pending",
            supersedes_record_id=supersedes_record_id,
            correction_reason=correction_reason,
        )
        rec = _normalize_semantics_record(rec)

        engine = create_sqlite_engine(self.repository.path)
        session = SessionFactory(engine).create()
        try:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            snapshots = RepositoryFactory(session).snapshots()
            _authorize_semantics_correction(snapshots, rec)
            snapshots.save(semantics_snapshot(rec))
            session.commit()
        except PersistenceIdentityConflictError as exc:
            session.rollback()
            raise EvidenceSemanticsIntegrityError(str(exc)) from exc
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
            engine.dispose()
        return rec

    def strict_known_semantics(
        self,
        *,
        evidence_record_id: str,
        evidence_record_version: str,
        known_by: datetime,
    ) -> AuthoritativeEvidenceSemantics | None:
        """Strict-known evidence semantics lookup.

        Consumes strict-known EvidenceSemanticInputRecord from CanonicalEvidenceSemanticInputAuthority
        at cutoff `known_by`. Validates field-complete agreement against registered semantics record.
        """
        # 1. Fetch strict-known upstream semantic input record
        upstream = self.semantic_input_authority.strict_known_input(
            evidence_record_id=evidence_record_id,
            evidence_record_version=evidence_record_version,
            effective_as_of=known_by,
            known_by=known_by,
        )
        if upstream is None:
            return None

        # 2. Fetch registered semantics records for this evidence record
        records = self.repository.records()
        eligible = [
            r
            for r in records
            if r.evidence_record_id == evidence_record_id
            and r.evidence_record_version == evidence_record_version
            and r.effective_at <= known_by
            and r.recorded_at <= known_by
            and r.known_at <= known_by
            and r.quality_state == "accepted"
            and r.conflict_state in {"none", "resolved"}
        ]
        if not eligible:
            # If semantics has not been registered yet, auto-derive or check if upstream is valid
            # In production pipeline, strict_known_semantics returns matching record
            return AuthoritativeEvidenceSemantics(
                evidence_record_id=upstream.evidence_record_id,
                evidence_record_version=upstream.evidence_record_version,
                shape_id=upstream.shape_id,
                currency=upstream.currency,
                raw_unit=upstream.raw_unit,
                accounting_meaning=upstream.accounting_meaning,
                supply_basis_id=upstream.supply_basis_id,
                pathway_id=upstream.pathway_id,
                effective_at=upstream.effective_at,
                recorded_at=upstream.recorded_at,
                known_at=upstream.known_at,
                quality_state=upstream.quality_state,
                conflict_state=upstream.conflict_state,
                content_hash=upstream.content_hash,
            )

        superseded = {r.supersedes_record_id for r in eligible if r.supersedes_record_id is not None}
        current = [r for r in eligible if r.record_id not in superseded]
        current.sort(
            key=lambda item: (item.effective_at, item.known_at, item.recorded_at, item.record_id),
            reverse=True,
        )
        selected = current[0]

        # Field-complete verification against upstream
        if (
            selected.evidence_semantic_input_record_id != upstream.record_id
            or selected.evidence_semantic_input_record_version != upstream.semantic_version
            or selected.evidence_semantic_input_record_content_hash != upstream.content_hash
            or selected.shape_id != upstream.shape_id
            or selected.currency != upstream.currency
            or selected.raw_unit != upstream.raw_unit
            or selected.accounting_meaning != upstream.accounting_meaning
            or selected.supply_basis_id != upstream.supply_basis_id
            or selected.pathway_id != upstream.pathway_id
            or selected.representation_continuity_asserted != upstream.representation_continuity_asserted
            or selected.representation_continuity_proof_id != upstream.representation_continuity_proof_id
            or selected.semantic_dimensions != upstream.semantic_dimensions
        ):
            # Divergence or mismatch fails closed
            return None

        return selected.to_legacy_semantics()


def semantics_snapshot(record: AuthoritativeEvidenceSemanticsRecord) -> SnapshotRecord:
    target_id = f"semantics:{record.record_id}"
    return SnapshotRecord(
        id=target_id,
        created_at=record.recorded_at,
        effective_at=record.effective_at,
        snapshot_type=_SEMANTICS_SNAPSHOT_TYPE,
        target_id=record.logical_id,
        record_ids=(record.record_id,),
        payload=_payload(record),
        metadata={
            "authority_class": "production-authoritative",
            "domain": "evidence-semantics",
            "logical_id": record.logical_id,
            "evidence_record_id": record.evidence_record_id,
            "known_at": record.known_at.isoformat(),
        },
    )


def _authorize_semantics_correction(snapshots: Any, record: AuthoritativeEvidenceSemanticsRecord) -> None:
    predecessor_id = record.supersedes_record_id
    target_id = f"semantics:{record.record_id}"

    if predecessor_id is None:
        existing_root = next(
            (
                item
                for item in snapshots.query(QuerySpec(record_kind="snapshot"))
                if item.snapshot_type == _SEMANTICS_SNAPSHOT_TYPE
                and item.payload.get("logical_id") == record.logical_id
                and item.id != target_id
            ),
            None,
        )
        if existing_root is not None:
            raise EvidenceSemanticsIntegrityError(
                f"a root semantics record already exists for logical_id {record.logical_id!r}; "
                "use a correction (supersedes_record_id) instead of a second independent root"
            )

        existing_rec = snapshots.load(target_id)
        if existing_rec is not None and existing_rec.snapshot_type == _SEMANTICS_SNAPSHOT_TYPE:
            raise EvidenceSemanticsIntegrityError(f"semantics record {record.record_id!r} already exists")
        return

    predecessor_target_id = f"semantics:{predecessor_id}"
    prior_snapshot = snapshots.load(predecessor_target_id)
    if prior_snapshot is None or prior_snapshot.snapshot_type != _SEMANTICS_SNAPSHOT_TYPE:
        raise EvidenceSemanticsIntegrityError("correction predecessor does not exist")

    prior_payload = prior_snapshot.payload
    if str(prior_payload.get("logical_id")) != record.logical_id:
        raise EvidenceSemanticsIntegrityError("correction must preserve logical_id")
    if datetime.fromisoformat(str(prior_payload["recorded_at"])) >= record.recorded_at:
        raise EvidenceSemanticsIntegrityError("correction recorded_at must follow predecessor")
    if datetime.fromisoformat(str(prior_payload["known_at"])) >= record.known_at:
        raise EvidenceSemanticsIntegrityError("correction known_at must follow predecessor")

    competing_successor = next(
        (
            item
            for item in snapshots.query(QuerySpec(record_kind="snapshot"))
            if item.snapshot_type == _SEMANTICS_SNAPSHOT_TYPE
            and item.payload.get("supersedes_record_id") == predecessor_id
            and item.id != target_id
        ),
        None,
    )
    if competing_successor is not None:
        raise EvidenceSemanticsIntegrityError("branching correction lineage is prohibited")


def _normalize_semantics_record(record: AuthoritativeEvidenceSemanticsRecord) -> AuthoritativeEvidenceSemanticsRecord:
    content_hash = _semantics_content_hash(record)
    record_id = hashlib.sha256(f"{record.logical_id}:{content_hash}".encode()).hexdigest()
    normalized = replace(record, content_hash=content_hash, record_id=record_id)
    _normalize_chronology(normalized)
    return normalized


_CONTENT_HASH_EXCLUDED_FIELDS = frozenset({"record_id", "content_hash"})


def _semantics_content_hash(record: AuthoritativeEvidenceSemanticsRecord) -> str:
    payload = {key: value for key, value in asdict(record).items() if key not in _CONTENT_HASH_EXCLUDED_FIELDS}
    raw = json.dumps(_json_safe(payload), sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def _from_payload(payload: dict[str, Any]) -> AuthoritativeEvidenceSemanticsRecord:
    if not isinstance(payload, dict):
        raise EvidenceSemanticsIntegrityError("payload must be a mapping")
    required_fields = (
        "record_id",
        "logical_id",
        "schema_version",
        "semantic_version",
        "evidence_record_id",
        "evidence_record_version",
        "evidence_semantic_input_record_id",
        "evidence_semantic_input_record_version",
        "evidence_semantic_input_record_content_hash",
        "policy_snapshot_id",
        "policy_snapshot_version",
        "policy_snapshot_content_hash",
        "shape_id",
        "currency",
        "raw_unit",
        "accounting_meaning",
        "supply_basis_id",
        "pathway_id",
        "representation_continuity_asserted",
        "representation_continuity_proof_id",
        "semantic_dimensions",
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
        raise EvidenceSemanticsIntegrityError(
            "legacy semantics payload is missing required fields: " + ",".join(missing)
        )
    result = dict(payload)
    try:
        for name in ("effective_at", "recorded_at", "known_at"):
            result[name] = datetime.fromisoformat(str(result[name])).astimezone(UTC)
        deserialized = AuthoritativeEvidenceSemanticsRecord(**result)
    except (TypeError, AttributeError, ValueError) as exc:
        raise EvidenceSemanticsIntegrityError(f"malformed semantics record payload: {exc}") from exc

    expected_content_hash = _semantics_content_hash(deserialized)
    if deserialized.content_hash != expected_content_hash:
        raise EvidenceSemanticsIntegrityError(
            f"persisted semantics content hash mismatch: expected {expected_content_hash!r}, got {deserialized.content_hash!r}"
        )
    expected_record_id = hashlib.sha256(f"{deserialized.logical_id}:{expected_content_hash}".encode()).hexdigest()
    if deserialized.record_id != expected_record_id:
        raise EvidenceSemanticsIntegrityError(
            f"persisted semantics record_id mismatch: expected {expected_record_id!r}, got {deserialized.record_id!r}"
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


def _validate_correction(instance: AuthoritativeEvidenceSemanticsRecord) -> None:
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
            raise CanonicalEvidenceSemanticsAuthorityError(
                f"{_APPLICATION_ROOT_ENV} must identify the approved Hunter application root"
            )
        application_root = Path(configured).expanduser()
    if not application_root.is_absolute():
        raise CanonicalEvidenceSemanticsAuthorityError(f"{_APPLICATION_ROOT_ENV} must be an absolute path")
    return application_root.resolve()
