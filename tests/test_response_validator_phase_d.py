from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest

from hunter.evidence_intelligence.response_validator import ResponseValidator, ValidationState
from hunter.evidence_intelligence.response_validator_corrections import (
    ResponseValidationCorrectionConflict,
    ResponseValidationCorrectionCorruption,
    ResponseValidationCorrectionDecision,
    ResponseValidationCorrectionRequest,
    ResponseValidationCorrectionService,
)
from hunter.evidence_intelligence.response_validator_terminal_persistence import (
    ResponseValidationRecord,
)


@dataclass(frozen=True)
class _BaseRecord:
    record_id: str
    validation_cutoff: datetime
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


def _request(predecessor_record_id: str, *, reason_code: str = "OWNER_APPROVED_CORRECTION"):
    return ResponseValidationCorrectionRequest(
        validation_event_id="event-1",
        predecessor_record_id=predecessor_record_id,
        reason_code=reason_code,
    )


def _decision(
    service: ResponseValidationCorrectionService,
    allocation,
    *,
    state: ValidationState = ValidationState.VALID,
):
    return service.issue_decision(
        allocation,
        state=state,
        findings=(("VALIDATION", state.value, "CORRECTED"),),
        executed=True,
        reason_code="OWNER_APPROVED_CORRECTION",
    )


def test_phase_d_generation_one_persists_and_replays_strict_known(tmp_path) -> None:
    t0 = datetime(2026, 8, 26, 10, 0, tzinfo=UTC)
    t1 = t0 + timedelta(minutes=1)
    t2 = t1 + timedelta(seconds=1)
    base = _BaseRecord("base-record", t0, t0)
    service = ResponseValidationCorrectionService(
        _owner(),
        db_path=tmp_path / "phase-d.sqlite3",
        base_record_loader=lambda event_id: cast(ResponseValidationRecord, base)
        if event_id == "event-1"
        else None,
        clock=_Clock(t1, t2),
    )

    request = _request(base.record_id)
    allocation = service.allocate(request)
    assert allocation.generation == 1
    assert allocation.predecessor_accepted_at == t0
    assert allocation.correction_cutoff == t1

    record = service.persist(allocation, _decision(service, allocation))
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
    base = _BaseRecord("base-record", t0, t0)
    service = ResponseValidationCorrectionService(
        _owner(),
        db_path=tmp_path / "phase-d.sqlite3",
        base_record_loader=lambda _: cast(ResponseValidationRecord, base),
        clock=_Clock(t1, t2, t3, t4),
    )

    first_request = _request(base.record_id)
    first_allocation = service.allocate(first_request)
    first = service.persist(first_allocation, _decision(service, first_allocation))
    second_request = _request(first.record_id, reason_code="SECOND_CORRECTION")
    second_allocation = service.allocate(second_request)
    assert second_allocation.generation == 2
    assert second_allocation.predecessor_record_id == first.record_id
    assert second_allocation.predecessor_accepted_at == first.correction_recorded_at

    second = service.persist(
        second_allocation,
        _decision(service, second_allocation, state=ValidationState.INVALID_SCHEMA),
    )
    assert service.replay("event-1", strict_known_at=t2) == first
    assert service.replay("event-1", strict_known_at=t4) == second


def test_phase_d_inverted_chronology_does_not_consume_generation(tmp_path) -> None:
    t0 = datetime(2026, 8, 26, 10, 0, tzinfo=UTC)
    invalid = t0 - timedelta(seconds=1)
    valid = t0 + timedelta(seconds=1)
    recorded = valid + timedelta(seconds=1)
    base = _BaseRecord("base-record", t0, t0)
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
    record = service.persist(allocation, _decision(service, allocation))
    assert record.generation == 1


def test_phase_d_identical_retry_joins_and_competing_retry_conflicts(tmp_path) -> None:
    t0 = datetime(2026, 8, 26, 10, 0, tzinfo=UTC)
    t1 = t0 + timedelta(seconds=1)
    t2 = t1 + timedelta(seconds=1)
    base = _BaseRecord("base-record", t0, t0)
    service = ResponseValidationCorrectionService(
        _owner(),
        db_path=tmp_path / "phase-d.sqlite3",
        base_record_loader=lambda _: cast(ResponseValidationRecord, base),
        clock=_Clock(t1, t2),
    )
    request = _request(base.record_id)

    first = service.allocate(request)
    joined = service.allocate(request)
    assert joined == first
    decision = _decision(service, first)
    persisted = service.persist(first, decision)
    assert service.persist(first, decision) == persisted

    competing = _request(base.record_id, reason_code="COMPETING_CORRECTION")
    with pytest.raises(ResponseValidationCorrectionConflict):
        service.allocate(competing)


def test_phase_d_caller_cannot_mint_decision_and_malformed_outcome_is_rejected(tmp_path) -> None:
    t0 = datetime(2026, 8, 26, 10, 0, tzinfo=UTC)
    t1 = t0 + timedelta(seconds=1)
    base = _BaseRecord("base-record", t0, t0)
    service = ResponseValidationCorrectionService(
        _owner(),
        db_path=tmp_path / "phase-d.sqlite3",
        base_record_loader=lambda _: cast(ResponseValidationRecord, base),
        clock=_Clock(t1),
    )
    allocation = service.allocate(_request(base.record_id))

    with pytest.raises(ResponseValidationCorrectionConflict, match="minted only"):
        ResponseValidationCorrectionDecision(
            object(),
            allocation=allocation,
            state=ValidationState.VALID,
            findings=(("VALIDATION", ValidationState.VALID.value, "FORGED"),),
            refusal_reason_code=None,
            executed=True,
            reason_code="FORGED",
        )

    with pytest.raises(ResponseValidationCorrectionConflict, match="precedence"):
        service.issue_decision(
            allocation,
            state=ValidationState.VALID,
            findings=(("SCHEMA", ValidationState.INVALID_SCHEMA.value, "MISMATCH"),),
            executed=True,
        )

    with pytest.raises(ResponseValidationCorrectionConflict, match="exact"):
        service.issue_decision(
            allocation,
            state=ValidationState.INVALID_SCHEMA,
            findings=cast(tuple[tuple[str, str, str], ...], (("SCHEMA", "INVALID_SCHEMA"),)),
            executed=True,
        )


def test_phase_d_index_tamper_fails_closed(tmp_path) -> None:
    t0 = datetime(2026, 8, 26, 10, 0, tzinfo=UTC)
    t1 = t0 + timedelta(seconds=1)
    t2 = t1 + timedelta(seconds=1)
    base = _BaseRecord("base-record", t0, t0)
    path = tmp_path / "phase-d.sqlite3"
    service = ResponseValidationCorrectionService(
        _owner(),
        db_path=path,
        base_record_loader=lambda _: cast(ResponseValidationRecord, base),
        clock=_Clock(t1, t2),
    )
    allocation = service.allocate(_request(base.record_id))
    service.persist(allocation, _decision(service, allocation))

    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE response_validation_correction_records "
            "SET correction_recorded_at = ? WHERE correction_decision_id = ?",
            ((t0 - timedelta(seconds=1)).isoformat(), allocation.correction_decision_id),
        )
    with pytest.raises(ResponseValidationCorrectionCorruption, match="correction_recorded_at"):
        service.replay("event-1", strict_known_at=t2)


def test_phase_d_concurrent_identical_allocation_claims_one_generation(tmp_path) -> None:
    t0 = datetime(2026, 8, 26, 10, 0, tzinfo=UTC)
    t1 = t0 + timedelta(seconds=1)
    base = _BaseRecord("base-record", t0, t0)
    service = ResponseValidationCorrectionService(
        _owner(),
        db_path=tmp_path / "phase-d.sqlite3",
        base_record_loader=lambda _: cast(ResponseValidationRecord, base),
        clock=_Clock(t1),
    )
    request = _request(base.record_id)

    with ThreadPoolExecutor(max_workers=2) as executor:
        allocations = list(executor.map(lambda _: service.allocate(request), range(2)))

    assert allocations[0] == allocations[1]
    assert allocations[0].generation == 1
