from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hunter.mispricing.models import MispricingAssessmentRecord, MispricingMethodologySnapshot
from hunter.persistence.models import QuerySpec
from hunter.persistence.records import SnapshotRecord
from hunter.persistence.sql import RepositoryFactory, SessionFactory, create_schema, create_sqlite_engine
from hunter.value_capture.models import EconomicClaimIdentity

DEFAULT_MISPRICING_DB = Path("data/data_ops.sqlite")
MISPRICING_MIGRATION_ID = "generic-sql-canonical-mispricing-v1"

_METHODOLOGY_TYPE = "mispricing-methodology-snapshot"
_ASSESSMENT_TYPE = "mispricing-assessment-record"


class MispricingIntegrityError(ValueError):
    """Raised when immutable Mispricing identity is reused with divergent content, when
    a correction lineage is invalid, or when a legacy/malformed snapshot is not
    authoritative under the current contract."""


class MispricingRepository:
    """Read boundary for records stored in Hunter's canonical generic SQL authority.

    Deliberately mirrors hunter.valuation.repository.CanonicalValuationRepository and
    hunter.comparative_valuation.repository.ComparativeValuationRepository: purely
    mechanical reads, no write/apply method and no authority validation of its own, and
    no replay selection of any kind (ADR 0009). Every write is authorized and performed
    exclusively by hunter.mispricing.service.CanonicalMispricingService, which opens its
    own session against self.path directly.
    """

    def __init__(self, path: str | Path = DEFAULT_MISPRICING_DB) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        engine = create_sqlite_engine(self.path)
        try:
            create_schema(engine)
        finally:
            engine.dispose()

    # -- point reads ---------------------------------------------------------

    def get_mispricing_methodology(self, record_id: str) -> MispricingMethodologySnapshot | None:
        snapshot = self._load(record_id, _METHODOLOGY_TYPE)
        return _methodology_from_payload(snapshot.payload) if snapshot is not None else None

    def get_assessment(self, record_id: str) -> MispricingAssessmentRecord | None:
        snapshot = self._load(record_id, _ASSESSMENT_TYPE)
        return _assessment_from_payload(snapshot.payload) if snapshot is not None else None

    # -- counts and migrations -----------------------------------------------

    def count(self, table: str) -> int:
        mapping = {
            "mispricing_schema_migrations": 1,
            "mispricing_methodologies": len(self._snapshots(_METHODOLOGY_TYPE)),
            "mispricing_assessments": len(self._snapshots(_ASSESSMENT_TYPE)),
        }
        if table not in mapping:
            raise ValueError("unsupported mispricing table")
        return mapping[table]

    def migration_ids(self) -> tuple[str, ...]:
        return (MISPRICING_MIGRATION_ID,)

    # -- history reads ----------------------------------------------------------

    def methodology_history(self, logical_id: str) -> tuple[MispricingMethodologySnapshot, ...]:
        return tuple(
            record
            for record in self._logical_history(_METHODOLOGY_TYPE, _methodology_from_payload, logical_id)
            if isinstance(record, MispricingMethodologySnapshot)
        )

    def assessment_history(self, logical_id: str) -> tuple[MispricingAssessmentRecord, ...]:
        return tuple(
            record
            for record in self._logical_history(_ASSESSMENT_TYPE, _assessment_from_payload, logical_id)
            if isinstance(record, MispricingAssessmentRecord)
        )

    def methodology_records(self) -> tuple[MispricingMethodologySnapshot, ...]:
        return self._typed_records(_METHODOLOGY_TYPE, _methodology_from_payload, MispricingMethodologySnapshot)

    def assessment_records(self) -> tuple[MispricingAssessmentRecord, ...]:
        return self._typed_records(_ASSESSMENT_TYPE, _assessment_from_payload, MispricingAssessmentRecord)

    # -- internals ---------------------------------------------------------------

    def _typed_records(self, snapshot_type: str, from_payload: Any, record_type: type[Any]) -> tuple[Any, ...]:
        records = self._records_skipping_malformed(snapshot_type, from_payload)
        return tuple(
            sorted(
                (item for item in records if isinstance(item, record_type)),
                key=lambda item: (item.logical_id, item.effective_at, item.recorded_at, item.known_at, item.record_id),
            )
        )

    def _logical_history(self, snapshot_type: str, from_payload: Any, logical_id: str) -> tuple[Any, ...]:
        if not logical_id.strip():
            raise ValueError("logical_id must not be blank")
        records = [
            record
            for record in self._records_skipping_malformed(snapshot_type, from_payload)
            if record.logical_id == logical_id
        ]
        records.sort(
            key=lambda item: (item.effective_at, item.recorded_at, item.known_at, item.record_id),
        )
        return tuple(records)

    def _records_skipping_malformed(self, snapshot_type: str, from_payload: Any) -> tuple[Any, ...]:
        # Fault-isolated by construction (the F4 lesson already applied in
        # hunter.valuation_methodology, hunter.valuation, and hunter.comparative_valuation):
        # a malformed/legacy row is excluded rather than aborting reads of every other
        # valid record.
        records = []
        for snapshot in self._snapshots(snapshot_type):
            try:
                records.append(from_payload(snapshot.payload))
            except (MispricingIntegrityError, ValueError, KeyError, TypeError):
                continue
        return tuple(records)

    def _load(self, record_id: str, snapshot_type: str) -> SnapshotRecord | None:
        engine = create_sqlite_engine(self.path)
        session = SessionFactory(engine).create()
        try:
            snapshot = RepositoryFactory(session).snapshots().load(record_id)
            if snapshot is None or snapshot.snapshot_type != snapshot_type:
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


# -- snapshot mapping -------------------------------------------------------------


def methodology_snapshot(record: MispricingMethodologySnapshot) -> SnapshotRecord:
    return SnapshotRecord(
        id=record.record_id,
        created_at=record.recorded_at,
        effective_at=record.effective_at,
        snapshot_type=_METHODOLOGY_TYPE,
        target_id=record.methodology_id,
        record_ids=(record.record_id,),
        payload=_payload(record),
        metadata={
            "authority_class": "production-authoritative",
            "domain": "mispricing",
            "logical_id": record.logical_id,
            "known_at": record.known_at.isoformat(),
        },
    )


def assessment_snapshot(record: MispricingAssessmentRecord) -> SnapshotRecord:
    return SnapshotRecord(
        id=record.record_id,
        created_at=record.recorded_at,
        effective_at=record.effective_at,
        snapshot_type=_ASSESSMENT_TYPE,
        target_id=record.identity.representation_id,
        record_ids=(record.record_id,),
        payload=_payload(record),
        metadata={
            "authority_class": "production-authoritative",
            "domain": "mispricing",
            "logical_id": record.logical_id,
            "known_at": record.known_at.isoformat(),
        },
    )


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


def _identity(payload: dict[str, Any]) -> EconomicClaimIdentity:
    return EconomicClaimIdentity(**payload)


def _base_payload(payload: dict[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    for name in ("effective_at", "recorded_at", "known_at"):
        result[name] = datetime.fromisoformat(str(result[name])).astimezone(UTC)
    return result


def _methodology_from_payload(payload: dict[str, Any]) -> MispricingMethodologySnapshot:
    _require_fields(payload, ("methodology_id", "raw_formula", "correlation_group"))
    return MispricingMethodologySnapshot(**_base_payload(payload))


def _assessment_from_payload(payload: dict[str, Any]) -> MispricingAssessmentRecord:
    _require_fields(payload, ("availability_state", "methodology_record_id", "correlation_group"))
    result = _base_payload(payload)
    result["identity"] = _identity(result["identity"])
    return MispricingAssessmentRecord(**result)


def _require_fields(payload: dict[str, Any], names: tuple[str, ...]) -> None:
    missing = tuple(name for name in names if name not in payload)
    if missing:
        raise MispricingIntegrityError(
            "legacy mispricing snapshot is not authoritative under the current contract: " + ",".join(missing)
        )
