from hunter.intelligence.engines.base import BaseIntelligenceEngine
from hunter.intelligence.engines.builder import HunterIntelligenceEngineBuilder
from hunter.intelligence.engines.capabilities import CapabilityRegistry
from hunter.intelligence.engines.categories import CategoryRegistry
from hunter.intelligence.engines.contracts import (
    EngineContext,
    EngineDefinition,
    EngineMetadata,
    EvidenceBundle,
    Finding,
    FindingBatch,
    FoundationalIntelligenceEngine,
    IntelligenceEngine,
    IntelligenceFindingRepository,
    finding_identity,
)
from hunter.intelligence.engines.factory import EngineFactory
from hunter.intelligence.engines.registry import EngineRegistry
from hunter.intelligence.engines.runner import EngineRunner
from hunter.intelligence.engines.service import IntelligenceEngineService

__all__ = [
    "BaseIntelligenceEngine",
    "CapabilityRegistry",
    "CategoryRegistry",
    "EngineContext",
    "EngineDefinition",
    "EngineFactory",
    "EngineMetadata",
    "EngineRegistry",
    "EngineRunner",
    "EvidenceBundle",
    "Finding",
    "FindingBatch",
    "FoundationalIntelligenceEngine",
    "HunterIntelligenceEngineBuilder",
    "IntelligenceEngine",
    "IntelligenceEngineService",
    "IntelligenceFindingRepository",
    "finding_identity",
]
"""Experimental plugin Intelligence Engine implementations for v2.1.x.

Production Market Validation uses persisted evidence sources through
EngineValidationSource rather than this plugin engine package.
"""
