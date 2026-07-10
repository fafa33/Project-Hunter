from __future__ import annotations

from datetime import UTC, datetime
from typing import Generic, TypeVar

from sqlalchemy import select
from sqlalchemy.orm import Session

from hunter.execution.hashing import stable_digest
from hunter.persistence.models import HistorySpec, QuerySpec, SnapshotSpec
from hunter.persistence.records import PersistenceRecord, SnapshotRecord
from hunter.persistence.repositories import Repository
from hunter.persistence.serialization import record_from_json, record_to_json
from hunter.persistence.sql.base import PersistenceRecordModel
from hunter.persistence.sql.exceptions import PersistenceIdentityConflictError, PersistenceRecordDeletedError

RecordT = TypeVar("RecordT", bound=PersistenceRecord)


class SQLRecordRepository(Repository[RecordT], Generic[RecordT]):
    record_type: str
    record_class: type[RecordT]

    def __init__(self, session: Session) -> None:
        self._session = session

    def save(self, record: RecordT) -> RecordT:
        self._require_record_type(record)
        payload = record_to_json(record)
        canonical_hash = stable_digest("persistence-record", payload, schema_version=record.schema_version)
        existing = self._session.get(PersistenceRecordModel, record.id)
        if existing is not None:
            if existing.deleted_at is not None:
                raise PersistenceRecordDeletedError(f"Cannot save deleted immutable record: {record.id}")
            if existing.canonical_hash != canonical_hash:
                raise PersistenceIdentityConflictError(f"Record identity conflict: {record.id}")
            return self._to_record(existing)
        self._session.add(
            PersistenceRecordModel(
                id=record.id,
                record_type=record.record_type,
                schema_version=record.schema_version,
                created_at=record.created_at,
                effective_at=record.effective_at,
                canonical_hash=canonical_hash,
                payload=payload,
            )
        )
        return record

    def save_many(self, records: tuple[RecordT, ...]) -> tuple[RecordT, ...]:
        return tuple(self.save(record) for record in records)

    def load(self, identity: str) -> RecordT | None:
        model = self._session.get(PersistenceRecordModel, identity)
        if model is None or model.deleted_at is not None or model.record_type != self.record_type:
            return None
        return self._to_record(model)

    def load_many(self, identities: tuple[str, ...]) -> tuple[RecordT, ...]:
        return tuple(record for identity in identities if (record := self.load(identity)) is not None)

    def exists(self, identity: str) -> bool:
        return self.load(identity) is not None

    def delete(self, identity: str) -> None:
        model = self._session.get(PersistenceRecordModel, identity)
        if model is not None and model.deleted_at is None and model.record_type == self.record_type:
            model.deleted_at = datetime.now(UTC)

    def query(self, spec: QuerySpec) -> tuple[RecordT, ...]:
        records = [record for record in self._all_records() if _matches(record, spec)]
        reverse = spec.direction == "desc"
        records.sort(key=lambda item: getattr(item, spec.sort_by), reverse=reverse)
        if spec.limit is not None:
            records = records[: spec.limit]
        return tuple(records)

    def latest(self, spec: QuerySpec) -> RecordT | None:
        scoped = QuerySpec(
            record_kind=spec.record_kind,
            filters=spec.filters,
            limit=1,
            sort_by=spec.sort_by,
            direction=spec.direction,
        )
        records = self.query(scoped)
        return records[0] if records else None

    def history(self, spec: HistorySpec) -> tuple[RecordT, ...]:
        record = self.load(spec.identity)
        if record is None:
            return ()
        if spec.as_of is not None and record.effective_at > spec.as_of:
            return ()
        return (record,)

    def snapshot(self, spec: SnapshotSpec) -> SnapshotRecord:
        msg = f"{self.__class__.__name__} does not provide snapshot retrieval"
        raise NotImplementedError(msg)

    def _all_records(self) -> list[RecordT]:
        models = self._session.scalars(
            select(PersistenceRecordModel).where(
                PersistenceRecordModel.record_type == self.record_type,
                PersistenceRecordModel.deleted_at.is_(None),
            )
        ).all()
        return [self._to_record(model) for model in models]

    def _to_record(self, model: PersistenceRecordModel) -> RecordT:
        record = record_from_json(model.payload)
        if not isinstance(record, self.record_class):
            msg = f"Stored record {model.id} is not {self.record_class.__name__}"
            raise TypeError(msg)
        return record

    def _require_record_type(self, record: RecordT) -> None:
        if record.record_type != self.record_type:
            msg = f"Repository for {self.record_type} cannot save {record.record_type}"
            raise TypeError(msg)


class SQLSnapshotRepository(SQLRecordRepository[SnapshotRecord]):
    record_type = "snapshot"
    record_class = SnapshotRecord

    def snapshot(self, spec: SnapshotSpec) -> SnapshotRecord:
        candidates = [
            record
            for record in self.query(QuerySpec(record_kind="snapshot"))
            if record.target_id == spec.target_id
            and record.snapshot_type == spec.snapshot_type
            and record.effective_at <= spec.effective_at
        ]
        if not candidates:
            msg = f"No snapshot found for {spec.target_id}:{spec.snapshot_type}"
            raise LookupError(msg)
        candidates.sort(key=lambda item: item.effective_at, reverse=True)
        return candidates[0]


def _matches(record: PersistenceRecord, spec: QuerySpec) -> bool:
    if spec.record_kind is not None and record.record_type != spec.record_kind:
        return False
    for item in spec.filters:
        if getattr(record, item.field, None) != item.value:
            return False
    return True
