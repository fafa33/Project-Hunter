from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from hunter.execution import FixedClock
from hunter.execution.identity import fingerprint
from hunter.execution.run import PipelineRun
from hunter.intelligence import Confidence, Evidence, Insight, Intelligence, Observation, Signal
from hunter.intelligence.engines import BaseIntelligenceEngine, EngineMetadata
from hunter.persistence.integration.adapter import PersistenceEventType, PersistenceIssue, PipelinePersistenceAdapter
from hunter.persistence.integration.exceptions import (
    ArtifactPersistenceError,
    EngineManifestError,
    LifecycleTransitionError,
    StalePipelineRunIdentityError,
)
from hunter.persistence.integration.history import PipelineHistory
from hunter.persistence.integration.lifecycle import RunLifecycle, RunLifecycleState
from hunter.persistence.integration.policies import PersistencePolicy, PipelinePersistenceSettings
from hunter.persistence.integration.snapshots import snapshot_for_run
from hunter.persistence.records import EvidenceRecord
from hunter.persistence.sql import RepositoryFactory, SessionFactory, UnitOfWork, create_schema, create_sqlite_engine
from hunter.persistence.sql.exceptions import PersistenceIdentityConflictError
from hunter.pipeline import PipelineOrchestrator
from hunter.plugins.contracts import PipelineContext

NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


@pytest.fixture
def session_factory(tmp_path: Path) -> SessionFactory:
    engine = create_sqlite_engine(tmp_path / "hunter.sqlite")
    create_schema(engine)
    return SessionFactory(engine)


def test_adapter_construction_and_disabled_policy_preserves_in_memory_behavior(session_factory: SessionFactory) -> None:
    adapter = PipelinePersistenceAdapter(lambda: UnitOfWork(session_factory))
    context = PipelineContext(clock=FixedClock(NOW), values={"market-data": "available"})

    PipelineOrchestrator().run(context=context, intelligence_engines=[ExampleEngine()], persistence_adapter=adapter)

    assert len(context.intelligence) == 1
    with session_factory.create() as session:
        assert RepositoryFactory(session).pipeline_runs().query(record_query("pipeline-run")) == ()


def test_lifecycle_transitions_and_invalid_transition() -> None:
    lifecycle = RunLifecycle(run_context().ensure_run(engine_manifest=manifest()))
    running = lifecycle.transition(RunLifecycleState.RUNNING, at=NOW)
    succeeded = running.transition(RunLifecycleState.SUCCEEDED, at=NOW)

    assert succeeded.state is RunLifecycleState.SUCCEEDED
    with pytest.raises(LifecycleTransitionError):
        succeeded.transition(RunLifecycleState.FAILED, at=NOW)


def test_pipeline_persists_pending_running_success_artifacts_and_snapshot(session_factory: SessionFactory) -> None:
    context = run_persisted_pipeline(session_factory)
    assert context.run is not None

    with session_factory.create() as session:
        repositories = RepositoryFactory(session)
        history = PipelineHistory(repositories).attempt_history(context.run.run_id)
        run_record = repositories.pipeline_runs().load(context.run.run_id)
        intelligence_record = repositories.intelligence().load(context.intelligence[0].id)
        snapshot = repositories.snapshots().snapshot(snapshot_spec(context.run.target_id, context.run.effective_at))

    assert [record.status for record in history] == ["pending", "running", "succeeded"]
    assert run_record is not None
    assert run_record.status == "analytical"
    assert intelligence_record is not None
    assert intelligence_record.pipeline_run_id == context.run.run_id
    assert intelligence_record.evidence_ids == tuple(item.id for item in context.intelligence[0].evidence)
    assert intelligence_record.metadata["engine_version"] == "1.0.0"
    assert snapshot.metadata["pipeline_run_id"] == context.run.run_id
    assert {event.event_type for event in context.persistence_events} >= {
        PersistenceEventType.RUN_PERSISTENCE_STARTED,
        PersistenceEventType.RUN_STATE_CHANGED,
        PersistenceEventType.ARTIFACT_PERSISTED,
        PersistenceEventType.TRANSACTION_COMMITTED,
    }


def test_repeated_execution_is_idempotent(session_factory: SessionFactory) -> None:
    first = run_persisted_pipeline(session_factory)
    second = run_persisted_pipeline(session_factory)

    assert first.run is not None
    assert second.run is not None
    assert first.run.run_id == second.run.run_id
    with session_factory.create() as session:
        repositories = RepositoryFactory(session)
        assert len(PipelineHistory(repositories).attempt_history(first.run.run_id)) == 6
        assert repositories.intelligence().load(first.intelligence[0].id) is not None


def test_atomic_artifact_failure_rolls_back(session_factory: SessionFactory) -> None:
    context = conflict_context()
    with session_factory.create() as session:
        RepositoryFactory(session).evidence().save(conflicting_evidence_record(context))
        session.commit()

    adapter = enabled_adapter(session_factory, policy=PersistencePolicy.ATOMIC)

    with pytest.raises(ArtifactPersistenceError):
        adapter.run(context, lambda: None, engine_manifest=manifest())

    assert context.run is not None
    with session_factory.create() as session:
        repositories = RepositoryFactory(session)
        assert repositories.pipeline_runs().load(context.run.run_id) is None
        assert len(PipelineHistory(repositories).run_history(context.run.run_id)) == 0


def test_run_durable_artifact_failure_persists_failed_run(session_factory: SessionFactory) -> None:
    context = conflict_context()
    with session_factory.create() as session:
        RepositoryFactory(session).evidence().save(conflicting_evidence_record(context))
        session.commit()

    adapter = enabled_adapter(session_factory, policy=PersistencePolicy.RUN_DURABLE)

    with pytest.raises(ArtifactPersistenceError):
        adapter.run(context, lambda: None, engine_manifest=manifest())

    assert context.run is not None
    with session_factory.create() as session:
        repositories = RepositoryFactory(session)
        failed = PipelineHistory(repositories).attempt_history(context.run.run_id)[-1]
        run_record = repositories.pipeline_runs().load(context.run.run_id)

    assert run_record is not None
    assert failed.status == "failed"


def test_pipeline_failure_persists_failed_status(session_factory: SessionFactory) -> None:
    context = run_context()
    adapter = enabled_adapter(session_factory)

    with pytest.raises(RuntimeError):
        adapter.run(context, failing_execution, engine_manifest=manifest())

    assert context.run is not None
    with session_factory.create() as session:
        repositories = RepositoryFactory(session)
        run_record = repositories.pipeline_runs().load(context.run.run_id)
        failed = PipelineHistory(repositories).attempt_history(context.run.run_id)[-1]

    assert run_record is not None
    assert run_record.status == "analytical"
    assert failed.status == "failed"
    assert failed.error_summary is not None
    assert "RuntimeError" in failed.error_summary


def test_stale_pipeline_run_identity_detection(session_factory: SessionFactory) -> None:
    context = run_context()
    adapter = enabled_adapter(session_factory)

    with pytest.raises(StalePipelineRunIdentityError):
        adapter.run(context, lambda: context.set("new-input", "changed"), engine_manifest=manifest())


def test_stale_pre_existing_pipeline_run_rejected_before_persistence(session_factory: SessionFactory) -> None:
    context = run_context()
    context.run = PipelineRun.create(
        run_type="manual",
        target_id="global-crypto",
        target_type="project",
        configuration_fingerprint=fingerprint("pipeline-configuration", {}),
        input_fingerprint=fingerprint("pipeline-input", {"market-data": "stale"}),
        engine_manifest_fingerprint=fingerprint("engine-manifest", manifest()),
        requested_at=NOW,
        effective_at=NOW,
        clock=FixedClock(NOW),
    )
    adapter = enabled_adapter(session_factory)

    with pytest.raises(StalePipelineRunIdentityError):
        adapter.run(context, lambda: None, engine_manifest=manifest())

    with session_factory.create() as session:
        assert RepositoryFactory(session).operational_attempts().query(record_query("operational-attempt")) == ()


def test_engine_manifest_completeness_and_undeclared_engine_handling(session_factory: SessionFactory) -> None:
    context = run_context()
    context.emit_intelligence(sample_intelligence(engine_id="undeclared-engine"))
    adapter = enabled_adapter(session_factory)

    with pytest.raises(EngineManifestError):
        adapter.run(context, lambda: None, engine_manifest=manifest())


def test_engine_and_plugin_versions_are_preserved_in_artifact_metadata(session_factory: SessionFactory) -> None:
    context = run_context()
    context.emit_intelligence(sample_intelligence(metadata={"plugin_id": "macro-plugin"}))
    adapter = enabled_adapter(session_factory)
    declared_manifest = {
        "engines": [
            {
                "id": "macro-engine",
                "version": "1.0.0",
                "category": "macro",
                "priority": 10,
                "required_inputs": ("market-data",),
                "produced_outputs": ("macro-intelligence",),
                "capabilities": ("collect", "analyze", "generate-intelligence"),
            }
        ],
        "plugins": [{"id": "macro-plugin", "version": "2.0.0", "category": "intelligence"}],
    }

    adapter.run(context, lambda: None, engine_manifest=declared_manifest)

    with session_factory.create() as session:
        record = RepositoryFactory(session).intelligence().load(context.intelligence[0].id)

    assert record is not None
    assert record.metadata["engine_version"] == "1.0.0"
    assert record.metadata["plugin_id"] == "macro-plugin"
    assert record.metadata["plugin_version"] == "2.0.0"


def test_strict_manifest_rejects_engine_and_plugin_version_mismatch(session_factory: SessionFactory) -> None:
    adapter = enabled_adapter(session_factory)
    context = run_context()
    context.emit_intelligence(
        sample_intelligence(
            metadata={
                "engine_version": "9.9.9",
                "plugin_id": "macro-plugin",
                "plugin_version": "2.0.0",
            }
        )
    )

    with pytest.raises(EngineManifestError, match="engine version mismatch"):
        adapter.run(context, lambda: None, engine_manifest=manifest_with_plugin())

    context = run_context()
    context.emit_intelligence(
        sample_intelligence(
            metadata={
                "engine_version": "1.0.0",
                "plugin_id": "macro-plugin",
                "plugin_version": "9.9.9",
            }
        )
    )

    with pytest.raises(EngineManifestError, match="plugin version mismatch"):
        adapter.run(context, lambda: None, engine_manifest=manifest_with_plugin())


def test_partial_transition_and_snapshot_determinism(session_factory: SessionFactory) -> None:
    context = run_context()
    context.persistence_errors.append("optional engine failed")
    context.emit_intelligence(sample_intelligence())
    adapter = enabled_adapter(session_factory)

    adapter.run(context, lambda: None, engine_manifest=manifest())

    assert context.run is not None
    first = snapshot_for_run(context.run, artifact_ids=tuple(context.persisted_artifact_ids), created_at=NOW)
    second = snapshot_for_run(context.run, artifact_ids=tuple(reversed(context.persisted_artifact_ids)), created_at=NOW)
    with session_factory.create() as session:
        history = PipelineHistory(RepositoryFactory(session))
        record = RepositoryFactory(session).pipeline_runs().load(context.run.run_id)
        final_attempt = history.attempt_history(context.run.run_id)[-1]
        snapshots = PipelineHistory(RepositoryFactory(session)).snapshot_history(context.run.target_id)

    assert record is not None
    assert record.status == "analytical"
    assert final_attempt.status == "partial"
    assert first.id == second.id
    assert len(snapshots) == 1


def test_history_helpers_return_target_engine_effective_and_artifact_history(session_factory: SessionFactory) -> None:
    context = run_persisted_pipeline(session_factory)
    assert context.run is not None

    with session_factory.create() as session:
        history = PipelineHistory(RepositoryFactory(session))

        assert history.target_history(context.run.target_id)
        assert history.engine_history("macro-engine")
        assert history.effective_time_history(target_id=context.run.target_id, as_of=NOW)
        assert history.artifact_history(context.run.run_id)


def test_failed_attempt_followed_by_successful_attempt(session_factory: SessionFactory) -> None:
    context = run_context()
    adapter = enabled_adapter(session_factory)

    with pytest.raises(RuntimeError):
        adapter.run(context, failing_execution, engine_manifest=manifest())
    context.intelligence.clear()
    PipelineOrchestrator().run(context=context, intelligence_engines=[ExampleEngine()], persistence_adapter=adapter)

    assert context.run is not None
    with session_factory.create() as session:
        attempts = PipelineHistory(RepositoryFactory(session)).attempt_history(context.run.run_id)

    assert [attempt.attempt_number for attempt in attempts] == [1, 1, 1, 2, 2, 2]
    assert [attempt.status for attempt in attempts] == [
        "pending",
        "running",
        "failed",
        "pending",
        "running",
        "succeeded",
    ]


def test_partial_attempt_followed_by_successful_attempt(session_factory: SessionFactory) -> None:
    context = run_context()
    context.persistence_errors.append("optional failure")
    context.emit_intelligence(sample_intelligence())
    adapter = enabled_adapter(session_factory)

    adapter.run(context, lambda: None, engine_manifest=manifest())
    context.persistence_errors.clear()
    context.intelligence.clear()
    PipelineOrchestrator().run(context=context, intelligence_engines=[ExampleEngine()], persistence_adapter=adapter)

    assert context.run is not None
    with session_factory.create() as session:
        attempts = PipelineHistory(RepositoryFactory(session)).attempt_history(context.run.run_id)

    assert [attempt.status for attempt in attempts] == [
        "pending",
        "running",
        "partial",
        "pending",
        "running",
        "succeeded",
    ]


def test_typed_persistence_issue_marks_partial_attempt(session_factory: SessionFactory) -> None:
    context = run_context()
    context.persistence_errors.append(PersistenceIssue(code="optional", summary="optional engine failed"))
    context.emit_intelligence(sample_intelligence())

    enabled_adapter(session_factory).run(context, lambda: None, engine_manifest=manifest())

    assert context.run is not None
    with session_factory.create() as session:
        final_attempt = PipelineHistory(RepositoryFactory(session)).attempt_history(context.run.run_id)[-1]

    assert final_attempt.status == "partial"
    assert final_attempt.warning_summary == "optional engine failed"


def test_operational_timestamps_do_not_alter_pipeline_run_identity() -> None:
    first = PipelineRun.create(
        run_type="manual",
        target_id="bitcoin",
        target_type="project",
        configuration_fingerprint="configuration:fingerprint-v1:abc",
        input_fingerprint="input:fingerprint-v1:abc",
        engine_manifest_fingerprint="engine-manifest:fingerprint-v1:abc",
        effective_at=NOW,
        requested_at=NOW,
        clock=FixedClock(NOW),
    )
    second = PipelineRun.create(
        run_type="manual",
        target_id="bitcoin",
        target_type="project",
        configuration_fingerprint="configuration:fingerprint-v1:abc",
        input_fingerprint="input:fingerprint-v1:abc",
        engine_manifest_fingerprint="engine-manifest:fingerprint-v1:abc",
        effective_at=NOW,
        requested_at=datetime(2026, 1, 2, 4, 4, 5, tzinfo=UTC),
        clock=FixedClock(NOW),
    )

    assert first.run_id == second.run_id


def test_attempt_idempotence_and_identity_conflict_handling(session_factory: SessionFactory) -> None:
    context = run_persisted_pipeline(session_factory)
    assert context.run is not None
    with session_factory.create() as session:
        repo = RepositoryFactory(session).operational_attempts()
        first = PipelineHistory(RepositoryFactory(session)).attempt_history(context.run.run_id)[0]
        repo.save(first)
        conflicting = type(first)(
            **{
                **first.serializable_fields(),
                "metadata": {"changed": "true"},
            }
        )
        with pytest.raises(PersistenceIdentityConflictError):
            repo.save(conflicting)


def test_no_sqlalchemy_leakage_outside_persistence_source() -> None:
    violations = []
    for path in Path("src").rglob("*.py"):
        if "src/hunter/persistence/" in path.as_posix():
            continue
        text = path.read_text()
        if "sqlalchemy" in text:
            violations.append(path.as_posix())

    assert violations == []


def run_persisted_pipeline(session_factory: SessionFactory) -> PipelineContext:
    context = run_context()
    PipelineOrchestrator().run(
        context=context,
        intelligence_engines=[ExampleEngine()],
        persistence_adapter=enabled_adapter(session_factory),
    )
    return context


def run_context() -> PipelineContext:
    return PipelineContext(clock=FixedClock(NOW), values={"market-data": "available"})


def enabled_adapter(
    session_factory: SessionFactory,
    *,
    policy: PersistencePolicy = PersistencePolicy.ATOMIC,
) -> PipelinePersistenceAdapter:
    return PipelinePersistenceAdapter(
        lambda: UnitOfWork(session_factory),
        PipelinePersistenceSettings(enabled=True, policy=policy),
    )


def manifest() -> dict[str, object]:
    return {
        "engines": [
            {
                "id": "macro-engine",
                "version": "1.0.0",
                "category": "macro",
                "priority": 10,
                "required_inputs": ("market-data",),
                "produced_outputs": ("macro-intelligence",),
                "capabilities": ("collect", "analyze", "generate-intelligence"),
            }
        ],
        "plugins": [],
    }


def manifest_with_plugin() -> dict[str, object]:
    data = manifest()
    data["plugins"] = [{"id": "macro-plugin", "version": "2.0.0", "category": "intelligence"}]
    return data


def record_query(kind: str):
    from hunter.persistence.models import QuerySpec

    return QuerySpec(record_kind=kind)


def snapshot_spec(target_id: str, effective_at: datetime):
    from hunter.persistence.models import SnapshotSpec

    return SnapshotSpec(target_id=target_id, snapshot_type="pipeline-run", effective_at=effective_at)


def failing_execution() -> None:
    raise RuntimeError("pipeline failed")


def conflict_context() -> PipelineContext:
    context = run_context()
    context.ensure_run(engine_manifest=manifest())
    context.emit_intelligence(sample_intelligence(raw_data={"value": 1}))
    return context


def conflicting_evidence_record(context: PipelineContext) -> EvidenceRecord:
    assert context.run is not None
    evidence = context.intelligence[0].evidence[0]
    return EvidenceRecord(
        id=evidence.id,
        created_at=NOW,
        effective_at=context.run.effective_at,
        pipeline_run_id=context.run.run_id,
        source=evidence.source,
        reference=evidence.reference,
        collected_at=evidence.collected_at,
        reliability=evidence.reliability,
        freshness=evidence.freshness,
        raw_data={"value": 999},
    )


@dataclass
class ExampleEngine(BaseIntelligenceEngine):
    metadata: EngineMetadata = EngineMetadata(
        id="macro-engine",
        name="Macro Engine",
        category="macro",
        version="1.0.0",
        priority=10,
        required_inputs=("market-data",),
        produced_outputs=("macro-intelligence",),
        capabilities=("collect", "analyze", "generate-intelligence"),
    )

    def validate(self, context: PipelineContext) -> None:
        if "market-data" not in context.values:
            context.set("market-data", "available")

    def collect(self, context: PipelineContext) -> dict[str, Any]:
        return {"input": context.get("market-data")}

    def analyze(self, context: PipelineContext, collected: Any) -> dict[str, Any]:
        return {"collected": collected}

    def generate_intelligence(self, context: PipelineContext, analysis: Any) -> Intelligence:
        return sample_intelligence()

    def health_check(self) -> bool:
        return True


def sample_intelligence(
    *,
    engine_id: str = "macro-engine",
    raw_data: dict[str, int] | None = None,
    metadata: dict[str, str] | None = None,
) -> Intelligence:
    evidence = Evidence(
        id=f"{engine_id}-evidence",
        source="fixture",
        collected_at=NOW,
        reliability=0.9,
        freshness=0.8,
        reference="fixture://macro",
        raw_data=raw_data or {"value": 1},
    )
    observation = Observation(
        id=f"{engine_id}-observation",
        engine=engine_id,
        project="bitcoin",
        description="Observed fixture evidence.",
        evidence=(evidence,),
        importance=0.7,
    )
    insight = Insight(
        id=f"{engine_id}-insight",
        title="Fixture insight",
        explanation="Evidence supports this fixture insight.",
        supporting_observations=(observation,),
        confidence=0.8,
        priority=0.6,
    )
    return Intelligence(
        id=f"{engine_id}-intelligence",
        project="bitcoin",
        engine=engine_id,
        signals=(
            Signal(
                id=f"{engine_id}-signal",
                source=engine_id,
                timestamp=NOW,
                category="macro",
                strength=0.7,
                confidence=0.8,
                severity=0.2,
            ),
        ),
        evidence=(evidence,),
        observations=(observation,),
        insights=(insight,),
        confidence=Confidence.calculate(
            completeness=0.9,
            evidence_quality=0.8,
            freshness=0.7,
            uncertainty=0.2,
        ),
        generated_at=NOW,
        metadata=metadata or {},
    )
