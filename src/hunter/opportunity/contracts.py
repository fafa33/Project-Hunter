from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol, runtime_checkable

from hunter.intelligence.fusion.models import FusionTarget
from hunter.opportunity.configuration import OpportunityTimingConfig
from hunter.opportunity.models import OpportunityTimingAssessment
from hunter.persistence.records import (
    FusedIntelligenceRecord,
    OpportunityTimingAssessmentRecord,
    OpportunityTimingSnapshotRecord,
)


@runtime_checkable
class OpportunityTimingEngine(Protocol):
    def assess(
        self,
        fused_records: Iterable[FusedIntelligenceRecord],
        target: FusionTarget,
        *,
        historical_snapshots: Iterable[OpportunityTimingSnapshotRecord | OpportunityTimingAssessmentRecord] = (),
        config: OpportunityTimingConfig | None = None,
    ) -> OpportunityTimingAssessment:
        raise NotImplementedError
