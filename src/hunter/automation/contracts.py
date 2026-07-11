from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Protocol, runtime_checkable

from hunter.automation.models import AutomationJob, AutomationPipelinePlan, AutomationRun
from hunter.persistence.models import QuerySpec
from hunter.persistence.records import AutomationJobRecord, AutomationRunRecord
from hunter.plugins.contracts import PipelineContext


@runtime_checkable
class PipelineExecutor(Protocol):
    def __call__(self, plan: AutomationPipelinePlan, context: PipelineContext) -> PipelineContext:
        raise NotImplementedError


@runtime_checkable
class AutomationJobRepositoryProtocol(Protocol):
    def save(self, record: AutomationJobRecord) -> AutomationJobRecord:
        raise NotImplementedError

    def load(self, identity: str) -> AutomationJobRecord | None:
        raise NotImplementedError


@runtime_checkable
class AutomationRunRepositoryProtocol(Protocol):
    def save(self, record: AutomationRunRecord) -> AutomationRunRecord:
        raise NotImplementedError

    def load(self, identity: str) -> AutomationRunRecord | None:
        raise NotImplementedError

    def query(self, spec: QuerySpec) -> tuple[AutomationRunRecord, ...]:
        raise NotImplementedError


ClockFn = Callable[[], datetime]


@runtime_checkable
class AutomationRepositoryFactoryProtocol(Protocol):
    def automation_jobs(self) -> AutomationJobRepositoryProtocol:
        raise NotImplementedError

    def automation_runs(self) -> AutomationRunRepositoryProtocol:
        raise NotImplementedError


@runtime_checkable
class AutomationRunnerProtocol(Protocol):
    def run_once(self, job: AutomationJob, *, scheduled_for: datetime | None = None) -> AutomationRun:
        raise NotImplementedError
