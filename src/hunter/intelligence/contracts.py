from __future__ import annotations

from typing import Protocol, runtime_checkable

from hunter.intelligence.intelligence import Intelligence


@runtime_checkable
class IntelligenceProducer(Protocol):
    def produce_intelligence(self) -> Intelligence:
        raise NotImplementedError
