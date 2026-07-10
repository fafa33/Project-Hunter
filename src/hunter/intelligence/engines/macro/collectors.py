from __future__ import annotations

from typing import Protocol, runtime_checkable

from hunter.intelligence.engines.macro.models import MacroDataPoint
from hunter.plugins.contracts import PipelineContext


@runtime_checkable
class MacroCollector(Protocol):
    id: str

    def collect(self, context: PipelineContext) -> tuple[MacroDataPoint, ...]:
        raise NotImplementedError


class ContextMacroCollector:
    id = "context"

    def collect(self, context: PipelineContext) -> tuple[MacroDataPoint, ...]:
        value = context.get("macro_data", ())
        if isinstance(value, tuple):
            return value
        if isinstance(value, list):
            return tuple(item for item in value if isinstance(item, MacroDataPoint))
        return ()

