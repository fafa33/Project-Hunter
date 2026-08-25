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

from hunter.asymmetry.models import (
    AsymmetryAssessmentRecord,
    AsymmetryMethodologySnapshot,
    ScenarioPayoffEstimateRecord,
    ScenarioProbabilityRecord,
    ScenarioSetSnapshot,
)
from hunter.persistence.models import QuerySpec
from hunter.persistence.records import SnapshotRecord
from hunter.persistence.sql import RepositoryFactory, SessionFactory, create_schema, create_sqlite_engine
from hunter.value_capture.models import EconomicClaimIdentity

DEFAULT_ASYMMETRY_DB = Path("data/data_ops.sqlite")
ASYMMETRY_MIGRATION_ID = "generic-sql-canonical-asymmetry-v1"

_METHODOLOGY_TYPE = "asymmetry-methodology-snapshot"
_SCENARIO_SET_TYPE = "asymmetry-scenario-set-snapshot"
_PROBABILITY_TYPE = "asymmetry-scenario-probability-record"
_PAYOFF_TYPE = "asymmetry-scenario-payoff-estimate-record"
_ASSESSMENT_TYPE = "asymmetry-assessment-record"


def _order_key(item: Any) -> tuple[Any, ...]:
    # Deterministic history ordering shared by every mechanical history read: effective,
    # recorded, known, then record id. Never a replay-selection decision (ADR 0009).
    return (item.effective_at, item.recorded_at, item.known_at, item.record_id)


_ORDER_KEY = _order_key


class AsymmetryIntegrityError(ValueError):
    """Raised when immutable Asymmetry identity is reused with divergent content, when
    a correction lineage is invalid, or when a legacy/malformed snapshot is not
    authoritative under the current contract."""


class AsymmetryRepository:
    """Read boundary for records stored in Hunter's canonical generic SQL authority.

    Deliberately mirrors hunter.mispricing.repository.MispricingRepository,
    hunter.valuation.repository.CanonicalValuationRepository, and
    hunter.comparative_valuation.repository.ComparativeValuationRepository: purely
    mechanical reads, no write/apply method and no authority validation of its own, and
    no replay selection of any kind (ADR 0009). Every write is authorized and performed
    exclusively by hunter.asymmetry.service.CanonicalAsymmetryService, which opens its
    own session against self.path directly.
    """

    def __init__(self, path: str | Path = DEFAULT_ASYMMETRY_DB) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        engine = create_sqlite_engine(self.path)
        try:
            create_schema(engine)
        finally:
            engine.dispose()

    # -- point reads ---------------------------------------------------------

    def get_asymmetry_methodology(self, record_id: str) -> AsymmetryMethodologySnapshot | None:
        snapshot = self._load(record_id, _METHODOLOGY_TYPE)
        return _methodology_from_payload(snapshot.payload) if snapshot is not None else None

    def get_scenario_set(self, record_id: str) -> ScenarioSetSnapshot | None:
        snapshot = self._load(record_id, _SCENARIO_SET_TYPE)
        return _scenario_set_from_payload(snapshot.payload) if snapshot is not None else None

    def get_scenario_probability(self, record_id: str) -> ScenarioProbabilityRecord | None:
        snapshot = self._load(record_id, _PROBABILITY_TYPE)
        return _probability_from_payload(snapshot.payload) if snapshot is not None else None

    def get_scenario_payoff(self, record_id: str) -> ScenarioPayoffEstimateRecord | None:
        snapshot = self._load(record_id, _PAYOFF_TYPE)
        return _payoff_from_payload(snapshot.payload) if snapshot is not None else None

    def get_assessment(self, record_id: str) -> AsymmetryAssessmentRecord | None:
        snapshot = self._load(record_id, _ASSESSMENT_TYPE)
        return _assessment_from_payload(snapshot.payload) if snapshot is not None else None

    # -- counts and migrations -----------------------------------------------

    def count(self, table: str) -> int:
        mapping = {
            "asymmetry_schema_migrations": 1,
            "asymmetry_methodologies": len(self._snapshots(_METHODOLOGY_TYPE)),
            "asymmetry_scenario_sets": len(self._snapshots(_SCENARIO_SET_TYPE)),
            "asymmetry_scenario_probabilities": len(self._snapshots(_PROBABILITY_TYPE)),
            "asymmetry_scenario_payoffs": len(self._snapshots(_PAYOFF_TYPE)),
            "asymmetry_assessments": len(self._snapshots(_ASSESSMENT_TYPE)),
        }
        if table not in mapping:
            raise ValueError("unsupported asymmetry table")
        return mapping[table]

    def migration_ids(self) -> tuple[str, ...]:
        return (ASYMMETRY_MIGRATION_ID,)

    # -- history reads ----------------------------------------------------------

    def methodology_history(self, logical_id: str) -> tuple[AsymmetryMethodologySnapshot, ...]:
        return tuple(
            record
            for record in self._logical_history(_METHODOLOGY_TYPE, _methodology_from_payload, logical_id)
            if isinstance(record, AsymmetryMethodologySnapshot)
        )

    def scenario_set_history(self, logical_id: str) -> tuple[ScenarioSetSnapshot, ...]:
        return tuple(
            record
            for record in self._logical_history(_SCENARIO_SET_TYPE, _scenario_set_from_payload, logical_id)
            if isinstance(record, ScenarioSetSnapshot)
        )

    def scenario_probability_history(self, logical_id: str) -> tuple[ScenarioProbabilityRecord, ...]:
        return tuple(
            record
            for record in self._logical_history(_PROBABILITY_TYPE, _probability_from_payload, logical_id)
            if isinstance(record, ScenarioProbabilityRecord)
        )

    def scenario_payoff_history(self, logical_id: str) -> tuple[ScenarioPayoffEstimateRecord, ...]:
        return tuple(
            record
            for record in self._logical_history(_PAYOFF_TYPE, _payoff_from_payload, logical_id)
            if isinstance(record, ScenarioPayoffEstimateRecord)
        )

    def assessment_history(self, logical_id: str) -> tuple[AsymmetryAssessmentRecord, ...]:
        return tuple(
            record
            for record in self._logical_history(_ASSESSMENT_TYPE, _assessment_from_payload, logical_id)
            if isinstance(record, AsymmetryAssessmentRecord)
        )

    def methodology_records(self) -> tuple[AsymmetryMethodologySnapshot, ...]:
        return self._typed_records(_METHODOLOGY_TYPE, _methodology_from_payload, AsymmetryMethodologySnapshot)

    def scenario_set_records(self) -> tuple[ScenarioSetSnapshot, ...]:
        return self._typed_records(_SCENARIO_SET_TYPE, _scenario_set_from_payload, ScenarioSetSnapshot)

    def probability_records(self) -> tuple[ScenarioProbabilityRecord, ...]:
        return self._typed_records(_PROBABILITY_TYPE, _probability_from_payload, ScenarioProbabilityRecord)

    def payoff_records(self) -> tuple[ScenarioPayoffEstimateRecord, ...]:
        return self._typed_records(_PAYOFF_TYPE, _payoff_from_payload, ScenarioPayoffEstimateRecord)

    def assessment_records(self) -> tuple[AsymmetryAssessmentRecord, ...]:
        return self._typed_records(_ASSESSMENT_TYPE, _assessment_from_payload, AsymmetryAssessmentRecord)

    # -- internals ---------------------------------------------------------------

    def _typed_records(self, snapshot_type: str, from_payload: Any, record_type: type[Any]) -> tuple[Any, ...]:
        records = self._records_skipping_malformed(snapshot_type, from_payload)
        return tuple(sorted((item for item in records if isinstance(item, record_type)), key=_ORDER_KEY))

    def _logical_history(self, snapshot_type: str, from_payload: Any, logical_id: str) -> tuple[Any, ...]:
        if not logical_id.strip():
            raise ValueError("logical_id must not be blank")
        records = [
            record
            for record in self._records_skipping_malformed(snapshot_type, from_payload)
            if record.logical_id == logical_id
        ]
        records.sort(key=_ORDER_KEY)
        return tuple(records)

    def _records_skipping_malformed(self, snapshot_type: str, from_payload: Any) -> tuple[Any, ...]:
        # Fault-isolated by construction (the F4 lesson already applied in
        # hunter.valuation_methodology, hunter.valuation, hunter.comparative_valuation,
        # and hunter.mispricing): a malformed/legacy row is excluded rather than aborting
        # reads of every other valid record.
        records = []
        for snapshot in self._snapshots(snapshot_type):
            try:
                records.append(from_payload(snapshot.payload))
            except (AsymmetryIntegrityError, ValueError, KeyError, TypeError):
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


def methodology_snapshot(record: AsymmetryMethodologySnapshot) -> SnapshotRecord:
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
            "domain": "asymmetry",
            "logical_id": record.logical_id,
            "known_at": record.known_at.isoformat(),
        },
    )


def scenario_set_snapshot(record: ScenarioSetSnapshot) -> SnapshotRecord:
    return SnapshotRecord(
        id=record.record_id,
        created_at=record.recorded_at,
        effective_at=record.effective_at,
        snapshot_type=_SCENARIO_SET_TYPE,
        target_id=record.identity.representation_id,
        record_ids=(record.record_id,),
        payload=_payload(record),
        metadata={
            "authority_class": "production-authoritative",
            "domain": "asymmetry",
            "logical_id": record.logical_id,
            "known_at": record.known_at.isoformat(),
        },
    )


def scenario_probability_snapshot(record: ScenarioProbabilityRecord) -> SnapshotRecord:
    return SnapshotRecord(
        id=record.record_id,
        created_at=record.recorded_at,
        effective_at=record.effective_at,
        snapshot_type=_PROBABILITY_TYPE,
        target_id=record.scenario_id,
        record_ids=(record.record_id,),
        payload=_payload(record),
        metadata={
            "authority_class": "production-authoritative",
            "domain": "asymmetry",
            "logical_id": record.logical_id,
            "known_at": record.known_at.isoformat(),
        },
    )


def scenario_payoff_snapshot(record: ScenarioPayoffEstimateRecord) -> SnapshotRecord:
    return SnapshotRecord(
        id=record.record_id,
        created_at=record.recorded_at,
        effective_at=record.effective_at,
        snapshot_type=_PAYOFF_TYPE,
        target_id=record.scenario_id,
        record_ids=(record.record_id,),
        payload=_payload(record),
        metadata={
            "authority_class": "production-authoritative",
            "domain": "asymmetry",
            "logical_id": record.logical_id,
            "known_at": record.known_at.isoformat(),
        },
    )


def assessment_snapshot(record: AsymmetryAssessmentRecord) -> SnapshotRecord:
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
            "domain": "asymmetry",
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


def _tuples(payload: dict[str, Any], names: tuple[str, ...]) -> None:
    for name in names:
        if name in payload:
            payload[name] = tuple(payload[name])


def _methodology_from_payload(payload: dict[str, Any]) -> AsymmetryMethodologySnapshot:
    _require_fields(payload, ("methodology_id", "raw_formula", "correlation_group"))
    return AsymmetryMethodologySnapshot(**_base_payload(payload))


def _scenario_set_from_payload(payload: dict[str, Any]) -> ScenarioSetSnapshot:
    _require_fields(payload, ("scenario_set_id", "scenario_ids", "methodology_record_id"))
    result = _base_payload(payload)
    _tuples(
        result,
        ("scenario_ids", "scenario_types", "evidence_record_ids", "source_versions", "dependency_pairs"),
    )
    if "dependency_pairs" in result:
        result["dependency_pairs"] = tuple(tuple(pair) for pair in result["dependency_pairs"])
    result["identity"] = _identity(result["identity"])
    return ScenarioSetSnapshot(**result)


def _probability_from_payload(payload: dict[str, Any]) -> ScenarioProbabilityRecord:
    _require_fields(payload, ("scenario_set_record_id", "scenario_id", "probability"))
    return ScenarioProbabilityRecord(**_base_payload(payload))


def _payoff_from_payload(payload: dict[str, Any]) -> ScenarioPayoffEstimateRecord:
    _require_fields(payload, ("scenario_set_record_id", "scenario_id", "terminal_payoff", "payoff_class"))
    result = _base_payload(payload)
    _tuples(result, ("evidence_record_ids",))
    return ScenarioPayoffEstimateRecord(**result)


def _assessment_from_payload(payload: dict[str, Any]) -> AsymmetryAssessmentRecord:
    _require_fields(payload, ("availability_state", "methodology_record_id", "correlation_group"))
    result = _base_payload(payload)
    _tuples(result, ("scenario_probability_record_ids", "scenario_payoff_record_ids"))
    result["identity"] = _identity(result["identity"])
    return AsymmetryAssessmentRecord(**result)


def _require_fields(payload: dict[str, Any], names: tuple[str, ...]) -> None:
    missing = tuple(name for name in names if name not in payload)
    if missing:
        raise AsymmetryIntegrityError(
            "legacy asymmetry snapshot is not authoritative under the current contract: " + ",".join(missing)
        )
