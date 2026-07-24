from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hunter.persistence.models import QuerySpec
from hunter.persistence.records import SnapshotRecord
from hunter.persistence.sql import RepositoryFactory, SessionFactory, create_schema, create_sqlite_engine
from hunter.value_capture.models import (
    EconomicClaimIdentity,
    FundamentalEvidenceRecord,
    SupplyBasisSnapshot,
    ValueCaptureRuleSnapshot,
)
from hunter.value_capture.providers import AcquisitionReceipt

DEFAULT_VALUE_CAPTURE_DB = Path("data/data_ops.sqlite")
VALUE_CAPTURE_MIGRATION_ID = "generic-sql-fundamental-value-evidence-v1"

_RECEIPT_TYPE = "fundamental-value-evidence-acquisition-receipt"
_EVIDENCE_TYPE = "fundamental-evidence"
_SUPPLY_TYPE = "supply-basis"
_RULE_TYPE = "value-capture-rule"

Record = FundamentalEvidenceRecord | SupplyBasisSnapshot | ValueCaptureRuleSnapshot


class ValueCaptureIntegrityError(ValueError):
    pass


class SupplyAndValueCaptureRepository:
    """Read boundary for records stored in Hunter's canonical generic SQL authority."""

    def __init__(self, path: str | Path = DEFAULT_VALUE_CAPTURE_DB) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        engine = create_sqlite_engine(self.path)
        try:
            create_schema(engine)
        finally:
            engine.dispose()

    def evidence(self, record_id: str) -> FundamentalEvidenceRecord | None:
        snapshot = self._load(record_id, _EVIDENCE_TYPE)
        return _evidence_from_payload(snapshot.payload) if snapshot is not None else None

    def supply(self, record_id: str) -> SupplyBasisSnapshot | None:
        snapshot = self._load(record_id, _SUPPLY_TYPE)
        return _supply_from_payload(snapshot.payload) if snapshot is not None else None

    def rule(self, record_id: str) -> ValueCaptureRuleSnapshot | None:
        snapshot = self._load(record_id, _RULE_TYPE)
        return _rule_from_payload(snapshot.payload) if snapshot is not None else None

    def receipt(self, acquisition_id: str) -> AcquisitionReceipt | None:
        snapshot = self._load(acquisition_id, _RECEIPT_TYPE)
        return _receipt_from_payload(snapshot.payload) if snapshot is not None else None

    def count(self, table: str) -> int:
        mapping = {
            "value_capture_acquisition_receipts": _RECEIPT_TYPE,
            "fundamental_evidence_records": _EVIDENCE_TYPE,
            "supply_basis_snapshots": _SUPPLY_TYPE,
            "value_capture_rule_snapshots": _RULE_TYPE,
        }
        if table == "value_capture_schema_migrations":
            return 1
        snapshot_type = mapping.get(table)
        if snapshot_type is None:
            raise ValueError("unsupported value-capture table")
        return len(self._snapshots(snapshot_type))

    def migration_ids(self) -> tuple[str, ...]:
        return (VALUE_CAPTURE_MIGRATION_ID,)

    def evidence_history(self, logical_id: str) -> tuple[FundamentalEvidenceRecord, ...]:
        return tuple(
            record
            for record in self._logical_history(snapshot_type=_EVIDENCE_TYPE, logical_id=logical_id)
            if isinstance(record, FundamentalEvidenceRecord)
        )

    def supply_history(self, logical_id: str) -> tuple[SupplyBasisSnapshot, ...]:
        return tuple(
            record
            for record in self._logical_history(snapshot_type=_SUPPLY_TYPE, logical_id=logical_id)
            if isinstance(record, SupplyBasisSnapshot)
        )

    def rule_history(self, logical_id: str) -> tuple[ValueCaptureRuleSnapshot, ...]:
        return tuple(
            record
            for record in self._logical_history(snapshot_type=_RULE_TYPE, logical_id=logical_id)
            if isinstance(record, ValueCaptureRuleSnapshot)
        )

    def strict_known_supply(self, **kwargs: Any) -> SupplyBasisSnapshot | None:
        record = self._strict_known(
            snapshot_type=_SUPPLY_TYPE,
            category_name="supply_basis_type",
            **kwargs,
        )
        return record if isinstance(record, SupplyBasisSnapshot) else None

    def strict_known_rule(self, **kwargs: Any) -> ValueCaptureRuleSnapshot | None:
        record = self._strict_known(
            snapshot_type=_RULE_TYPE,
            category_name="rule_type",
            **kwargs,
        )
        return record if isinstance(record, ValueCaptureRuleSnapshot) else None

    def _strict_known(
        self,
        *,
        snapshot_type: str,
        category_name: str,
        entity_id: str,
        economic_claim_id: str,
        representation_id: str,
        effective_as_of: datetime,
        known_by: datetime,
        **category: str,
    ) -> Record | None:
        effective_as_of = _aware(effective_as_of)
        known_by = _aware(known_by)
        category_value = category[category_name]
        records = tuple(_record_from_snapshot(item) for item in self._snapshots(snapshot_type))
        eligible = [
            item
            for item in records
            if item.identity.entity_id == entity_id
            and item.identity.economic_claim_id == economic_claim_id
            and item.identity.representation_id == representation_id
            and getattr(item, category_name) == category_value
            and item.effective_at <= effective_as_of
            and (
                not isinstance(item, ValueCaptureRuleSnapshot)
                or item.applicability_start <= effective_as_of <= item.applicability_end
            )
            and item.recorded_at <= known_by
            and item.known_at <= known_by
            and item.quality_state == "accepted"
            and item.conflict_state in {"none", "resolved"}
        ]
        superseded = {item.supersedes_record_id for item in eligible if item.supersedes_record_id is not None}
        current = [item for item in eligible if item.record_id not in superseded]
        current.sort(
            key=lambda item: (
                item.effective_at,
                item.recorded_at,
                item.known_at,
                item.record_id,
            ),
            reverse=True,
        )
        return current[0] if current else None

    def _logical_history(self, *, snapshot_type: str, logical_id: str) -> tuple[Record, ...]:
        if not logical_id.strip():
            raise ValueError("logical_id must not be blank")
        records = [
            _record_from_snapshot(item)
            for item in self._snapshots(snapshot_type)
            if item.metadata.get("logical_id") == logical_id
        ]
        records.sort(
            key=lambda item: (
                item.effective_at,
                item.recorded_at,
                item.known_at,
                item.record_id,
            )
        )
        return tuple(records)

    def _load(self, identity: str, snapshot_type: str) -> SnapshotRecord | None:
        engine = create_sqlite_engine(self.path)
        session = SessionFactory(engine).create()
        try:
            snapshot = RepositoryFactory(session).snapshots().load(identity)
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


def receipt_snapshot(receipt: AcquisitionReceipt) -> SnapshotRecord:
    return SnapshotRecord(
        id=receipt.acquisition_id,
        created_at=receipt.acquired_at,
        effective_at=receipt.acquired_at,
        snapshot_type=_RECEIPT_TYPE,
        target_id=receipt.identity.representation_id,
        record_ids=(receipt.acquisition_id,),
        payload=_payload(receipt),
        metadata={
            "authority_class": "acquisition-provenance",
            "domain": "fundamental-value-evidence",
            "known_at": receipt.acquired_at.isoformat(),
        },
    )


def record_snapshot(record: Record) -> SnapshotRecord:
    if isinstance(record, FundamentalEvidenceRecord):
        snapshot_type = _EVIDENCE_TYPE
    elif isinstance(record, SupplyBasisSnapshot):
        snapshot_type = _SUPPLY_TYPE
    else:
        snapshot_type = _RULE_TYPE
    return SnapshotRecord(
        id=record.record_id,
        created_at=record.recorded_at,
        effective_at=record.effective_at,
        snapshot_type=snapshot_type,
        target_id=record.identity.representation_id,
        record_ids=(record.record_id,),
        payload=_payload(record),
        metadata={
            "authority_class": "production-authoritative",
            "domain": "fundamental-value-evidence",
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
    result["identity"] = _identity(result["identity"])
    for name in ("effective_at", "recorded_at", "known_at"):
        result[name] = datetime.fromisoformat(str(result[name])).astimezone(UTC)
    return result


def _receipt_from_payload(payload: dict[str, Any]) -> AcquisitionReceipt:
    result = dict(payload)
    result["identity"] = _identity(result["identity"])
    result["acquired_at"] = datetime.fromisoformat(str(result["acquired_at"])).astimezone(UTC)
    return AcquisitionReceipt(**result)


def _evidence_from_payload(payload: dict[str, Any]) -> FundamentalEvidenceRecord:
    required_v2_fields = (
        "accounting_period_start",
        "accounting_period_end",
        "attribution_rule_id",
        "source_methodology",
        "source_record_id",
        "source_record_version",
        "entity_link_confidence",
        "evidence_confidence",
        "uncertainty",
    )
    missing = tuple(name for name in required_v2_fields if name not in payload)
    if missing:
        raise ValueCaptureIntegrityError(
            "legacy fundamental evidence snapshot is not authoritative under the current contract: " + ",".join(missing)
        )
    result = _base_payload(payload)
    for name in ("accounting_period_start", "accounting_period_end"):
        result[name] = datetime.fromisoformat(str(result[name])).astimezone(UTC)
    return FundamentalEvidenceRecord(**result)


def _supply_from_payload(payload: dict[str, Any]) -> SupplyBasisSnapshot:
    required_v2_fields = (
        "supply_policy_id",
        "supply_policy_version",
        "quantity_components",
        "observed_market_fact_ids",
        "observed_market_fact_versions",
        "source_record_id",
        "source_record_version",
        "confidence",
        "uncertainty",
    )
    missing = tuple(name for name in required_v2_fields if name not in payload)
    if missing:
        raise ValueCaptureIntegrityError(
            "legacy supply basis snapshot is not authoritative under the current contract: " + ",".join(missing)
        )
    result = _base_payload(payload)
    result["evidence_record_ids"] = tuple(result["evidence_record_ids"])
    result["quantity_components"] = tuple((str(item[0]), str(item[1])) for item in result["quantity_components"])
    result["observed_market_fact_ids"] = tuple(result["observed_market_fact_ids"])
    result["observed_market_fact_versions"] = tuple(result["observed_market_fact_versions"])
    return SupplyBasisSnapshot(**result)


def _rule_from_payload(payload: dict[str, Any]) -> ValueCaptureRuleSnapshot:
    required_v2_fields = (
        "mechanism_policy_id",
        "mechanism_policy_version",
        "dilution_treatment",
        "claim_seniority",
        "applicability_start",
        "applicability_end",
        "limitations",
        "evidence_record_versions",
        "source_record_id",
        "source_record_version",
        "confidence",
        "uncertainty",
    )
    missing = tuple(name for name in required_v2_fields if name not in payload)
    if missing:
        raise ValueCaptureIntegrityError(
            "legacy value capture rule snapshot is not authoritative under the current contract: " + ",".join(missing)
        )
    result = _base_payload(payload)
    result["evidence_record_ids"] = tuple(result["evidence_record_ids"])
    result["evidence_record_versions"] = tuple(result["evidence_record_versions"])
    result["limitations"] = tuple(result["limitations"])
    for name in ("applicability_start", "applicability_end"):
        result[name] = datetime.fromisoformat(str(result[name])).astimezone(UTC)
    return ValueCaptureRuleSnapshot(**result)


def _record_from_snapshot(snapshot: SnapshotRecord) -> Record:
    if snapshot.snapshot_type == _EVIDENCE_TYPE:
        return _evidence_from_payload(snapshot.payload)
    if snapshot.snapshot_type == _SUPPLY_TYPE:
        return _supply_from_payload(snapshot.payload)
    if snapshot.snapshot_type == _RULE_TYPE:
        return _rule_from_payload(snapshot.payload)
    raise ValueError("unsupported fundamental-value-evidence snapshot")


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(UTC)
