from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from hunter.intelligence import Evidence, Insight, Intelligence, Observation, Signal
from hunter.intelligence.engines.base import BaseIntelligenceEngine
from hunter.intelligence.engines.contracts import EngineMetadata
from hunter.intelligence.engines.runner import EngineRunner
from hunter.intelligence.engines.whale.analyzers import WhaleAnalyzer
from hunter.intelligence.engines.whale.collectors import ContextWhaleCollector, WhaleCollector
from hunter.intelligence.engines.whale.confidence import WhaleConfidenceModel
from hunter.intelligence.engines.whale.configuration import WhaleEngineConfiguration, WhaleEngineConfigurationLoader
from hunter.intelligence.engines.whale.exceptions import WhaleCollectionError
from hunter.intelligence.engines.whale.models import WHALE_SIGNAL_TYPES, WhaleAnalysis, WhaleDataset
from hunter.intelligence.engines.whale.normalization import WhaleNormalizer
from hunter.plugins.contracts import PipelineContext, PluginMetadata


class WhaleIntelligenceEngine(BaseIntelligenceEngine):
    def __init__(
        self,
        *,
        collectors: tuple[WhaleCollector, ...] | None = None,
        analyzer: WhaleAnalyzer | None = None,
        normalizer: WhaleNormalizer | None = None,
        confidence_model: WhaleConfidenceModel | None = None,
        configuration: WhaleEngineConfiguration | None = None,
    ) -> None:
        self.configuration = configuration or WhaleEngineConfigurationLoader().load()
        self.metadata = EngineMetadata(
            id=self.configuration.engine_id,
            name="Whale Intelligence Engine",
            category="whale",
            version="1.0.0",
            priority=self.configuration.priority,
            required_inputs=("whale_events",),
            produced_outputs=("whale_intelligence",),
            capabilities=("collect", "analyze", "generate-intelligence", "whale-intelligence"),
        )
        self._collectors = collectors or (ContextWhaleCollector(),)
        self._analyzer = analyzer or WhaleAnalyzer()
        self._normalizer = normalizer or WhaleNormalizer()
        self._confidence_model = confidence_model or WhaleConfidenceModel()
        self._latest_dataset = WhaleDataset()

    def validate(self, context: PipelineContext) -> None:
        if not self.configuration.enabled:
            raise WhaleCollectionError("Whale Intelligence Engine is disabled")

    def collect(self, context: PipelineContext) -> WhaleDataset:
        events = []
        for collector in self._collectors:
            events.extend(collector.collect(context))
        self._latest_dataset = self._normalizer.normalize(tuple(events))
        return self._latest_dataset

    def analyze(self, context: PipelineContext, collected: Any) -> WhaleAnalysis:
        if not isinstance(collected, WhaleDataset):
            raise WhaleCollectionError("Whale engine expected a WhaleDataset")
        return self._analyzer.analyze(collected)

    def generate_intelligence(self, context: PipelineContext, analysis: Any) -> Intelligence:
        if not isinstance(analysis, WhaleAnalysis):
            raise WhaleCollectionError("Whale engine expected WhaleAnalysis")
        confidence = self._confidence_model.calculate(self._latest_dataset)
        generated_at = datetime.now(UTC)
        evidence = tuple(
            Evidence(
                id=f"whale-evidence-{event.id}",
                source=event.source,
                collected_at=event.timestamp,
                reliability=event.reliability,
                freshness=1.0,
                reference=event.reference,
                raw_data=event.raw_data,
                metadata={"event_type": event.event_type, "asset": event.asset},
            )
            for event in self._latest_dataset.events
        )
        observations = tuple(
            Observation(
                id=f"whale-observation-{signal.name}",
                engine=self.id,
                project=self.configuration.project,
                description=f"{signal.event_type} activity is {signal.direction}.",
                evidence=evidence,
                importance=signal.strength,
                metadata={"event_type": signal.event_type, "event_count": signal.event_count},
            )
            for signal in analysis.signals
        )
        return Intelligence(
            id=f"{self.id}-{generated_at.isoformat()}",
            project=self.configuration.project,
            engine=self.id,
            signals=tuple(
                Signal(
                    id=f"whale-signal-{signal.name}",
                    source=self.id,
                    timestamp=generated_at,
                    category=signal.event_type,
                    strength=signal.strength,
                    confidence=signal.confidence,
                    severity=_severity(signal.strength),
                    metadata={"direction": signal.direction, "event_count": signal.event_count},
                )
                for signal in analysis.signals
            ),
            evidence=evidence,
            observations=observations,
            insights=self._insights(analysis, observations),
            confidence=confidence,
            generated_at=generated_at,
            metadata={
                "exchange_flow": analysis.exchange_flow,
                "smart_money_activity": analysis.smart_money_activity,
                "accumulating_assets": ",".join(analysis.accumulating_assets),
                "distributing_assets": ",".join(analysis.distributing_assets),
                "signal_types_supported": len(WHALE_SIGNAL_TYPES),
            },
        )

    def health_check(self) -> bool:
        return self.configuration.enabled

    def _insights(self, analysis: WhaleAnalysis, observations: tuple[Observation, ...]) -> tuple[Insight, ...]:
        if not observations:
            return ()
        return (
            Insight(
                id="whale-insight-flow",
                title="Whale exchange flow",
                explanation=f"Observed whale exchange flow is {analysis.exchange_flow}.",
                supporting_observations=observations,
                confidence=0.7,
                priority=0.8,
            ),
            Insight(
                id="whale-insight-capital-behavior",
                title="Large-capital behavior",
                explanation=(
                    "Accumulating assets: "
                    f"{', '.join(analysis.accumulating_assets) or 'none'}; "
                    "Distributing assets: "
                    f"{', '.join(analysis.distributing_assets) or 'none'}."
                ),
                supporting_observations=observations,
                confidence=0.7,
                priority=0.7,
            ),
        )


@dataclass
class WhaleIntelligencePlugin:
    metadata: PluginMetadata
    engine: WhaleIntelligenceEngine

    def initialize(self, context: PipelineContext) -> None:
        context.record("whale:intelligence:initialize")

    def validate(self, context: PipelineContext) -> None:
        context.record("whale:intelligence:validate")

    def execute(self, context: PipelineContext) -> None:
        EngineRunner().run([self.engine], context)
        context.record("whale:intelligence:execute")

    def shutdown(self, context: PipelineContext) -> None:
        context.record("whale:intelligence:shutdown")


def create_plugin() -> WhaleIntelligencePlugin:
    return WhaleIntelligencePlugin(
        metadata=PluginMetadata(
            id="whale-intelligence",
            name="Whale Intelligence Engine",
            version="1.0.0",
            author="Project Hunter",
            description="Generates standardized large-capital behavior intelligence.",
            category="intelligence",
            capabilities=("whale-intelligence", "intelligence"),
        ),
        engine=WhaleIntelligenceEngine(),
    )


def _severity(strength: float) -> float:
    return round(abs(strength - 0.5) * 2, 4)

