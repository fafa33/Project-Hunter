from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hunter.persistence.models import QuerySpec
from hunter.persistence.records import SnapshotRecord
from hunter.persistence.sql import RepositoryFactory, SessionFactory, create_schema, create_sqlite_engine
from hunter.valuation_methodology.models import ValuationMethodologySnapshot

DEFAULT_VALUATION_METHODOLOGY_DB = Path("data/data_ops.sqlite")
VALUATION_METHODOLOGY_MIGRATION_ID = "generic-sql-valuation-methodology-snapshot-v2"

_METHODOLOGY_TYPE = "valuation-methodology-snapshot"


class ValuationMethodologyIntegrityError(ValueError):
    pass


class ValuationMethodologyRepository:
    """Read boundary for records stored in Hunter's canonical generic SQL authority.

    Deliberately mirrors hunter.value_capture.repository.SupplyAndValueCaptureRepository's
    shape: purely mechanical reads, no write/apply method and no authority validation of
    its own. Every write is authorized and performed exclusively by
    CanonicalValuationMethodologyAuthority (hunter.valuation_methodology.service), which
    opens its own session against self.path directly -- exactly the same division of
    responsibility already used for value-capture records.
    """

    def __init__(self, path: str | Path = DEFAULT_VALUATION_METHODOLOGY_DB) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        engine = create_sqlite_engine(self.path)
        try:
            create_schema(engine)
        finally:
            engine.dispose()

    def get(self, record_id: str) -> ValuationMethodologySnapshot | None:
        snapshot = self._load(record_id)
        return _from_payload(snapshot.payload) if snapshot is not None else None

    def count(self, table: str) -> int:
        if table == "valuation_methodology_schema_migrations":
            return 1
        if table == "valuation_methodology_snapshots":
            return len(self._snapshots())
        raise ValueError("unsupported valuation-methodology table")

    def migration_ids(self) -> tuple[str, ...]:
        return (VALUATION_METHODOLOGY_MIGRATION_ID,)

    def methodology_history(self, logical_id: str) -> tuple[ValuationMethodologySnapshot, ...]:
        if not logical_id.strip():
            raise ValueError("logical_id must not be blank")
        records = [record for record in self._records_skipping_malformed() if record.logical_id == logical_id]
        records.sort(
            key=lambda item: (item.effective_at, item.recorded_at, item.known_at, item.record_id),
        )
        return tuple(records)

    def unresolved_conflicts(self) -> tuple[ValuationMethodologySnapshot, ...]:
        records = self._records_skipping_malformed()
        superseded_ids = {item.supersedes_record_id for item in records if item.supersedes_record_id is not None}
        current = (item for item in records if item.record_id not in superseded_ids)
        unresolved = [item for item in current if item.conflict_state in {"open", "contested"}]
        return tuple(sorted(unresolved, key=lambda item: (item.logical_id, item.effective_at, item.record_id)))

    def strict_known_methodology(
        self,
        *,
        effective_as_of: datetime,
        known_by: datetime,
    ) -> ValuationMethodologySnapshot | None:
        effective_as_of = _aware(effective_as_of)
        known_by = _aware(known_by)
        records = self._records_skipping_malformed()
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
        current.sort(
            key=lambda item: (item.effective_at, item.recorded_at, item.known_at, item.record_id),
            reverse=True,
        )
        return current[0] if current else None

    def _records_skipping_malformed(self) -> tuple[ValuationMethodologySnapshot, ...]:
        # Fault-isolated by construction: a malformed/legacy row is excluded rather than
        # aborting reads of every other, valid record -- the gap audit finding F4
        # identified in hunter.value_capture. New code should not repeat that gap rather
        # than waiting for F4 itself to be remediated there.
        records = []
        for snapshot in self._snapshots():
            try:
                records.append(_from_payload(snapshot.payload))
            except (ValuationMethodologyIntegrityError, ValueError, KeyError):
                continue
        return tuple(records)

    def _load(self, record_id: str) -> SnapshotRecord | None:
        engine = create_sqlite_engine(self.path)
        session = SessionFactory(engine).create()
        try:
            snapshot = RepositoryFactory(session).snapshots().load(record_id)
            if snapshot is None or snapshot.snapshot_type != _METHODOLOGY_TYPE:
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
            return tuple(item for item in records if item.snapshot_type == _METHODOLOGY_TYPE)
        finally:
            session.close()
            engine.dispose()


def methodology_snapshot(record: ValuationMethodologySnapshot) -> SnapshotRecord:
    return SnapshotRecord(
        id=record.record_id,
        created_at=record.recorded_at,
        effective_at=record.effective_at,
        snapshot_type=_METHODOLOGY_TYPE,
        target_id=record.logical_id,
        record_ids=(record.record_id,),
        payload=_payload(record),
        metadata={
            "authority_class": "production-authoritative",
            "domain": "valuation-methodology",
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


def _from_payload(payload: dict[str, Any]) -> ValuationMethodologySnapshot:
    required_fields = (
        "entity_class_criteria_id",
        "entity_class_criteria_version",
        "permitted_model_identifier",
        "horizon_days",
        "currency",
        "discount_rate_policy_id",
        "discount_rate_policy_version",
        "sensitivity_policy_id",
        "sensitivity_policy_version",
        "supply_basis_selection_rule",
        "correlation_group",
        "authorizing_adr_reference",
        "authorized_by",
    )
    missing = tuple(name for name in required_fields if name not in payload)
    if missing:
        raise ValuationMethodologyIntegrityError(
            "legacy valuation methodology snapshot is not authoritative under the current contract: "
            + ",".join(missing)
        )
    result = dict(payload)
    for name in ("effective_at", "recorded_at", "known_at"):
        result[name] = datetime.fromisoformat(str(result[name])).astimezone(UTC)
    return ValuationMethodologySnapshot(**result)


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(UTC)
