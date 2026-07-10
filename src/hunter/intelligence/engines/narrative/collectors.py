from __future__ import annotations

from typing import Protocol, runtime_checkable

from hunter.intelligence.engines.narrative.models import NarrativeEvidence, NarrativeRecord
from hunter.plugins.contracts import PipelineContext


@runtime_checkable
class NarrativeCollector(Protocol):
    id: str

    def collect(self, context: PipelineContext) -> tuple[NarrativeRecord, ...]:
        raise NotImplementedError


class ContextNarrativeCollector:
    id = "context"

    def collect(self, context: PipelineContext) -> tuple[NarrativeRecord, ...]:
        value = context.get("narrative_records", ())
        if isinstance(value, NarrativeEvidence):
            return (value,)
        if isinstance(value, tuple | list):
            return tuple(item for item in value if isinstance(item, NarrativeEvidence))
        return ()
