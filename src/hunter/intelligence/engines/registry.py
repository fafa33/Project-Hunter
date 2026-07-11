from __future__ import annotations

import re

from hunter.intelligence.engines.capabilities import CapabilityRegistry
from hunter.intelligence.engines.categories import CategoryRegistry
from hunter.intelligence.engines.contracts import IntelligenceEngine
from hunter.intelligence.engines.exceptions import (
    IntelligenceEngineRegistrationError,
    IntelligenceEngineValidationError,
)


class EngineRegistry:
    def __init__(
        self,
        *,
        category_registry: CategoryRegistry | None = None,
        capability_registry: CapabilityRegistry | None = None,
    ) -> None:
        self._engines: dict[str, IntelligenceEngine] = {}
        self._category_registry = category_registry or CategoryRegistry()
        self._capability_registry = capability_registry or CapabilityRegistry()

    def register(self, engine: IntelligenceEngine) -> None:
        self._validate_engine(engine)
        if engine.id in self._engines:
            raise IntelligenceEngineRegistrationError(f"Duplicate intelligence engine id: {engine.id}")
        self._category_registry.register(engine.category)
        for capability in engine.capabilities:
            self._capability_registry.register(capability)
        self._engines[engine.id] = engine

    def get(self, engine_id: str) -> IntelligenceEngine | None:
        return self._engines.get(engine_id)

    def by_category(self, category: str) -> list[IntelligenceEngine]:
        return [engine for engine in self._engines.values() if engine.category == category]

    def by_capability(self, capability: str) -> list[IntelligenceEngine]:
        return [engine for engine in self._engines.values() if capability in engine.capabilities]

    def ordered(self) -> list[IntelligenceEngine]:
        return sorted(self._engines.values(), key=lambda engine: (-engine.priority, engine.id))

    def all(self) -> list[IntelligenceEngine]:
        return list(self._engines.values())

    def _validate_engine(self, engine: IntelligenceEngine) -> None:
        metadata = engine.metadata
        required = [
            metadata.id,
            metadata.name,
            metadata.category,
            metadata.version,
            *metadata.produced_outputs,
            *metadata.capabilities,
        ]
        if any(not str(value).strip() for value in required):
            raise IntelligenceEngineValidationError("Intelligence engine metadata is incomplete")
        if not re.fullmatch(r"\d+\.\d+\.\d+", metadata.version):
            raise IntelligenceEngineValidationError(f"Invalid intelligence engine version: {metadata.version}")
        if metadata.priority < 0:
            raise IntelligenceEngineValidationError("Intelligence engine priority must be non-negative")
