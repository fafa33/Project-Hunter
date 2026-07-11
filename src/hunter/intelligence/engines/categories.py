from __future__ import annotations

from dataclasses import dataclass, field

from hunter.intelligence.engines.exceptions import IntelligenceEngineRegistrationError

DEFAULT_ENGINE_CATEGORIES = (
    "macro",
    "whale",
    "developer",
    "protocol",
    "news",
    "social",
    "on-chain",
    "governance",
    "portfolio",
    "ai",
    "opportunity-timing",
)


@dataclass
class CategoryRegistry:
    _categories: set[str] = field(default_factory=lambda: set(DEFAULT_ENGINE_CATEGORIES))

    def register(self, category: str) -> None:
        normalized = _normalize(category)
        if not normalized:
            raise IntelligenceEngineRegistrationError("Engine category is required")
        self._categories.add(normalized)

    def contains(self, category: str) -> bool:
        return _normalize(category) in self._categories

    def all(self) -> tuple[str, ...]:
        return tuple(sorted(self._categories))


def _normalize(value: str) -> str:
    return value.strip().lower()
