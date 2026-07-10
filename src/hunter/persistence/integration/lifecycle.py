from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum

from hunter.execution.hashing import stable_identifier
from hunter.execution.run import PipelineRun
from hunter.persistence.integration.exceptions import LifecycleTransitionError
from hunter.persistence.records import OperationalAttemptRecord, PipelineRunRecord


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
        return PipelineRunRecord(
            id=self.run.run_id,
            created_at=created_at,
            effective_at=self.run.effective_at,
            run_type=self.run.run_type,
            target_id=self.run.target_id,
            target_type=self.run.target_type,
            configuration_fingerprint=self.run.configuration_fingerprint,
            input_fingerprint=self.run.input_fingerprint,
            engine_manifest_fingerprint=self.run.engine_manifest_fingerprint,
            status="analytical",
            requested_at=None,
            started_at=None,
            finished_at=None,
            parent_run_id=self.run.parent_run_id,
            replay_of_run_id=self.run.replay_of_run_id,
            metadata=dict(self.run.metadata),
        )


@dataclass(frozen=True)
class OperationalAttempt:
    attempt_id: str
    run_id: str
    attempt_number: int
    requested_at: datetime
    state: RunLifecycleState = RunLifecycleState.PENDING
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error_summary: str | None = None
    warning_summary: str | None = None
    metadata: dict[str, str | int | float | bool | None] | None = None

    @classmethod
    def create(
        cls,
        *,
        run_id: str,
        attempt_number: int,
        requested_at: datetime,
        metadata: dict[str, str | int | float | bool | None] | None = None,
    ) -> OperationalAttempt:
        return cls(
            attempt_id=stable_identifier(
                "operational-attempt",
                {"run_id": run_id, "attempt_number": attempt_number},
                schema_version="operational-attempt-v1",
            ),
            run_id=run_id,
            attempt_number=attempt_number,
            requested_at=requested_at,
            metadata=dict(metadata or {}),
        )

    def transition(
        self,
        state: RunLifecycleState,
        *,
        at: datetime,
        error_summary: str | None = None,
        warning_summary: str | None = None,
    ) -> OperationalAttempt:
        if state not in VALID_TRANSITIONS[self.state]:
            msg = f"Invalid operational attempt transition: {self.state.value} -> {state.value}"
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

    def to_record(self, *, created_at: datetime, effective_at: datetime) -> OperationalAttemptRecord:
        metadata = dict(self.metadata or {})
        metadata["attempt_id"] = self.attempt_id
        metadata["run_id"] = self.run_id
        return OperationalAttemptRecord(
            id=_attempt_state_record_id(self),
            created_at=created_at,
            effective_at=effective_at,
            attempt_id=self.attempt_id,
            run_id=self.run_id,
            attempt_number=self.attempt_number,
            requested_at=self.requested_at,
            started_at=self.started_at,
            finished_at=self.finished_at,
            status=self.state.value,
            error_summary=self.error_summary,
            warning_summary=self.warning_summary,
            metadata=metadata,
        )


def _attempt_state_record_id(attempt: OperationalAttempt) -> str:
    return stable_identifier(
        "operational-attempt-state",
        {"attempt_id": attempt.attempt_id, "state": attempt.state.value},
        schema_version="operational-attempt-state-v1",
    )
