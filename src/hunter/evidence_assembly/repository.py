from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hunter.evidence_assembly.models import (
    AssembledFundamentalEvidenceRecord,
    AssemblyConflictRecord,
    AssemblyLineageProjection,
)
from hunter.persistence.models import QuerySpec
from hunter.persistence.records import SnapshotRecord
from hunter.persistence.sql import RepositoryFactory, SessionFactory, create_schema, create_sqlite_engine
from hunter.persistence.sql.exceptions import PersistenceIdentityConflictError
from hunter.value_capture.models import EconomicClaimIdentity

DEFAULT_EVIDENCE_ASSEMBLY_DB = Path("data/data_ops.sqlite")
EVIDENCE_ASSEMBLY_MIGRATION_ID = "generic-sql-canonical-evidence-assembly-v1"
ASSEMBLY_SNAPSHOT_TYPE = "assembled-fundamental-evidence"
CONFLICT_SNAPSHOT_TYPE = "evidence-assembly-conflict"


class EvidenceAssemblyPersistenceError(ValueError):
    pass


class AssembledEvidenceRepository:
    """Mechanical append-only reads in Hunter's generic analytical SQL envelope."""

    def __init__(self, path: str | Path = DEFAULT_EVIDENCE_ASSEMBLY_DB) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        engine = create_sqlite_engine(self.path)
        try:
            create_schema(engine)
        finally:
            engine.dispose()

    def migration_ids(self) -> tuple[str, ...]:
        return (EVIDENCE_ASSEMBLY_MIGRATION_ID,)

    def _insert_authorized(self, record: AssembledFundamentalEvidenceRecord) -> AssembledFundamentalEvidenceRecord:
        self._save(_snapshot(record, ASSEMBLY_SNAPSHOT_TYPE, record.logical_id))
        return record

    def _insert_conflict_authorized(self, record: AssemblyConflictRecord) -> AssemblyConflictRecord:
        self._save(_snapshot(record, CONFLICT_SNAPSHOT_TYPE, record.logical_id))
        return record

    def get(self, record_id: str) -> AssembledFundamentalEvidenceRecord | None:
        item = self._load(record_id, ASSEMBLY_SNAPSHOT_TYPE)
        return _assembly(item.payload) if item else None

    def history(self, logical_id: str) -> tuple[AssembledFundamentalEvidenceRecord, ...]:
        records = [record for record in self._assemblies() if record.logical_id == logical_id]
        records.sort(key=lambda record: (record.effective_at, record.recorded_at, record.known_at, record.record_id))
        return tuple(records)

    def projected_history(self, logical_id: str) -> tuple[AssemblyLineageProjection, ...]:
        records = self.history(logical_id)
        superseded = {record.supersedes_record_id for record in records if record.supersedes_record_id}
        return tuple(
            AssemblyLineageProjection(
                record=record,
                supersession_state="superseded" if record.record_id in superseded else "active",
            )
            for record in records
        )

    def successor(
        self, predecessor_id: str, *, known_by: datetime | None = None
    ) -> AssembledFundamentalEvidenceRecord | None:
        records = [record for record in self._assemblies() if record.supersedes_record_id == predecessor_id]
        if known_by is not None:
            cutoff = _aware(known_by)
            records = [
                record
                for record in records
                if record.effective_at <= cutoff and record.recorded_at <= cutoff and record.known_at <= cutoff
            ]
        if len(records) > 1:
            raise EvidenceAssemblyPersistenceError("branching correction lineage")
        return records[0] if records else None

    def strict_known_candidates(
        self, *, logical_id: str, effective_as_of: datetime, known_by: datetime
    ) -> tuple[AssembledFundamentalEvidenceRecord, ...]:
        effective_as_of, known_by = _aware(effective_as_of), _aware(known_by)
        records = [
            record
            for record in self._assemblies()
            if record.logical_id == logical_id
            and record.effective_at <= effective_as_of
            and record.recorded_at <= known_by
            and record.known_at <= known_by
        ]
        records.sort(
            key=lambda record: (record.effective_at, record.known_at, record.recorded_at, record.record_id),
            reverse=True,
        )
        return tuple(records)

    def unresolved_assembly_conflicts(self, *, known_by: datetime | None = None) -> tuple[AssemblyConflictRecord, ...]:
        records = list(self._conflicts())
        if known_by is not None:
            cutoff = _aware(known_by)
            records = [
                record
                for record in records
                if record.effective_at <= cutoff and record.recorded_at <= cutoff and record.known_at <= cutoff
            ]
        records.sort(key=lambda record: (record.known_at, record.record_id))
        return tuple(records)

    def _assemblies(self) -> tuple[AssembledFundamentalEvidenceRecord, ...]:
        return tuple(_assembly(item.payload) for item in self._snapshots(ASSEMBLY_SNAPSHOT_TYPE))

    def _conflicts(self) -> tuple[AssemblyConflictRecord, ...]:
        return tuple(_conflict(item.payload) for item in self._snapshots(CONFLICT_SNAPSHOT_TYPE))

    def _load(self, record_id: str, snapshot_type: str) -> SnapshotRecord | None:
        engine = create_sqlite_engine(self.path)
        session = SessionFactory(engine).create()
        try:
            item = RepositoryFactory(session).snapshots().load(record_id)
            return item if item is not None and item.snapshot_type == snapshot_type else None
        finally:
            session.close()
            engine.dispose()

    def _snapshots(self, snapshot_type: str) -> tuple[SnapshotRecord, ...]:
        engine = create_sqlite_engine(self.path)
        session = SessionFactory(engine).create()
        try:
            items = RepositoryFactory(session).snapshots().query(QuerySpec(record_kind="snapshot"))
            return tuple(item for item in items if item.snapshot_type == snapshot_type)
        finally:
            session.close()
            engine.dispose()

    def _save(self, record: SnapshotRecord) -> None:
        engine = create_sqlite_engine(self.path)
        session = SessionFactory(engine).create()
        try:
            RepositoryFactory(session).snapshots().save(record)
            session.commit()
        except PersistenceIdentityConflictError as exc:
            session.rollback()
            raise EvidenceAssemblyPersistenceError(str(exc)) from exc
        finally:
            session.close()
            engine.dispose()


def _snapshot(record: Any, snapshot_type: str, target_id: str) -> SnapshotRecord:
    return SnapshotRecord(
        id=record.record_id,
        created_at=record.recorded_at,
        effective_at=record.effective_at,
        snapshot_type=snapshot_type,
        target_id=target_id,
        record_ids=(record.record_id,),
        payload=_json_safe(asdict(record)),
        metadata={"known_at": record.known_at.isoformat(), "content_hash": record.content_hash},
    )


def _assembly(payload: dict[str, Any]) -> AssembledFundamentalEvidenceRecord:
    result = dict(payload)
    result["identity"] = EconomicClaimIdentity(**result["identity"])
    _decode_times(result, "accounting_window_start", "accounting_window_end", "effective_at", "recorded_at", "known_at")
    for name in (
        "constituent_record_ids",
        "constituent_logical_ids",
        "constituent_versions",
        "constituent_content_hashes",
        "constituent_source_ids",
        "constituent_source_references",
        "constituent_shape_ids",
    ):
        result[name] = tuple(result[name])
    return AssembledFundamentalEvidenceRecord(**result)


def _conflict(payload: dict[str, Any]) -> AssemblyConflictRecord:
    result = dict(payload)
    _decode_times(
        result,
        "accounting_window_start",
        "accounting_window_end",
        "replay_cutoff",
        "effective_at",
        "recorded_at",
        "known_at",
    )
    for name in (
        "candidate_record_ids",
        "candidate_logical_ids",
        "candidate_versions",
        "candidate_content_hashes",
        "candidate_source_ids",
        "candidate_effective_at",
        "candidate_recorded_at",
        "candidate_known_at",
        "pathway_classifications",
        "shape_classifications",
    ):
        result[name] = tuple(result[name])
    return AssemblyConflictRecord(**result)


def _decode_times(payload: dict[str, Any], *names: str) -> None:
    for name in names:
        payload[name] = datetime.fromisoformat(str(payload[name]))


def _json_safe(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(UTC)
