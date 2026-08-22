from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hunter.persistence.models import QuerySpec
from hunter.persistence.records import SnapshotRecord
from hunter.persistence.sql import RepositoryFactory, SessionFactory, create_schema, create_sqlite_engine
from hunter.valuation_methodology.models import (
    MethodologyEvidenceInputContract,
    ValuationMethodologySnapshot,
)

DEFAULT_VALUATION_METHODOLOGY_DB = Path("data/data_ops.sqlite")
VALUATION_METHODOLOGY_MIGRATION_ID = "generic-sql-valuation-methodology-snapshot-v1"

_METHODOLOGY_TYPE = "valuation-methodology-snapshot"
_CONTRACT_TYPE = "methodology-evidence-input-contract"


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

    def get_contract(self, contract_id: str, contract_version: str) -> MethodologyEvidenceInputContract | None:
        snapshot = self._load_contract(contract_id, contract_version)
        return _contract_from_payload(snapshot.payload) if snapshot is not None else None

    def count(self, table: str) -> int:
        if table == "valuation_methodology_schema_migrations":
            return 1
        if table == "valuation_methodology_snapshots":
            return len(self._snapshots())
        if table == "valuation_methodology_contracts":
            return len(self._contract_snapshots())
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

    def contract_history(self, contract_id: str) -> tuple[MethodologyEvidenceInputContract, ...]:
        if not contract_id.strip():
            raise ValueError("contract_id must not be blank")
        records = [contract for contract in self._contracts_skipping_malformed() if contract.contract_id == contract_id]
        records.sort(
            key=lambda item: (item.effective_at, item.recorded_at, item.known_at, item.contract_version),
        )
        return tuple(records)

    def records(self) -> tuple[ValuationMethodologySnapshot, ...]:
        """Return every decodable record in deterministic storage order."""
        return tuple(
            sorted(
                self._records_skipping_malformed(),
                key=lambda item: (item.logical_id, item.effective_at, item.recorded_at, item.known_at, item.record_id),
            )
        )

    def contracts(self) -> tuple[MethodologyEvidenceInputContract, ...]:
        """Return every decodable contract in deterministic storage order."""
        return tuple(
            sorted(
                self._contracts_skipping_malformed(),
                key=lambda item: (
                    item.contract_id,
                    item.effective_at,
                    item.recorded_at,
                    item.known_at,
                    item.contract_version,
                ),
            )
        )

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

    def _contracts_skipping_malformed(self) -> tuple[MethodologyEvidenceInputContract, ...]:
        records = []
        for snapshot in self._contract_snapshots():
            try:
                records.append(_contract_from_payload(snapshot.payload))
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

    def _load_contract(self, contract_id: str, contract_version: str) -> SnapshotRecord | None:
        target_id = f"{contract_id}:{contract_version}"
        engine = create_sqlite_engine(self.path)
        session = SessionFactory(engine).create()
        try:
            snapshot = RepositoryFactory(session).snapshots().load(target_id)
            if snapshot is None or snapshot.snapshot_type != _CONTRACT_TYPE:
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

    def _contract_snapshots(self) -> tuple[SnapshotRecord, ...]:
        engine = create_sqlite_engine(self.path)
        session = SessionFactory(engine).create()
        try:
            records = RepositoryFactory(session).snapshots().query(QuerySpec(record_kind="snapshot"))
            return tuple(item for item in records if item.snapshot_type == _CONTRACT_TYPE)
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


def methodology_contract_snapshot(contract: MethodologyEvidenceInputContract) -> SnapshotRecord:
    target_id = f"{contract.contract_id}:{contract.contract_version}"
    return SnapshotRecord(
        id=target_id,
        created_at=contract.recorded_at,
        effective_at=contract.effective_at,
        snapshot_type=_CONTRACT_TYPE,
        target_id=contract.contract_id,
        record_ids=(target_id,),
        payload=_payload(contract),
        metadata={
            "authority_class": "production-authoritative",
            "domain": "valuation-methodology-contract",
            "contract_id": contract.contract_id,
            "contract_version": contract.contract_version,
            "known_at": contract.known_at.isoformat(),
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
    result.setdefault("accepts_assembled_evidence", False)
    for name in ("effective_at", "recorded_at", "known_at"):
        result[name] = datetime.fromisoformat(str(result[name])).astimezone(UTC)
    return ValuationMethodologySnapshot(**result)


def _contract_from_payload(payload: dict[str, Any]) -> MethodologyEvidenceInputContract:
    required_fields = (
        "contract_id",
        "contract_version",
        "accepts_assembled_evidence",
        "accepted_shape_ids",
        "accepted_assembly_rule_versions",
        "accounting_window_start",
        "accounting_window_end",
        "entity_id",
        "representation_id",
        "currency",
        "unit",
        "effective_at",
        "recorded_at",
        "known_at",
        "quality_state",
        "conflict_state",
        "content_hash",
    )
    missing = tuple(name for name in required_fields if name not in payload)
    if missing:
        raise ValuationMethodologyIntegrityError(
            "legacy methodology contract snapshot is missing required fields: " + ",".join(missing)
        )
    result = dict(payload)
    for name in ("accepted_shape_ids", "accepted_assembly_rule_versions"):
        if isinstance(result.get(name), list):
            result[name] = tuple(result[name])
    for name in ("accounting_window_start", "accounting_window_end", "effective_at", "recorded_at", "known_at"):
        result[name] = datetime.fromisoformat(str(result[name])).astimezone(UTC)
    return MethodologyEvidenceInputContract(**result)


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(UTC)
