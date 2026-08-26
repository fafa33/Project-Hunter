"""ADR 0035 Phase D governed correction allocation and strict-known replay.

This module owns only non-content correction chronology.  It never receives or
persists response bodies.  ``ResponseValidator`` is the trusted allocation owner;
SQLite is mechanical storage for already-decided allocation and correction data.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import sqlite3
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hunter.evidence_intelligence.response_validator import (
    ResponseValidator,
    ValidationState,
    canonical_validation_state,
)
from hunter.evidence_intelligence.response_validator_terminal_persistence import (
    ResponseValidationRecord,
)
from hunter.execution import Clock, SystemClock

CORRECTION_ALLOCATION_SCHEMA_VERSION = "response-validation-correction-allocation-v1"
CORRECTION_RECORD_SCHEMA_VERSION = "response-validation-correction-record-v1"


class ResponseValidationCorrectionError(RuntimeError):
    """Base class for governed correction failures."""


class ResponseValidationCorrectionConflict(ResponseValidationCorrectionError):
    """Raised when retry/lineage/chronology conflicts with immutable state."""


class ResponseValidationCorrectionCorruption(ResponseValidationCorrectionError):
    """Raised when durable correction bytes or indexed coordinates are corrupt."""


@dataclass(frozen=True)
class ResponseValidationCorrectionRequest:
    """Non-content semantic correction request; identity excludes trusted time."""

    validation_event_id: str
    predecessor_record_id: str
    state: ValidationState
    findings: tuple[tuple[str, str, str], ...] = ()
    refusal_reason_code: str | None = None
    executed: bool | None = None
    reason_code: str = "GOVERNED_CORRECTION"
    schema_version: str = "response-validation-correction-request-v1"

    def __post_init__(self) -> None:
        if not self.validation_event_id.strip() or not self.predecessor_record_id.strip():
            raise ResponseValidationCorrectionConflict("correction request requires canonical predecessor identity")
        if not self.reason_code.strip():
            raise ResponseValidationCorrectionConflict("correction request requires a non-blank reason code")
        object.__setattr__(self, "state", canonical_validation_state(self.state))
        object.__setattr__(self, "findings", tuple(sorted(tuple(item) for item in self.findings)))

    @property
    def request_id(self) -> str:
        return _identity("response-validation-correction-request", _jsonable(asdict(self)))


@dataclass(frozen=True)
class ResponseValidationCorrectionAllocation:
    """Trusted exact-next-generation correction allocation."""

    validation_event_id: str
    generation: int
    predecessor_record_id: str
    predecessor_accepted_at: datetime
    correction_cutoff: datetime
    correction_decision_id: str
    request_id: str
    schema_version: str = CORRECTION_ALLOCATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.generation < 1:
            raise ResponseValidationCorrectionConflict("correction generation starts at 1")
        lower = _aware_utc("predecessor_accepted_at", self.predecessor_accepted_at)
        cutoff = _aware_utc("correction_cutoff", self.correction_cutoff)
        if lower > cutoff:
            raise ResponseValidationCorrectionConflict("correction cutoff precedes predecessor durable acceptance")
        object.__setattr__(self, "predecessor_accepted_at", lower)
        object.__setattr__(self, "correction_cutoff", cutoff)


@dataclass(frozen=True)
class ResponseValidationCorrectionRecord:
    """Immutable accepted correction containing canonical non-content semantics."""

    validation_event_id: str
    generation: int
    predecessor_record_id: str
    correction_decision_id: str
    correction_cutoff: datetime
    correction_recorded_at: datetime
    request_id: str
    state: ValidationState
    findings: tuple[tuple[str, str, str], ...]
    refusal_reason_code: str | None
    executed: bool | None
    reason_code: str
    schema_version: str = CORRECTION_RECORD_SCHEMA_VERSION

    def __post_init__(self) -> None:
        cutoff = _aware_utc("correction_cutoff", self.correction_cutoff)
        recorded = _aware_utc("correction_recorded_at", self.correction_recorded_at)
        if recorded < cutoff:
            raise ResponseValidationCorrectionConflict("correction_recorded_at precedes correction_cutoff")
        if self.generation < 1:
            raise ResponseValidationCorrectionConflict("correction generation starts at 1")
        object.__setattr__(self, "correction_cutoff", cutoff)
        object.__setattr__(self, "correction_recorded_at", recorded)
        object.__setattr__(self, "state", canonical_validation_state(self.state))
        object.__setattr__(self, "findings", tuple(sorted(tuple(item) for item in self.findings)))

    @property
    def record_id(self) -> str:
        return _identity("response-validation-correction-record", _jsonable(asdict(self)))


class ResponseValidationCorrectionService:
    """ResponseValidator-owned allocator, persistence, and strict-known replay."""

    def __init__(
        self,
        owner: ResponseValidator,
        *,
        db_path: str | Path,
        base_record_loader: Callable[[str], ResponseValidationRecord | None],
        clock: Clock | None = None,
    ) -> None:
        if type(owner) is not ResponseValidator:
            raise TypeError("Phase D correction allocation requires the canonical ResponseValidator owner")
        self._owner = owner
        self._path = str(db_path)
        self._base_record_loader = base_record_loader
        self._clock = clock or SystemClock()
        self._initialize()

    def allocate(self, request: ResponseValidationCorrectionRequest) -> ResponseValidationCorrectionAllocation:
        if not isinstance(request, ResponseValidationCorrectionRequest):
            raise ResponseValidationCorrectionConflict("canonical correction request is required")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = self._allocation_for_request(connection, request.request_id)
            if existing is not None:
                if existing.predecessor_record_id != request.predecessor_record_id:
                    raise ResponseValidationCorrectionConflict("retry changed immutable predecessor")
                return existing

            head_id, generation, lower_bound = self._current_head(connection, request.validation_event_id)
            if head_id != request.predecessor_record_id:
                raise ResponseValidationCorrectionConflict("correction request does not name exact current predecessor")

            cutoff = _aware_utc("correction_cutoff", self._clock.now())
            if cutoff < lower_bound:
                raise ResponseValidationCorrectionConflict(
                    "correction chronology is inverted; generation remains unclaimed"
                )
            next_generation = generation + 1
            decision_id = _identity(
                "response-validation-correction-decision",
                {
                    "validation_event_id": request.validation_event_id,
                    "generation": next_generation,
                    "predecessor_record_id": head_id,
                    "predecessor_accepted_at": lower_bound.isoformat(),
                    "correction_cutoff": cutoff.isoformat(),
                    "request_id": request.request_id,
                },
            )
            allocation = ResponseValidationCorrectionAllocation(
                validation_event_id=request.validation_event_id,
                generation=next_generation,
                predecessor_record_id=head_id,
                predecessor_accepted_at=lower_bound,
                correction_cutoff=cutoff,
                correction_decision_id=decision_id,
                request_id=request.request_id,
            )
            payload = _canonical_json(_jsonable(asdict(allocation)))
            try:
                connection.execute(
                    """
                    INSERT INTO response_validation_correction_allocations (
                        validation_event_id, generation, predecessor_record_id,
                        correction_decision_id, request_id, payload_hash, payload_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        allocation.validation_event_id,
                        allocation.generation,
                        allocation.predecessor_record_id,
                        allocation.correction_decision_id,
                        allocation.request_id,
                        _sha256(payload),
                        payload,
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise ResponseValidationCorrectionConflict(
                    "exact next correction generation is already claimed"
                ) from error
            return allocation

    def persist(
        self,
        allocation: ResponseValidationCorrectionAllocation,
        request: ResponseValidationCorrectionRequest,
    ) -> ResponseValidationCorrectionRecord:
        if allocation.request_id != request.request_id:
            raise ResponseValidationCorrectionConflict("correction allocation/request identity mismatch")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            canonical = self._allocation_by_decision(connection, allocation.correction_decision_id)
            if canonical != allocation:
                raise ResponseValidationCorrectionConflict("correction allocation was substituted or tampered")
            existing = self._record_by_decision(connection, allocation.correction_decision_id)
            if existing is not None:
                if _record_retry_tuple(existing) != _request_retry_tuple(request):
                    raise ResponseValidationCorrectionConflict("retry conflicts with immutable corrected semantics")
                return existing
            head_id, generation, _ = self._current_head(connection, allocation.validation_event_id)
            if head_id != allocation.predecessor_record_id or generation + 1 != allocation.generation:
                raise ResponseValidationCorrectionConflict("correction allocation no longer extends exact current head")
            recorded_at = _aware_utc("correction_recorded_at", self._clock.now())
            if recorded_at < allocation.correction_cutoff:
                raise ResponseValidationCorrectionConflict("correction acceptance precedes trusted correction cutoff")
            record = ResponseValidationCorrectionRecord(
                validation_event_id=allocation.validation_event_id,
                generation=allocation.generation,
                predecessor_record_id=allocation.predecessor_record_id,
                correction_decision_id=allocation.correction_decision_id,
                correction_cutoff=allocation.correction_cutoff,
                correction_recorded_at=recorded_at,
                request_id=request.request_id,
                state=request.state,
                findings=request.findings,
                refusal_reason_code=request.refusal_reason_code,
                executed=request.executed,
                reason_code=request.reason_code,
            )
            payload = _canonical_json(_jsonable(asdict(record)))
            try:
                connection.execute(
                    """
                    INSERT INTO response_validation_correction_records (
                        validation_event_id, generation, predecessor_record_id,
                        correction_decision_id, correction_recorded_at,
                        record_id, payload_hash, payload_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.validation_event_id,
                        record.generation,
                        record.predecessor_record_id,
                        record.correction_decision_id,
                        record.correction_recorded_at.isoformat(),
                        record.record_id,
                        _sha256(payload),
                        payload,
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise ResponseValidationCorrectionConflict("immutable correction record identity conflict") from error
            return record

    def replay(
        self,
        validation_event_id: str,
        *,
        strict_known_at: datetime,
    ) -> ResponseValidationRecord | ResponseValidationCorrectionRecord | None:
        cutoff = _aware_utc("strict_known_at", strict_known_at)
        base = self._base_record_loader(validation_event_id)
        if base is None or base.validation_recorded_at > cutoff:
            return None
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT correction_decision_id, payload_hash, payload_json
                FROM response_validation_correction_records
                WHERE validation_event_id = ? AND correction_recorded_at <= ?
                ORDER BY generation ASC
                """,
                (validation_event_id, cutoff.isoformat()),
            ).fetchall()
        current: ResponseValidationRecord | ResponseValidationCorrectionRecord = base
        expected_generation = 1
        for row in rows:
            record = _correction_record_from_row(row)
            if record.generation != expected_generation or record.predecessor_record_id != current.record_id:
                raise ResponseValidationCorrectionCorruption("correction replay lineage is non-contiguous")
            current = record
            expected_generation += 1
        return current

    def _current_head(self, connection: sqlite3.Connection, validation_event_id: str) -> tuple[str, int, datetime]:
        row = connection.execute(
            """
            SELECT correction_decision_id, payload_hash, payload_json
            FROM response_validation_correction_records
            WHERE validation_event_id = ?
            ORDER BY generation DESC
            LIMIT 1
            """,
            (validation_event_id,),
        ).fetchone()
        if row is not None:
            record = _correction_record_from_row(row)
            return record.record_id, record.generation, record.correction_recorded_at
        base = self._base_record_loader(validation_event_id)
        if base is None:
            raise ResponseValidationCorrectionConflict("correction predecessor base record is unknown")
        return base.record_id, 0, base.validation_recorded_at

    def _allocation_for_request(
        self, connection: sqlite3.Connection, request_id: str
    ) -> ResponseValidationCorrectionAllocation | None:
        row = connection.execute(
            "SELECT payload_hash, payload_json FROM response_validation_correction_allocations WHERE request_id = ?",
            (request_id,),
        ).fetchone()
        return _allocation_from_row(row) if row is not None else None

    def _allocation_by_decision(
        self, connection: sqlite3.Connection, decision_id: str
    ) -> ResponseValidationCorrectionAllocation | None:
        row = connection.execute(
            "SELECT payload_hash, payload_json FROM response_validation_correction_allocations WHERE correction_decision_id = ?",
            (decision_id,),
        ).fetchone()
        return _allocation_from_row(row) if row is not None else None

    def _record_by_decision(
        self, connection: sqlite3.Connection, decision_id: str
    ) -> ResponseValidationCorrectionRecord | None:
        row = connection.execute(
            "SELECT correction_decision_id, payload_hash, payload_json FROM response_validation_correction_records WHERE correction_decision_id = ?",
            (decision_id,),
        ).fetchone()
        return _correction_record_from_row(row) if row is not None else None

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS response_validation_correction_allocations (
                    validation_event_id TEXT NOT NULL,
                    generation INTEGER NOT NULL,
                    predecessor_record_id TEXT NOT NULL,
                    correction_decision_id TEXT PRIMARY KEY,
                    request_id TEXT NOT NULL UNIQUE,
                    payload_hash TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    UNIQUE(validation_event_id, generation),
                    UNIQUE(predecessor_record_id)
                );
                CREATE TABLE IF NOT EXISTS response_validation_correction_records (
                    validation_event_id TEXT NOT NULL,
                    generation INTEGER NOT NULL,
                    predecessor_record_id TEXT NOT NULL,
                    correction_decision_id TEXT PRIMARY KEY,
                    correction_recorded_at TEXT NOT NULL,
                    record_id TEXT NOT NULL UNIQUE,
                    payload_hash TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    UNIQUE(validation_event_id, generation),
                    UNIQUE(predecessor_record_id)
                );
                CREATE INDEX IF NOT EXISTS response_validation_correction_strict_known_idx
                    ON response_validation_correction_records(validation_event_id, correction_recorded_at, generation);
                """
            )

    @contextlib.contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self._path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()


def _allocation_from_row(row: sqlite3.Row) -> ResponseValidationCorrectionAllocation:
    payload = str(row["payload_json"])
    if _sha256(payload) != str(row["payload_hash"]):
        raise ResponseValidationCorrectionCorruption("correction allocation payload hash mismatch")
    data = json.loads(payload)
    allocation = ResponseValidationCorrectionAllocation(
        validation_event_id=data["validation_event_id"],
        generation=int(data["generation"]),
        predecessor_record_id=data["predecessor_record_id"],
        predecessor_accepted_at=datetime.fromisoformat(data["predecessor_accepted_at"]),
        correction_cutoff=datetime.fromisoformat(data["correction_cutoff"]),
        correction_decision_id=data["correction_decision_id"],
        request_id=data["request_id"],
        schema_version=data["schema_version"],
    )
    return allocation


def _correction_record_from_row(row: sqlite3.Row) -> ResponseValidationCorrectionRecord:
    payload = str(row["payload_json"])
    if _sha256(payload) != str(row["payload_hash"]):
        raise ResponseValidationCorrectionCorruption("correction record payload hash mismatch")
    data = json.loads(payload)
    record = ResponseValidationCorrectionRecord(
        validation_event_id=data["validation_event_id"],
        generation=int(data["generation"]),
        predecessor_record_id=data["predecessor_record_id"],
        correction_decision_id=data["correction_decision_id"],
        correction_cutoff=datetime.fromisoformat(data["correction_cutoff"]),
        correction_recorded_at=datetime.fromisoformat(data["correction_recorded_at"]),
        request_id=data["request_id"],
        state=ValidationState(data["state"]),
        findings=tuple(tuple(item) for item in data["findings"]),
        refusal_reason_code=data["refusal_reason_code"],
        executed=data["executed"],
        reason_code=data["reason_code"],
        schema_version=data["schema_version"],
    )
    if "correction_decision_id" in row.keys() and str(row["correction_decision_id"]) != record.correction_decision_id:
        raise ResponseValidationCorrectionCorruption("correction decision index does not match payload")
    return record


def _request_retry_tuple(request: ResponseValidationCorrectionRequest) -> tuple[Any, ...]:
    return (
        request.validation_event_id,
        request.predecessor_record_id,
        request.state,
        request.findings,
        request.refusal_reason_code,
        request.executed,
        request.reason_code,
    )


def _record_retry_tuple(record: ResponseValidationCorrectionRecord) -> tuple[Any, ...]:
    return (
        record.validation_event_id,
        record.predecessor_record_id,
        record.state,
        record.findings,
        record.refusal_reason_code,
        record.executed,
        record.reason_code,
    )


def _identity(prefix: str, payload: Any) -> str:
    return f"{prefix}:{hashlib.sha256(_canonical_json(payload).encode('utf-8')).hexdigest()}"


def _sha256(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _canonical_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return _aware_utc("datetime", value).isoformat()
    if isinstance(value, ValidationState):
        return value.value
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _aware_utc(name: str, value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ResponseValidationCorrectionConflict(f"{name} must be timezone-aware")
    return value.astimezone(UTC)
