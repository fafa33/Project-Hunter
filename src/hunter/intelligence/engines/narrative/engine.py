from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC
from typing import Any

from hunter.intelligence import Evidence, Insight, Intelligence, Observation, Signal
from hunter.intelligence.engines.base import BaseIntelligenceEngine
from hunter.intelligence.engines.contracts import EngineMetadata
from hunter.intelligence.engines.narrative.analyzers import NarrativeAnalyzer
from hunter.intelligence.engines.narrative.collectors import ContextNarrativeCollector, NarrativeCollector
from hunter.intelligence.engines.narrative.confidence import NarrativeConfidenceModel
from hunter.intelligence.engines.narrative.configuration import (
    NarrativeEngineConfiguration,
    NarrativeEngineConfigurationLoader,
)
from hunter.intelligence.engines.narrative.exceptions import NarrativeCollectionError
from hunter.intelligence.engines.narrative.models import NARRATIVE_CATEGORIES, NarrativeAnalysis, NarrativeDataset
from hunter.intelligence.engines.narrative.normalization import NarrativeNormalizer
from hunter.intelligence.engines.runner import EngineRunner
from hunter.plugins.contracts import PipelineContext, PluginMetadata


class NarrativeIntelligenceEngine(BaseIntelligenceEngine):
    def __init__(
        self,
        *,
        collectors: tuple[NarrativeCollector, ...] | None = None,
        analyzer: NarrativeAnalyzer | None = None,
        normalizer: NarrativeNormalizer | None = None,
        confidence_model: NarrativeConfidenceModel | None = None,
        configuration: NarrativeEngineConfiguration | None = None,
    ) -> None:
        self.configuration = configuration or NarrativeEngineConfigurationLoader().load()
        self.metadata = EngineMetadata(
            id=self.configuration.engine_id,
            name="Narrative Intelligence Engine",
            category="narrative",
            version="1.0.0",
            priority=self.configuration.priority,
            required_inputs=("narrative_records",),
            produced_outputs=("narrative_intelligence",),
            capabilities=("collect", "analyze", "generate-intelligence", "narrative-intelligence"),
        )
        self._collectors = collectors or (ContextNarrativeCollector(),)
        self._normalizer = normalizer or NarrativeNormalizer(self.configuration)
        self._analyzer = analyzer or NarrativeAnalyzer()
        self._confidence_model = confidence_model or NarrativeConfidenceModel(self.configuration)
        self._latest_dataset = NarrativeDataset(project=self.configuration.project)

    def validate(self, context: PipelineContext) -> None:
        if not self.configuration.enabled:
            raise NarrativeCollectionError("Narrative Intelligence Engine is disabled")

    def collect(self, context: PipelineContext) -> NarrativeDataset:
        records = []
        for collector in self._collectors:
            records.extend(collector.collect(context))
        self._latest_dataset = self._normalizer.normalize(tuple(records))
        return self._latest_dataset

    def analyze(self, context: PipelineContext, collected: Any) -> NarrativeAnalysis:
        if not isinstance(collected, NarrativeDataset):
            raise NarrativeCollectionError("Narrative engine expected a NarrativeDataset")
        return self._analyzer.analyze(collected)

    def generate_intelligence(self, context: PipelineContext, analysis: Any) -> Intelligence:
        if not isinstance(analysis, NarrativeAnalysis):
            raise NarrativeCollectionError("Narrative engine expected NarrativeAnalysis")
        generated_at = context.clock.now().astimezone(UTC)
        evidence = self._evidence()
        observations = tuple(
            Observation(
                id=f"narrative-observation-{lifecycle.narrative_id}",
                engine=self.id,
                project=self._latest_dataset.project,
                description=f"{lifecycle.category} narrative is in {lifecycle.phase} phase: {lifecycle.reason}.",
                evidence=evidence,
                importance=_lifecycle_importance(lifecycle.phase),
                metadata={"category": lifecycle.category, "phase": lifecycle.phase},
            )
            for lifecycle in analysis.lifecycles
        )
        return Intelligence(
            id=f"{self.id}-{generated_at.isoformat()}",
            project=self._latest_dataset.project,
            engine=self.id,
            signals=tuple(
                Signal(
                    id=f"narrative-signal-{signal.narrative_id}-{signal.signal_type}",
                    source=self.id,
                    timestamp=generated_at,
                    category=signal.category,
                    strength=signal.strength,
                    confidence=signal.confidence,
                    severity=_lifecycle_importance(signal.signal_type),
                    metadata={"signal_type": signal.signal_type},
                )
                for signal in analysis.signals
            ),
            evidence=evidence,
            observations=observations,
            insights=self._insights(analysis, observations),
            confidence=self._confidence_model.calculate(self._latest_dataset),
            generated_at=generated_at,
            metadata={
                "strengths": ",".join(analysis.strengths),
                "risks": ",".join(analysis.risks),
                "missing_evidence": ",".join(analysis.missing_evidence),
                "lifecycle": ",".join(f"{item.category}:{item.phase}" for item in analysis.lifecycles),
                "relationships": str(len(analysis.relationships)),
                "supported_categories": str(len(NARRATIVE_CATEGORIES)),
            },
        )

    def health_check(self) -> bool:
        return self.configuration.enabled

    def _evidence(self) -> tuple[Evidence, ...]:
        return tuple(
            Evidence(
                id=f"narrative-evidence-{item.id}",
                source=item.source,
                collected_at=item.timestamp,
                reliability=item.reliability,
                freshness=1.0,
                reference=item.reference,
                raw_data={"category": item.category, "text": item.text, "engine": item.engine},
                metadata={
                    "institutional": str(item.institutional).lower(),
                    "retail": str(item.retail).lower(),
                    "project": item.project,
                },
            )
            for item in self._latest_dataset.evidence
        )

    def _insights(self, analysis: NarrativeAnalysis, observations: tuple[Observation, ...]) -> tuple[Insight, ...]:
        if not observations:
            return ()
        return (
            Insight(
                id="narrative-insight-lifecycle",
                title="Narrative lifecycle",
                explanation=(
                    "Lifecycle phases: "
                    f"{', '.join(f'{item.category}:{item.phase}' for item in analysis.lifecycles) or 'none'}."
                ),
                supporting_observations=observations,
                confidence=0.75,
                priority=0.8,
            ),
            Insight(
                id="narrative-insight-relationships",
                title="Narrative relationships",
                explanation=(
                    f"Detected {len(analysis.relationships)} narrative relationships; strengths: "
                    f"{', '.join(analysis.strengths) or 'none'}; risks: {', '.join(analysis.risks) or 'none'}."
                ),
                supporting_observations=observations,
                confidence=0.7,
                priority=0.7,
            ),
        )


@dataclass
class NarrativeIntelligencePlugin:
    metadata: PluginMetadata
    engine: NarrativeIntelligenceEngine

    def initialize(self, context: PipelineContext) -> None:
        context.record("narrative:intelligence:initialize")

    def validate(self, context: PipelineContext) -> None:
        context.record("narrative:intelligence:validate")

    def execute(self, context: PipelineContext) -> None:
        EngineRunner().run([self.engine], context)
        context.record("narrative:intelligence:execute")

    def shutdown(self, context: PipelineContext) -> None:
        context.record("narrative:intelligence:shutdown")


def create_plugin() -> NarrativeIntelligencePlugin:
    return NarrativeIntelligencePlugin(
        metadata=PluginMetadata(
            id="narrative-intelligence",
            name="Narrative Intelligence Engine",
            version="1.0.0",
            author="Project Hunter",
            description="Generates standardized structural crypto narrative intelligence.",
            category="intelligence",
            capabilities=("narrative-intelligence", "intelligence"),
        ),
        engine=NarrativeIntelligenceEngine(),
    )


def _lifecycle_importance(phase: str) -> float:
    weights = {
        "unknown": 0.1,
        "emerging": 0.35,
        "early_expansion": 0.45,
        "expansion": 0.60,
        "acceleration": 0.85,
        "mainstream": 0.65,
        "crowded": 0.70,
        "saturation": 0.80,
        "decline": 0.65,
        "obsolete": 0.50,
    }
    return weights.get(phase, 0.1)
