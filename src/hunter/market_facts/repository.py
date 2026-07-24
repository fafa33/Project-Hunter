from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hunter.market_facts.models import (
    MarketFactAvailabilityEvent,
    MarketFactConflictResolution,
    MarketFactIdentity,
    ObservedMarketFactRecord,
)
from hunter.persistence.models import QuerySpec
from hunter.persistence.records import SnapshotRecord
from hunter.persistence.sql import (
    RepositoryFactory,
    SessionFactory,
    create_schema,
    create_sqlite_engine,
)
from hunter.persistence.sql.exceptions import PersistenceIdentityConflictError

DEFAULT_MARKET_FACTS_DB = Path("data/data_ops.sqlite")
MARKET_FACTS_MIGRATION_ID = "generic-sql-observed-market-facts-v1"
_FACT_SNAPSHOT_TYPE = "observed-market-fact"
_AVAILABILITY_SNAPSHOT_TYPE = "observed-market-fact-availability"
_CONFLICT_RESOLUTION_SNAPSHOT_TYPE = "observed-market-fact-conflict-resolution"


class MarketFactIntegrityError(ValueError):
    """Raised when immutable market-fact identity is reused with divergent content."""


class RepositoryAuthorizationError(PermissionError):
    """Raised when a caller attempts an unauthorized authoritative mutation."""


class _RepositoryAuthority:
    pass


class MarketFactWritePlan:
    __slots__ = ("records", "availability_events", "conflict_resolutions", "_authority")

    def __init__(
        self,
        *,
        records: tuple[ObservedMarketFactRecord, ...] = (),
        availability_events: tuple[MarketFactAvailabilityEvent, ...] = (),
        conflict_resolutions: tuple[MarketFactConflictResolution, ...] = (),
        authority: object,
    ) -> None:
        self.records = records
        self.availability_events = availability_events
        self.conflict_resolutions = conflict_resolutions
        self._authority = authority


class ObservedMarketFactRepository:
    """Domain repository backed exclusively by Hunter's canonical generic SQL store."""

    def __init__(self, path: str | Path = DEFAULT_MARKET_FACTS_DB) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._authority = _RepositoryAuthority()
        engine = create_sqlite_engine(self.path)
        try:
            create_schema(engine)
        finally:
            engine.dispose()

    def apply(self, plan: MarketFactWritePlan) -> None:
        if plan._authority is not self._authority:
            raise RepositoryAuthorizationError("market fact write plan was not authorized for this repository")
        engine = create_sqlite_engine(self.path)
        session = SessionFactory(engine).create()
        try:
            snapshots = RepositoryFactory(session).snapshots()
            for record in plan.records:
                self._validate_successor(record)
                snapshots.save(_record_snapshot(record))
            for event in plan.availability_events:
                snapshots.save(_availability_snapshot(event))
            for resolution in plan.conflict_resolutions:
                self._validate_resolution(resolution)
                snapshots.save(_conflict_resolution_snapshot(resolution))
            session.commit()
        except PersistenceIdentityConflictError as exc:
            session.rollback()
            raise MarketFactIntegrityError(str(exc)) from exc
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
            engine.dispose()

    def record(self, record_id: str) -> ObservedMarketFactRecord | None:
        snapshot = self._load_snapshot(record_id, _FACT_SNAPSHOT_TYPE)
        return _record_from_snapshot(snapshot) if snapshot is not None else None

    def lineage(self, logical_id: str) -> tuple[ObservedMarketFactRecord, ...]:
        records = tuple(
            _record_from_snapshot(item)
            for item in self._snapshots(_FACT_SNAPSHOT_TYPE)
            if item.payload.get("logical_id") == logical_id
        )
        return tuple(
            sorted(
                records,
                key=lambda item: (
                    item.effective_at,
                    item.recorded_at,
                    item.known_at,
                    item.record_id,
                ),
            )
        )

    def facts(
        self,
        *,
        entity_id: str,
        asset_id: str,
        representation_id: str,
        fact_type: str,
        effective_at: datetime | None = None,
    ) -> tuple[ObservedMarketFactRecord, ...]:
        records = (
            _record_from_snapshot(item)
            for item in self._snapshots(_FACT_SNAPSHOT_TYPE)
            if item.payload.get("identity", {}).get("entity_id") == entity_id
            and item.payload.get("identity", {}).get("asset_id") == asset_id
            and item.payload.get("identity", {}).get("representation_id") == representation_id
            and item.payload.get("fact_type") == fact_type
        )
        filtered = tuple(
            item
            for item in records
            if effective_at is None or item.effective_at == _aware("effective_at", effective_at)
        )
        return tuple(
            sorted(
                filtered,
                key=lambda item: (
                    item.effective_at,
                    item.recorded_at,
                    item.known_at,
                    item.record_id,
                ),
            )
        )

    def unresolved_conflicts(self) -> tuple[ObservedMarketFactRecord, ...]:
        records = tuple(_record_from_snapshot(item) for item in self._snapshots(_FACT_SNAPSHOT_TYPE))
        superseded_ids = {item.supersedes_record_id for item in records if item.supersedes_record_id is not None}
        groups: dict[tuple[str, datetime], list[ObservedMarketFactRecord]] = {}
        for item in records:
            if item.quality_state == "accepted" and item.record_id not in superseded_ids:
                groups.setdefault((item.logical_id, item.effective_at), []).append(item)
        unresolved: list[ObservedMarketFactRecord] = []
        for (logical_id, candidate_effective_at), group in groups.items():
            open_records = [item for item in group if item.conflict_state in {"open", "contested"}]
            if not open_records:
                continue
            candidate_ids = tuple(sorted(item.record_id for item in group))
            resolved = any(
                item.candidate_effective_at == candidate_effective_at and item.candidate_record_ids == candidate_ids
                for item in self.conflict_resolutions(logical_id)
            )
            if not resolved:
                unresolved.extend(open_records)
        return tuple(
            sorted(
                unresolved,
                key=lambda item: (
                    item.logical_id,
                    item.effective_at,
                    item.record_id,
                ),
            )
        )

    def conflict_resolutions(self, logical_id: str) -> tuple[MarketFactConflictResolution, ...]:
        resolutions = tuple(
            _conflict_resolution_from_snapshot(item)
            for item in self._snapshots(_CONFLICT_RESOLUTION_SNAPSHOT_TYPE)
            if item.payload.get("logical_id") == logical_id
        )
        return tuple(
            sorted(
                resolutions,
                key=lambda item: (
                    item.effective_at,
                    item.recorded_at,
                    item.known_at,
                    item.resolution_id,
                ),
            )
        )

    def strict_known_fact(
        self,
        *,
        entity_id: str,
        representation_id: str,
        fact_type: str,
        effective_as_of: datetime,
        known_by: datetime,
        quote_currency: str | None = None,
    ) -> ObservedMarketFactRecord | None:
        effective_as_of = _aware("effective_as_of", effective_as_of)
        known_by = _aware("known_by", known_by)
        records = (_record_from_snapshot(item) for item in self._snapshots(_FACT_SNAPSHOT_TYPE))
        temporal_eligible = [
            item
            for item in records
            if item.identity.entity_id == entity_id
            and item.identity.representation_id == representation_id
            and item.fact_type == fact_type
            and item.effective_at <= effective_as_of
            and item.recorded_at <= known_by
            and item.known_at <= known_by
            and item.quality_state == "accepted"
            and item.quote_currency == (quote_currency.lower() if quote_currency is not None else None)
        ]
        superseded_ids = {
            item.supersedes_record_id for item in temporal_eligible if item.supersedes_record_id is not None
        }
        unsuperseded = [item for item in temporal_eligible if item.record_id not in superseded_ids]
        groups: dict[tuple[str, datetime], list[ObservedMarketFactRecord]] = {}
        for item in unsuperseded:
            groups.setdefault((item.logical_id, item.effective_at), []).append(item)
        candidates: list[ObservedMarketFactRecord] = []
        for (logical_id, candidate_effective_at), group in groups.items():
            values = {(item.value, item.unit, item.quote_currency, item.venue_scope) for item in group}
            conflicted = len(values) > 1 or any(
                item.conflict_state in {"open", "contested", "resolved"} for item in group
            )
            if not conflicted:
                candidates.extend(group)
                continue
            resolution = self._eligible_resolution(
                logical_id=logical_id,
                candidate_effective_at=candidate_effective_at,
                candidate_record_ids=tuple(item.record_id for item in group),
                effective_as_of=effective_as_of,
                known_by=known_by,
            )
            if resolution is None:
                return None
            selected = next(
                (item for item in group if item.record_id == resolution.selected_record_id),
                None,
            )
            if selected is None:
                return None
            candidates.append(selected)
        candidates.sort(
            key=lambda item: (
                item.effective_at,
                item.recorded_at,
                item.known_at,
                item.record_id,
            ),
            reverse=True,
        )
        return candidates[0] if candidates else None

    def availability_events(self, *, source_id: str | None = None) -> tuple[dict[str, Any], ...]:
        payloads = [dict(item.payload) for item in self._snapshots(_AVAILABILITY_SNAPSHOT_TYPE)]
        if source_id is not None:
            payloads = [item for item in payloads if item.get("source_id") == source_id]
        payloads.sort(key=lambda item: (str(item["requested_at"]), str(item["event_id"])))
        return tuple(payloads)

    def migration_ids(self) -> tuple[str, ...]:
        return (MARKET_FACTS_MIGRATION_ID,)

    def count(self, table: str) -> int:
        mapping = {
            "market_fact_schema_migrations": 1,
            "observed_market_facts": len(self._snapshots(_FACT_SNAPSHOT_TYPE)),
            "market_fact_availability_events": len(self._snapshots(_AVAILABILITY_SNAPSHOT_TYPE)),
            "market_fact_conflict_resolutions": len(self._snapshots(_CONFLICT_RESOLUTION_SNAPSHOT_TYPE)),
        }
        if table not in mapping:
            raise ValueError("unsupported market fact table")
        return mapping[table]

    def _validate_successor(self, record: ObservedMarketFactRecord) -> None:
        if record.supersedes_record_id is None:
            return
        predecessor = self.record(record.supersedes_record_id)
        if predecessor is None:
            raise MarketFactIntegrityError("superseded market fact record does not exist")
        if predecessor.logical_id != record.logical_id:
            raise MarketFactIntegrityError("correction must preserve logical_id")

    def _validate_resolution(self, resolution: MarketFactConflictResolution) -> None:
        candidates = tuple(self.record(record_id) for record_id in resolution.candidate_record_ids)
        if any(item is None for item in candidates):
            raise MarketFactIntegrityError("conflict resolution candidate does not exist")
        records = tuple(item for item in candidates if item is not None)
        if any(item.logical_id != resolution.logical_id for item in records):
            raise MarketFactIntegrityError("conflict resolution candidates must share logical_id")
        if any(item.effective_at != resolution.candidate_effective_at for item in records):
            raise MarketFactIntegrityError("conflict resolution candidates must share effective_at")
        values = {(item.value, item.unit, item.quote_currency, item.venue_scope) for item in records}
        if len(values) < 2:
            raise MarketFactIntegrityError("conflict resolution requires divergent candidate values")

    def _eligible_resolution(
        self,
        *,
        logical_id: str,
        candidate_effective_at: datetime,
        candidate_record_ids: tuple[str, ...],
        effective_as_of: datetime,
        known_by: datetime,
    ) -> MarketFactConflictResolution | None:
        expected_ids = tuple(sorted(candidate_record_ids))
        eligible = [
            item
            for item in self.conflict_resolutions(logical_id)
            if item.candidate_effective_at == candidate_effective_at
            and item.candidate_record_ids == expected_ids
            and item.effective_at <= effective_as_of
            and item.recorded_at <= known_by
            and item.known_at <= known_by
        ]
        eligible.sort(
            key=lambda item: (
                item.effective_at,
                item.recorded_at,
                item.known_at,
                item.resolution_id,
            ),
            reverse=True,
        )
        return eligible[0] if eligible else None

    def _load_snapshot(self, identity: str, snapshot_type: str) -> SnapshotRecord | None:
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


def _record_snapshot(record: ObservedMarketFactRecord) -> SnapshotRecord:
    return SnapshotRecord(
        id=record.record_id,
        created_at=record.recorded_at,
        effective_at=record.effective_at,
        snapshot_type=_FACT_SNAPSHOT_TYPE,
        target_id=record.identity.representation_id,
        record_ids=(record.record_id,),
        payload=_record_payload(record),
        metadata={
            "authority_class": "production-authoritative",
            "domain": "observed-market-facts",
            "logical_id": record.logical_id,
            "known_at": _serialize(record.known_at),
        },
    )


def _availability_snapshot(event: MarketFactAvailabilityEvent) -> SnapshotRecord:
    return SnapshotRecord(
        id=event.event_id,
        created_at=event.recorded_at,
        effective_at=event.requested_at,
        snapshot_type=_AVAILABILITY_SNAPSHOT_TYPE,
        target_id=event.representation_id,
        record_ids=(event.event_id,),
        payload=_availability_payload(event),
        metadata={
            "authority_class": "operational-availability-evidence",
            "domain": "observed-market-facts",
            "known_at": _serialize(event.known_at),
        },
    )


def _conflict_resolution_snapshot(resolution: MarketFactConflictResolution) -> SnapshotRecord:
    return SnapshotRecord(
        id=resolution.resolution_id,
        created_at=resolution.recorded_at,
        effective_at=resolution.effective_at,
        snapshot_type=_CONFLICT_RESOLUTION_SNAPSHOT_TYPE,
        target_id=resolution.logical_id,
        record_ids=resolution.candidate_record_ids,
        payload=_conflict_resolution_payload(resolution),
        metadata={
            "authority_class": "production-authoritative",
            "domain": "observed-market-fact-conflict-resolution",
            "known_at": _serialize(resolution.known_at),
            "policy_fingerprint": resolution.policy_fingerprint,
        },
    )


def _record_payload(record: ObservedMarketFactRecord) -> dict[str, Any]:
    return {
        "record_id": record.record_id,
        "logical_id": record.logical_id,
        "schema_version": record.schema_version,
        "semantic_version": record.semantic_version,
        "identity": {
            "entity_id": record.identity.entity_id,
            "asset_id": record.identity.asset_id,
            "representation_id": record.identity.representation_id,
            "chain": record.identity.chain,
            "contract_address": record.identity.contract_address,
            "provider_listing_id": record.identity.provider_listing_id,
        },
        "source_id": record.source_id,
        "provider_id": record.provider_id,
        "endpoint": record.endpoint,
        "parser_version": record.parser_version,
        "fact_type": record.fact_type,
        "value": record.value,
        "unit": record.unit,
        "quote_currency": record.quote_currency,
        "venue_scope": record.venue_scope,
        "effective_at": _serialize(record.effective_at),
        "observed_at": _serialize(record.observed_at),
        "recorded_at": _serialize(record.recorded_at),
        "known_at": _serialize(record.known_at),
        "raw_payload_hash": record.raw_payload_hash,
        "provider_source_record_id": record.provider_source_record_id,
        "provider_source_record_version": record.provider_source_record_version,
        "confidence": record.confidence,
        "quality_state": record.quality_state,
        "conflict_state": record.conflict_state,
        "content_hash": record.content_hash,
        "supersedes_record_id": record.supersedes_record_id,
        "correction_reason": record.correction_reason,
    }


def _availability_payload(event: MarketFactAvailabilityEvent) -> dict[str, Any]:
    return {
        "event_id": event.event_id,
        "schema_version": event.schema_version,
        "source_id": event.source_id,
        "provider_id": event.provider_id,
        "entity_id": event.entity_id,
        "representation_id": event.representation_id,
        "status": event.status,
        "requested_at": _serialize(event.requested_at),
        "recorded_at": _serialize(event.recorded_at),
        "known_at": _serialize(event.known_at),
        "endpoint": event.endpoint,
        "parser_version": event.parser_version,
        "raw_payload_hash": event.raw_payload_hash,
        "failure_reason": event.failure_reason,
    }


def _conflict_resolution_payload(resolution: MarketFactConflictResolution) -> dict[str, Any]:
    return {
        "resolution_id": resolution.resolution_id,
        "logical_id": resolution.logical_id,
        "candidate_record_ids": resolution.candidate_record_ids,
        "selected_record_id": resolution.selected_record_id,
        "policy_id": resolution.policy_id,
        "policy_version": resolution.policy_version,
        "policy_fingerprint": resolution.policy_fingerprint,
        "rationale": resolution.rationale,
        "candidate_effective_at": _serialize(resolution.candidate_effective_at),
        "effective_at": _serialize(resolution.effective_at),
        "recorded_at": _serialize(resolution.recorded_at),
        "known_at": _serialize(resolution.known_at),
        "schema_version": resolution.schema_version,
    }


def _record_from_snapshot(snapshot: SnapshotRecord) -> ObservedMarketFactRecord:
    payload = snapshot.payload
    identity = payload["identity"]
    has_canonical_provenance = all(
        payload.get(name)
        for name in (
            "provider_source_record_id",
            "provider_source_record_version",
            "confidence",
        )
    )
    return ObservedMarketFactRecord(
        record_id=str(payload["record_id"]),
        logical_id=str(payload["logical_id"]),
        schema_version=str(payload["schema_version"]),
        semantic_version=str(payload["semantic_version"]),
        identity=MarketFactIdentity(
            entity_id=str(identity["entity_id"]),
            asset_id=str(identity["asset_id"]),
            representation_id=str(identity["representation_id"]),
            chain=str(identity["chain"]),
            contract_address=str(identity["contract_address"]),
            provider_listing_id=str(identity["provider_listing_id"]),
        ),
        source_id=str(payload["source_id"]),
        provider_id=str(payload["provider_id"]),
        endpoint=str(payload["endpoint"]),
        parser_version=str(payload["parser_version"]),
        fact_type=str(payload["fact_type"]),  # type: ignore[arg-type]
        value=str(payload["value"]),
        unit=str(payload["unit"]),
        quote_currency=(str(payload["quote_currency"]) if payload["quote_currency"] is not None else None),
        venue_scope=str(payload["venue_scope"]),
        effective_at=_deserialize(str(payload["effective_at"])),
        observed_at=_deserialize(str(payload["observed_at"])),
        recorded_at=_deserialize(str(payload["recorded_at"])),
        known_at=_deserialize(str(payload["known_at"])),
        raw_payload_hash=str(payload["raw_payload_hash"]),
        provider_source_record_id=str(payload.get("provider_source_record_id", identity["provider_listing_id"])),
        provider_source_record_version=str(payload.get("provider_source_record_version", "legacy-unversioned")),
        confidence=str(payload.get("confidence", "0")),
        quality_state=str(payload["quality_state"] if has_canonical_provenance else "unavailable"),  # type: ignore[arg-type]
        conflict_state=str(payload["conflict_state"]),  # type: ignore[arg-type]
        content_hash=str(payload["content_hash"]),
        supersedes_record_id=(
            str(payload["supersedes_record_id"]) if payload["supersedes_record_id"] is not None else None
        ),
        correction_reason=str(payload["correction_reason"]),
    )


def _conflict_resolution_from_snapshot(snapshot: SnapshotRecord) -> MarketFactConflictResolution:
    payload = snapshot.payload
    return MarketFactConflictResolution(
        resolution_id=str(payload["resolution_id"]),
        logical_id=str(payload["logical_id"]),
        candidate_record_ids=tuple(str(item) for item in payload["candidate_record_ids"]),
        selected_record_id=str(payload["selected_record_id"]),
        policy_id=str(payload["policy_id"]),
        policy_version=str(payload["policy_version"]),
        policy_fingerprint=str(payload["policy_fingerprint"]),
        rationale=str(payload["rationale"]),
        candidate_effective_at=_deserialize(str(payload["candidate_effective_at"])),
        effective_at=_deserialize(str(payload["effective_at"])),
        recorded_at=_deserialize(str(payload["recorded_at"])),
        known_at=_deserialize(str(payload["known_at"])),
        schema_version=str(payload["schema_version"]),
    )


def _serialize(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _deserialize(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(UTC)


def _aware(name: str, value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)
