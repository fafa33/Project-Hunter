from __future__ import annotations

from typing import Protocol, runtime_checkable

from hunter.persistence.models import HistorySpec, QuerySpec, SnapshotSpec
from hunter.persistence.records import PersistenceRecord


@runtime_checkable
class PersistenceRecordContract(Protocol):
    id: str
    schema_version: str

    def validate(self) -> None:
        raise NotImplementedError

    def identity_payload(self) -> dict[str, object]:
        raise NotImplementedError


@runtime_checkable
class RepositoryContract(Protocol):
    def save(self, record: PersistenceRecord) -> PersistenceRecord:
        raise NotImplementedError

    def save_many(self, records: tuple[PersistenceRecord, ...]) -> tuple[PersistenceRecord, ...]:
        raise NotImplementedError

    def load(self, identity: str) -> PersistenceRecord | None:
        raise NotImplementedError

    def load_many(self, identities: tuple[str, ...]) -> tuple[PersistenceRecord, ...]:
        raise NotImplementedError

    def exists(self, identity: str) -> bool:
        raise NotImplementedError

    def delete(self, identity: str) -> None:
        raise NotImplementedError

    def query(self, spec: QuerySpec) -> tuple[PersistenceRecord, ...]:
        raise NotImplementedError

    def latest(self, spec: QuerySpec) -> PersistenceRecord | None:
        raise NotImplementedError

    def history(self, spec: HistorySpec) -> tuple[PersistenceRecord, ...]:
        raise NotImplementedError

    def snapshot(self, spec: SnapshotSpec) -> PersistenceRecord:
        raise NotImplementedError
