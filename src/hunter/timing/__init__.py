from __future__ import annotations

from hunter.timing.models import TIMING_CLASSIFICATIONS, TimingAssessment, TimingDependencySnapshot, TimingRebuildStatus
from hunter.timing.repository import TimingRepository

__all__ = [
    "OpportunityTimingEvidenceEngine",
    "REQUIRED_TIMING_ENGINES",
    "TIMING_CLASSIFICATIONS",
    "TimingAssessment",
    "TimingDependencySnapshot",
    "TimingRebuildStatus",
    "TimingRepository",
    "current_timing_dependencies",
]


def __getattr__(name: str) -> object:
    if name in {"OpportunityTimingEvidenceEngine", "REQUIRED_TIMING_ENGINES", "current_timing_dependencies"}:
        from hunter.timing.engine import (
            REQUIRED_TIMING_ENGINES,
            OpportunityTimingEvidenceEngine,
            current_timing_dependencies,
        )

        values = {
            "OpportunityTimingEvidenceEngine": OpportunityTimingEvidenceEngine,
            "REQUIRED_TIMING_ENGINES": REQUIRED_TIMING_ENGINES,
            "current_timing_dependencies": current_timing_dependencies,
        }
        return values[name]
    raise AttributeError(name)
