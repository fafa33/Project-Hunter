from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol, runtime_checkable

from hunter.intelligence.fusion.configuration import FusionConfig
from hunter.intelligence.fusion.models import FusedIntelligence, FusionTarget
from hunter.intelligence.intelligence import Intelligence
from hunter.persistence.records import IntelligenceRecord


@runtime_checkable
class FusionEngine(Protocol):
    def fuse(
        self,
        intelligence: Iterable[Intelligence | IntelligenceRecord],
        target: FusionTarget,
        *,
        config: FusionConfig | None = None,
    ) -> FusedIntelligence:
        raise NotImplementedError
