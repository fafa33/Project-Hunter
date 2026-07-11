from __future__ import annotations

from hunter.opportunity.models import TemporalComparison
from hunter.persistence.records import FusedIntelligenceRecord


def analyze_temporal(records: tuple[FusedIntelligenceRecord, ...], *, required_depth: int) -> TemporalComparison:
    ordered = tuple(sorted(records, key=lambda item: (item.effective_at, item.id)))
    current = _score(ordered[-1]) if ordered else 0.0
    previous = _score(ordered[-2]) if len(ordered) > 1 else None
    change = 0.0 if previous is None else current - previous
    scores = tuple(_score(item) for item in ordered)
    positive_steps = sum(1 for left, right in zip(scores, scores[1:], strict=False) if right >= left)
    persistence = 0.0 if len(scores) <= 1 else positive_steps / (len(scores) - 1)
    reversal = len(scores) >= 3 and (scores[-1] - scores[-2]) * (scores[-2] - scores[-3]) < 0
    deterioration = change < -0.1
    one_off = len(scores) >= 3 and abs(change) > 0.25 and persistence < 0.5
    structural = abs(change) >= 0.15 and len(ordered) >= required_depth and not one_off
    return TemporalComparison(
        historical_depth=len(ordered),
        current_score=current,
        previous_score=previous,
        change=round(change, 4),
        persistence=round(persistence, 4),
        structural_change=structural,
        deterioration=deterioration,
        reversal=reversal,
        one_off_event=one_off,
        summary=f"{len(ordered)} fused records; current={current:.2f}; change={change:.2f}; persistence={persistence:.2f}.",
    )


def _score(record: FusedIntelligenceRecord) -> float:
    raw = record.confidence.get("score", record.confidence.get("weighted", 0.0))
    try:
        return max(0.0, min(1.0, float(raw)))
    except (TypeError, ValueError):
        return 0.0
