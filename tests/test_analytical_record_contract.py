from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from hunter.persistence import AnalyticalRecord, AnalyticalReplaySpec, AuthorizedAnalyticalWrite
from hunter.persistence.exceptions import PersistenceValidationError
from hunter.persistence.serialization import record_from_json, record_to_json
from hunter.persistence.sql import (
    AnalyticalCorrectionConflictError,
    AnalyticalWriteAuthorizationError,
    RepositoryFactory,
    SessionFactory,
    create_schema,
    create_sqlite_engine,
)
from hunter.persistence.sql.exceptions import PersistenceIdentityConflictError

NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


@pytest.fixture
def session_factory() -> SessionFactory:
    engine = create_sqlite_engine()
    create_schema(engine)
    return SessionFactory(engine)


def test_authorized_analytical_record_round_trips_with_complete_contract(session_factory: SessionFactory) -> None:
    record = analytical_record()

    with session_factory.create() as session:
        repository = RepositoryFactory(session).analytical_records()
        repository.persist(AuthorizedAnalyticalWrite(record, "create"))
        session.commit()
        restored = repository.load(record.id)

    assert restored == record
    assert restored is not None
    assert restored.recorded_at == NOW
    assert restored.strict_known_eligible is True
    assert record_from_json(record_to_json(record)) == record


def test_identical_authorized_write_is_idempotent_and_conflict_is_rejected(
    session_factory: SessionFactory,
) -> None:
    record = analytical_record()

    with session_factory.create() as session:
        repository = RepositoryFactory(session).analytical_records()
        plan = AuthorizedAnalyticalWrite(record, "create")

        assert repository.persist(plan) == record
        assert repository.persist(plan) == record
        with pytest.raises(PersistenceIdentityConflictError):
            repository.persist(AuthorizedAnalyticalWrite(replace(record, payload={"value": 2}), "create"))


def test_correction_preserves_predecessor_and_queryable_history(session_factory: SessionFactory) -> None:
    original = analytical_record()
    correction = analytical_record(
        record_id="analytical-record:identity-v1:corrected",
        recorded_at=NOW + timedelta(hours=2),
        supersedes_id=original.id,
        correction_reason="Source issued a corrected value.",
        payload={"value": 2},
    )

    with session_factory.create() as session:
        repository = RepositoryFactory(session).analytical_records()
        repository.persist(AuthorizedAnalyticalWrite(original, "create"))
        repository.persist(AuthorizedAnalyticalWrite(correction, "correct"))
        session.commit()

        assert repository.load(original.id) == original
        assert repository.load(correction.id) == correction
        assert repository.lineage(original.logical_identity) == (original, correction)


def test_correction_requires_explicit_authorized_lineage(session_factory: SessionFactory) -> None:
    original = analytical_record()
    correction = analytical_record(
        record_id="analytical-record:identity-v1:corrected",
        recorded_at=NOW + timedelta(hours=1),
        supersedes_id="analytical-record:identity-v1:missing",
        correction_reason="Correction.",
    )

    with session_factory.create() as session:
        repository = RepositoryFactory(session).analytical_records()
        with pytest.raises(AnalyticalWriteAuthorizationError):
            repository.save(original)
        with pytest.raises(AnalyticalWriteAuthorizationError):
            repository.delete(original.id)
        with pytest.raises(AnalyticalCorrectionConflictError):
            repository.persist(AuthorizedAnalyticalWrite(correction, "correct"))


def test_repository_does_not_generate_temporal_or_lifecycle_authority(session_factory: SessionFactory) -> None:
    with pytest.raises(PersistenceValidationError):
        analytical_record(recorded_at=None)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="explicit predecessor"):
        AuthorizedAnalyticalWrite(analytical_record(), "correct")
    with pytest.raises(ValueError, match="cannot supersede"):
        AuthorizedAnalyticalWrite(
            analytical_record(
                record_id="analytical-record:identity-v1:corrected",
                supersedes_id="analytical-record:identity-v1:original",
                correction_reason="Correction.",
            ),
            "create",
        )


def test_strict_known_selection_excludes_late_corrections(session_factory: SessionFactory) -> None:
    original = analytical_record()
    correction = analytical_record(
        record_id="analytical-record:identity-v1:corrected",
        recorded_at=NOW + timedelta(days=2),
        known_at=NOW + timedelta(hours=1),
        supersedes_id=original.id,
        correction_reason="Correction recorded later.",
        payload={"value": 2},
    )

    with session_factory.create() as session:
        repository = RepositoryFactory(session).analytical_records()
        repository.persist(AuthorizedAnalyticalWrite(original, "create"))
        repository.persist(AuthorizedAnalyticalWrite(correction, "correct"))
        session.commit()

        before_correction = repository.strict_known(
            AnalyticalReplaySpec(original.logical_identity, NOW, NOW + timedelta(days=1))
        )
        after_correction = repository.strict_known(
            AnalyticalReplaySpec(original.logical_identity, NOW, NOW + timedelta(days=3))
        )

    assert before_correction == original
    assert after_correction == correction


def test_unknown_known_time_cannot_claim_strict_known_replay(session_factory: SessionFactory) -> None:
    legacy = analytical_record(known_at=None, known_time_limitation="legacy source has no known-time field")

    with session_factory.create() as session:
        repository = RepositoryFactory(session).analytical_records()
        repository.persist(AuthorizedAnalyticalWrite(legacy, "create"))
        session.commit()

        selected = repository.strict_known(AnalyticalReplaySpec(legacy.logical_identity, NOW, NOW))

    assert legacy.strict_known_eligible is False
    assert selected is None


def analytical_record(
    *,
    record_id: str = "analytical-record:identity-v1:original",
    recorded_at: datetime = NOW,
    known_at: datetime | None = NOW - timedelta(hours=1),
    known_time_limitation: str | None = None,
    supersedes_id: str | None = None,
    correction_reason: str | None = None,
    payload: dict[str, int] | None = None,
) -> AnalyticalRecord:
    return AnalyticalRecord(
        id=record_id,
        schema_version="analytical-envelope-v1",
        created_at=recorded_at,
        effective_at=NOW - timedelta(days=1),
        logical_identity="fixture-assessment:project-a",
        semantic_type="fixture-assessment",
        known_at=known_at,
        known_time_limitation=known_time_limitation,
        model_version="fixture-model-v1",
        methodology_fingerprint="methodology:fingerprint-v1:abc",
        source_record_ids=("source-record:identity-v1:abc",),
        source_versions=("source-schema-v1",),
        evidence_references=("evidence:identity-v1:abc",),
        confidence=0.75,
        missing_evidence=("secondary-source",),
        supersedes_id=supersedes_id,
        correction_reason=correction_reason,
        payload=payload or {"value": 1},
    )
