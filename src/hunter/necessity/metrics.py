from __future__ import annotations

from hunter.persistence.records import EvidenceRecord, FusedIntelligenceRecord, OpportunityTimingAssessmentRecord


def average(values: tuple[float, ...]) -> float:
    if not values:
        return 0.0
    return round(sum(_clamp01(value) for value in values) / len(values), 4)


def confidence_score(confidence: dict[str, object]) -> float:
    for key in ("score", "overall", "fused_confidence", "confidence"):
        if key in confidence:
            return numeric(confidence[key])
    return average(tuple(numeric(value) for value in confidence.values()))


def evidence_quality(evidence: tuple[EvidenceRecord, ...], fused: tuple[FusedIntelligenceRecord, ...]) -> float:
    values = [record.reliability for record in evidence]
    values.extend(confidence_score(record.confidence) for record in fused)
    return average(tuple(values))


def missing_evidence(
    fused: tuple[FusedIntelligenceRecord, ...], timing: tuple[OpportunityTimingAssessmentRecord, ...]
) -> tuple[str, ...]:
    missing = {str(item) for record in fused for item in record.missing_evidence.get("missing_categories", ()) or ()}
    missing.update(str(item) for record in timing for item in record.missing_evidence)
    return tuple(sorted(missing))


def numeric(value: object) -> float:
    if isinstance(value, int | float):
        return _clamp01(float(value))
    return 0.0


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))
