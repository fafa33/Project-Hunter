from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hunter.comparative_valuation.models import (
    ComparativeMetricObservationRecord,
    ComparativeValuationAssessmentRecord,
    PeerCandidate,
    PeerEligibilityDecisionRecord,
    PeerUniversePolicyRecord,
    PeerUniverseSnapshot,
)
from hunter.persistence.models import QuerySpec
from hunter.persistence.records import SnapshotRecord
from hunter.persistence.sql import RepositoryFactory, SessionFactory, create_schema, create_sqlite_engine
from hunter.value_capture.models import EconomicClaimIdentity

DEFAULT_COMPARATIVE_VALUATION_DB = Path("data/data_ops.sqlite")
COMPARATIVE_VALUATION_MIGRATION_ID = "generic-sql-comparative-valuation-v1"

_POLICY_TYPE = "peer-universe-policy"
_UNIVERSE_TYPE = "peer-universe-snapshot"
_DECISION_TYPE = "peer-eligibility-decision"
_OBSERVATION_TYPE = "comparative-metric-observation"
_ASSESSMENT_TYPE = "comparative-valuation-assessment"


class ComparativeValuationIntegrityError(ValueError):
    """Raised when immutable Comparative Valuation identity is reused with divergent
    content, when a correction lineage is invalid, or when a legacy/malformed snapshot
    is not authoritative under the current contract."""


class ComparativeValuationRepository:
    """Read boundary for records stored in Hunter's canonical generic SQL authority.

    Deliberately mirrors hunter.valuation.repository.CanonicalValuationRepository and
    hunter.value_capture.repository.SupplyAndValueCaptureRepository: purely mechanical
    reads, no write/apply method and no authority validation of its own. Every write is
    authorized and performed exclusively by
    hunter.comparative_valuation.service.CanonicalComparativeValuationService, which
    opens its own session against self.path directly.
    """

    def __init__(self, path: str | Path = DEFAULT_COMPARATIVE_VALUATION_DB) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        engine = create_sqlite_engine(self.path)
        try:
            create_schema(engine)
        finally:
            engine.dispose()

    # -- point reads ---------------------------------------------------------

    def get_peer_policy(self, record_id: str) -> PeerUniversePolicyRecord | None:
        snapshot = self._load(record_id, _POLICY_TYPE)
        return _policy_from_payload(snapshot.payload) if snapshot is not None else None

    def get_peer_universe(self, record_id: str) -> PeerUniverseSnapshot | None:
        snapshot = self._load(record_id, _UNIVERSE_TYPE)
        return _universe_from_payload(snapshot.payload) if snapshot is not None else None

    def get_eligibility_decision(self, record_id: str) -> PeerEligibilityDecisionRecord | None:
        snapshot = self._load(record_id, _DECISION_TYPE)
        return _decision_from_payload(snapshot.payload) if snapshot is not None else None

    def get_metric_observation(self, record_id: str) -> ComparativeMetricObservationRecord | None:
        snapshot = self._load(record_id, _OBSERVATION_TYPE)
        return _observation_from_payload(snapshot.payload) if snapshot is not None else None

    def get_assessment(self, record_id: str) -> ComparativeValuationAssessmentRecord | None:
        snapshot = self._load(record_id, _ASSESSMENT_TYPE)
        return _assessment_from_payload(snapshot.payload) if snapshot is not None else None

    # -- counts and migrations -----------------------------------------------

    def count(self, table: str) -> int:
        mapping = {
            "comparative_valuation_schema_migrations": 1,
            "peer_universe_policies": len(self._snapshots(_POLICY_TYPE)),
            "peer_universe_snapshots": len(self._snapshots(_UNIVERSE_TYPE)),
            "peer_eligibility_decisions": len(self._snapshots(_DECISION_TYPE)),
            "comparative_metric_observations": len(self._snapshots(_OBSERVATION_TYPE)),
            "comparative_valuation_assessments": len(self._snapshots(_ASSESSMENT_TYPE)),
        }
        if table not in mapping:
            raise ValueError("unsupported comparative valuation table")
        return mapping[table]

    def migration_ids(self) -> tuple[str, ...]:
        return (COMPARATIVE_VALUATION_MIGRATION_ID,)

    # -- history reads ----------------------------------------------------------

    def policy_history(self, logical_id: str) -> tuple[PeerUniversePolicyRecord, ...]:
        return tuple(
            record
            for record in self._logical_history(_POLICY_TYPE, _policy_from_payload, logical_id)
            if isinstance(record, PeerUniversePolicyRecord)
        )

    def universe_history(self, logical_id: str) -> tuple[PeerUniverseSnapshot, ...]:
        return tuple(
            record
            for record in self._logical_history(_UNIVERSE_TYPE, _universe_from_payload, logical_id)
            if isinstance(record, PeerUniverseSnapshot)
        )

    def decision_history(self, logical_id: str) -> tuple[PeerEligibilityDecisionRecord, ...]:
        return tuple(
            record
            for record in self._logical_history(_DECISION_TYPE, _decision_from_payload, logical_id)
            if isinstance(record, PeerEligibilityDecisionRecord)
        )

    def observation_history(self, logical_id: str) -> tuple[ComparativeMetricObservationRecord, ...]:
        return tuple(
            record
            for record in self._logical_history(_OBSERVATION_TYPE, _observation_from_payload, logical_id)
            if isinstance(record, ComparativeMetricObservationRecord)
        )

    def assessment_history(self, logical_id: str) -> tuple[ComparativeValuationAssessmentRecord, ...]:
        return tuple(
            record
            for record in self._logical_history(_ASSESSMENT_TYPE, _assessment_from_payload, logical_id)
            if isinstance(record, ComparativeValuationAssessmentRecord)
        )

    # -- universe-scoped queries -------------------------------------------------

    def decisions_for_universe(self, universe_snapshot_id: str) -> tuple[PeerEligibilityDecisionRecord, ...]:
        records = self._records_skipping_malformed(_DECISION_TYPE, _decision_from_payload)
        return tuple(
            sorted(
                (record for record in records if record.universe_snapshot_id == universe_snapshot_id),
                key=lambda item: (
                    item.candidate_identity.entity_id,
                    item.candidate_identity.representation_id,
                    item.record_id,
                ),
            )
        )

    def observations_for_universe(self, universe_snapshot_id: str) -> tuple[ComparativeMetricObservationRecord, ...]:
        records = self._records_skipping_malformed(_OBSERVATION_TYPE, _observation_from_payload)
        return tuple(
            sorted(
                (record for record in records if record.universe_snapshot_id == universe_snapshot_id),
                key=lambda item: (
                    item.identity.entity_id,
                    item.identity.representation_id,
                    item.record_id,
                ),
            )
        )

    # -- strict-known replay reads -------------------------------------------------

    def strict_known_policy(
        self,
        *,
        effective_as_of: datetime,
        known_by: datetime,
        logical_id: str,
    ) -> PeerUniversePolicyRecord | None:
        records = [
            record
            for record in self._records_skipping_malformed(_POLICY_TYPE, _policy_from_payload)
            if record.logical_id == logical_id
        ]
        return _strict_known(records, effective_as_of=effective_as_of, known_by=known_by)

    def strict_known_universe(
        self,
        *,
        effective_as_of: datetime,
        known_by: datetime,
        logical_id: str,
    ) -> PeerUniverseSnapshot | None:
        records = [
            record
            for record in self._records_skipping_malformed(_UNIVERSE_TYPE, _universe_from_payload)
            if record.logical_id == logical_id
        ]
        return _strict_known(records, effective_as_of=effective_as_of, known_by=known_by)

    def strict_known_assessment(
        self,
        *,
        effective_as_of: datetime,
        known_by: datetime,
        logical_id: str,
    ) -> ComparativeValuationAssessmentRecord | None:
        records = [
            record
            for record in self._records_skipping_malformed(_ASSESSMENT_TYPE, _assessment_from_payload)
            if record.logical_id == logical_id
        ]
        return _strict_known(records, effective_as_of=effective_as_of, known_by=known_by)

    # -- unresolved-conflict queries -------------------------------------------------

    def unresolved_policy_conflicts(self) -> tuple[PeerUniversePolicyRecord, ...]:
        return self._unresolved_conflicts(_POLICY_TYPE, _policy_from_payload)

    def unresolved_universe_conflicts(self) -> tuple[PeerUniverseSnapshot, ...]:
        return self._unresolved_conflicts(_UNIVERSE_TYPE, _universe_from_payload)

    def unresolved_decision_conflicts(self) -> tuple[PeerEligibilityDecisionRecord, ...]:
        return self._unresolved_conflicts(_DECISION_TYPE, _decision_from_payload)

    def unresolved_observation_conflicts(self) -> tuple[ComparativeMetricObservationRecord, ...]:
        return self._unresolved_conflicts(_OBSERVATION_TYPE, _observation_from_payload)

    def unresolved_assessment_conflicts(self) -> tuple[ComparativeValuationAssessmentRecord, ...]:
        return self._unresolved_conflicts(_ASSESSMENT_TYPE, _assessment_from_payload)

    # -- internals ---------------------------------------------------------------

    def _unresolved_conflicts(self, snapshot_type: str, from_payload: Any) -> tuple[Any, ...]:
        records = self._records_skipping_malformed(snapshot_type, from_payload)
        superseded_ids = {item.supersedes_record_id for item in records if item.supersedes_record_id is not None}
        current = (item for item in records if item.record_id not in superseded_ids)
        unresolved = [item for item in current if item.conflict_state in {"open", "contested"}]
        return tuple(
            sorted(
                unresolved,
                key=lambda item: (item.logical_id, item.effective_at, item.record_id),
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
        # hunter.valuation_methodology and hunter.valuation): a malformed/legacy row is
        # excluded rather than aborting reads of every other valid record.
        records = []
        for snapshot in self._snapshots(snapshot_type):
            try:
                records.append(from_payload(snapshot.payload))
            except (ComparativeValuationIntegrityError, ValueError, KeyError, TypeError):
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


def _strict_known(records: list[Any], *, effective_as_of: datetime, known_by: datetime) -> Any | None:
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


# -- snapshot mapping -------------------------------------------------------------


def policy_snapshot(record: PeerUniversePolicyRecord) -> SnapshotRecord:
    return SnapshotRecord(
        id=record.record_id,
        created_at=record.recorded_at,
        effective_at=record.effective_at,
        snapshot_type=_POLICY_TYPE,
        target_id=record.logical_id,
        record_ids=(record.record_id,),
        payload=_payload(record),
        metadata={
            "authority_class": "production-authoritative",
            "domain": "comparative-valuation",
            "logical_id": record.logical_id,
            "known_at": record.known_at.isoformat(),
        },
    )


def universe_snapshot(record: PeerUniverseSnapshot) -> SnapshotRecord:
    return SnapshotRecord(
        id=record.record_id,
        created_at=record.recorded_at,
        effective_at=record.effective_at,
        snapshot_type=_UNIVERSE_TYPE,
        target_id=record.identity.representation_id,
        record_ids=(record.record_id,),
        payload=_payload(record),
        metadata={
            "authority_class": "production-authoritative",
            "domain": "comparative-valuation",
            "logical_id": record.logical_id,
            "known_at": record.known_at.isoformat(),
        },
    )


def decision_snapshot(record: PeerEligibilityDecisionRecord) -> SnapshotRecord:
    return SnapshotRecord(
        id=record.record_id,
        created_at=record.recorded_at,
        effective_at=record.effective_at,
        snapshot_type=_DECISION_TYPE,
        target_id=record.candidate_identity.representation_id,
        record_ids=(record.record_id,),
        payload=_payload(record),
        metadata={
            "authority_class": "production-authoritative",
            "domain": "comparative-valuation",
            "logical_id": record.logical_id,
            "known_at": record.known_at.isoformat(),
        },
    )


def observation_snapshot(record: ComparativeMetricObservationRecord) -> SnapshotRecord:
    return SnapshotRecord(
        id=record.record_id,
        created_at=record.recorded_at,
        effective_at=record.effective_at,
        snapshot_type=_OBSERVATION_TYPE,
        target_id=record.identity.representation_id,
        record_ids=(record.record_id,),
        payload=_payload(record),
        metadata={
            "authority_class": "production-authoritative",
            "domain": "comparative-valuation",
            "logical_id": record.logical_id,
            "known_at": record.known_at.isoformat(),
        },
    )


def assessment_snapshot(record: ComparativeValuationAssessmentRecord) -> SnapshotRecord:
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
            "domain": "comparative-valuation",
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


def _peer_candidate(payload: dict[str, Any]) -> PeerCandidate:
    result = dict(payload)
    result["identity"] = _identity(result["identity"])
    return PeerCandidate(**result)


def _base_payload(payload: dict[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    for name in ("effective_at", "recorded_at", "known_at"):
        result[name] = datetime.fromisoformat(str(result[name])).astimezone(UTC)
    return result


def _policy_from_payload(payload: dict[str, Any]) -> PeerUniversePolicyRecord:
    _require_fields(payload, ("policy_id", "supported_entity_class", "taxonomy", "required_quote_currency"))
    result = _base_payload(payload)
    result["required_value_capture_mechanism_types"] = tuple(result["required_value_capture_mechanism_types"])
    result["required_native_evidence_types"] = tuple(result["required_native_evidence_types"])
    return PeerUniversePolicyRecord(**result)


def _universe_from_payload(payload: dict[str, Any]) -> PeerUniverseSnapshot:
    _require_fields(payload, ("policy_record_id", "deterministic_construction_fingerprint"))
    result = _base_payload(payload)
    result["identity"] = _identity(result["identity"])
    result["candidates"] = tuple(_peer_candidate(item) for item in result["candidates"])
    result["cutoff"] = datetime.fromisoformat(str(result["cutoff"])).astimezone(UTC)
    return PeerUniverseSnapshot(**result)


def _decision_from_payload(payload: dict[str, Any]) -> PeerEligibilityDecisionRecord:
    _require_fields(payload, ("decision", "universe_snapshot_id", "reason"))
    result = _base_payload(payload)
    result["target_identity"] = _identity(result["target_identity"])
    result["candidate_identity"] = _identity(result["candidate_identity"])
    result["dimension_decisions"] = tuple(_dimension_decision(item) for item in result["dimension_decisions"])
    return PeerEligibilityDecisionRecord(**result)


def _dimension_decision(payload: dict[str, Any]) -> Any:
    from hunter.comparative_valuation.models import DimensionDecision

    return DimensionDecision(**payload)


def _observation_from_payload(payload: dict[str, Any]) -> ComparativeMetricObservationRecord:
    _require_fields(payload, ("comparative_multiple", "universe_snapshot_id", "calculation_fingerprint"))
    result = _base_payload(payload)
    result["identity"] = _identity(result["identity"])
    return ComparativeMetricObservationRecord(**result)


def _assessment_from_payload(payload: dict[str, Any]) -> ComparativeValuationAssessmentRecord:
    _require_fields(payload, ("availability_state", "universe_snapshot_id", "correlation_group"))
    result = _base_payload(payload)
    result["identity"] = _identity(result["identity"])
    result["included_decision_record_ids"] = tuple(result["included_decision_record_ids"])
    result["peer_observation_record_ids"] = tuple(result["peer_observation_record_ids"])
    result["peer_multiple_distribution"] = tuple(result["peer_multiple_distribution"])
    return ComparativeValuationAssessmentRecord(**result)


def _require_fields(payload: dict[str, Any], names: tuple[str, ...]) -> None:
    missing = tuple(name for name in names if name not in payload)
    if missing:
        raise ComparativeValuationIntegrityError(
            "legacy comparative valuation snapshot is not authoritative under the current contract: "
            + ",".join(missing)
        )


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(UTC)
