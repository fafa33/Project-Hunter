from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum

from hunter.execution.hashing import stable_identifier
from hunter.execution.run import PipelineRun
from hunter.persistence.integration.exceptions import LifecycleTransitionError
from hunter.persistence.records import PipelineRunRecord


class RunLifecycleState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    PARTIAL = "partial"
    CANCELLED = "cancelled"


VALID_TRANSITIONS: dict[RunLifecycleState, frozenset[RunLifecycleState]] = {
    RunLifecycleState.PENDING: frozenset({RunLifecycleState.RUNNING, RunLifecycleState.CANCELLED}),
    RunLifecycleState.RUNNING: frozenset(
        {
            RunLifecycleState.SUCCEEDED,
            RunLifecycleState.FAILED,
            RunLifecycleState.PARTIAL,
            RunLifecycleState.CANCELLED,
        }
    ),
    RunLifecycleState.SUCCEEDED: frozenset(),
    RunLifecycleState.FAILED: frozenset(),
    RunLifecycleState.PARTIAL: frozenset(),
    RunLifecycleState.CANCELLED: frozenset(),
}


@dataclass(frozen=True)
class RunLifecycle:
    run: PipelineRun
    state: RunLifecycleState = RunLifecycleState.PENDING
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error_summary: str | None = None
    warning_summary: str | None = None

    def transition(
        self,
        state: RunLifecycleState,
        *,
        at: datetime,
        error_summary: str | None = None,
        warning_summary: str | None = None,
    ) -> RunLifecycle:
        if state not in VALID_TRANSITIONS[self.state]:
            msg = f"Invalid pipeline run transition: {self.state.value} -> {state.value}"
            raise LifecycleTransitionError(msg)
        started_at = self.started_at
        finished_at = self.finished_at
        if state is RunLifecycleState.RUNNING:
            started_at = at
        if state in {
            RunLifecycleState.SUCCEEDED,
            RunLifecycleState.FAILED,
            RunLifecycleState.PARTIAL,
            RunLifecycleState.CANCELLED,
        }:
            finished_at = at
        return replace(
            self,
            state=state,
            started_at=started_at,
            finished_at=finished_at,
            error_summary=error_summary,
            warning_summary=warning_summary,
        )

    def to_record(self, *, created_at: datetime) -> PipelineRunRecord:
        metadata = dict(self.run.metadata)
        metadata["pipeline_run_id"] = self.run.run_id
        metadata["lifecycle_state"] = self.state.value
        if self.error_summary:
            metadata["error_summary"] = self.error_summary
        if self.warning_summary:
            metadata["warning_summary"] = self.warning_summary
        return PipelineRunRecord(
            id=_lifecycle_record_id(self.run, self.state),
            created_at=created_at,
            effective_at=self.run.effective_at,
            run_type=self.run.run_type,
            target_id=self.run.target_id,
            target_type=self.run.target_type,
            configuration_fingerprint=self.run.configuration_fingerprint,
            input_fingerprint=self.run.input_fingerprint,
            engine_manifest_fingerprint=self.run.engine_manifest_fingerprint,
            status=self.state.value,
            requested_at=self.run.requested_at,
            started_at=self.started_at,
            finished_at=self.finished_at,
            parent_run_id=self.run.parent_run_id,
            replay_of_run_id=self.run.replay_of_run_id,
            metadata=metadata,
        )


def _lifecycle_record_id(run: PipelineRun, state: RunLifecycleState) -> str:
    if state in {
        RunLifecycleState.SUCCEEDED,
        RunLifecycleState.FAILED,
        RunLifecycleState.PARTIAL,
        RunLifecycleState.CANCELLED,
    }:
        return run.run_id
    return stable_identifier(
        "pipeline-run-lifecycle",
        {"pipeline_run_id": run.run_id, "state": state.value, "effective_at": run.effective_at},
        schema_version="pipeline-run-lifecycle-v1",
    )
