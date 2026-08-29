"""ADR 0035 Phase C terminal mechanical persistence.

This module persists only already-decided Phase B validation results. It never
sees, hashes, serializes, or reconstructs response content. The durable payload
contains canonical non-content decision, finding/refusal, attestation, and
lineage metadata only.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import sqlite3
from collections.abc import Iterator, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from hunter.evidence_intelligence.response_validator import (
    ResponseValidationExecutionResult,
    ResponseValidationRefusalResult,
    ValidationAttestationKind,
    ValidationState,
    canonical_validation_state,
)
from hunter.evidence_intelligence.response_validator_persistence import (
    ResponseValidatorPersistenceRepository,
)
from hunter.execution import Clock, SystemClock

RESPONSE_VALIDATION_RECORD_SCHEMA_VERSION = "response-validation-record-v1"
RESPONSE_VALIDATION_RECORD_HASH_VERSION = "sha256-canonical-noncontent-json-v1"


class ResponseValidationTerminalPersistenceError(RuntimeError):
    """Base class for Phase C terminal persistence failures."""


class ResponseValidationTerminalConflict(ResponseValidationTerminalPersistenceError):
    """Raised when an append/retry conflicts with immutable terminal state."""


class ResponseValidationTerminalCorruption(ResponseValidationTerminalPersistenceError):
    """Raised when durable terminal bytes or indexed coordinates are corrupt."""


class ResponseValidationDecisionKind(StrEnum):
    SUCCESS = "SUCCESS"
    REFUSAL = "REFUSAL"


@dataclass(frozen=True)
class ResponseValidationRecord:
    """Immutable generation-0 terminal record containing non-content metadata only."""

    validation_event_id: str
    validation_cutoff: datetime
    validation_recorded_at: datetime
    decision_kind: ResponseValidationDecisionKind
    decision_id: str
    state: ValidationState
    authorization_id: str | None
    attestation_id: str
    findings: tuple[tuple[str, str, str], ...]
    refusal_reason_code: str | None
    executed: bool | None
    profile_resolution_id: str | None
    profile_publication_id: str | None
    profile_version: int | None
    validator_contract_identity: str | None
    validator_contract_version: str | None
    requested_output_contract_identity: str | None
    requested_output_contract_version: str | None
    output_contract_hash: str | None
    source_handling_resolution_id: str | None
    source_handling_fact_record_id: str | None
    source_handling_policy_record_id: str | None
    source_handling_registry_id: str | None
    source_handling_authorization_rule_id: str | None
    response_capture_identity: str | None
    response_evidence_state: str | None
    attempt_id: str | None
    handoff_id: str | None
    outcome_id: str | None
    execution_profile_identity: str | None
    request_evidence_identity: str | None
    build_record_id: str | None
    prompt_artifact_id: str | None
    intent_id: str | None
    ledger_id: str | None
    allocation_id: str | None
    package_id: str | None
    evidence_input_identity: str | None
    input_mode: str | None
    revalidation_generation: int
    predecessor_validation_event_id: str | None
    available_authority: tuple[tuple[str, str], ...] = ()
    hash_version: str = RESPONSE_VALIDATION_RECORD_HASH_VERSION
    schema_version: str = RESPONSE_VALIDATION_RECORD_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "validation_cutoff", _aware_utc("validation_cutoff", self.validation_cutoff))
        object.__setattr__(
            self,
            "validation_recorded_at",
            _aware_utc("validation_recorded_at", self.validation_recorded_at),
        )
        object.__setattr__(self, "state", canonical_validation_state(self.state))
        object.__setattr__(self, "findings", _normalize_findings(self.findings))
        if self.validation_recorded_at < self.validation_cutoff:
            raise ResponseValidationTerminalConflict("validation_recorded_at precedes validation_cutoff")
        if self.revalidation_generation < 0:
            raise ResponseValidationTerminalConflict("revalidation_generation cannot be negative")
        if self.hash_version != RESPONSE_VALIDATION_RECORD_HASH_VERSION:
            raise ResponseValidationTerminalConflict("unknown terminal record hash version")
        if self.schema_version != RESPONSE_VALIDATION_RECORD_SCHEMA_VERSION:
            raise ResponseValidationTerminalConflict("unknown terminal record schema version")
        for name in ("validation_event_id", "decision_id", "attestation_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ResponseValidationTerminalConflict(f"{name} must be non-blank")
        normalized_authority = tuple(sorted((str(key), str(value)) for key, value in self.available_authority))
        if len({key for key, _ in normalized_authority}) != len(normalized_authority):
            raise ResponseValidationTerminalConflict("available_authority keys must be unique")
        object.__setattr__(self, "available_authority", normalized_authority)
        if self.decision_kind is ResponseValidationDecisionKind.SUCCESS:
            if not self.findings:
                raise ResponseValidationTerminalConflict("success terminal record requires canonical findings")
            if self.refusal_reason_code is not None:
                raise ResponseValidationTerminalConflict("success terminal record cannot contain refusal reason")
            if not isinstance(self.executed, bool):
                raise ResponseValidationTerminalConflict("success terminal record requires execution metadata")
        elif self.decision_kind is ResponseValidationDecisionKind.REFUSAL:
            if self.findings:
                raise ResponseValidationTerminalConflict("refusal terminal record cannot contain success findings")
            if not isinstance(self.refusal_reason_code, str) or not self.refusal_reason_code.strip():
                raise ResponseValidationTerminalConflict("refusal terminal record requires canonical reason code")
            if self.executed is not None:
                raise ResponseValidationTerminalConflict("refusal terminal record cannot claim semantic execution")

    @property
    def record_id(self) -> str:
        payload = _canonical_json(_record_identity_payload(self))
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        return f"response-validation-record:{digest}"


class ResponseValidationTerminalPersistenceService:
    """Mechanical append-or-join service for Phase B terminal decisions."""

    def __init__(
        self,
        repository: ResponseValidatorPersistenceRepository,
        *,
        clock: Clock | None = None,
    ) -> None:
        if not isinstance(repository, ResponseValidatorPersistenceRepository):
            raise TypeError("terminal persistence requires ResponseValidatorPersistenceRepository")
        self._repository = repository
        self._clock = clock or SystemClock()
        self.path = repository.path
        self._initialize()

    def persist_execution(self, result: ResponseValidationExecutionResult) -> ResponseValidationRecord:
        if not isinstance(result, ResponseValidationExecutionResult):
            raise ResponseValidationTerminalConflict("canonical execution result is required")
        outcome = result.outcome
        attestation = result.attestation
        coordinates = outcome.coordinates
        if attestation.kind is not ValidationAttestationKind.SUCCESS:
            raise ResponseValidationTerminalConflict("execution result requires SUCCESS attestation")
        if (
            attestation.decision_id != outcome.semantic_outcome_id
            or attestation.validation_event_id != coordinates.validation_event_id
            or attestation.state is not outcome.state
            or attestation.authorization_id != outcome.authorization_id
        ):
            raise ResponseValidationTerminalConflict("execution attestation does not match canonical outcome")
        desired = {
            "validation_event_id": coordinates.validation_event_id,
            "validation_cutoff": coordinates.validation_cutoff,
            "decision_kind": ResponseValidationDecisionKind.SUCCESS,
            "decision_id": outcome.semantic_outcome_id,
            "state": outcome.state,
            "authorization_id": outcome.authorization_id,
            "attestation_id": attestation.attestation_id,
            "findings": tuple((item.dimension, item.state.value, item.reason_code) for item in outcome.findings),
            "refusal_reason_code": None,
            "executed": outcome.executed,
            **_success_lineage(coordinates),
            "available_authority": (),
        }
        return self._append_or_join(desired)

    def persist_refusal(self, result: ResponseValidationRefusalResult) -> ResponseValidationRecord:
        if not isinstance(result, ResponseValidationRefusalResult):
            raise ResponseValidationTerminalConflict("canonical refusal result is required")
        refusal = result.refusal
        attestation = result.attestation
        if attestation.kind is not ValidationAttestationKind.REFUSAL:
            raise ResponseValidationTerminalConflict("refusal result requires REFUSAL attestation")
        if (
            attestation.decision_id != refusal.refusal_id
            or attestation.validation_event_id != refusal.validation_event_id
            or attestation.state is not refusal.state
            or attestation.authorization_id is not None
        ):
            raise ResponseValidationTerminalConflict("refusal attestation does not match canonical refusal")
        allocation = self._require_allocation(refusal.validation_event_id, refusal.validation_cutoff)
        desired = {
            "validation_event_id": refusal.validation_event_id,
            "validation_cutoff": refusal.validation_cutoff,
            "decision_kind": ResponseValidationDecisionKind.REFUSAL,
            "decision_id": refusal.refusal_id,
            "state": refusal.state,
            "authorization_id": None,
            "attestation_id": attestation.attestation_id,
            "findings": (),
            "refusal_reason_code": refusal.reason_code,
            "executed": None,
            **_empty_lineage(
                revalidation_generation=allocation.revalidation_generation,
                predecessor_validation_event_id=allocation.predecessor_validation_event_id,
            ),
            "available_authority": refusal.available_authority,
        }
        return self._append_or_join(desired)

    def load(
        self,
        validation_event_id: str,
        *,
        strict_known_at: datetime | None = None,
    ) -> ResponseValidationRecord | None:
        if not validation_event_id:
            raise ResponseValidationTerminalConflict("validation_event_id must be non-blank")
        cutoff = _aware_utc("strict_known_at", strict_known_at) if strict_known_at is not None else None
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT validation_event_id, validation_recorded_at, record_id,
                       payload_hash, payload_json, hash_version, schema_version
                FROM response_validation_terminal_records
                WHERE validation_event_id = ?
                """,
                (validation_event_id,),
            ).fetchone()
        if row is None:
            return None
        record = _record_from_row(row)
        if cutoff is not None and record.validation_recorded_at > cutoff:
            return None
        self._require_allocation(record.validation_event_id, record.validation_cutoff)
        return record

    def _append_or_join(self, desired: Mapping[str, Any]) -> ResponseValidationRecord:
        allocation = self._require_allocation(
            str(desired["validation_event_id"]),
            _aware_utc("validation_cutoff", desired["validation_cutoff"]),
        )
        if allocation.revalidation_generation != int(desired["revalidation_generation"]):
            raise ResponseValidationTerminalConflict("terminal lineage generation does not match allocation")
        if allocation.predecessor_validation_event_id != desired["predecessor_validation_event_id"]:
            raise ResponseValidationTerminalConflict("terminal predecessor does not match allocation")

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT validation_event_id, validation_recorded_at, record_id,
                       payload_hash, payload_json, hash_version, schema_version
                FROM response_validation_terminal_records
                WHERE validation_event_id = ?
                """,
                (desired["validation_event_id"],),
            ).fetchone()
            if existing is not None:
                record = _record_from_row(existing)
                if _retry_tuple(record) != _desired_retry_tuple(desired):
                    raise ResponseValidationTerminalConflict(
                        "validation_event_id already has conflicting immutable terminal lineage"
                    )
                return record

            recorded_at = _aware_utc("validation_recorded_at", self._clock.now())
            record = ResponseValidationRecord(validation_recorded_at=recorded_at, **dict(desired))
            payload = _record_payload(record)
            try:
                connection.execute(
                    """
                    INSERT INTO response_validation_terminal_records (
                        validation_event_id, validation_recorded_at, record_id,
                        payload_hash, payload_json, hash_version, schema_version
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.validation_event_id,
                        record.validation_recorded_at.isoformat(),
                        record.record_id,
                        _sha256(payload),
                        payload,
                        record.hash_version,
                        record.schema_version,
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise ResponseValidationTerminalConflict("terminal record identity conflict") from error
            return record

    def _require_allocation(self, validation_event_id: str, validation_cutoff: datetime):
        allocation = self._repository.validation_event(validation_event_id)
        if allocation is None:
            raise ResponseValidationTerminalConflict("terminal record references unknown validation event")
        if allocation.validation_cutoff != validation_cutoff:
            raise ResponseValidationTerminalConflict("terminal validation_cutoff does not match canonical allocation")
        return allocation

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS response_validation_terminal_records (
                    validation_event_id TEXT PRIMARY KEY,
                    validation_recorded_at TEXT NOT NULL,
                    record_id TEXT NOT NULL UNIQUE,
                    payload_hash TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    hash_version TEXT NOT NULL,
                    schema_version TEXT NOT NULL,
                    FOREIGN KEY (validation_event_id)
                        REFERENCES response_validation_event_allocations(validation_event_id)
                );
                CREATE INDEX IF NOT EXISTS response_validation_terminal_strict_known_idx
                    ON response_validation_terminal_records(validation_recorded_at, validation_event_id);
                """)

    @contextlib.contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()


def _success_lineage(coordinates: Any) -> dict[str, Any]:
    return {
        "profile_resolution_id": coordinates.profile_resolution_id,
        "profile_publication_id": coordinates.profile_publication_id,
        "profile_version": coordinates.profile_version,
        "validator_contract_identity": coordinates.validator_contract_identity,
        "validator_contract_version": coordinates.validator_contract_version,
        "requested_output_contract_identity": coordinates.requested_output_contract_identity,
        "requested_output_contract_version": coordinates.requested_output_contract_version,
        "output_contract_hash": coordinates.output_contract_hash,
        "source_handling_resolution_id": coordinates.source_handling_resolution_id,
        "source_handling_fact_record_id": coordinates.source_handling_fact_record_id,
        "source_handling_policy_record_id": coordinates.source_handling_policy_record_id,
        "source_handling_registry_id": coordinates.source_handling_registry_id,
        "source_handling_authorization_rule_id": coordinates.source_handling_authorization_rule_id,
        "response_capture_identity": coordinates.response_capture_identity,
        "response_evidence_state": coordinates.response_evidence_state,
        "attempt_id": coordinates.attempt_id,
        "handoff_id": coordinates.handoff_id,
        "outcome_id": coordinates.outcome_id,
        "execution_profile_identity": coordinates.execution_profile_identity,
        "request_evidence_identity": coordinates.request_evidence_identity,
        "build_record_id": coordinates.build_record_id,
        "prompt_artifact_id": coordinates.prompt_artifact_id,
        "intent_id": coordinates.intent_id,
        "ledger_id": coordinates.ledger_id,
        "allocation_id": coordinates.allocation_id,
        "package_id": coordinates.package_id,
        "evidence_input_identity": coordinates.evidence_input_identity,
        "input_mode": coordinates.input_mode.value,
        "revalidation_generation": coordinates.revalidation_generation,
        "predecessor_validation_event_id": coordinates.predecessor_validation_event_id,
    }


def _empty_lineage(*, revalidation_generation: int, predecessor_validation_event_id: str | None) -> dict[str, Any]:
    return {
        "profile_resolution_id": None,
        "profile_publication_id": None,
        "profile_version": None,
        "validator_contract_identity": None,
        "validator_contract_version": None,
        "requested_output_contract_identity": None,
        "requested_output_contract_version": None,
        "output_contract_hash": None,
        "source_handling_resolution_id": None,
        "source_handling_fact_record_id": None,
        "source_handling_policy_record_id": None,
        "source_handling_registry_id": None,
        "source_handling_authorization_rule_id": None,
        "response_capture_identity": None,
        "response_evidence_state": None,
        "attempt_id": None,
        "handoff_id": None,
        "outcome_id": None,
        "execution_profile_identity": None,
        "request_evidence_identity": None,
        "build_record_id": None,
        "prompt_artifact_id": None,
        "intent_id": None,
        "ledger_id": None,
        "allocation_id": None,
        "package_id": None,
        "evidence_input_identity": None,
        "input_mode": None,
        "revalidation_generation": revalidation_generation,
        "predecessor_validation_event_id": predecessor_validation_event_id,
    }


def _record_payload(record: ResponseValidationRecord) -> str:
    return _canonical_json({**_jsonable(asdict(record)), "record_id": record.record_id})


def _record_identity_payload(record: ResponseValidationRecord) -> dict[str, Any]:
    payload = _jsonable(asdict(record))
    payload.pop("validation_recorded_at")
    return payload


def _record_from_row(row: sqlite3.Row) -> ResponseValidationRecord:
    payload = str(row["payload_json"])
    if str(row["hash_version"]) != RESPONSE_VALIDATION_RECORD_HASH_VERSION:
        raise ResponseValidationTerminalCorruption("terminal hash version is unknown")
    if _sha256(payload) != str(row["payload_hash"]):
        raise ResponseValidationTerminalCorruption("terminal payload hash mismatch")
    try:
        item = json.loads(payload)
        record_id = str(item.pop("record_id"))
        item["validation_cutoff"] = _parse_time(str(item["validation_cutoff"]))
        item["validation_recorded_at"] = _parse_time(str(item["validation_recorded_at"]))
        item["decision_kind"] = ResponseValidationDecisionKind(item["decision_kind"])
        item["state"] = canonical_validation_state(item["state"])
        item["findings"] = tuple(tuple(entry) for entry in item.get("findings", ()))
        item["available_authority"] = tuple(tuple(pair) for pair in item.get("available_authority", ()))
        record = ResponseValidationRecord(**item)
    except (KeyError, TypeError, ValueError, ResponseValidationTerminalPersistenceError) as error:
        raise ResponseValidationTerminalCorruption("terminal payload is not canonical") from error
    if record.record_id != record_id:
        raise ResponseValidationTerminalCorruption("terminal record identity mismatch")
    expected = {
        "validation_event_id": record.validation_event_id,
        "validation_recorded_at": record.validation_recorded_at.isoformat(),
        "record_id": record.record_id,
        "hash_version": record.hash_version,
        "schema_version": record.schema_version,
    }
    for name, value in expected.items():
        if row[name] != value:
            raise ResponseValidationTerminalCorruption(f"terminal SQL metadata mismatch: {name}")
    return record


def _retry_tuple(record: ResponseValidationRecord) -> tuple[tuple[str, Any], ...]:
    values = _jsonable(asdict(record))
    values.pop("validation_recorded_at")
    return tuple(sorted(values.items()))


def _desired_retry_tuple(desired: Mapping[str, Any]) -> tuple[tuple[str, Any], ...]:
    values = _jsonable(dict(desired))
    values["hash_version"] = RESPONSE_VALIDATION_RECORD_HASH_VERSION
    values["schema_version"] = RESPONSE_VALIDATION_RECORD_SCHEMA_VERSION
    return tuple(sorted(values.items()))


def _normalize_findings(findings: tuple[tuple[str, str, str], ...]) -> tuple[tuple[str, str, str], ...]:
    normalized: list[tuple[str, str, str]] = []
    for entry in findings:
        if len(entry) != 3:
            raise ResponseValidationTerminalConflict("terminal finding must contain dimension, state, and reason code")
        dimension, state, reason_code = (str(value) for value in entry)
        if not dimension.strip() or not reason_code.strip():
            raise ResponseValidationTerminalConflict("terminal finding coordinates must be non-blank")
        canonical_state = canonical_validation_state(state)
        normalized.append((dimension, canonical_state.value, reason_code))
    return tuple(normalized)


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _aware_utc(name: str, value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ResponseValidationTerminalConflict(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ResponseValidationTerminalCorruption("persisted terminal timestamp must be timezone-aware")
    return parsed.astimezone(UTC)
