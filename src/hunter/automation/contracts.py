from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Protocol, runtime_checkable

from hunter.automation.models import AutomationJob, AutomationRun
from hunter.plugins.contracts import PipelineContext


@runtime_checkable
class PipelineExecutor(Protocol):
    def __call__(self, job: AutomationJob, context: PipelineContext) -> PipelineContext:
        raise NotImplementedError


@runtime_checkable
class AutomationRunRepositoryProtocol(Protocol):
    def save(self, record: object) -> object:
        raise NotImplementedError


ClockFn = Callable[[], datetime]


@runtime_checkable
class AutomationRunnerProtocol(Protocol):
    def run_once(self, job: AutomationJob, *, scheduled_for: datetime | None = None) -> AutomationRun:
        raise NotImplementedError
