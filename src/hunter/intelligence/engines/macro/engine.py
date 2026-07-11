from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC
from typing import Any

from hunter.intelligence import Evidence, Insight, Intelligence, Observation, Signal
from hunter.intelligence.engines.base import BaseIntelligenceEngine
from hunter.intelligence.engines.contracts import EngineMetadata
from hunter.intelligence.engines.macro.analyzers import MacroAnalyzer
from hunter.intelligence.engines.macro.collectors import ContextMacroCollector, MacroCollector
from hunter.intelligence.engines.macro.confidence import MacroConfidenceModel
from hunter.intelligence.engines.macro.configuration import MacroEngineConfiguration, MacroEngineConfigurationLoader
from hunter.intelligence.engines.macro.exceptions import MacroCollectionError
from hunter.intelligence.engines.macro.models import MACRO_DOMAINS, MacroAnalysis, MacroDataset
from hunter.intelligence.engines.macro.normalization import MacroNormalizer
from hunter.intelligence.engines.macro.scoring import MacroEnvironmentScorer
from hunter.intelligence.engines.runner import EngineRunner
from hunter.plugins.contracts import PipelineContext, PluginMetadata


class MacroIntelligenceEngine(BaseIntelligenceEngine):
    def __init__(
        self,
        *,
        collectors: tuple[MacroCollector, ...] | None = None,
        analyzer: MacroAnalyzer | None = None,
        normalizer: MacroNormalizer | None = None,
        confidence_model: MacroConfidenceModel | None = None,
        scorer: MacroEnvironmentScorer | None = None,
        configuration: MacroEngineConfiguration | None = None,
    ) -> None:
        self.configuration = configuration or MacroEngineConfigurationLoader().load()
        self.metadata = EngineMetadata(
            id=self.configuration.engine_id,
            name="Macro Intelligence Engine",
            category="macro",
            version="1.0.0",
            priority=self.configuration.priority,
            required_inputs=("macro_data",),
            produced_outputs=("macro_intelligence",),
            capabilities=("collect", "analyze", "generate-intelligence", "macro-intelligence"),
        )
        self._collectors = collectors or (ContextMacroCollector(),)
        self._analyzer = analyzer or MacroAnalyzer()
        self._normalizer = normalizer or MacroNormalizer()
        self._confidence_model = confidence_model or MacroConfidenceModel()
        self._scorer = scorer or MacroEnvironmentScorer()
        self._latest_dataset = MacroDataset()

    def validate(self, context: PipelineContext) -> None:
        if not self.configuration.enabled:
            raise MacroCollectionError("Macro Intelligence Engine is disabled")

    def collect(self, context: PipelineContext) -> MacroDataset:
        points = []
        for collector in self._collectors:
            points.extend(collector.collect(context))
        self._latest_dataset = self._normalizer.normalize(tuple(points))
        return self._latest_dataset

    def analyze(self, context: PipelineContext, collected: Any) -> MacroAnalysis:
        if not isinstance(collected, MacroDataset):
            raise MacroCollectionError("Macro engine expected a MacroDataset")
        return self._analyzer.analyze(collected)

    def generate_intelligence(self, context: PipelineContext, analysis: Any) -> Intelligence:
        if not isinstance(analysis, MacroAnalysis):
            raise MacroCollectionError("Macro engine expected MacroAnalysis")
        confidence = self._confidence_model.calculate(self._latest_dataset)
        generated_at = context.clock.now().astimezone(UTC)
        evidence = tuple(
            Evidence(
                id=f"macro-evidence-{point.domain}",
                source=point.source,
                collected_at=point.timestamp,
                reliability=point.reliability,
                freshness=1.0,
                reference=point.reference,
                raw_data=point.raw_data,
                metadata={"domain": point.domain},
            )
            for point in self._latest_dataset.points
        )
        observations = tuple(
            Observation(
                id=f"macro-observation-{indicator.name}",
                engine=self.id,
                project=self.configuration.project,
                description=f"{indicator.name} is {indicator.direction}.",
                evidence=evidence,
                importance=indicator.value,
                metadata={"domain": indicator.domain},
            )
            for indicator in analysis.indicators
        )
        insights = self._insights(analysis, observations)
        strength = self._scorer.environment_strength(analysis)
        return Intelligence(
            id=f"{self.id}-{generated_at.isoformat()}",
            project=self.configuration.project,
            engine=self.id,
            signals=tuple(
                Signal(
                    id=f"macro-signal-{indicator.name}",
                    source=self.id,
                    timestamp=generated_at,
                    category=indicator.domain,
                    strength=indicator.value,
                    confidence=indicator.confidence,
                    severity=self._scorer.severity(indicator.value),
                    metadata={"direction": indicator.direction},
                )
                for indicator in analysis.indicators
            ),
            evidence=evidence,
            observations=observations,
            insights=insights,
            confidence=confidence,
            generated_at=generated_at,
            metadata={
                "risk_regime": analysis.risk_regime,
                "liquidity_flow": analysis.liquidity_flow,
                "environment_strength": strength,
                "domains_supported": len(MACRO_DOMAINS),
            },
        )

    def health_check(self) -> bool:
        return self.configuration.enabled

    def _insights(self, analysis: MacroAnalysis, observations: tuple[Observation, ...]) -> tuple[Insight, ...]:
        if not observations:
            return ()
        return (
            Insight(
                id="macro-insight-risk-regime",
                title="Macro risk regime",
                explanation=f"Observed macro indicators classify the environment as {analysis.risk_regime}.",
                supporting_observations=observations,
                confidence=0.7,
                priority=0.8,
            ),
            Insight(
                id="macro-insight-sector-rotation",
                title="Sector rotation",
                explanation=(
                    "Strengthening domains: "
                    f"{', '.join(analysis.strengthening_domains) or 'none'}; "
                    "Weakening domains: "
                    f"{', '.join(analysis.weakening_domains) or 'none'}."
                ),
                supporting_observations=observations,
                confidence=0.7,
                priority=0.7,
            ),
        )


@dataclass
class MacroIntelligencePlugin:
    metadata: PluginMetadata
    engine: MacroIntelligenceEngine

    def initialize(self, context: PipelineContext) -> None:
        context.record("macro:intelligence:initialize")

    def validate(self, context: PipelineContext) -> None:
        context.record("macro:intelligence:validate")

    def execute(self, context: PipelineContext) -> None:
        EngineRunner().run([self.engine], context)
        context.record("macro:intelligence:execute")

    def shutdown(self, context: PipelineContext) -> None:
        context.record("macro:intelligence:shutdown")


def create_plugin() -> MacroIntelligencePlugin:
    return MacroIntelligencePlugin(
        metadata=PluginMetadata(
            id="macro-intelligence",
            name="Macro Intelligence Engine",
            version="1.0.0",
            author="Project Hunter",
            description="Generates standardized macro environment intelligence.",
            category="intelligence",
            capabilities=("macro-intelligence", "intelligence"),
        ),
        engine=MacroIntelligenceEngine(),
    )
