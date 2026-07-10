from __future__ import annotations

from typing import Protocol, runtime_checkable

from hunter.intelligence.engines.protocol.models import ProtocolRecord
from hunter.plugins.contracts import PipelineContext


@runtime_checkable
class ProtocolCollector(Protocol):
    id: str

    def collect(self, context: PipelineContext) -> tuple[ProtocolRecord, ...]:
        raise NotImplementedError


class ContextProtocolCollector:
    id = "context"

    def collect(self, context: PipelineContext) -> tuple[ProtocolRecord, ...]:
        value = context.get("protocol_records", ())
        if isinstance(value, tuple | list):
            return tuple(item for item in value if _is_protocol_record(item))
        return ()


def _is_protocol_record(value: object) -> bool:
    from hunter.intelligence.engines.protocol.models import ProtocolSnapshot

    return isinstance(value, ProtocolSnapshot)
