"""ADR 0035 Phase D governed correction allocation and strict-known replay.

The module persists only non-content correction authority and terminal metadata.
Caller proposals cannot become durable semantic state directly: a correction must
first be allocated by the ResponseValidator-owned service and then converted to a
non-caller-mintable validator decision whose canonical outcome invariants are
checked before persistence.
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
    highest_precedence_validation_state,
)
from hunter.evidence_intelligence.response_validator_terminal_persistence import (
    ResponseValidationRecord,
)
from hunter.execution import Clock, SystemClock

CORRECTION_ALLOCATION_SCHEMA_VERSION = "response-validation-correction-allocation-v1"
CORRECTION_DECISION_SCHEMA_VERSION = "response-validation-correction-decision-v1"
CORRECTION_RECORD_SCHEMA_VERSION = "response-validation-correction-record-v1"


class ResponseValidationCorrectionError(RuntimeError):
    """Base class for governed correction failures."""


class ResponseValidationCorrectionConflict(ResponseValidationCorrectionError):
    """Raised when retry, authority, semantics, lineage, or chronology conflicts."""


class ResponseValidationCorrectionCorruption(ResponseValidationCorrectionError):
    """Raised when durable correction bytes or indexed coordinates are corrupt."""


@dataclass(frozen=True)
class ResponseValidationCorrectionRequest:
    """Caller proposal for correction allocation; it contains no semantic outcome."""

    validation_event_id: str
    predecessor_record_id: str
    reason_code: str = "GOVERNED_CORRECTION"
    schema_version: str = "response-validation-correction-request-v2"

    def __post_init__(self) -> None:
        _required_text("validation_event_id", self.validation_event_id)
        _required_text("predecessor_record_id", self.predecessor_record_id)
        _required_text("reason_code", self.reason_code)
        if self.schema_version != "response-validation-correction-request-v2":
            raise ResponseValidationCorrectionConflict("unknown correction request schema version")

    @property
    def request_id(self) -> str:
        """Return the stable caller-proposal identity, excluding trusted time."""
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
        _required_text("validation_event_id", self.validation_event_id)
        _required_text("predecessor_record_id", self.predecessor_record_id)
        _required_text("correction_decision_id", self.correction_decision_id)
        _required_text("request_id", self.request_id)
        if self.generation < 1:
            raise ResponseValidationCorrectionConflict("correction generation starts at 1")
        if self.schema_version != CORRECTION_ALLOCATION_SCHEMA_VERSION:
            raise ResponseValidationCorrectionConflict("unknown correction allocation schema version")
        lower = _aware_utc("predecessor_accepted_at", self.predecessor_accepted_at)
        cutoff = _aware_utc("correction_cutoff", self.correction_cutoff)
        if lower > cutoff:
            raise ResponseValidationCorrectionConflict(
                "correction cutoff precedes predecessor durable acceptance"
            )
        object.__setattr__(self, "predecessor_accepted_at", lower)
        object.__setattr__(self, "correction_cutoff", cutoff)


_DECISION_MINT = object()


@dataclass(frozen=True, init=False)
class ResponseValidationCorrectionDecision:
    """Non-caller-mintable semantic decision bound to one trusted allocation."""

    validation_event_id: str
    generation: int
    predecessor_record_id: str
    correction_decision_id: str
    correction_cutoff: datetime
    request_id: str
    state: ValidationState
    findings: tuple[tuple[str, str, str], ...]
    refusal_reason_code: str | None
    executed: bool | None
    reason_code: str
    schema_version: str

    def __init__(
        self,
        mint: object,
        *,
        allocation: ResponseValidationCorrectionAllocation,
        state: ValidationState | str,
        findings: tuple[tuple[str, str, str], ...],
        refusal_reason_code: str | None,
        executed: bool | None,
        reason_code: str,
        schema_version: str = CORRECTION_DECISION_SCHEMA_VERSION,
    ) -> None:
        if mint is not _DECISION_MINT:
            raise ResponseValidationCorrectionConflict(
                "correction decisions are minted only by the ResponseValidator-owned authority"
            )
        if not isinstance(allocation, ResponseValidationCorrectionAllocation):
            raise ResponseValidationCorrectionConflict("correction decision requires canonical allocation")
        if schema_version != CORRECTION_DECISION_SCHEMA_VERSION:
            raise ResponseValidationCorrectionConflict("unknown correction decision schema version")
        canonical_state, canonical_findings = _canonical_outcome(
            state=state,
            findings=findings,
            refusal_reason_code=refusal_reason_code,
            executed=executed,
        )
        object.__setattr__(self, "validation_event_id", allocation.validation_event_id)
        object.__setattr__(self, "generation", allocation.generation)
        object.__setattr__(self, "predecessor_record_id", allocation.predecessor_record_id)
        object.__setattr__(self, "correction_decision_id", allocation.correction_decision_id)
        object.__setattr__(self, "correction_cutoff", allocation.correction_cutoff)
        object.__setattr__(self, "request_id", allocation.request_id)
        object.__setattr__(self, "state", canonical_state)
        object.__setattr__(self, "findings", canonical_findings)
        object.__setattr__(self, "refusal_reason_code", refusal_reason_code)
        object.__setattr__(self, "executed", executed)
        object.__setattr__(self, "reason_code", _required_text("reason_code", reason_code))
        object.__setattr__(self, "schema_version", schema_version)

    @property
    def decision_payload_id(self) -> str:
        """Bind the semantic payload to its exact trusted allocation."""
        return _identity("response-validation-correction-semantic-decision", _jsonable(asdict(self)))


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
    decision_payload_id: str
    state: ValidationState
    findings: tuple[tuple[str, str, str], ...]
    refusal_reason_code: str | None
    executed: bool | None
    reason_code: str
    schema_version: str = CORRECTION_RECORD_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _required_text("validation_event_id", self.validation_event_id)
        _required_text("predecessor_record_id", self.predecessor_record_id)
        _required_text("correction_decision_id", self.correction_decision_id)
        _required_text("request_id", self.request_id)
        _required_text("decision_payload_id", self.decision_payload_id)
        _required_text("reason_code", self.reason_code)
        cutoff = _aware_utc("correction_cutoff", self.correction_cutoff)
        recorded = _aware_utc("correction_recorded_at", self.correction_recorded_at)
        if recorded < cutoff:
            raise ResponseValidationCorrectionConflict("correction_recorded_at precedes correction_cutoff")
        if self.generation < 1:
            raise ResponseValidationCorrectionConflict("correction generation starts at 1")
        if self.schema_version != CORRECTION_RECORD_SCHEMA_VERSION:
            raise ResponseValidationCorrectionConflict("unknown correction record schema version")
        canonical_state, canonical_findings = _canonical_outcome(
            state=self.state,
            findings=self.findings,
            refusal_reason_code=self.refusal_reason_code,
            executed=self.executed,
        )
        object.__setattr__(self, "correction_cutoff", cutoff)
        object.__setattr__(self, "correction_recorded_at", recorded)
        object.__setattr__(self, "state", canonical_state)
        object.__setattr__(self, "findings", canonical_findings)

    @property
    def record_id(self) -> str:
        """Return the immutable record identity over the complete non-content payload."""
        return _identity("response-validation-correction-record", _jsonable(asdict(self)))


class ResponseValidationCorrectionService:
    """ResponseValidator-owned allocator, decision gate, persistence, and replay."""

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
        """Atomically create or join the exact next correction allocation."""
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
                raise ResponseValidationCorrectionConflict(
                    "correction request does not name exact current predecessor"
                )
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

    def issue_decision(
        self,
        allocation: ResponseValidationCorrectionAllocation,
        *,
        state: ValidationState | str,
        findings: tuple[tuple[str, str, str], ...] = (),
        refusal_reason_code: str | None = None,
        executed: bool | None = None,
        reason_code: str = "GOVERNED_CORRECTION",
    ) -> ResponseValidationCorrectionDecision:
        """Mint a canonical semantic decision only for an existing trusted allocation."""
        if type(self._owner) is not ResponseValidator:
            raise ResponseValidationCorrectionConflict("correction authority owner was substituted")
        with self._connect() as connection:
            canonical = self._allocation_by_decision(connection, allocation.correction_decision_id)
        if canonical != allocation:
            raise ResponseValidationCorrectionConflict("correction allocation was substituted or tampered")
        return ResponseValidationCorrectionDecision(
            _DECISION_MINT,
            allocation=canonical,
            state=state,
            findings=findings,
            refusal_reason_code=refusal_reason_code,
            executed=executed,
            reason_code=reason_code,
        )

    def persist(
        self,
        allocation: ResponseValidationCorrectionAllocation,
        decision: ResponseValidationCorrectionDecision,
    ) -> ResponseValidationCorrectionRecord:
        """Persist only a non-caller-mintable decision bound to the exact allocation."""
        if type(decision) is not ResponseValidationCorrectionDecision:
            raise ResponseValidationCorrectionConflict("validator-issued correction decision is required")
        if _decision_allocation_tuple(decision) != _allocation_identity_tuple(allocation):
            raise ResponseValidationCorrectionConflict("correction decision/allocation identity mismatch")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            canonical = self._allocation_by_decision(connection, allocation.correction_decision_id)
            if canonical != allocation:
                raise ResponseValidationCorrectionConflict("correction allocation was substituted or tampered")
            existing = self._record_by_decision(connection, allocation.correction_decision_id)
            if existing is not None:
                if _record_decision_tuple(existing) != _decision_retry_tuple(decision):
                    raise ResponseValidationCorrectionConflict(
                        "retry conflicts with immutable corrected semantics"
                    )
                return existing
            head_id, generation, predecessor_accepted_at = self._current_head(
                connection, allocation.validation_event_id
            )
            if head_id != allocation.predecessor_record_id or generation + 1 != allocation.generation:
                raise ResponseValidationCorrectionConflict(
                    "correction allocation no longer extends exact current head"
                )
            if predecessor_accepted_at != allocation.predecessor_accepted_at:
                raise ResponseValidationCorrectionConflict(
                    "correction allocation predecessor acceptance was substituted"
                )
            if predecessor_accepted_at > allocation.correction_cutoff:
                raise ResponseValidationCorrectionConflict("correction predecessor chronology is invalid")
            recorded_at = _aware_utc("correction_recorded_at", self._clock.now())
            if recorded_at < allocation.correction_cutoff:
                raise ResponseValidationCorrectionConflict(
                    "correction acceptance precedes trusted correction cutoff"
                )
            record = ResponseValidationCorrectionRecord(
                validation_event_id=allocation.validation_event_id,
                generation=allocation.generation,
                predecessor_record_id=allocation.predecessor_record_id,
                correction_decision_id=allocation.correction_decision_id,
                correction_cutoff=allocation.correction_cutoff,
                correction_recorded_at=recorded_at,
                request_id=allocation.request_id,
                decision_payload_id=decision.decision_payload_id,
                state=decision.state,
                findings=decision.findings,
                refusal_reason_code=decision.refusal_reason_code,
                executed=decision.executed,
                reason_code=decision.reason_code,
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
                raise ResponseValidationCorrectionConflict(
                    "immutable correction record identity conflict"
                ) from error
            return record

    def replay(
        self,
        validation_event_id: str,
        *,
        strict_known_at: datetime,
    ) -> ResponseValidationRecord | ResponseValidationCorrectionRecord | None:
        """Replay only records whose decision and durable-known coordinates are eligible."""
        cutoff = _aware_utc("strict_known_at", strict_known_at)
        base = self._base_record_loader(validation_event_id)
        if base is None:
            return None
        if base.validation_cutoff > cutoff or base.validation_recorded_at > cutoff:
            return None
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT validation_event_id, generation, predecessor_record_id,
                       correction_decision_id, correction_recorded_at, record_id,
                       payload_hash, payload_json
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
            if record.correction_cutoff > cutoff:
                continue
            if record.generation != expected_generation or record.predecessor_record_id != current.record_id:
                raise ResponseValidationCorrectionCorruption("correction replay lineage is non-contiguous")
            current = record
            expected_generation += 1
        return current

    def _current_head(
        self, connection: sqlite3.Connection, validation_event_id: str
    ) -> tuple[str, int, datetime]:
        """Read and integrity-check the exact durable head for one event."""
        row = connection.execute(
            """
            SELECT validation_event_id, generation, predecessor_record_id,
                   correction_decision_id, correction_recorded_at, record_id,
                   payload_hash, payload_json
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
        """Load one request allocation and verify every denormalized coordinate."""
        row = connection.execute(
            """
            SELECT validation_event_id, generation, predecessor_record_id,
                   correction_decision_id, request_id, payload_hash, payload_json
            FROM response_validation_correction_allocations
            WHERE request_id = ?
            """,
            (request_id,),
        ).fetchone()
        return _allocation_from_row(row) if row is not None else None

    def _allocation_by_decision(
        self, connection: sqlite3.Connection, decision_id: str
    ) -> ResponseValidationCorrectionAllocation | None:
        """Load one decision allocation and verify every denormalized coordinate."""
        row = connection.execute(
            """
            SELECT validation_event_id, generation, predecessor_record_id,
                   correction_decision_id, request_id, payload_hash, payload_json
            FROM response_validation_correction_allocations
            WHERE correction_decision_id = ?
            """,
            (decision_id,),
        ).fetchone()
        return _allocation_from_row(row) if row is not None else None

    def _record_by_decision(
        self, connection: sqlite3.Connection, decision_id: str
    ) -> ResponseValidationCorrectionRecord | None:
        """Load one correction and verify every denormalized coordinate."""
        row = connection.execute(
            """
            SELECT validation_event_id, generation, predecessor_record_id,
                   correction_decision_id, correction_recorded_at, record_id,
                   payload_hash, payload_json
            FROM response_validation_correction_records
            WHERE correction_decision_id = ?
            """,
            (decision_id,),
        ).fetchone()
        return _correction_record_from_row(row) if row is not None else None

    def _initialize(self) -> None:
        """Install append-only allocation/record tables and strict-known index."""
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
                    ON response_validation_correction_records(
                        validation_event_id, correction_recorded_at, generation
                    );
                """
            )

    @contextlib.contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """Open a transactional SQLite connection with named-row access."""
        connection = sqlite3.connect(self._path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()


def _allocation_from_row(row: sqlite3.Row) -> ResponseValidationCorrectionAllocation:
    """Decode one allocation and fail closed on payload or index mismatch."""
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
    expected = {
        "validation_event_id": allocation.validation_event_id,
        "generation": allocation.generation,
        "predecessor_record_id": allocation.predecessor_record_id,
        "correction_decision_id": allocation.correction_decision_id,
        "request_id": allocation.request_id,
    }
    for name, value in expected.items():
        if name in row.keys() and row[name] != value:
            raise ResponseValidationCorrectionCorruption(
                f"correction allocation {name} index does not match payload"
            )
    return allocation


def _correction_record_from_row(row: sqlite3.Row) -> ResponseValidationCorrectionRecord:
    """Decode one correction and fail closed on payload or every SQL index mismatch."""
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
        decision_payload_id=data["decision_payload_id"],
        state=ValidationState(data["state"]),
        findings=tuple(tuple(item) for item in data["findings"]),
        refusal_reason_code=data["refusal_reason_code"],
        executed=data["executed"],
        reason_code=data["reason_code"],
        schema_version=data["schema_version"],
    )
    expected = {
        "validation_event_id": record.validation_event_id,
        "generation": record.generation,
        "predecessor_record_id": record.predecessor_record_id,
        "correction_decision_id": record.correction_decision_id,
        "correction_recorded_at": record.correction_recorded_at.isoformat(),
        "record_id": record.record_id,
    }
    for name, value in expected.items():
        if name in row.keys() and row[name] != value:
            raise ResponseValidationCorrectionCorruption(
                f"correction record {name} index does not match payload"
            )
    return record


def _canonical_outcome(
    *,
    state: ValidationState | str,
    findings: tuple[tuple[str, str, str], ...],
    refusal_reason_code: str | None,
    executed: bool | None,
) -> tuple[ValidationState, tuple[tuple[str, str, str], ...]]:
    """Apply generation-0-compatible shape, precedence, and refusal invariants."""
    canonical_state = canonical_validation_state(state)
    canonical_findings: list[tuple[str, str, str]] = []
    for item in findings:
        if not isinstance(item, tuple) or len(item) != 3:
            raise ResponseValidationCorrectionConflict(
                "correction findings must be exact (dimension, state, reason_code) triples"
            )
        dimension, finding_state, reason_code = item
        canonical_findings.append(
            (
                _required_text("finding dimension", dimension),
                canonical_validation_state(finding_state).value,
                _required_text("finding reason_code", reason_code),
            )
        )
    normalized = tuple(sorted(canonical_findings))
    refusal_states = {
        ValidationState.INPUT_UNAVAILABLE,
        ValidationState.RULE_UNAVAILABLE,
        ValidationState.VALIDATOR_CAPABILITY_UNKNOWN,
        ValidationState.SOURCE_HANDLING_BLOCKED,
        ValidationState.SECURITY_BLOCKED,
    }
    if refusal_reason_code is not None:
        _required_text("refusal_reason_code", refusal_reason_code)
        if normalized or executed is not None or canonical_state not in refusal_states:
            raise ResponseValidationCorrectionConflict(
                "correction refusal semantics contradict canonical refusal invariants"
            )
        return canonical_state, normalized
    if not normalized or not isinstance(executed, bool):
        raise ResponseValidationCorrectionConflict(
            "correction semantic outcome requires canonical findings and execution metadata"
        )
    precedence = highest_precedence_validation_state(
        ValidationState(item[1]) for item in normalized
    )
    if precedence is not canonical_state:
        raise ResponseValidationCorrectionConflict(
            "correction state does not match canonical finding precedence"
        )
    if canonical_state is ValidationState.VALID and any(
        ValidationState(item[1]) is not ValidationState.VALID for item in normalized
    ):
        raise ResponseValidationCorrectionConflict(
            "VALID correction cannot contain a non-VALID finding"
        )
    return canonical_state, normalized


def _allocation_identity_tuple(allocation: ResponseValidationCorrectionAllocation) -> tuple[Any, ...]:
    """Return the coordinates a semantic decision must bind exactly."""
    return (
        allocation.validation_event_id,
        allocation.generation,
        allocation.predecessor_record_id,
        allocation.correction_decision_id,
        allocation.correction_cutoff,
        allocation.request_id,
    )


def _decision_allocation_tuple(decision: ResponseValidationCorrectionDecision) -> tuple[Any, ...]:
    """Return allocation coordinates embedded in one validator decision."""
    return (
        decision.validation_event_id,
        decision.generation,
        decision.predecessor_record_id,
        decision.correction_decision_id,
        decision.correction_cutoff,
        decision.request_id,
    )


def _decision_retry_tuple(decision: ResponseValidationCorrectionDecision) -> tuple[Any, ...]:
    """Return immutable semantic coordinates for retry equality."""
    return (
        decision.validation_event_id,
        decision.generation,
        decision.predecessor_record_id,
        decision.correction_decision_id,
        decision.correction_cutoff,
        decision.request_id,
        decision.decision_payload_id,
        decision.state,
        decision.findings,
        decision.refusal_reason_code,
        decision.executed,
        decision.reason_code,
    )


def _record_decision_tuple(record: ResponseValidationCorrectionRecord) -> tuple[Any, ...]:
    """Return durable semantic coordinates corresponding to a validator decision."""
    return (
        record.validation_event_id,
        record.generation,
        record.predecessor_record_id,
        record.correction_decision_id,
        record.correction_cutoff,
        record.request_id,
        record.decision_payload_id,
        record.state,
        record.findings,
        record.refusal_reason_code,
        record.executed,
        record.reason_code,
    )


def _identity(prefix: str, payload: Any) -> str:
    """Build a namespaced SHA-256 identity over canonical JSON."""
    return f"{prefix}:{hashlib.sha256(_canonical_json(payload).encode('utf-8')).hexdigest()}"


def _sha256(payload: str) -> str:
    """Hash one already-canonical UTF-8 JSON string."""
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _canonical_json(payload: Any) -> str:
    """Serialize deterministic compact JSON."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _jsonable(value: Any) -> Any:
    """Convert supported canonical values to deterministic JSON-compatible values."""
    if isinstance(value, datetime):
        return _aware_utc("datetime", value).isoformat()
    if isinstance(value, ValidationState):
        return value.value
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _required_text(name: str, value: Any) -> str:
    """Require a non-blank string and return it unchanged."""
    if not isinstance(value, str) or not value.strip():
        raise ResponseValidationCorrectionConflict(f"{name} must be non-blank")
    return value


def _aware_utc(name: str, value: datetime) -> datetime:
    """Require a timezone-aware datetime and normalize it to UTC."""
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ResponseValidationCorrectionConflict(f"{name} must be timezone-aware")
    return value.astimezone(UTC)
