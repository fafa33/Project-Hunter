from __future__ import annotations

from typing import Protocol, runtime_checkable

from hunter.intelligence.engines.whale.models import WhaleEvent
from hunter.plugins.contracts import PipelineContext


@runtime_checkable
class WhaleCollector(Protocol):
    id: str

    def collect(self, context: PipelineContext) -> tuple[WhaleEvent, ...]:
        raise NotImplementedError


class ContextWhaleCollector:
    id = "context"

    def collect(self, context: PipelineContext) -> tuple[WhaleEvent, ...]:
        value = context.get("whale_events", ())
        if isinstance(value, tuple):
            return tuple(item for item in value if isinstance(item, WhaleEvent))
        if isinstance(value, list):
            return tuple(item for item in value if isinstance(item, WhaleEvent))
        return ()
