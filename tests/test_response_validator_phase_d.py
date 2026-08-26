from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest

from hunter.evidence_intelligence.response_validator import ResponseValidator, ValidationState
from hunter.evidence_intelligence.response_validator_corrections import (
    ResponseValidationCorrectionConflict,
    ResponseValidationCorrectionCorruption,
    ResponseValidationCorrectionRequest,
    ResponseValidationCorrectionService,
)
from hunter.evidence_intelligence.response_validator_terminal_persistence import (
    ResponseValidationRecord,
)


@dataclass(frozen=True)
class _BaseRecord:
    record_id: str
    validation_recorded_at: datetime


class _Clock:
    def __init__(self, *values: datetime) -> None:
        self._values = list(values)

    def now(self) -> datetime:
        if not self._values:
            raise AssertionError("clock exhausted")
        return self._values.pop(0)


def _owner() -> ResponseValidator:
    return object.__new__(ResponseValidator)


def _request(predecessor_record_id: str, *, state: ValidationState = ValidationState.VALID):
    return ResponseValidationCorrectionRequest(
        validation_event_id="event-1",
        predecessor_record_id=predecessor_record_id,
        state=state,
        findings=(("VALIDATION", state.value, "CORRECTED"),),
        executed=True,
        reason_code="OWNER_APPROVED_CORRECTION",
    )


def test_phase_d_generation_one_persists_and_replays_strict_known(tmp_path) -> None:
    t0 = datetime(2026, 8, 26, 10, 0, tzinfo=UTC)
    t1 = t0 + timedelta(minutes=1)
    t2 = t1 + timedelta(seconds=1)
    base = _BaseRecord("base-record", t0)
    service = ResponseValidationCorrectionService(
        _owner(),
        db_path=tmp_path / "phase-d.sqlite3",
        base_record_loader=lambda event_id: cast(ResponseValidationRecord, base) if event_id == "event-1" else None,
        clock=_Clock(t1, t2),
    )

    allocation = service.allocate(_request(base.record_id))
    assert allocation.generation == 1
    assert allocation.predecessor_accepted_at == t0
    assert allocation.correction_cutoff == t1

    record = service.persist(allocation, _request(base.record_id))
    assert record.generation == 1
    assert record.correction_recorded_at == t2
    assert service.replay("event-1", strict_known_at=t0) == base
    assert service.replay("event-1", strict_known_at=t2) == record


def test_phase_d_generation_two_uses_predecessor_correction_recorded_at(tmp_path) -> None:
    t0 = datetime(2026, 8, 26, 10, 0, tzinfo=UTC)
    t1 = t0 + timedelta(minutes=1)
    t2 = t1 + timedelta(seconds=1)
    t3 = t2 + timedelta(minutes=1)
    t4 = t3 + timedelta(seconds=1)
    base = _BaseRecord("base-record", t0)
    service = ResponseValidationCorrectionService(
        _owner(),
        db_path=tmp_path / "phase-d.sqlite3",
        base_record_loader=lambda _: cast(ResponseValidationRecord, base),
        clock=_Clock(t1, t2, t3, t4),
    )

    first_request = _request(base.record_id)
    first = service.persist(service.allocate(first_request), first_request)
    second_request = _request(first.record_id, state=ValidationState.INVALID_SCHEMA)
    second_allocation = service.allocate(second_request)
    assert second_allocation.generation == 2
    assert second_allocation.predecessor_record_id == first.record_id
    assert second_allocation.predecessor_accepted_at == first.correction_recorded_at

    second = service.persist(second_allocation, second_request)
    assert service.replay("event-1", strict_known_at=t2) == first
    assert service.replay("event-1", strict_known_at=t4) == second


def test_phase_d_inverted_chronology_does_not_consume_generation(tmp_path) -> None:
    t0 = datetime(2026, 8, 26, 10, 0, tzinfo=UTC)
    invalid = t0 - timedelta(seconds=1)
    valid = t0 + timedelta(seconds=1)
    recorded = valid + timedelta(seconds=1)
    base = _BaseRecord("base-record", t0)
    service = ResponseValidationCorrectionService(
        _owner(),
        db_path=tmp_path / "phase-d.sqlite3",
        base_record_loader=lambda _: cast(ResponseValidationRecord, base),
        clock=_Clock(invalid, valid, recorded),
    )
    request = _request(base.record_id)

    with pytest.raises(ResponseValidationCorrectionConflict, match="chronology is inverted"):
        service.allocate(request)

    allocation = service.allocate(request)
    assert allocation.generation == 1
    record = service.persist(allocation, request)
    assert record.generation == 1


def test_phase_d_identical_retry_joins_and_tamper_fails_closed(tmp_path) -> None:
    t0 = datetime(2026, 8, 26, 10, 0, tzinfo=UTC)
    t1 = t0 + timedelta(seconds=1)
    t2 = t1 + timedelta(seconds=1)
    base = _BaseRecord("base-record", t0)
    path = tmp_path / "phase-d.sqlite3"
    service = ResponseValidationCorrectionService(
        _owner(),
        db_path=path,
        base_record_loader=lambda _: cast(ResponseValidationRecord, base),
        clock=_Clock(t1, t2),
    )
    request = _request(base.record_id)

    first = service.allocate(request)
    joined = service.allocate(request)
    assert joined == first
    persisted = service.persist(first, request)
    assert service.persist(first, request) == persisted

    competing = ResponseValidationCorrectionRequest(
        validation_event_id="event-1",
        predecessor_record_id=base.record_id,
        state=ValidationState.INVALID_LINEAGE,
        findings=(("LINEAGE", ValidationState.INVALID_LINEAGE.value, "OTHER"),),
        executed=True,
        reason_code="COMPETING_CORRECTION",
    )
    with pytest.raises(ResponseValidationCorrectionConflict):
        service.allocate(competing)

    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE response_validation_correction_records SET payload_hash = 'tampered' WHERE correction_decision_id = ?",
            (first.correction_decision_id,),
        )
    with pytest.raises(ResponseValidationCorrectionCorruption):
        service.replay("event-1", strict_known_at=t2)
