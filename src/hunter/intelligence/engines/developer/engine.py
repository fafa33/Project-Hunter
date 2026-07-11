from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC
from typing import Any

from hunter.intelligence import Evidence, Insight, Intelligence, Observation, Signal
from hunter.intelligence.engines.base import BaseIntelligenceEngine
from hunter.intelligence.engines.contracts import EngineMetadata
from hunter.intelligence.engines.developer.analyzers import DeveloperAnalyzer
from hunter.intelligence.engines.developer.collectors import ContextDeveloperCollector, DeveloperCollector
from hunter.intelligence.engines.developer.confidence import DeveloperConfidenceModel
from hunter.intelligence.engines.developer.configuration import (
    DeveloperEngineConfiguration,
    DeveloperEngineConfigurationLoader,
)
from hunter.intelligence.engines.developer.exceptions import DeveloperCollectionError
from hunter.intelligence.engines.developer.models import DEVELOPER_DOMAINS, DeveloperAnalysis, DeveloperDataset
from hunter.intelligence.engines.developer.normalization import DeveloperNormalizer
from hunter.intelligence.engines.runner import EngineRunner
from hunter.plugins.contracts import PipelineContext, PluginMetadata


class DeveloperIntelligenceEngine(BaseIntelligenceEngine):
    def __init__(
        self,
        *,
        collectors: tuple[DeveloperCollector, ...] | None = None,
        analyzer: DeveloperAnalyzer | None = None,
        normalizer: DeveloperNormalizer | None = None,
        confidence_model: DeveloperConfidenceModel | None = None,
        configuration: DeveloperEngineConfiguration | None = None,
    ) -> None:
        self.configuration = configuration or DeveloperEngineConfigurationLoader().load()
        self.metadata = EngineMetadata(
            id=self.configuration.engine_id,
            name="Developer Intelligence Engine",
            category="developer",
            version="1.0.0",
            priority=self.configuration.priority,
            required_inputs=("developer_records",),
            produced_outputs=("developer_intelligence",),
            capabilities=("collect", "analyze", "generate-intelligence", "developer-intelligence"),
        )
        self._collectors = collectors or (ContextDeveloperCollector(),)
        self._normalizer = normalizer or DeveloperNormalizer(self.configuration)
        self._analyzer = analyzer or DeveloperAnalyzer(configuration=self.configuration)
        self._confidence_model = confidence_model or DeveloperConfidenceModel(self.configuration)
        self._latest_dataset = DeveloperDataset(project=self.configuration.project)

    def validate(self, context: PipelineContext) -> None:
        if not self.configuration.enabled:
            raise DeveloperCollectionError("Developer Intelligence Engine is disabled")

    def collect(self, context: PipelineContext) -> DeveloperDataset:
        records = []
        for collector in self._collectors:
            records.extend(collector.collect(context))
        self._latest_dataset = self._normalizer.normalize(tuple(records))
        return self._latest_dataset

    def analyze(self, context: PipelineContext, collected: Any) -> DeveloperAnalysis:
        if not isinstance(collected, DeveloperDataset):
            raise DeveloperCollectionError("Developer engine expected a DeveloperDataset")
        return self._analyzer.analyze(collected)

    def generate_intelligence(self, context: PipelineContext, analysis: Any) -> Intelligence:
        if not isinstance(analysis, DeveloperAnalysis):
            raise DeveloperCollectionError("Developer engine expected DeveloperAnalysis")
        generated_at = context.clock.now().astimezone(UTC)
        confidence = self._confidence_model.calculate(self._latest_dataset)
        evidence = self._evidence()
        observations = tuple(
            Observation(
                id=f"developer-observation-{indicator.name}",
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
                    id=f"developer-signal-{indicator.name}",
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
                "developer_health": analysis.health,
                "development_trend": analysis.trend,
                "strengths": ",".join(analysis.strengths),
                "risks": ",".join(analysis.risks),
                "missing_evidence": ",".join(analysis.missing_evidence),
                "supported_domains": str(len(DEVELOPER_DOMAINS)),
            },
        )

    def health_check(self) -> bool:
        return self.configuration.enabled

    def _evidence(self) -> tuple[Evidence, ...]:
        evidence: list[Evidence] = []
        for repository in self._latest_dataset.repositories:
            evidence.append(
                Evidence(
                    id=f"developer-evidence-repository-{repository.id}",
                    source=repository.source,
                    collected_at=repository.timestamp,
                    reliability=repository.reliability,
                    freshness=1.0,
                    reference=repository.url,
                    raw_data={"name": repository.name, "core": repository.is_core, "archived": repository.is_archived},
                    metadata={"record_type": "repository"},
                )
            )
        for event in self._latest_dataset.events:
            evidence.append(
                Evidence(
                    id=f"developer-evidence-event-{event.id}",
                    source=event.source,
                    collected_at=event.timestamp,
                    reliability=event.reliability,
                    freshness=1.0,
                    reference=event.reference,
                    raw_data={"event_type": event.event_type, "actor": event.actor},
                    metadata={"record_type": "event", "repository_id": event.repository_id},
                )
            )
        return tuple(evidence)

    def _insights(self, analysis: DeveloperAnalysis, observations: tuple[Observation, ...]) -> tuple[Insight, ...]:
        if not observations:
            return ()
        return (
            Insight(
                id="developer-insight-health",
                title="Developer health",
                explanation=f"Current developer health is {analysis.health} with a {analysis.trend} development trend.",
                supporting_observations=observations,
                confidence=0.75,
                priority=0.8,
            ),
            Insight(
                id="developer-insight-risk",
                title="Developer evidence risk",
                explanation=(
                    "Developer strengths: "
                    f"{', '.join(analysis.strengths) or 'none'}; risks: "
                    f"{', '.join(analysis.risks) or 'none'}; missing evidence: "
                    f"{', '.join(analysis.missing_evidence) or 'none'}."
                ),
                supporting_observations=observations,
                confidence=0.7,
                priority=0.7,
            ),
        )


@dataclass
class DeveloperIntelligencePlugin:
    metadata: PluginMetadata
    engine: DeveloperIntelligenceEngine

    def initialize(self, context: PipelineContext) -> None:
        context.record("developer:intelligence:initialize")

    def validate(self, context: PipelineContext) -> None:
        context.record("developer:intelligence:validate")

    def execute(self, context: PipelineContext) -> None:
        EngineRunner().run([self.engine], context)
        context.record("developer:intelligence:execute")

    def shutdown(self, context: PipelineContext) -> None:
        context.record("developer:intelligence:shutdown")


def create_plugin() -> DeveloperIntelligencePlugin:
    return DeveloperIntelligencePlugin(
        metadata=PluginMetadata(
            id="developer-intelligence",
            name="Developer Intelligence Engine",
            version="1.0.0",
            author="Project Hunter",
            description="Generates standardized developer activity and engineering health intelligence.",
            category="intelligence",
            capabilities=("developer-intelligence", "intelligence"),
        ),
        engine=DeveloperIntelligenceEngine(),
    )


def _severity(value: float, direction: str) -> float:
    if direction == "negative":
        return round(value, 4)
    return round(abs(value - 0.5) * 2, 4)
