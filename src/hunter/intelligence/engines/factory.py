from __future__ import annotations

from collections.abc import Callable

from hunter.intelligence.engines.contracts import IntelligenceEngine
from hunter.intelligence.engines.exceptions import IntelligenceEngineFactoryError

EngineBuilder = Callable[[], IntelligenceEngine]


class EngineFactory:
    def __init__(self) -> None:
        self._builders: dict[str, EngineBuilder] = {}

    def register(self, engine_id: str, builder: EngineBuilder) -> None:
        if not engine_id.strip():
            raise IntelligenceEngineFactoryError("Engine id is required")
        if engine_id in self._builders:
            raise IntelligenceEngineFactoryError(f"Duplicate engine factory registration: {engine_id}")
        self._builders[engine_id] = builder

    def create(self, engine_id: str) -> IntelligenceEngine:
        builder = self._builders.get(engine_id)
        if builder is None:
            raise IntelligenceEngineFactoryError(f"Unknown intelligence engine: {engine_id}")
        return builder()

    def available(self) -> tuple[str, ...]:
        return tuple(sorted(self._builders))
