from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@runtime_checkable
class AutomationLock(Protocol):
    def acquire(self, key: str) -> bool:
        raise NotImplementedError

    def release(self, key: str) -> None:
        raise NotImplementedError

    def locked(self, key: str) -> bool:
        raise NotImplementedError


@dataclass
class InProcessAutomationLock:
    _locks: set[str] = field(default_factory=set)

    def acquire(self, key: str) -> bool:
        if key in self._locks:
            return False
        self._locks.add(key)
        return True

    def release(self, key: str) -> None:
        self._locks.discard(key)

    def locked(self, key: str) -> bool:
        return key in self._locks
