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
from hunter.persistence.integration.lifecycle import OperationalAttempt, RunLifecycle, RunLifecycleState
from hunter.persistence.integration.policies import PersistencePolicy, PipelinePersistenceSettings
from hunter.persistence.integration.snapshots import snapshot_for_run
from hunter.persistence.models import QueryFilter, QuerySpec
from hunter.persistence.records import (
    EvidenceRecord,
    FusedIntelligenceRecord,
    InsightRecord,
    IntelligenceRecord,
    ObservationRecord,
    OperationalAttemptRecord,
    OpportunityTimingAssessmentRecord,
    OpportunityTimingSnapshotRecord,
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
class PersistenceIssue:
    code: str
    summary: str
    record_id: str | None = None


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
        if not context.validate_run_identity(engine_manifest=engine_manifest):
            raise StalePipelineRunIdentityError("Pre-existing PipelineRun does not match current context identity")
        context.freeze_run_identity(engine_manifest=engine_manifest)
        self._validate_engine_manifest(context, engine_manifest=engine_manifest)
        if self.settings.policy is PersistencePolicy.RUN_DURABLE:
            return self._run_durable(context, execute, run=run, engine_manifest=engine_manifest)
        return self._run_atomic(context, execute, run=run, engine_manifest=engine_manifest)

    def persist_partial(self, context: PipelineContext, *, engine_manifest: Any | None = None) -> None:
        run = context.ensure_run(engine_manifest=engine_manifest)
        with self.unit_of_work_factory() as uow:
            repositories = _repositories(uow)
            attempt = self._new_attempt(context, repositories, run)
            attempt = attempt.transition(RunLifecycleState.RUNNING, at=context.clock.now())
            attempt = attempt.transition(RunLifecycleState.PARTIAL, at=context.clock.now())
            self._save_attempt_record(context, repositories, attempt.to_record(created_at=context.clock.now(), effective_at=run.effective_at))

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
                attempt = self._start_attempt(context, repositories, run)
                try:
                    result = execute()
                except BaseException as exc:
                    deferred_error = exc
                    failed = attempt.transition(
                        RunLifecycleState.FAILED,
                        at=context.clock.now(),
                        error_summary=_summary(exc),
                    )
                    self._save_attempt_record(
                        context,
                        repositories,
                        failed.to_record(created_at=context.clock.now(), effective_at=run.effective_at),
                    )
                if deferred_error is None:
                    if not context.validate_run_identity(engine_manifest=engine_manifest):
                        raise StalePipelineRunIdentityError(
                            "PipelineRun identity-bearing inputs changed after persistence started"
                        )
                    self._validate_emitted_engines(context, engine_manifest=engine_manifest)
                    artifact_ids = self._persist_artifacts(context, repositories, run, engine_manifest=engine_manifest)
                    issues = _issues(context)
                    final_state = RunLifecycleState.PARTIAL if issues else RunLifecycleState.SUCCEEDED
                    final = attempt.transition(
                        final_state,
                        at=context.clock.now(),
                        warning_summary="; ".join(issue.summary for issue in issues) or None,
                    )
                    self._save_attempt_record(
                        context,
                        repositories,
                        final.to_record(created_at=context.clock.now(), effective_at=run.effective_at),
                    )
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
            _add_issue(context, "artifact_persistence_failed", _summary(exc))
            with self.unit_of_work_factory() as uow:
                repositories = _repositories(uow)
                repositories.pipeline_runs().save(RunLifecycle(run).to_record(created_at=context.clock.now()))
                attempt = self._new_attempt(context, repositories, run)
                self._save_attempt_record(
                    context,
                    repositories,
                    attempt.to_record(created_at=context.clock.now(), effective_at=run.effective_at),
                )
                attempt = attempt.transition(RunLifecycleState.RUNNING, at=context.clock.now())
                self._save_attempt_record(
                    context,
                    repositories,
                    attempt.to_record(created_at=context.clock.now(), effective_at=run.effective_at),
                )
                failed = attempt.transition(
                    RunLifecycleState.FAILED,
                    at=context.clock.now(),
                    error_summary=_summary(exc),
                )
                self._save_attempt_record(
                    context,
                    repositories,
                    failed.to_record(created_at=context.clock.now(), effective_at=run.effective_at),
                )
            self._record(context, run, PersistenceEventType.TRANSACTION_COMMITTED, "durable failure state committed")
            raise

    def _start_attempt(self, context: PipelineContext, repositories: Any, run: PipelineRun) -> OperationalAttempt:
        repositories.pipeline_runs().save(RunLifecycle(run).to_record(created_at=context.clock.now()))
        attempt = self._new_attempt(context, repositories, run)
        self._record(context, run, PersistenceEventType.RUN_PERSISTENCE_STARTED, "pipeline persistence started")
        self._save_attempt_record(
            context,
            repositories,
            attempt.to_record(created_at=context.clock.now(), effective_at=run.effective_at),
        )
        attempt = attempt.transition(RunLifecycleState.RUNNING, at=context.clock.now())
        self._save_attempt_record(
            context,
            repositories,
            attempt.to_record(created_at=context.clock.now(), effective_at=run.effective_at),
        )
        return attempt

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

    def _save_attempt_record(self, context: PipelineContext, repositories: Any, record: OperationalAttemptRecord) -> None:
        repositories.operational_attempts().save(record)
        self._record(
            context,
            context.run,
            PersistenceEventType.RUN_STATE_CHANGED,
            f"attempt state persisted: {record.status}",
            record_id=record.id,
            pipeline_run_id=record.run_id,
        )

    def _new_attempt(self, context: PipelineContext, repositories: Any, run: PipelineRun) -> OperationalAttempt:
        attempt_number = _next_attempt_number(repositories, run.run_id)
        return OperationalAttempt.create(
            run_id=run.run_id,
            attempt_number=attempt_number,
            requested_at=context.clock.now(),
            metadata={"pipeline_run_id": run.run_id},
        )

    def _persist_artifacts(
        self,
        context: PipelineContext,
        repositories: Any,
        run: PipelineRun,
        *,
        engine_manifest: Any | None,
    ) -> tuple[str, ...]:
        artifact_ids: list[str] = []
        try:
            for intelligence in context.intelligence:
                for record in records_for_intelligence(
                    intelligence,
                    pipeline_run_id=run.run_id,
                    created_at=context.clock.now(),
                    effective_at=run.effective_at,
                    declaration_metadata=_declaration_metadata(intelligence.engine, intelligence.metadata.as_dict(), engine_manifest),
                ):
                    if not self._should_persist(record):
                        continue
                    self._save_artifact(context, repositories, record)
                    artifact_ids.append(record.id)
            fused_records: list[FusedIntelligenceRecord] = []
            for fused in context.fused_intelligence:
                record = _record_for_fused_intelligence(
                    fused,
                    pipeline_run_id=run.run_id,
                    created_at=context.clock.now(),
                )
                self._save_artifact(context, repositories, record)
                artifact_ids.append(record.id)
                fused_records.append(record)
            if fused_records:
                context.set("persisted_fused_intelligence", tuple(fused_records))
            timing_engine = context.opportunity_timing_engine
            timing_target = context.opportunity_timing_target
            if timing_engine is not None and timing_target is not None and fused_records:
                assessment = timing_engine.assess(tuple(fused_records), timing_target)
                context.opportunity_timing.append(assessment)
            for assessment in context.opportunity_timing:
                record = _record_for_opportunity_timing(
                    assessment,
                    pipeline_run_id=run.run_id,
                    created_at=context.clock.now(),
                )
                snapshot = _snapshot_for_opportunity_timing(assessment, created_at=context.clock.now())
                self._save_artifact(context, repositories, record)
                self._save_artifact(context, repositories, snapshot)
                artifact_ids.extend((record.id, snapshot.id))
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
        _add_issue(context, "missing_engine_manifest", msg)
        raise EngineManifestError(msg)

    def _validate_emitted_engines(self, context: PipelineContext, *, engine_manifest: Any | None) -> None:
        if not self.settings.enforce_engine_manifest or not isinstance(engine_manifest, dict):
            return
        violations = [
            _manifest_violation(item.engine, item.metadata.as_dict(), engine_manifest)
            for item in context.intelligence
        ]
        violations = [item for item in violations if item is not None]
        if violations:
            msg = "; ".join(violations)
            _add_issue(context, "manifest_violation", msg)
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
    if isinstance(record, FusedIntelligenceRecord):
        return repositories.fused_intelligence()
    if isinstance(record, OpportunityTimingAssessmentRecord):
        return repositories.opportunity_timing_assessments()
    if isinstance(record, OpportunityTimingSnapshotRecord):
        return repositories.opportunity_timing_snapshots()
    if isinstance(record, SnapshotRecord):
        return repositories.snapshots()
    if isinstance(record, OperationalAttemptRecord):
        return repositories.operational_attempts()
    msg = f"Unsupported artifact record type: {record.record_type}"
    raise TypeError(msg)


def _record_for_fused_intelligence(fused: Any, *, pipeline_run_id: str, created_at: datetime) -> FusedIntelligenceRecord:
    from hunter.intelligence.fusion import FusedIntelligence, fused_intelligence_to_record

    if not isinstance(fused, FusedIntelligence):
        msg = f"Unsupported fused intelligence type: {fused.__class__.__name__}"
        raise TypeError(msg)
    return fused_intelligence_to_record(fused, pipeline_run_id=pipeline_run_id, created_at=created_at)


def _record_for_opportunity_timing(assessment: Any, *, pipeline_run_id: str, created_at: datetime) -> OpportunityTimingAssessmentRecord:
    from hunter.opportunity import OpportunityTimingAssessment, opportunity_assessment_to_record

    if not isinstance(assessment, OpportunityTimingAssessment):
        msg = f"Unsupported opportunity timing type: {assessment.__class__.__name__}"
        raise TypeError(msg)
    return opportunity_assessment_to_record(assessment, pipeline_run_id=pipeline_run_id, created_at=created_at)


def _snapshot_for_opportunity_timing(assessment: Any, *, created_at: datetime) -> OpportunityTimingSnapshotRecord:
    from hunter.opportunity import OpportunityTimingAssessment, opportunity_snapshot_from_assessment

    if not isinstance(assessment, OpportunityTimingAssessment):
        msg = f"Unsupported opportunity timing type: {assessment.__class__.__name__}"
        raise TypeError(msg)
    return opportunity_snapshot_from_assessment(assessment, created_at=created_at)


def _summary(exc: BaseException) -> str:
    return f"{exc.__class__.__name__}: {exc}"


def _add_issue(context: PipelineContext, code: str, summary: str, record_id: str | None = None) -> None:
    context.persistence_errors.append(PersistenceIssue(code=code, summary=summary, record_id=record_id))


def _issues(context: PipelineContext) -> tuple[PersistenceIssue, ...]:
    normalized: list[PersistenceIssue] = []
    for item in context.persistence_errors:
        if isinstance(item, PersistenceIssue):
            normalized.append(item)
        else:
            normalized.append(PersistenceIssue(code="legacy", summary=str(item)))
    return tuple(normalized)


def _next_attempt_number(repositories: Any, run_id: str) -> int:
    attempts = repositories.operational_attempts().query(
        QuerySpec(record_kind="operational-attempt", filters=(QueryFilter("run_id", run_id),))
    )
    if not attempts:
        return 1
    return max(attempt.attempt_number for attempt in attempts) + 1


def _declaration_metadata(
    engine_id: str,
    intelligence_metadata: dict[str, str | int | float | bool | None],
    engine_manifest: Any | None,
) -> dict[str, str | int | float | bool | None]:
    metadata: dict[str, str | int | float | bool | None] = {"engine_id": engine_id}
    if isinstance(engine_manifest, dict):
        for item in engine_manifest.get("engines", ()):
            if isinstance(item, dict) and item.get("id") == engine_id:
                metadata["engine_version"] = str(item.get("version", ""))
                break
        plugin_id = intelligence_metadata.get("plugin_id")
        for item in engine_manifest.get("plugins", ()):
            if isinstance(item, dict) and item.get("id") == plugin_id:
                metadata["plugin_id"] = str(item.get("id", ""))
                metadata["plugin_version"] = str(item.get("version", ""))
                break
    return metadata


def _manifest_violation(
    engine_id: str,
    intelligence_metadata: dict[str, str | int | float | bool | None],
    engine_manifest: Any,
) -> str | None:
    declared_engines = {
        str(item.get("id")): str(item.get("version", ""))
        for item in engine_manifest.get("engines", ())
        if isinstance(item, dict)
    }
    declared_plugins = {
        str(item.get("id")): str(item.get("version", ""))
        for item in engine_manifest.get("plugins", ())
        if isinstance(item, dict)
    }
    engine_version = intelligence_metadata.get("engine_version")
    plugin_id = intelligence_metadata.get("plugin_id")
    plugin_version = intelligence_metadata.get("plugin_version")
    if engine_id not in declared_engines:
        return f"undeclared engine: {engine_id}"
    if engine_version is not None and str(engine_version) != declared_engines[engine_id]:
        return f"engine version mismatch: {engine_id}"
    if plugin_id is not None:
        if str(plugin_id) not in declared_plugins:
            return f"undeclared plugin: {plugin_id}"
        if plugin_version is not None and str(plugin_version) != declared_plugins[str(plugin_id)]:
            return f"plugin version mismatch: {plugin_id}"
    return None
