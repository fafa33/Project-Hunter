from __future__ import annotations

from collections.abc import Iterable

from hunter.persistence.records import (
    EvidenceRecord,
    FusedIntelligenceRecord,
    IntelligenceRecord,
    OpportunityTimingAssessmentRecord,
    SnapshotRecord,
)


def average(values: Iterable[float]) -> float:
    normalized = tuple(_clamp01(value) for value in values)
    if not normalized:
        return 0.0
    return round(sum(normalized) / len(normalized), 4)


def evidence_quality(records: tuple[EvidenceRecord, ...], fused: tuple[FusedIntelligenceRecord, ...]) -> float:
    evidence_scores = [record.reliability for record in records]
    fused_scores = [
        _value(record.confidence, "evidence_quality") for record in fused if "evidence_quality" in record.confidence
    ]
    return average((*evidence_scores, *fused_scores))


def evidence_freshness(records: tuple[EvidenceRecord, ...], fused: tuple[FusedIntelligenceRecord, ...]) -> float:
    evidence_scores = [record.freshness for record in records]
    group_scores = [
        float(group.get("freshness", 0.0))
        for record in fused
        for group in record.canonical_evidence_groups
        if isinstance(group, dict) and "freshness" in group
    ]
    return average((*evidence_scores, *group_scores))


def record_confidence(
    intelligence: tuple[IntelligenceRecord, ...],
    fused: tuple[FusedIntelligenceRecord, ...],
    timing: tuple[OpportunityTimingAssessmentRecord, ...],
) -> float:
    values = [_value(record.confidence, "score") for record in intelligence]
    values.extend(_value(record.confidence, "fused_confidence") for record in fused)
    values.extend(_value(record.confidence, "overall") for record in timing)
    return average(values)


def historical_reliability(snapshots: tuple[SnapshotRecord, ...]) -> float:
    values = []
    for snapshot in snapshots:
        payload = snapshot.payload
        for key in ("historical_reliability", "backtesting_reliability", "backtesting_quality", "reliability"):
            if key in payload:
                values.append(float(payload[key]))
                break
    return average(values)


def missing_evidence(
    fused: tuple[FusedIntelligenceRecord, ...], timing: tuple[OpportunityTimingAssessmentRecord, ...]
) -> tuple[str, ...]:
    missing = {str(item) for record in fused for item in record.missing_evidence.get("missing_categories", ()) or ()}
    missing.update(str(item) for record in timing for item in record.missing_evidence)
    return tuple(sorted(missing))


def conflict_categories(
    fused: tuple[FusedIntelligenceRecord, ...], timing: tuple[OpportunityTimingAssessmentRecord, ...]
) -> tuple[str, ...]:
    conflicts = {
        str(item) for record in fused for item in record.contradictions.get("contradicted_categories", ()) or ()
    }
    conflicts.update(str(item) for record in timing for item in record.contradictions)
    return tuple(sorted(conflicts))


def _value(payload: dict[str, object], key: str) -> float:
    value = payload.get(key, payload.get("score", 0.0))
    if isinstance(value, int | float):
        return _clamp01(float(value))
    return 0.0


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))
