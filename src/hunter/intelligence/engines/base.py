from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from hunter.intelligence.engines.contracts import EngineMetadata
from hunter.intelligence.intelligence import Intelligence
from hunter.plugins.contracts import PipelineContext


class BaseIntelligenceEngine(ABC):
    metadata: EngineMetadata

    @property
    def id(self) -> str:
        return self.metadata.id

    @property
    def name(self) -> str:
        return self.metadata.name

    @property
    def category(self) -> str:
        return self.metadata.category

    @property
    def version(self) -> str:
        return self.metadata.version

    @property
    def priority(self) -> int:
        return self.metadata.priority

    @property
    def required_inputs(self) -> tuple[str, ...]:
        return self.metadata.required_inputs

    @property
    def produced_outputs(self) -> tuple[str, ...]:
        return self.metadata.produced_outputs

    @property
    def capabilities(self) -> tuple[str, ...]:
        return self.metadata.capabilities

    @abstractmethod
    def validate(self, context: PipelineContext) -> None:
        raise NotImplementedError

    @abstractmethod
    def collect(self, context: PipelineContext) -> Any:
        raise NotImplementedError

    @abstractmethod
    def analyze(self, context: PipelineContext, collected: Any) -> Any:
        raise NotImplementedError

    @abstractmethod
    def generate_intelligence(self, context: PipelineContext, analysis: Any) -> Intelligence:
        raise NotImplementedError

    @abstractmethod
    def health_check(self) -> bool:
        raise NotImplementedError

