from __future__ import annotations

from datetime import UTC, datetime
from typing import Final

from hunter.committee.authority import CommitteeInputIdentity, ResolvedCommitteeInput
from hunter.execution.hashing import stable_digest
from hunter.persistence.models import QuerySpec
from hunter.persistence.records import PersistenceRecord
from hunter.persistence.serialization import record_to_json
from hunter.persistence.sql import RepositoryFactory
from hunter.persistence.sql.repositories import SQLRecordRepository

_REPOSITORY_NAMESPACE: Final[str] = "hunter.persistence.sql"


class RepositoryBackedCommitteeInputResolver:
    """Resolve authoritative committee inputs from Hunter-owned SQL repositories."""

    def __init__(self, repositories: RepositoryFactory) -> None:
        self._repositories = repositories

    def resolve(
        self,
        *,
        record_id: str,
        family: str,
        known_at: datetime,
    ) -> ResolvedCommitteeInput | None:
        repository = self._repository_for(family)
        if repository is None:
            return None
        record = repository.load(record_id)
        if record is None or record.created_at > known_at:
            return None

        lineage_id = _required_metadata(record, "lineage_id")
        revision_id = str(record.metadata.get("revision_id") or record.id)
        current = self._current_lineage_record(
            repository,
            lineage_id=lineage_id,
            known_at=known_at,
        )
        if current is None:
            return None
        current_revision_id = str(current.metadata.get("revision_id") or current.id)

        return ResolvedCommitteeInput(
            record_id=record.id,
            family=family,
            value=record,
            authority_class=_required_metadata(record, "authority_class"),
            identity=CommitteeInputIdentity(
                project_id=_required_metadata(record, "project_id"),
                entity_id=_required_metadata(record, "entity_id"),
                representation_id=_required_metadata(record, "representation_id"),
                chain_id=_optional_metadata(record, "chain_id"),
            ),
            recorded_at=record.created_at,
            effective_at=record.effective_at,
            lineage_id=lineage_id,
            revision_id=revision_id,
            current_revision_id=current_revision_id,
            superseded_at=_optional_datetime(record, "superseded_at"),
            invalidated_at=_optional_datetime(record, "invalidated_at"),
            repository_namespace=_REPOSITORY_NAMESPACE,
            repository_record_type=record.record_type,
            repository_fingerprint=_fingerprint(record),
        )

    def _repository_for(self, family: str) -> SQLRecordRepository[PersistenceRecord] | None:
        repositories: dict[str, SQLRecordRepository[PersistenceRecord]] = {
            "intelligence": self._repositories.intelligence(),
            "fused_intelligence": self._repositories.fused_intelligence(),
            "evidence": self._repositories.evidence(),
            "snapshot": self._repositories.snapshots(),
            "opportunity": self._repositories.opportunity_timing_assessments(),
        }
        return repositories.get(family)

    @staticmethod
    def _current_lineage_record(
        repository: SQLRecordRepository[PersistenceRecord],
        *,
        lineage_id: str,
        known_at: datetime,
    ) -> PersistenceRecord | None:
        candidates = tuple(
            record
            for record in repository.query(QuerySpec(record_kind=repository.record_type))
            if record.created_at <= known_at
            and str(record.metadata.get("lineage_id", "")).strip() == lineage_id
            and _optional_datetime(record, "invalidated_at") is None
        )
        if not candidates:
            return None
        return max(candidates, key=lambda item: (item.effective_at, item.created_at, item.id))


def verify_repository_binding(resolved: ResolvedCommitteeInput) -> None:
    if resolved.repository_namespace != _REPOSITORY_NAMESPACE:
        raise ValueError("committee input is not bound to the approved persistence namespace")
    value = resolved.value
    if not isinstance(value, PersistenceRecord):
        raise ValueError("committee input is not bound to a persistence record")
    if value.record_type != resolved.repository_record_type:
        raise ValueError("committee input repository record type mismatch")
    if _fingerprint(value) != resolved.repository_fingerprint:
        raise ValueError("committee input repository fingerprint mismatch")


def _fingerprint(record: PersistenceRecord) -> str:
    return stable_digest(
        "committee-authority-record",
        record_to_json(record),
        schema_version=record.schema_version,
    )


def _required_metadata(record: PersistenceRecord, name: str) -> str:
    value = str(record.metadata.get(name, "")).strip()
    if not value:
        raise ValueError(f"authoritative persistence record requires {name}")
    return value


def _optional_metadata(record: PersistenceRecord, name: str) -> str | None:
    value = record.metadata.get(name)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_datetime(record: PersistenceRecord, name: str) -> datetime | None:
    value = record.metadata.get(name)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{name} metadata must be an ISO-8601 string")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError(f"{name} metadata must be timezone-aware")
    return parsed.astimezone(UTC)
