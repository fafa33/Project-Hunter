from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from hunter.evidence_assembly.models import EvidenceShape
from hunter.persistence.models import QuerySpec
from hunter.persistence.records import SnapshotRecord
from hunter.persistence.sql import RepositoryFactory, SessionFactory, create_schema, create_sqlite_engine
from hunter.persistence.sql.exceptions import PersistenceIdentityConflictError

DEFAULT_EVIDENCE_SHAPE_REGISTRY_DB = Path("data/data_ops.sqlite")
EVIDENCE_SHAPE_REGISTRY_MIGRATION_ID = "generic-sql-evidence-shape-registry-snapshot-v1"
EVIDENCE_SHAPE_REGISTRY_SCHEMA_VERSION = "evidence-shape-registry-v1"

_REGISTRY_SNAPSHOT_TYPE = "evidence-shape-registry-snapshot"
_APPLICATION_ROOT_ENV = "HUNTER_APPLICATION_ROOT"
CANONICAL_REGISTRY_AUTHORITY_ID = "canonical-evidence-shape-registry-authority-v1"
AUTHORIZING_ADR_REFERENCE = "ADR-0025"
REGISTRY_LOGICAL_ID = "canonical-evidence-shape-registry"


class EvidenceShapeRegistryError(ValueError):
    pass


class EvidenceShapeRegistryIntegrityError(ValueError):
    pass


class CanonicalEvidenceShapeRegistryAuthorityError(ValueError):
    """Raised when an unattested or unauthorized registry write is attempted."""


@dataclass(frozen=True)
class EvidenceShapeRegistry:
    record_id: str
    logical_id: str
    schema_version: str
    semantic_version: str
    version: str
    shapes: tuple[EvidenceShape, ...]
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
        if not self.shapes:
            raise EvidenceShapeRegistryError("registry must contain governed reference data")
        identifiers = [shape.shape_id for shape in self.shapes]
        if len(set(identifiers)) != len(identifiers):
            raise EvidenceShapeRegistryError("shape IDs must be unique within a registry version")
        if any(shape.registry_version != self.version for shape in self.shapes):
            raise EvidenceShapeRegistryError("shape entry version does not match registry version")
        if self.quality_state != "accepted" or self.conflict_state not in {"none", "resolved"}:
            raise EvidenceShapeRegistryError("registry snapshot is not authoritative")
        if self.logical_id != REGISTRY_LOGICAL_ID:
            raise EvidenceShapeRegistryError(f"logical_id must equal {REGISTRY_LOGICAL_ID!r}")
        if self.authorizing_adr_reference != AUTHORIZING_ADR_REFERENCE:
            raise EvidenceShapeRegistryError(f"authorizing_adr_reference must equal {AUTHORIZING_ADR_REFERENCE!r}")
        if self.authorized_by != CANONICAL_REGISTRY_AUTHORITY_ID:
            raise EvidenceShapeRegistryError(f"authorized_by must equal {CANONICAL_REGISTRY_AUTHORITY_ID!r}")
        _normalize_chronology(self)
        _validate_correction(self)

    def require_exact_sum_compatible(self, shape_ids: tuple[str, ...]) -> tuple[EvidenceShape, ...]:
        by_id = {shape.shape_id: shape for shape in self.shapes}
        resolved: list[EvidenceShape] = []
        for shape_id in shape_ids:
            shape = by_id.get(shape_id)
            if shape is None:
                raise EvidenceShapeRegistryError(f"unknown evidence shape: {shape_id}")
            if not shape.active:
                raise EvidenceShapeRegistryError(f"inactive evidence shape: {shape_id}")
            if shape.composition_operation != "exact_sum":
                raise EvidenceShapeRegistryError(f"evidence shape is not exact-sum compatible: {shape_id}")
            resolved.append(shape)
        if len({shape.accounting_meaning for shape in resolved}) != 1:
            raise EvidenceShapeRegistryError("evidence shapes have incompatible accounting meanings")
        return tuple(resolved)


class EvidenceShapeRegistryRepository:
    """Read boundary for EvidenceShapeRegistry records stored in generic SQL persistence."""

    def __init__(self, path: str | Path = DEFAULT_EVIDENCE_SHAPE_REGISTRY_DB) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        engine = create_sqlite_engine(self.path)
        try:
            create_schema(engine)
        finally:
            engine.dispose()

    def get(self, record_id: str) -> EvidenceShapeRegistry | None:
        snapshot = self._load(record_id)
        return _from_payload(snapshot.payload) if snapshot is not None else None

    def get_version(self, version: str) -> EvidenceShapeRegistry | None:
        snapshot = self._load_version(version)
        return _from_payload(snapshot.payload) if snapshot is not None else None

    def count(self, table: str) -> int:
        if table == "evidence_shape_registry_schema_migrations":
            return 1
        if table == "evidence_shape_registry_snapshots":
            return len(self._snapshots())
        raise ValueError("unsupported evidence-shape-registry table")

    def migration_ids(self) -> tuple[str, ...]:
        return (EVIDENCE_SHAPE_REGISTRY_MIGRATION_ID,)

    def registry_history(self, logical_id: str = REGISTRY_LOGICAL_ID) -> tuple[EvidenceShapeRegistry, ...]:
        if not logical_id.strip():
            raise ValueError("logical_id must not be blank")
        records = [record for record in self._records_skipping_malformed() if record.logical_id == logical_id]
        records.sort(
            key=lambda item: (item.effective_at, item.recorded_at, item.known_at, item.record_id),
        )
        return tuple(records)

    def records(self) -> tuple[EvidenceShapeRegistry, ...]:
        """Return every decodable record in deterministic storage order."""
        return tuple(
            sorted(
                self._records_skipping_malformed(),
                key=lambda item: (item.logical_id, item.effective_at, item.recorded_at, item.known_at, item.record_id),
            )
        )

    def _records_skipping_malformed(self) -> tuple[EvidenceShapeRegistry, ...]:
        records = []
        for snapshot in self._snapshots():
            try:
                records.append(_from_payload(snapshot.payload))
            except (EvidenceShapeRegistryIntegrityError, ValueError, KeyError, TypeError, AttributeError):
                continue
        return tuple(records)

    def _load(self, record_id: str) -> SnapshotRecord | None:
        engine = create_sqlite_engine(self.path)
        session = SessionFactory(engine).create()
        try:
            snapshots = RepositoryFactory(session).snapshots().query(QuerySpec(record_kind="snapshot"))
            for snapshot in snapshots:
                if snapshot.snapshot_type == _REGISTRY_SNAPSHOT_TYPE and snapshot.payload.get("record_id") == record_id:
                    return snapshot
            return None
        finally:
            session.close()
            engine.dispose()

    def _load_version(self, version: str) -> SnapshotRecord | None:
        target_id = f"{REGISTRY_LOGICAL_ID}:{version}"
        engine = create_sqlite_engine(self.path)
        session = SessionFactory(engine).create()
        try:
            snapshot = RepositoryFactory(session).snapshots().load(target_id)
            if snapshot is None or snapshot.snapshot_type != _REGISTRY_SNAPSHOT_TYPE:
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
            return tuple(item for item in records if item.snapshot_type == _REGISTRY_SNAPSHOT_TYPE)
        finally:
            session.close()
            engine.dispose()


class CanonicalEvidenceShapeRegistryAuthority:
    """Service-owned authority boundary for EvidenceShapeRegistry snapshots."""

    def __init__(
        self,
        *,
        repository: EvidenceShapeRegistryRepository,
        application_root: Path | None = None,
    ) -> None:
        self.repository = repository
        self._application_root = _authorized_application_root(application_root)

    def persist_registry(
        self,
        *,
        version: str,
        shapes: tuple[EvidenceShape, ...],
        effective_at: datetime,
        recorded_at: datetime,
        known_at: datetime,
        quality_state: Literal["accepted"] = "accepted",
        conflict_state: Literal["none", "resolved"] = "none",
        supersedes_version: str | None = None,
        supersedes_record_id: str | None = None,
        correction_reason: str = "",
    ) -> EvidenceShapeRegistry:
        record = EvidenceShapeRegistry(
            record_id="pending",
            logical_id=REGISTRY_LOGICAL_ID,
            schema_version=EVIDENCE_SHAPE_REGISTRY_SCHEMA_VERSION,
            semantic_version="1.0.0",
            version=version,
            shapes=shapes,
            effective_at=effective_at,
            recorded_at=recorded_at,
            known_at=known_at,
            quality_state=quality_state,
            conflict_state=conflict_state,
            authorizing_adr_reference=AUTHORIZING_ADR_REFERENCE,
            authorized_by=CANONICAL_REGISTRY_AUTHORITY_ID,
            content_hash="pending",
            supersedes_version=supersedes_version,
            supersedes_record_id=supersedes_record_id,
            correction_reason=correction_reason,
        )
        record = _normalize_registry(record)

        engine = create_sqlite_engine(self.repository.path)
        session = SessionFactory(engine).create()
        try:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            snapshots = RepositoryFactory(session).snapshots()
            _authorize_registry_correction(snapshots, record)
            snapshots.save(registry_snapshot(record))
            session.commit()
        except PersistenceIdentityConflictError as exc:
            session.rollback()
            raise EvidenceShapeRegistryIntegrityError(str(exc)) from exc
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
            engine.dispose()
        return record

    def get(self, record_id: str) -> EvidenceShapeRegistry | None:
        return self.repository.get(record_id)

    def get_version(self, version: str) -> EvidenceShapeRegistry | None:
        return self.repository.get_version(version)

    def registry_history(self, logical_id: str = REGISTRY_LOGICAL_ID) -> tuple[EvidenceShapeRegistry, ...]:
        return self.repository.registry_history(logical_id)

    def strict_known_registry(
        self,
        *,
        version: str,
        known_by: datetime,
    ) -> EvidenceShapeRegistry | None:
        """Strict-known lookup of exact registry version.

        Requires:
        - exact version match;
        - effective_at <= known_by (or effective_as_of coordinate context if provided, but replay cutoff known_by);
        - recorded_at <= known_by;
        - known_at <= known_by;
        - quality_state == 'accepted';
        - conflict_state in {'none', 'resolved'};
        - non-superseded tip for that exact version.

        Note: Strict-known replay must select only what was effective and known by cutoff.
        No latest or current fallback is allowed.
        """
        records = self.repository.records()
        return _strict_known_registry(records, version=version, known_by=known_by)

    def unresolved_conflicts(self) -> tuple[EvidenceShapeRegistry, ...]:
        return _unresolved_conflicts(self.repository.records())


def registry_snapshot(record: EvidenceShapeRegistry) -> SnapshotRecord:
    target_id = f"{record.logical_id}:{record.version}"
    return SnapshotRecord(
        id=target_id,
        created_at=record.recorded_at,
        effective_at=record.effective_at,
        snapshot_type=_REGISTRY_SNAPSHOT_TYPE,
        target_id=record.logical_id,
        record_ids=(record.record_id,),
        payload=_payload(record),
        metadata={
            "authority_class": "production-authoritative",
            "domain": "evidence-shape-registry",
            "logical_id": record.logical_id,
            "version": record.version,
            "known_at": record.known_at.isoformat(),
        },
    )


def _authorize_registry_correction(snapshots: Any, record: EvidenceShapeRegistry) -> None:
    predecessor_id = record.supersedes_record_id
    predecessor_version = record.supersedes_version
    target_id = f"{record.logical_id}:{record.version}"

    if predecessor_id is None and predecessor_version is None:
        # Once a root snapshot exists for this registry logical identity, another version
        # cannot be persisted as an independent root.
        existing_root = next(
            (
                item
                for item in snapshots.query(QuerySpec(record_kind="snapshot"))
                if item.snapshot_type == _REGISTRY_SNAPSHOT_TYPE
                and item.payload.get("logical_id") == record.logical_id
                and item.id != target_id
            ),
            None,
        )
        if existing_root is not None:
            raise EvidenceShapeRegistryIntegrityError(
                f"a root registry record already exists for logical_id {record.logical_id!r}; "
                "use a correction (supersedes_version and supersedes_record_id) instead of a second independent root"
            )

        existing_version = snapshots.load(target_id)
        if existing_version is not None and existing_version.snapshot_type == _REGISTRY_SNAPSHOT_TYPE:
            raise EvidenceShapeRegistryIntegrityError(
                f"registry version {record.version!r} already exists; use a correction instead"
            )
        return

    if predecessor_id is None or predecessor_version is None:
        raise EvidenceShapeRegistryIntegrityError(
            "supersedes_version and supersedes_record_id must be provided together"
        )

    predecessor_target_id = f"{record.logical_id}:{predecessor_version}"
    prior_snapshot = snapshots.load(predecessor_target_id)
    if prior_snapshot is None or prior_snapshot.snapshot_type != _REGISTRY_SNAPSHOT_TYPE:
        raise EvidenceShapeRegistryIntegrityError("correction predecessor does not exist")

    prior_payload = prior_snapshot.payload
    if prior_payload.get("record_id") != predecessor_id:
        raise EvidenceShapeRegistryIntegrityError("correction predecessor record_id mismatch")
    if str(prior_payload.get("logical_id")) != record.logical_id:
        raise EvidenceShapeRegistryIntegrityError("correction must preserve logical_id")
    if datetime.fromisoformat(str(prior_payload["recorded_at"])) >= record.recorded_at:
        raise EvidenceShapeRegistryIntegrityError("correction recorded_at must follow predecessor")
    if datetime.fromisoformat(str(prior_payload["known_at"])) >= record.known_at:
        raise EvidenceShapeRegistryIntegrityError("correction known_at must follow predecessor")

    competing_successor = next(
        (
            item
            for item in snapshots.query(QuerySpec(record_kind="snapshot"))
            if item.snapshot_type == _REGISTRY_SNAPSHOT_TYPE
            and item.payload.get("supersedes_version") == predecessor_version
            and item.id != target_id
        ),
        None,
    )
    if competing_successor is not None:
        raise EvidenceShapeRegistryIntegrityError("branching correction lineage is prohibited")


def _strict_known_registry(
    records: tuple[EvidenceShapeRegistry, ...],
    *,
    version: str,
    known_by: datetime,
) -> EvidenceShapeRegistry | None:
    known_by = _aware(known_by)
    eligible = [
        item
        for item in records
        if item.version == version
        and item.effective_at <= known_by
        and item.recorded_at <= known_by
        and item.known_at <= known_by
        and item.quality_state == "accepted"
        and item.conflict_state in {"none", "resolved"}
    ]
    if not eligible:
        return None

    # Check supersession across all eligible records
    eligible_all = [
        item
        for item in records
        if item.effective_at <= known_by
        and item.recorded_at <= known_by
        and item.known_at <= known_by
        and item.quality_state == "accepted"
        and item.conflict_state in {"none", "resolved"}
    ]
    superseded_versions = {item.supersedes_version for item in eligible_all if item.supersedes_version is not None}
    superseded_records = {item.supersedes_record_id for item in eligible_all if item.supersedes_record_id is not None}

    current = [
        item
        for item in eligible
        if item.version not in superseded_versions and item.record_id not in superseded_records
    ]
    current.sort(
        key=lambda item: (item.effective_at, item.recorded_at, item.known_at, item.version, item.record_id),
        reverse=True,
    )
    return current[0] if current else None


def _unresolved_conflicts(
    records: tuple[EvidenceShapeRegistry, ...],
) -> tuple[EvidenceShapeRegistry, ...]:
    superseded_versions = {item.supersedes_version for item in records if item.supersedes_version is not None}
    superseded_records = {item.supersedes_record_id for item in records if item.supersedes_record_id is not None}
    unresolved = [
        item
        for item in records
        if item.version not in superseded_versions
        and item.record_id not in superseded_records
        and item.conflict_state in {"open", "contested"}
    ]
    return tuple(sorted(unresolved, key=lambda item: (item.logical_id, item.effective_at, item.record_id)))


def _normalize_registry(record: EvidenceShapeRegistry) -> EvidenceShapeRegistry:
    content_hash = _registry_content_hash(record)
    record_id = hashlib.sha256(f"{record.logical_id}:{record.version}:{content_hash}".encode()).hexdigest()
    normalized = replace(record, content_hash=content_hash, record_id=record_id)
    _normalize_chronology(normalized)
    return normalized


_CONTENT_HASH_EXCLUDED_FIELDS = frozenset({"record_id", "content_hash"})


def _registry_content_hash(record: EvidenceShapeRegistry) -> str:
    payload = {key: value for key, value in asdict(record).items() if key not in _CONTENT_HASH_EXCLUDED_FIELDS}
    raw = json.dumps(_json_safe(payload), sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def _from_payload(payload: dict[str, Any]) -> EvidenceShapeRegistry:
    if not isinstance(payload, dict):
        raise EvidenceShapeRegistryIntegrityError("registry payload must be a mapping")
    required_fields = (
        "record_id",
        "logical_id",
        "schema_version",
        "semantic_version",
        "version",
        "shapes",
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
        raise EvidenceShapeRegistryIntegrityError(
            "legacy evidence shape registry snapshot is missing required fields: " + ",".join(missing)
        )
    result = dict(payload)
    try:
        shapes_raw = result.get("shapes")
        if not isinstance(shapes_raw, (list, tuple)):
            raise EvidenceShapeRegistryIntegrityError("shapes must be an iterable sequence")
        shapes_list = []
        for s in shapes_raw:
            if isinstance(s, dict):
                shapes_list.append(EvidenceShape(**s))
            elif isinstance(s, EvidenceShape):
                shapes_list.append(s)
            else:
                raise EvidenceShapeRegistryIntegrityError(f"invalid shape item: {s!r}")
        result["shapes"] = tuple(shapes_list)
        for name in ("effective_at", "recorded_at", "known_at"):
            result[name] = datetime.fromisoformat(str(result[name])).astimezone(UTC)

        deserialized = EvidenceShapeRegistry(**result)
    except (TypeError, AttributeError, ValueError) as exc:
        raise EvidenceShapeRegistryIntegrityError(f"malformed evidence shape registry payload: {exc}") from exc

    # Re-verify content hash and derived record_id integrity
    expected_content_hash = _registry_content_hash(deserialized)
    if deserialized.content_hash != expected_content_hash:
        raise EvidenceShapeRegistryIntegrityError(
            f"persisted registry content hash mismatch: expected {expected_content_hash!r}, got {deserialized.content_hash!r}"
        )
    expected_record_id = hashlib.sha256(
        f"{deserialized.logical_id}:{deserialized.version}:{expected_content_hash}".encode()
    ).hexdigest()
    if deserialized.record_id != expected_record_id:
        raise EvidenceShapeRegistryIntegrityError(
            f"persisted registry record_id mismatch: expected {expected_record_id!r}, got {deserialized.record_id!r}"
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


def _normalize_chronology(instance: EvidenceShapeRegistry) -> None:
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


def _validate_correction(instance: EvidenceShapeRegistry) -> None:
    predecessor_v = instance.supersedes_version
    predecessor_id = instance.supersedes_record_id
    reason = instance.correction_reason
    if bool(predecessor_v) != bool(predecessor_id):
        raise ValueError("supersedes_version and supersedes_record_id must be provided together")
    if bool(predecessor_v) != bool(reason.strip()):
        raise ValueError("supersedes fields and correction_reason must be provided together")


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
            raise CanonicalEvidenceShapeRegistryAuthorityError(
                f"{_APPLICATION_ROOT_ENV} must identify the approved Hunter application root"
            )
        application_root = Path(configured).expanduser()
    if not application_root.is_absolute():
        raise CanonicalEvidenceShapeRegistryAuthorityError(f"{_APPLICATION_ROOT_ENV} must be an absolute path")
    return application_root.resolve()
