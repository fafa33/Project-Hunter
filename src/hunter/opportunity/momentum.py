from __future__ import annotations

from hunter.persistence.records import FusedIntelligenceRecord


def momentum(records: tuple[FusedIntelligenceRecord, ...]) -> float:
    ordered = tuple(sorted(records, key=lambda item: (item.effective_at, item.id)))
    if len(ordered) < 2:
        return 0.0
    return round(_score(ordered[-1]) - _score(ordered[0]), 4)


def _score(record: FusedIntelligenceRecord) -> float:
    return float(record.confidence.get("score", 0.0) or 0.0)
