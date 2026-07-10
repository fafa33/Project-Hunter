from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import inspect

from hunter.persistence.models import HistorySpec, QueryFilter, QuerySpec, SnapshotSpec
from hunter.persistence.records import (
    ConfigurationRecord,
    EngineManifestRecord,
    EvidenceRecord,
    InsightRecord,
    IntelligenceRecord,
    ObservationRecord,
    PipelineRunRecord,
    SignalRecord,
    SnapshotRecord,
)
from hunter.persistence.sql import (
    RepositoryFactory,
    SessionFactory,
    SessionManager,
    UnitOfWork,
    create_schema,
    create_sqlite_engine,
)
from hunter.persistence.sql.base import PersistenceRecordModel
from hunter.persistence.sql.exceptions import PersistenceIdentityConflictError

NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


@pytest.fixture
def session_factory() -> SessionFactory:
    engine = create_sqlite_engine()
    create_schema(engine)
    return SessionFactory(engine)


def test_session_lifecycle_context_manager_commits(session_factory: SessionFactory) -> None:
    manager = SessionManager(session_factory)

    with manager.session() as session:
        RepositoryFactory(session).pipeline_runs().save(pipeline_run_record())

    with session_factory.create() as session:
        assert RepositoryFactory(session).pipeline_runs().exists("pipeline-run:identity-v1:abc")


def test_session_lifecycle_context_manager_rolls_back(session_factory: SessionFactory) -> None:
    manager = SessionManager(session_factory)

    with pytest.raises(RuntimeError), manager.session() as session:
        RepositoryFactory(session).pipeline_runs().save(pipeline_run_record())
        raise RuntimeError("force rollback")

    with session_factory.create() as session:
        assert not RepositoryFactory(session).pipeline_runs().exists("pipeline-run:identity-v1:abc")


def test_scoped_session_support(session_factory: SessionFactory) -> None:
    scoped = session_factory.scoped()
    try:
        session = scoped()
        RepositoryFactory(session).pipeline_runs().save(pipeline_run_record())
        session.commit()
    finally:
        scoped.remove()

    with session_factory.create() as session:
        assert RepositoryFactory(session).pipeline_runs().exists("pipeline-run:identity-v1:abc")


def test_repository_factory_creates_all_repositories(session_factory: SessionFactory) -> None:
    with session_factory.create() as session:
        factory = RepositoryFactory(session)

        assert factory.pipeline_runs().record_type == "pipeline-run"
        assert factory.evidence().record_type == "evidence"
        assert factory.signals().record_type == "signal"
        assert factory.observations().record_type == "observation"
        assert factory.insights().record_type == "insight"
        assert factory.intelligence().record_type == "intelligence"
        assert factory.snapshots().record_type == "snapshot"
        assert factory.configurations().record_type == "configuration"
        assert factory.engine_manifests().record_type == "engine-manifest"


def test_unit_of_work_commits_and_exposes_repositories(session_factory: SessionFactory) -> None:
    with UnitOfWork(session_factory) as uow:
        assert uow.repositories is not None
        uow.repositories.pipeline_runs().save(pipeline_run_record())

    with session_factory.create() as session:
        assert RepositoryFactory(session).pipeline_runs().load("pipeline-run:identity-v1:abc") == pipeline_run_record()


def test_unit_of_work_rolls_back_on_error(session_factory: SessionFactory) -> None:
    with pytest.raises(RuntimeError):
        with UnitOfWork(session_factory) as uow:
            assert uow.repositories is not None
            uow.repositories.pipeline_runs().save(pipeline_run_record())
            raise RuntimeError("force rollback")

    with session_factory.create() as session:
        assert RepositoryFactory(session).pipeline_runs().load("pipeline-run:identity-v1:abc") is None


def test_orm_mapping_stores_serialized_record_in_internal_table(session_factory: SessionFactory) -> None:
    with session_factory.create() as session:
        RepositoryFactory(session).pipeline_runs().save(pipeline_run_record())
        session.commit()
        model = session.get(PersistenceRecordModel, "pipeline-run:identity-v1:abc")

    assert model is not None
    assert model.record_type == "pipeline-run"
    assert "pipeline-run:identity-v1:abc" in model.payload
    assert model.canonical_hash
    assert "persistence_records" in inspect(session_factory.engine).get_table_names()


def test_idempotent_save_identity_preservation_and_conflict_rejection(session_factory: SessionFactory) -> None:
    with session_factory.create() as session:
        repo = RepositoryFactory(session).evidence()
        first = evidence_record(raw_data={"value": 1})

        assert repo.save(first) == first
        assert repo.save(first) == first
        with pytest.raises(PersistenceIdentityConflictError):
            repo.save(evidence_record(raw_data={"value": 2}))


def test_no_physical_delete(session_factory: SessionFactory) -> None:
    with session_factory.create() as session:
        repo = RepositoryFactory(session).pipeline_runs()
        repo.save(pipeline_run_record())
        repo.delete("pipeline-run:identity-v1:abc")
        session.commit()
        model = session.get(PersistenceRecordModel, "pipeline-run:identity-v1:abc")

    assert model is not None
    assert model.deleted_at is not None


def test_query_latest_history_and_load_many(session_factory: SessionFactory) -> None:
    older = pipeline_run_record(record_id="pipeline-run:identity-v1:old", effective_at=NOW - timedelta(days=1))
    newer = pipeline_run_record(record_id="pipeline-run:identity-v1:new", effective_at=NOW)

    with session_factory.create() as session:
        repo = RepositoryFactory(session).pipeline_runs()
        repo.save_many((older, newer))
        session.commit()

        results = repo.query(QuerySpec(record_kind="pipeline-run", filters=(QueryFilter("target_id", "bitcoin"),)))
        latest = repo.latest(QuerySpec(record_kind="pipeline-run", filters=(QueryFilter("target_id", "bitcoin"),)))
        history = repo.history(HistorySpec(identity="pipeline-run:identity-v1:old"))
        loaded = repo.load_many(("pipeline-run:identity-v1:new", "missing"))

    assert [record.id for record in results] == ["pipeline-run:identity-v1:new", "pipeline-run:identity-v1:old"]
    assert latest == newer
    assert history == (older,)
    assert loaded == (newer,)


def test_snapshot_retrieval(session_factory: SessionFactory) -> None:
    old_snapshot = snapshot_record(record_id="snapshot:identity-v1:old", effective_at=NOW - timedelta(days=1))
    new_snapshot = snapshot_record(record_id="snapshot:identity-v1:new", effective_at=NOW)

    with session_factory.create() as session:
        repo = RepositoryFactory(session).snapshots()
        repo.save_many((old_snapshot, new_snapshot))
        session.commit()

        selected = repo.snapshot(SnapshotSpec(target_id="bitcoin", snapshot_type="daily", effective_at=NOW))

    assert selected == new_snapshot


def test_all_requested_concrete_repositories_persist_records(session_factory: SessionFactory) -> None:
    with session_factory.create() as session:
        factory = RepositoryFactory(session)

        assert factory.pipeline_runs().save(pipeline_run_record()).id
        assert factory.evidence().save(evidence_record()).id
        assert factory.signals().save(signal_record()).id
        assert factory.observations().save(observation_record()).id
        assert factory.insights().save(insight_record()).id
        assert factory.intelligence().save(intelligence_record()).id
        assert factory.snapshots().save(snapshot_record()).id
        assert factory.configurations().save(configuration_record()).id
        assert factory.engine_manifests().save(engine_manifest_record()).id


def pipeline_run_record(
    *,
    record_id: str = "pipeline-run:identity-v1:abc",
    effective_at: datetime = NOW,
) -> PipelineRunRecord:
    return PipelineRunRecord(
        id=record_id,
        created_at=NOW,
        effective_at=effective_at,
        run_type="test",
        target_id="bitcoin",
        target_type="project",
        configuration_fingerprint="configuration:fingerprint-v1:abc",
        input_fingerprint="input:fingerprint-v1:abc",
        engine_manifest_fingerprint="engine-manifest:fingerprint-v1:abc",
        status="succeeded",
        requested_at=NOW,
    )


def evidence_record(
    *,
    raw_data: dict[str, int] | None = None,
) -> EvidenceRecord:
    return EvidenceRecord(
        id="evidence:identity-v1:abc",
        created_at=NOW,
        effective_at=NOW,
        pipeline_run_id="pipeline-run:identity-v1:abc",
        source="fixture",
        reference="fixture://evidence",
        collected_at=NOW,
        reliability=0.9,
        freshness=0.8,
        raw_data=raw_data or {"value": 1},
    )


def signal_record() -> SignalRecord:
    return SignalRecord(
        id="signal:identity-v1:abc",
        created_at=NOW,
        effective_at=NOW,
        pipeline_run_id="pipeline-run:identity-v1:abc",
        intelligence_id="intelligence:identity-v1:abc",
        engine_id="macro-intelligence",
        project="bitcoin",
        timestamp=NOW,
        category="macro",
        strength=0.7,
        confidence=0.8,
        severity=0.2,
    )


def observation_record() -> ObservationRecord:
    return ObservationRecord(
        id="observation:identity-v1:abc",
        created_at=NOW,
        effective_at=NOW,
        pipeline_run_id="pipeline-run:identity-v1:abc",
        intelligence_id="intelligence:identity-v1:abc",
        engine_id="macro-intelligence",
        project="bitcoin",
        description="Observed evidence.",
        evidence_ids=("evidence:identity-v1:abc",),
        importance=0.7,
    )


def insight_record() -> InsightRecord:
    return InsightRecord(
        id="insight:identity-v1:abc",
        created_at=NOW,
        effective_at=NOW,
        pipeline_run_id="pipeline-run:identity-v1:abc",
        intelligence_id="intelligence:identity-v1:abc",
        title="Insight",
        explanation="Evidence supports the insight.",
        observation_ids=("observation:identity-v1:abc",),
        confidence=0.8,
        priority=0.6,
    )


def intelligence_record() -> IntelligenceRecord:
    return IntelligenceRecord(
        id="intelligence:identity-v1:abc",
        created_at=NOW,
        effective_at=NOW,
        pipeline_run_id="pipeline-run:identity-v1:abc",
        project="bitcoin",
        engine_id="macro-intelligence",
        generated_at=NOW,
        signal_ids=("signal:identity-v1:abc",),
        evidence_ids=("evidence:identity-v1:abc",),
        observation_ids=("observation:identity-v1:abc",),
        insight_ids=("insight:identity-v1:abc",),
        confidence={"score": 0.8},
    )


def snapshot_record(
    *,
    record_id: str = "snapshot:identity-v1:abc",
    effective_at: datetime = NOW,
) -> SnapshotRecord:
    return SnapshotRecord(
        id=record_id,
        created_at=NOW,
        effective_at=effective_at,
        snapshot_type="daily",
        target_id="bitcoin",
        record_ids=("intelligence:identity-v1:abc",),
        payload={"count": 1},
    )


def configuration_record() -> ConfigurationRecord:
    return ConfigurationRecord(
        id="configuration:fingerprint-v1:abc",
        created_at=NOW,
        effective_at=NOW,
        configuration_fingerprint="configuration:fingerprint-v1:abc",
        configuration_type="engine",
        payload={"threshold": 1},
    )


def engine_manifest_record() -> EngineManifestRecord:
    return EngineManifestRecord(
        id="engine-manifest:fingerprint-v1:abc",
        created_at=NOW,
        effective_at=NOW,
        engine_manifest_fingerprint="engine-manifest:fingerprint-v1:abc",
        engines=({"id": "macro-intelligence", "version": "1.0.0"},),
    )
