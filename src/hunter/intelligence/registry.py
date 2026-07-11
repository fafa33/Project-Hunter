from __future__ import annotations

from hunter.intelligence.exceptions import IntelligenceRegistryError
from hunter.intelligence.intelligence import Intelligence
from hunter.intelligence.validator import IntelligenceValidator


class IntelligenceRegistry:
    def __init__(self, validator: IntelligenceValidator | None = None) -> None:
        self._validator = validator or IntelligenceValidator()
        self._types: dict[str, type[Intelligence]] = {}
        self._outputs: dict[str, Intelligence] = {}

    def register_intelligence_type(self, type_id: str, intelligence_type: type[Intelligence]) -> None:
        if not type_id.strip():
            raise IntelligenceRegistryError("Intelligence type id is required")
        if type_id in self._types:
            raise IntelligenceRegistryError(f"Duplicate intelligence type: {type_id}")
        self._types[type_id] = intelligence_type

    def register_engine_output(self, intelligence: Intelligence) -> None:
        self._validator.validate(intelligence)
        if intelligence.id in self._outputs:
            raise IntelligenceRegistryError(f"Duplicate intelligence id: {intelligence.id}")
        self._outputs[intelligence.id] = intelligence

    def by_engine(self, engine: str) -> list[Intelligence]:
        return [item for item in self._outputs.values() if item.engine == engine]

    def by_project(self, project: str) -> list[Intelligence]:
        return [item for item in self._outputs.values() if item.project == project]

    def by_category(self, category: str) -> list[Intelligence]:
        return [
            item
            for item in self._outputs.values()
            if any(signal.category == category for signal in item.signals) or item.metadata.get("category") == category
        ]

    def all(self) -> list[Intelligence]:
        return list(self._outputs.values())

    def registered_types(self) -> dict[str, type[Intelligence]]:
        return dict(self._types)
