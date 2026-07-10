from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from types import TracebackType
from typing import Any, Protocol, TypeVar

from hunter.execution.run import PipelineRun
from hunter.persistence.integration.artifacts import records_for_intelligence
from hunter.persistence.integration.exceptions import (
    ArtifactPersistenceError,
    EngineManifestError,
    StalePipelineRunIdentityError,
)
from hunter.persistence.integration.lifecycle import RunLifecycle, RunLifecycleState
from hunter.persistence.integration.policies import PersistencePolicy, PipelinePersistenceSettings
from hunter.persistence.integration.snapshots import snapshot_for_run
from hunter.persistence.records import (
    EvidenceRecord,
    InsightRecord,
    IntelligenceRecord,
    ObservationRecord,
    PersistenceRecord,
    PipelineRunRecord,
    SignalRecord,
    SnapshotRecord,
)
from hunter.plugins.contracts import PipelineContext


class PersistenceEventType(StrEnum):
    RUN_PERSISTENCE_STARTED = "run_persistence_started"
    RUN_STATE_CHANGED = "run_state_changed"
    ARTIFACT_PERSISTED = "artifact_persisted"
    ARTIFACT_SKIPPED_IDEMPOTENT = "artifact_skipped_idempotent"
    IDENTITY_CONFLICT = "identity_conflict"
    TRANSACTION_COMMITTED = "transaction_committed"
    TRANSACTION_ROLLED_BACK = "transaction_rolled_back"
    PERSISTENCE_FAILED = "persistence_failed"


@dataclass(frozen=True)
class PersistenceEvent:
    event_type: PersistenceEventType
    pipeline_run_id: str
    detail: str
    record_id: str | None = None
    at: datetime | None = None


class UnitOfWorkProtocol(Protocol):
    repositories: Any | None

    def __enter__(self) -> UnitOfWorkProtocol:
        raise NotImplementedError

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        raise NotImplementedError


T = TypeVar("T")


@dataclass
class PipelinePersistenceAdapter:
    unit_of_work_factory: Callable[[], UnitOfWorkProtocol]
    settings: PipelinePersistenceSettings = PipelinePersistenceSettings()

    def run(
        self,
        context: PipelineContext,
        execute: Callable[[], T],
        *,
        engine_manifest: Any | None = None,
    ) -> T:
        if not self.settings.enabled:
            return execute()
        run = context.ensure_run(engine_manifest=engine_manifest)
        context.freeze_run_identity(engine_manifest=engine_manifest)
        self._validate_engine_manifest(context, engine_manifest=engine_manifest)
        if self.settings.policy is PersistencePolicy.RUN_DURABLE:
            return self._run_durable(context, execute, run=run, engine_manifest=engine_manifest)
        return self._run_atomic(context, execute, run=run, engine_manifest=engine_manifest)

    def persist_partial(self, context: PipelineContext, *, engine_manifest: Any | None = None) -> None:
        run = context.ensure_run(engine_manifest=engine_manifest)
        lifecycle = RunLifecycle(run).transition(RunLifecycleState.RUNNING, at=context.clock.now())
        lifecycle = lifecycle.transition(RunLifecycleState.PARTIAL, at=context.clock.now())
        with self.unit_of_work_factory() as uow:
            repositories = _repositories(uow)
            repositories.pipeline_runs().save(lifecycle.to_record(created_at=context.clock.now()))

    def _run_atomic(
        self,
        context: PipelineContext,
        execute: Callable[[], T],
        *,
        run: PipelineRun,
        engine_manifest: Any | None,
    ) -> T:
        deferred_error: BaseException | None = None
        result: T | None = None
        try:
            with self.unit_of_work_factory() as uow:
                repositories = _repositories(uow)
                lifecycle = self._start_lifecycle(context, repositories, run)
                try:
                    result = execute()
                except BaseException as exc:
                    deferred_error = exc
                    failed = lifecycle.transition(
                        RunLifecycleState.FAILED,
                        at=context.clock.now(),
                        error_summary=_summary(exc),
                    )
                    self._save_run_record(context, repositories, failed.to_record(created_at=context.clock.now()))
                if deferred_error is None:
                    if not context.validate_run_identity(engine_manifest=engine_manifest):
                        raise StalePipelineRunIdentityError(
                            "PipelineRun identity-bearing inputs changed after persistence started"
                        )
                    self._validate_emitted_engines(context, engine_manifest=engine_manifest)
                    artifact_ids = self._persist_artifacts(context, repositories, run)
                    final_state = RunLifecycleState.PARTIAL if context.persistence_errors else RunLifecycleState.SUCCEEDED
                    final = lifecycle.transition(
                        final_state,
                        at=context.clock.now(),
                        warning_summary="; ".join(context.persistence_errors) or None,
                    )
                    self._save_run_record(context, repositories, final.to_record(created_at=context.clock.now()))
                    self._maybe_snapshot(context, repositories, run, artifact_ids, final_state)
            self._record(context, run, PersistenceEventType.TRANSACTION_COMMITTED, "pipeline persistence committed")
        except BaseException as exc:
            self._record(context, run, PersistenceEventType.TRANSACTION_ROLLED_BACK, "pipeline persistence rolled back")
            self._record(context, run, PersistenceEventType.PERSISTENCE_FAILED, _summary(exc))
            raise
        if deferred_error is not None:
            raise deferred_error
        return result  # type: ignore[return-value]

    def _run_durable(
        self,
        context: PipelineContext,
        execute: Callable[[], T],
        *,
        run: PipelineRun,
        engine_manifest: Any | None,
    ) -> T:
        try:
            return self._run_atomic(context, execute, run=run, engine_manifest=engine_manifest)
        except ArtifactPersistenceError as exc:
            context.persistence_errors.append(_summary(exc))
            with self.unit_of_work_factory() as uow:
                repositories = _repositories(uow)
                lifecycle = RunLifecycle(run)
                self._save_run_record(context, repositories, lifecycle.to_record(created_at=context.clock.now()))
                lifecycle = lifecycle.transition(RunLifecycleState.RUNNING, at=context.clock.now())
                self._save_run_record(context, repositories, lifecycle.to_record(created_at=context.clock.now()))
                failed = lifecycle.transition(
                    RunLifecycleState.FAILED,
                    at=context.clock.now(),
                    error_summary=_summary(exc),
                )
                self._save_run_record(context, repositories, failed.to_record(created_at=context.clock.now()))
            self._record(context, run, PersistenceEventType.TRANSACTION_COMMITTED, "durable failure state committed")
            raise

    def _start_lifecycle(self, context: PipelineContext, repositories: Any, run: PipelineRun) -> RunLifecycle:
        lifecycle = RunLifecycle(run)
        self._record(context, run, PersistenceEventType.RUN_PERSISTENCE_STARTED, "pipeline persistence started")
        self._save_run_record(context, repositories, lifecycle.to_record(created_at=context.clock.now()))
        lifecycle = lifecycle.transition(RunLifecycleState.RUNNING, at=context.clock.now())
        self._save_run_record(context, repositories, lifecycle.to_record(created_at=context.clock.now()))
        return lifecycle

    def _save_run_record(self, context: PipelineContext, repositories: Any, record: PipelineRunRecord) -> None:
        repositories.pipeline_runs().save(record)
        run_id = str(record.metadata.get("pipeline_run_id", record.id))
        self._record(
            context,
            context.run,
            PersistenceEventType.RUN_STATE_CHANGED,
            f"run state persisted: {record.status}",
            record_id=record.id,
            pipeline_run_id=run_id,
        )

    def _persist_artifacts(self, context: PipelineContext, repositories: Any, run: PipelineRun) -> tuple[str, ...]:
        artifact_ids: list[str] = []
        try:
            for intelligence in context.intelligence:
                for record in records_for_intelligence(
                    intelligence,
                    pipeline_run_id=run.run_id,
                    created_at=context.clock.now(),
                    effective_at=run.effective_at,
                ):
                    if not self._should_persist(record):
                        continue
                    self._save_artifact(context, repositories, record)
                    artifact_ids.append(record.id)
            context.persisted_artifact_ids.extend(artifact_ids)
        except BaseException as exc:
            self._record(context, run, PersistenceEventType.IDENTITY_CONFLICT, _summary(exc))
            raise ArtifactPersistenceError(_summary(exc)) from exc
        return tuple(artifact_ids)

    def _save_artifact(self, context: PipelineContext, repositories: Any, record: PersistenceRecord) -> None:
        repository = _repository_for_record(repositories, record)
        exists = repository.exists(record.id)
        repository.save(record)
        event_type = PersistenceEventType.ARTIFACT_SKIPPED_IDEMPOTENT if exists else PersistenceEventType.ARTIFACT_PERSISTED
        self._record(
            context,
            context.run,
            event_type,
            f"{record.record_type} persisted",
            record_id=record.id,
        )

    def _maybe_snapshot(
        self,
        context: PipelineContext,
        repositories: Any,
        run: PipelineRun,
        artifact_ids: tuple[str, ...],
        final_state: RunLifecycleState,
    ) -> None:
        if not artifact_ids:
            return
        if final_state is RunLifecycleState.SUCCEEDED and not self.settings.snapshots.create_on_success:
            return
        if final_state is RunLifecycleState.PARTIAL and not self.settings.snapshots.create_on_partial:
            return
        snapshot = snapshot_for_run(
            run,
            artifact_ids=artifact_ids,
            created_at=context.clock.now(),
            snapshot_type=self.settings.snapshots.snapshot_type,
        )
        repositories.snapshots().save(snapshot)
        self._record(context, run, PersistenceEventType.ARTIFACT_PERSISTED, "snapshot persisted", record_id=snapshot.id)

    def _should_persist(self, record: PersistenceRecord) -> bool:
        settings = self.settings.artifacts
        return not (
            isinstance(record, EvidenceRecord)
            and not settings.persist_evidence
            or isinstance(record, SignalRecord)
            and not settings.persist_signals
            or isinstance(record, ObservationRecord)
            and not settings.persist_observations
            or isinstance(record, InsightRecord)
            and not settings.persist_insights
            or isinstance(record, IntelligenceRecord)
            and not settings.persist_intelligence
        )

    def _validate_engine_manifest(self, context: PipelineContext, *, engine_manifest: Any | None) -> None:
        if not self.settings.enforce_engine_manifest or engine_manifest is not None:
            return
        msg = "Persistence-enabled pipeline execution requires a declared engine manifest"
        context.persistence_errors.append(msg)
        raise EngineManifestError(msg)

    def _validate_emitted_engines(self, context: PipelineContext, *, engine_manifest: Any | None) -> None:
        if not self.settings.enforce_engine_manifest or not isinstance(engine_manifest, dict):
            return
        declared = {str(item.get("id")) for item in engine_manifest.get("engines", ()) if isinstance(item, dict)}
        plugins = {str(item.get("id")) for item in engine_manifest.get("plugins", ()) if isinstance(item, dict)}
        if not declared and plugins:
            return
        emitted = {item.engine for item in context.intelligence}
        undeclared = emitted - declared
        if undeclared:
            msg = f"Emitted intelligence from undeclared engines: {', '.join(sorted(undeclared))}"
            context.persistence_errors.append(msg)
            raise EngineManifestError(msg)

    def _record(
        self,
        context: PipelineContext,
        run: PipelineRun | None,
        event_type: PersistenceEventType,
        detail: str,
        *,
        record_id: str | None = None,
        pipeline_run_id: str | None = None,
    ) -> None:
        context.persistence_events.append(
            PersistenceEvent(
                event_type=event_type,
                pipeline_run_id=pipeline_run_id or (run.run_id if run is not None else "unknown"),
                detail=detail,
                record_id=record_id,
                at=context.clock.now(),
            )
        )


def _repositories(uow: UnitOfWorkProtocol) -> Any:
    if uow.repositories is None:
        msg = "UnitOfWork did not expose repositories"
        raise RuntimeError(msg)
    return uow.repositories


def _repository_for_record(repositories: Any, record: PersistenceRecord) -> Any:
    if isinstance(record, EvidenceRecord):
        return repositories.evidence()
    if isinstance(record, SignalRecord):
        return repositories.signals()
    if isinstance(record, ObservationRecord):
        return repositories.observations()
    if isinstance(record, InsightRecord):
        return repositories.insights()
    if isinstance(record, IntelligenceRecord):
        return repositories.intelligence()
    if isinstance(record, SnapshotRecord):
        return repositories.snapshots()
    msg = f"Unsupported artifact record type: {record.record_type}"
    raise TypeError(msg)


def _summary(exc: BaseException) -> str:
    return f"{exc.__class__.__name__}: {exc}"
