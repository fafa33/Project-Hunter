"""ADR 0035 Phase C terminal persistence tests."""

from __future__ import annotations

import dataclasses
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path

import pytest
import response_validator_phase_b_fixture as fixture

from hunter.evidence_intelligence.response_validator import ValidationState
from hunter.evidence_intelligence.response_validator_terminal_persistence import (
    RESPONSE_VALIDATION_RECORD_HASH_VERSION,
    ResponseValidationDecisionKind,
    ResponseValidationTerminalConflict,
    ResponseValidationTerminalCorruption,
    ResponseValidationTerminalPersistenceService,
)


def _execute(harness: fixture.Harness):
    authorization = harness.validator.authorize_event(harness.allocation).authorization
    assert authorization is not None
    return harness.validator.execute(authorization)


def test_success_append_retry_reload_and_strict_known(tmp_path: Path) -> None:
    harness = fixture.make_harness(tmp_path)
    result = _execute(harness)
    recorded_at = fixture.VALIDATION_CUTOFF + timedelta(minutes=5)
    service = ResponseValidationTerminalPersistenceService(
        harness.validation_repository,
        clock=fixture.SequenceClock(recorded_at),
    )

    first = service.persist_execution(result)
    retry = service.persist_execution(result)
    reloaded = service.load(first.validation_event_id)

    assert retry == first
    assert first.validation_recorded_at == recorded_at
    assert first.validation_event_id == harness.allocation.validation_event_id
    assert first.decision_kind is ResponseValidationDecisionKind.SUCCESS
    assert first.state is ValidationState.VALID
    assert first.authorization_id == result.outcome.authorization_id
    assert first.attestation_id == result.attestation.attestation_id
    assert first.findings == tuple(
        (finding.dimension, finding.state.value, finding.reason_code) for finding in result.outcome.findings
    )
    assert first.executed is result.outcome.executed
    assert first.refusal_reason_code is None
    assert first.profile_publication_id == harness.profile.publication_id
    assert first.attempt_id == harness.prepared.attempt.attempt_id
    assert first.build_record_id == harness.build_result.build_record.build_record_id
    assert first.hash_version == RESPONSE_VALIDATION_RECORD_HASH_VERSION
    assert reloaded == first
    assert reloaded is not None
    assert reloaded.findings == first.findings
    assert reloaded.executed is result.outcome.executed
    assert service.load(first.validation_event_id, strict_known_at=recorded_at - timedelta(microseconds=1)) is None
    assert service.load(first.validation_event_id, strict_known_at=recorded_at) == first


def test_refusal_is_persisted_without_inventing_missing_authority(tmp_path: Path) -> None:
    harness = fixture.make_harness(tmp_path)
    harness.foundation._clock = fixture.SequenceClock(fixture.VALIDATION_CUTOFF + timedelta(seconds=1))  # noqa: SLF001
    allocation = harness.foundation.allocate_base_validation(
        dataclasses.replace(
            harness.allocation.base_validation_key,
            requested_profile_selector="profile-not-known-at-cutoff",
        )
    )
    refusal = harness.validator.authorize_event(allocation).refusal
    assert refusal is not None
    service = ResponseValidationTerminalPersistenceService(
        harness.validation_repository,
        clock=fixture.SequenceClock(allocation.validation_cutoff + timedelta(minutes=1)),
    )

    record = service.persist_refusal(refusal)
    reloaded = service.load(record.validation_event_id)

    assert record.decision_kind is ResponseValidationDecisionKind.REFUSAL
    assert record.authorization_id is None
    assert record.state is ValidationState.RULE_UNAVAILABLE
    assert record.profile_publication_id is None
    assert record.available_authority == refusal.refusal.available_authority
    assert record.findings == ()
    assert record.refusal_reason_code == refusal.refusal.reason_code
    assert record.executed is None
    assert reloaded == record
    assert reloaded is not None
    assert reloaded.refusal_reason_code == refusal.refusal.reason_code


def test_concurrent_identical_retry_joins_one_record(tmp_path: Path) -> None:
    harness = fixture.make_harness(tmp_path)
    result = _execute(harness)
    service = ResponseValidationTerminalPersistenceService(
        harness.validation_repository,
        clock=fixture.SequenceClock(fixture.VALIDATION_CUTOFF + timedelta(minutes=5)),
    )

    with ThreadPoolExecutor(max_workers=8) as pool:
        records = tuple(pool.map(lambda _: service.persist_execution(result), range(16)))

    assert len({record.record_id for record in records}) == 1
    assert len({record.validation_recorded_at for record in records}) == 1
    with sqlite3.connect(harness.database) as connection:
        count = connection.execute("SELECT COUNT(*) FROM response_validation_terminal_records").fetchone()[0]
    assert count == 1


def test_chronology_inversion_fails_before_append(tmp_path: Path) -> None:
    harness = fixture.make_harness(tmp_path)
    result = _execute(harness)
    service = ResponseValidationTerminalPersistenceService(
        harness.validation_repository,
        clock=fixture.SequenceClock(fixture.VALIDATION_CUTOFF - timedelta(seconds=1)),
    )

    with pytest.raises(ResponseValidationTerminalConflict, match="precedes validation_cutoff"):
        service.persist_execution(result)

    with sqlite3.connect(harness.database) as connection:
        count = connection.execute("SELECT COUNT(*) FROM response_validation_terminal_records").fetchone()[0]
    assert count == 0


def test_terminal_payload_tamper_is_detected_and_transient_body_is_never_stored(tmp_path: Path) -> None:
    harness = fixture.make_harness(tmp_path, transient=True)
    result = _execute(harness)
    service = ResponseValidationTerminalPersistenceService(
        harness.validation_repository,
        clock=fixture.SequenceClock(fixture.VALIDATION_CUTOFF + timedelta(minutes=5)),
    )
    record = service.persist_execution(result)

    with sqlite3.connect(harness.database) as connection:
        payload = connection.execute(
            "SELECT payload_json FROM response_validation_terminal_records WHERE validation_event_id = ?",
            (record.validation_event_id,),
        ).fetchone()[0]
        assert "supported answer" not in payload
        assert "ALL_REQUIRED_DIMENSIONS_VALID" in payload
        connection.execute(
            "UPDATE response_validation_terminal_records SET payload_json = '{}' WHERE validation_event_id = ?",
            (record.validation_event_id,),
        )

    with pytest.raises(ResponseValidationTerminalCorruption, match="payload hash mismatch"):
        service.load(record.validation_event_id)
