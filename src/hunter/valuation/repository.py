from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
try:
    from datetime import UTC
except ImportError:
    from datetime import timezone as _tz
    UTC = _tz.utc
from pathlib import Path
from typing import Any

from hunter.persistence.models import QuerySpec
from hunter.persistence.records import SnapshotRecord
from hunter.persistence.sql import RepositoryFactory, SessionFactory, create_schema, create_sqlite_engine
from hunter.valuation.models import FairValueEstimateRecord, ValuationAssessmentRecord

DEFAULT_VALUATION_DB = Path("data/data_ops.sqlite")
VALUATION_MIGRATION_ID = "generic-sql-canonical-valuation-v1"

_ESTIMATE_TYPE = "fair-value-estimate-record"
_ASSESSMENT_TYPE = "valuation-assessment-record"


class CanonicalValuationIntegrityError(ValueError):
    pass


class CanonicalValuationRepository:
    """Read boundary for records stored in Hunter's canonical generic SQL authority.

    Purely mechanical, mirroring hunter.value_capture.repository.SupplyAndValueCaptureRepository
    and hunter.valuation_methodology.repository.ValuationMethodologyRepository: no write/apply
    method and no validation of its own. Every write is authorized and performed exclusively by
    CanonicalValuationService (hunter.valuation.service), which opens its own session against
    self.path directly.
    """

    def __init__(self, path: str | Path = DEFAULT_VALUATION_DB) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        engine = create_sqlite_engine(self.path)
        try:
            create_schema(engine)
        finally:
            engine.dispose()

    def get_fair_value_estimate(self, record_id: str) -> FairValueEstimateRecord | None:
        snapshot = self._load(record_id, _ESTIMATE_TYPE)
        return _estimate_from_payload(snapshot.payload) if snapshot is not None else None

    def get_valuation_assessment(self, record_id: str) -> ValuationAssessmentRecord | None:
        snapshot = self._load(record_id, _ASSESSMENT_TYPE)
        return _assessment_from_payload(snapshot.payload) if snapshot is not None else None

    def count(self, table: str) -> int:
        mapping = {
            "fair_value_estimate_records": _ESTIMATE_TYPE,
            "valuation_assessment_records": _ASSESSMENT_TYPE,
        }
        if table == "valuation_schema_migrations":
            return 1
        snapshot_type = mapping.get(table)
        if snapshot_type is None:
            raise ValueError("unsupported valuation table")
        return len(self._snapshots(snapshot_type))

    def migration_ids(self) -> tuple[str, ...]:
        return (VALUATION_MIGRATION_ID,)

    def fair_value_history(self, logical_id: str) -> tuple[FairValueEstimateRecord, ...]:
        return tuple(
            record
            for record in self._records_skipping_malformed(_ESTIMATE_TYPE, _estimate_from_payload)
            if record.logical_id == logical_id
        )

    def valuation_assessment_history(self, logical_id: str) -> tuple[ValuationAssessmentRecord, ...]:
        return tuple(
            record
            for record in self._records_skipping_malformed(_ASSESSMENT_TYPE, _assessment_from_payload)
            if record.logical_id == logical_id
        )

    def _records_skipping_malformed(self, snapshot_type: str, from_payload: Any) -> tuple[Any, ...]:
        # Fault-isolated by construction (F4's lesson, already applied in
        # hunter.valuation_methodology): a malformed/legacy row is excluded rather than
        # aborting reads of every other, valid record.
        records = []
        for snapshot in self._snapshots(snapshot_type):
            try:
                records.append(from_payload(snapshot.payload))
            except (CanonicalValuationIntegrityError, ValueError, KeyError):
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


def estimate_snapshot(record: FairValueEstimateRecord) -> SnapshotRecord:
    return SnapshotRecord(
        id=record.record_id,
        created_at=record.recorded_at,
        effective_at=record.effective_at,
        snapshot_type=_ESTIMATE_TYPE,
        target_id=record.identity.representation_id,
        record_ids=(record.record_id,),
        payload=_payload(record),
        metadata={
            "authority_class": "production-authoritative",
            "domain": "canonical-valuation",
            "logical_id": record.logical_id,
            "known_at": record.known_at.isoformat(),
        },
    )


def assessment_snapshot(record: ValuationAssessmentRecord) -> SnapshotRecord:
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
            "domain": "canonical-valuation",
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


def _identity(payload: dict[str, Any]) -> Any:
    from hunter.value_capture.models import EconomicClaimIdentity

    return EconomicClaimIdentity(**payload)


def _base_payload(payload: dict[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    result["identity"] = _identity(result["identity"])
    for name in ("effective_at", "recorded_at", "known_at"):
        result[name] = datetime.fromisoformat(str(result[name])).astimezone(UTC)
    return result


def _estimate_from_payload(payload: dict[str, Any]) -> FairValueEstimateRecord:
    required_fields = (
        "currency",
        "horizon_days",
        "supply_basis_selection_rule",
        "discount_rate_policy_id",
        "discount_rate_policy_version",
        "discount_rate_applied",
        "fair_value_per_diluted_unit_p10",
        "fair_value_per_diluted_unit_p50",
        "fair_value_per_diluted_unit_p90",
        "total_diluted_value_p10",
        "total_diluted_value_p50",
        "total_diluted_value_p90",
        "model_dispersion_ratio",
        "confidence",
        "confidence_base",
        "confidence_dispersion_factor",
        "confidence_freshness_factor",
        "methodology_record_id",
        "methodology_record_version",
        "evidence_record_id",
        "evidence_record_version",
        "rule_record_id",
        "rule_record_version",
        "supply_record_id",
        "supply_record_version",
        "market_fact_record_ids",
        "market_fact_record_versions",
    )
    missing = tuple(name for name in required_fields if name not in payload)
    if missing:
        raise CanonicalValuationIntegrityError(
            "legacy fair-value estimate snapshot is not authoritative under the current contract: " + ",".join(missing)
        )
    result = _base_payload(payload)
    result["market_fact_record_ids"] = tuple(result["market_fact_record_ids"])
    result["market_fact_record_versions"] = tuple(result["market_fact_record_versions"])
    return FairValueEstimateRecord(**result)


def _assessment_from_payload(payload: dict[str, Any]) -> ValuationAssessmentRecord:
    required_fields = (
        "fair_value_estimate_record_id",
        "fair_value_estimate_record_version",
        "confidence",
        "normalization_status",
        "normalized_value",
        "correlation_group",
    )
    missing = tuple(name for name in required_fields if name not in payload)
    if missing:
        raise CanonicalValuationIntegrityError(
            "legacy valuation assessment snapshot is not authoritative under the current contract: " + ",".join(missing)
        )
    result = _base_payload(payload)
    return ValuationAssessmentRecord(**result)


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(UTC)
