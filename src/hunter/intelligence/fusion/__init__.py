"""Experimental Fusion package for v2.1.x.

The canonical production runtime consumes persisted evidence through Market
Validation and does not execute Fusion in the current production path.
"""

from hunter.intelligence.fusion.configuration import FusionConfig, FusionWeightingConfig, load_fusion_config
from hunter.intelligence.fusion.engine import CrossEngineFusionEngine, fused_intelligence_to_record
from hunter.intelligence.fusion.models import (
    CanonicalEvidence,
    ContradictionAssessment,
    CorroborationAssessment,
    DependencyAssessment,
    EngineContribution,
    FrozenFloatMap,
    FrozenScalarMap,
    FusedIntelligence,
    FusionInput,
    FusionTarget,
    IntelligenceGraphEdge,
    IntelligenceGraphNode,
    MissingEvidenceAssessment,
    UnifiedInsight,
    UnifiedNarrative,
    UnifiedObservation,
    UnifiedSignal,
)

__all__ = [
    "ContradictionAssessment",
    "CorroborationAssessment",
    "CanonicalEvidence",
    "CrossEngineFusionEngine",
    "DependencyAssessment",
    "EngineContribution",
    "FusedIntelligence",
    "FrozenFloatMap",
    "FrozenScalarMap",
    "FusionConfig",
    "FusionInput",
    "FusionTarget",
    "FusionWeightingConfig",
    "IntelligenceGraphEdge",
    "IntelligenceGraphNode",
    "MissingEvidenceAssessment",
    "UnifiedInsight",
    "UnifiedNarrative",
    "UnifiedObservation",
    "UnifiedSignal",
    "fused_intelligence_to_record",
    "load_fusion_config",
]
