from __future__ import annotations

from hunter.intelligence.fusion.configuration import FusionConfig
from hunter.intelligence.fusion.models import (
    ContradictionAssessment,
    CorroborationAssessment,
    DependencyAssessment,
    EngineContribution,
    MissingEvidenceAssessment,
)


def calculate_fused_confidence(
    contributions: tuple[EngineContribution, ...],
    corroboration: CorroborationAssessment,
    contradictions: ContradictionAssessment,
    dependencies: DependencyAssessment,
    missing_evidence: MissingEvidenceAssessment,
    config: FusionConfig,
) -> dict[str, float]:
    if not contributions:
        input_confidence = 0.0
    else:
        total_weight = sum(item.weight for item in contributions)
        if total_weight <= 0.0:
            input_confidence = sum(item.confidence for item in contributions) / len(contributions)
        else:
            input_confidence = sum(item.confidence * item.weight for item in contributions) / total_weight
    score = (
        input_confidence
        + (corroboration.score * config.weighting.corroboration_bonus)
        - (contradictions.severity * config.weighting.contradiction_penalty)
        - dependencies.penalty
        - (missing_evidence.severity * config.weighting.missing_evidence_penalty)
    )
    score = _clamp(score)
    return {
        "score": score,
        "input_confidence": _clamp(input_confidence),
        "corroboration": _clamp(corroboration.score),
        "contradiction_penalty": _clamp(contradictions.severity * config.weighting.contradiction_penalty),
        "dependency_penalty": _clamp(dependencies.penalty),
        "missing_evidence_penalty": _clamp(missing_evidence.severity * config.weighting.missing_evidence_penalty),
        "uncertainty": _clamp(1.0 - score),
    }


def _clamp(value: float) -> float:
    return round(min(max(value, 0.0), 1.0), 4)
