from __future__ import annotations

from typing import Protocol, runtime_checkable

from hunter.market_validation.models import MarketValidationRun, ProjectValidationResult, ProjectValidationTarget


@runtime_checkable
class ProjectValidationExecutor(Protocol):
    def execute_project(self, target: ProjectValidationTarget, *, run_id: str) -> ProjectValidationResult:
        raise NotImplementedError


@runtime_checkable
class MarketValidationRunRepository(Protocol):
    def save(self, run: MarketValidationRun) -> MarketValidationRun:
        raise NotImplementedError

    def load(self, run_id: str) -> MarketValidationRun | None:
        raise NotImplementedError

    def history(self) -> tuple[MarketValidationRun, ...]:
        raise NotImplementedError
