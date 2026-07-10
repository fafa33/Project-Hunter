from __future__ import annotations

from dataclasses import dataclass, field

from hunter.intelligence.engines.exceptions import IntelligenceEngineRegistrationError

DEFAULT_ENGINE_CAPABILITIES = (
    "collect",
    "analyze",
    "generate-intelligence",
    "health-check",
    "validate",
)


@dataclass
class CapabilityRegistry:
    _capabilities: set[str] = field(default_factory=lambda: set(DEFAULT_ENGINE_CAPABILITIES))

    def register(self, capability: str) -> None:
        normalized = _normalize(capability)
        if not normalized:
            raise IntelligenceEngineRegistrationError("Engine capability is required")
        self._capabilities.add(normalized)

    def contains(self, capability: str) -> bool:
        return _normalize(capability) in self._capabilities

    def all(self) -> tuple[str, ...]:
        return tuple(sorted(self._capabilities))


def _normalize(value: str) -> str:
    return value.strip().lower()

