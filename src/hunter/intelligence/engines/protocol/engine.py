from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from hunter.intelligence import Evidence, Insight, Intelligence, Observation, Signal
from hunter.intelligence.engines.base import BaseIntelligenceEngine
from hunter.intelligence.engines.contracts import EngineMetadata
from hunter.intelligence.engines.protocol.analyzers import ProtocolAnalyzer
from hunter.intelligence.engines.protocol.collectors import ContextProtocolCollector, ProtocolCollector
from hunter.intelligence.engines.protocol.confidence import ProtocolConfidenceModel
from hunter.intelligence.engines.protocol.configuration import (
    ProtocolEngineConfiguration,
    ProtocolEngineConfigurationLoader,
)
from hunter.intelligence.engines.protocol.exceptions import ProtocolCollectionError
from hunter.intelligence.engines.protocol.models import PROTOCOL_DOMAINS, ProtocolAnalysis, ProtocolDataset
from hunter.intelligence.engines.protocol.normalization import ProtocolNormalizer
from hunter.intelligence.engines.runner import EngineRunner
from hunter.plugins.contracts import PipelineContext, PluginMetadata


class ProtocolIntelligenceEngine(BaseIntelligenceEngine):
    def __init__(
        self,
        *,
        collectors: tuple[ProtocolCollector, ...] | None = None,
        analyzer: ProtocolAnalyzer | None = None,
        normalizer: ProtocolNormalizer | None = None,
        confidence_model: ProtocolConfidenceModel | None = None,
        configuration: ProtocolEngineConfiguration | None = None,
    ) -> None:
        self.configuration = configuration or ProtocolEngineConfigurationLoader().load()
        self.metadata = EngineMetadata(
            id=self.configuration.engine_id,
            name="Protocol Intelligence Engine",
            category="protocol",
            version="1.0.0",
            priority=self.configuration.priority,
            required_inputs=("protocol_records",),
            produced_outputs=("protocol_intelligence",),
            capabilities=("collect", "analyze", "generate-intelligence", "protocol-intelligence"),
        )
        self._collectors = collectors or (ContextProtocolCollector(),)
        self._normalizer = normalizer or ProtocolNormalizer()
        self._analyzer = analyzer or ProtocolAnalyzer(configuration=self.configuration)
        self._confidence_model = confidence_model or ProtocolConfidenceModel(self.configuration)
        self._latest_dataset = ProtocolDataset(project=self.configuration.project, protocol=self.configuration.protocol)

    def validate(self, context: PipelineContext) -> None:
        if not self.configuration.enabled:
            raise ProtocolCollectionError("Protocol Intelligence Engine is disabled")

    def collect(self, context: PipelineContext) -> ProtocolDataset:
        records = []
        for collector in self._collectors:
            records.extend(collector.collect(context))
        self._latest_dataset = self._normalizer.normalize(tuple(records))
        return self._latest_dataset

    def analyze(self, context: PipelineContext, collected: Any) -> ProtocolAnalysis:
        if not isinstance(collected, ProtocolDataset):
            raise ProtocolCollectionError("Protocol engine expected a ProtocolDataset")
        return self._analyzer.analyze(collected)

    def generate_intelligence(self, context: PipelineContext, analysis: Any) -> Intelligence:
        if not isinstance(analysis, ProtocolAnalysis):
            raise ProtocolCollectionError("Protocol engine expected ProtocolAnalysis")
        generated_at = datetime.now(UTC)
        confidence = self._confidence_model.calculate(self._latest_dataset)
        evidence = self._evidence()
        observations = tuple(
            Observation(
                id=f"protocol-observation-{indicator.name}",
                engine=self.id,
                project=self._latest_dataset.project,
                description=indicator.description,
                evidence=evidence,
                importance=indicator.value,
                metadata={
                    "indicator": indicator.name,
                    "direction": indicator.direction,
                    "missing_evidence": ",".join(indicator.missing_evidence),
                },
            )
            for indicator in analysis.indicators
        )
        return Intelligence(
            id=f"{self.id}-{generated_at.isoformat()}",
            project=self._latest_dataset.project,
            engine=self.id,
            signals=tuple(
                Signal(
                    id=f"protocol-signal-{indicator.name}",
                    source=self.id,
                    timestamp=generated_at,
                    category=indicator.name,
                    strength=indicator.value,
                    confidence=indicator.confidence,
                    severity=_severity(indicator.value, indicator.direction),
                    metadata={"direction": indicator.direction},
                )
                for indicator in analysis.indicators
            ),
            evidence=evidence,
            observations=observations,
            insights=self._insights(analysis, observations),
            confidence=confidence,
            generated_at=generated_at,
            metadata={
                "protocol_health": analysis.health,
                "operational_trend": analysis.operational_trend,
                "economic_trend": analysis.economic_trend,
                "adoption_trend": analysis.adoption_trend,
                "resilience": analysis.resilience,
                "sustainability": analysis.sustainability,
                "strengths": ",".join(analysis.strengths),
                "risks": ",".join(analysis.risks),
                "missing_evidence": ",".join(analysis.missing_evidence),
                "supported_domains": str(len(PROTOCOL_DOMAINS)),
                "chains": ",".join(self._latest_dataset.chains()),
                "deployments": ",".join(self._latest_dataset.deployments()),
            },
        )

    def health_check(self) -> bool:
        return self.configuration.enabled

    def _evidence(self) -> tuple[Evidence, ...]:
        return tuple(
            Evidence(
                id=f"protocol-evidence-{type(record).__name__}-{record.id}",
                source=record.source,
                collected_at=record.timestamp,
                reliability=record.reliability,
                freshness=1.0,
                reference=record.reference,
                raw_data={"record_type": type(record).__name__, "chain": record.chain, "deployment": record.deployment},
                metadata={"protocol": record.protocol},
            )
            for record in self._latest_dataset.records
        )

    def _insights(self, analysis: ProtocolAnalysis, observations: tuple[Observation, ...]) -> tuple[Insight, ...]:
        if not observations:
            return ()
        return (
            Insight(
                id="protocol-insight-health",
                title="Protocol health",
                explanation=(
                    f"Current protocol health is {analysis.health}; operational trend is "
                    f"{analysis.operational_trend}; economic trend is {analysis.economic_trend}; "
                    f"adoption trend is {analysis.adoption_trend}."
                ),
                supporting_observations=observations,
                confidence=0.75,
                priority=0.8,
            ),
            Insight(
                id="protocol-insight-risk",
                title="Protocol sustainability and risk",
                explanation=(
                    f"Resilience is {analysis.resilience}; sustainability is {analysis.sustainability}; "
                    f"risks: {', '.join(analysis.risks) or 'none'}; missing evidence: "
                    f"{', '.join(analysis.missing_evidence) or 'none'}."
                ),
                supporting_observations=observations,
                confidence=0.7,
                priority=0.7,
            ),
        )


@dataclass
class ProtocolIntelligencePlugin:
    metadata: PluginMetadata
    engine: ProtocolIntelligenceEngine

    def initialize(self, context: PipelineContext) -> None:
        context.record("protocol:intelligence:initialize")

    def validate(self, context: PipelineContext) -> None:
        context.record("protocol:intelligence:validate")

    def execute(self, context: PipelineContext) -> None:
        EngineRunner().run([self.engine], context)
        context.record("protocol:intelligence:execute")

    def shutdown(self, context: PipelineContext) -> None:
        context.record("protocol:intelligence:shutdown")


def create_plugin() -> ProtocolIntelligencePlugin:
    return ProtocolIntelligencePlugin(
        metadata=PluginMetadata(
            id="protocol-intelligence",
            name="Protocol Intelligence Engine",
            version="1.0.0",
            author="Project Hunter",
            description="Generates standardized protocol health, adoption, economics, and resilience intelligence.",
            category="intelligence",
            capabilities=("protocol-intelligence", "intelligence"),
        ),
        engine=ProtocolIntelligenceEngine(),
    )


def _severity(value: float, direction: str) -> float:
    if direction == "negative":
        return round(1.0 - value if value <= 1.0 else value, 4)
    return round(abs(value - 0.5) * 2, 4)
