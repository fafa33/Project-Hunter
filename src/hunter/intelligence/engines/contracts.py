from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from hunter.intelligence.intelligence import Intelligence
from hunter.plugins.contracts import PipelineContext


@dataclass(frozen=True)
class EngineMetadata:
    id: str
    name: str
    category: str
    version: str
    priority: int
    required_inputs: tuple[str, ...]
    produced_outputs: tuple[str, ...]
    capabilities: tuple[str, ...]


@runtime_checkable
class IntelligenceEngine(Protocol):
    @property
    def metadata(self) -> EngineMetadata:
        raise NotImplementedError

    @property
    def id(self) -> str:
        raise NotImplementedError

    @property
    def name(self) -> str:
        raise NotImplementedError

    @property
    def category(self) -> str:
        raise NotImplementedError

    @property
    def version(self) -> str:
        raise NotImplementedError

    @property
    def priority(self) -> int:
        raise NotImplementedError

    @property
    def required_inputs(self) -> tuple[str, ...]:
        raise NotImplementedError

    @property
    def produced_outputs(self) -> tuple[str, ...]:
        raise NotImplementedError

    @property
    def capabilities(self) -> tuple[str, ...]:
        raise NotImplementedError

    def validate(self, context: PipelineContext) -> None:
        raise NotImplementedError

    def collect(self, context: PipelineContext) -> Any:
        raise NotImplementedError

    def analyze(self, context: PipelineContext, collected: Any) -> Any:
        raise NotImplementedError

    def generate_intelligence(self, context: PipelineContext, analysis: Any) -> Intelligence:
        raise NotImplementedError

    def health_check(self) -> bool:
        raise NotImplementedError

