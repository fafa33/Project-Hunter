from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from hunter.intelligence import Evidence, Insight, Intelligence, Observation, Signal
from hunter.intelligence.engines.base import BaseIntelligenceEngine
from hunter.intelligence.engines.contracts import EngineMetadata
from hunter.intelligence.engines.runner import EngineRunner
from hunter.intelligence.engines.social.analyzers import SocialAnalyzer
from hunter.intelligence.engines.social.collectors import ContextSocialCollector, SocialCollector
from hunter.intelligence.engines.social.confidence import SocialConfidenceModel
from hunter.intelligence.engines.social.configuration import SocialEngineConfiguration, SocialEngineConfigurationLoader
from hunter.intelligence.engines.social.exceptions import SocialCollectionError
from hunter.intelligence.engines.social.models import SOCIAL_DOMAINS, SocialAnalysis, SocialDataset
from hunter.intelligence.engines.social.normalization import SocialNormalizer
from hunter.plugins.contracts import PipelineContext, PluginMetadata


class SocialIntelligenceEngine(BaseIntelligenceEngine):
    def __init__(
        self,
        *,
        collectors: tuple[SocialCollector, ...] | None = None,
        analyzer: SocialAnalyzer | None = None,
        normalizer: SocialNormalizer | None = None,
        confidence_model: SocialConfidenceModel | None = None,
        configuration: SocialEngineConfiguration | None = None,
    ) -> None:
        self.configuration = configuration or SocialEngineConfigurationLoader().load()
        self.metadata = EngineMetadata(
            id=self.configuration.engine_id,
            name="Social Intelligence Engine",
            category="social",
            version="1.0.0",
            priority=self.configuration.priority,
            required_inputs=("social_records", "intelligence"),
            produced_outputs=("social_intelligence",),
            capabilities=("collect", "analyze", "generate-intelligence", "social-intelligence"),
        )
        self._collectors = collectors or (ContextSocialCollector(),)
        self._normalizer = normalizer or SocialNormalizer(self.configuration)
        self._analyzer = analyzer or SocialAnalyzer(configuration=self.configuration)
        self._confidence_model = confidence_model or SocialConfidenceModel(self.configuration)
        self._latest_dataset = SocialDataset(project=self.configuration.project)

    def validate(self, context: PipelineContext) -> None:
        if not self.configuration.enabled:
            raise SocialCollectionError("Social Intelligence Engine is disabled")

    def collect(self, context: PipelineContext) -> SocialDataset:
        records = []
        for collector in self._collectors:
            records.extend(collector.collect(context))
        self._latest_dataset = self._normalizer.normalize(tuple(records), tuple(context.intelligence))
        return self._latest_dataset

    def analyze(self, context: PipelineContext, collected: Any) -> SocialAnalysis:
        if not isinstance(collected, SocialDataset):
            raise SocialCollectionError("Social engine expected a SocialDataset")
        return self._analyzer.analyze(collected)

    def generate_intelligence(self, context: PipelineContext, analysis: Any) -> Intelligence:
        if not isinstance(analysis, SocialAnalysis):
            raise SocialCollectionError("Social engine expected SocialAnalysis")
        generated_at = datetime.now(UTC)
        evidence = self._evidence()
        observations = tuple(
            Observation(
                id=f"social-observation-{indicator.name}",
                engine=self.id,
                project=self._latest_dataset.project,
                description=indicator.description,
                evidence=evidence,
                importance=indicator.value,
                metadata={"indicator": indicator.name, "direction": indicator.direction},
            )
            for indicator in analysis.indicators
        )
        return Intelligence(
            id=f"{self.id}-{generated_at.isoformat()}",
            project=self._latest_dataset.project,
            engine=self.id,
            signals=tuple(
                Signal(
                    id=f"social-signal-{indicator.name}",
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
            confidence=self._confidence_model.calculate(self._latest_dataset),
            generated_at=generated_at,
            metadata={
                "attention_level": analysis.attention_level,
                "attention_trend": analysis.attention_trend,
                "community_quality": analysis.community_quality,
                "sentiment_structure": analysis.sentiment_structure,
                "manipulation_level": analysis.manipulation.level,
                "manipulation_reason": analysis.manipulation.explanation,
                "strengths": ",".join(analysis.strengths),
                "risks": ",".join(analysis.risks),
                "missing_evidence": ",".join(analysis.missing_evidence),
                "supported_domains": str(len(SOCIAL_DOMAINS)),
            },
        )

    def health_check(self) -> bool:
        return self.configuration.enabled

    def _evidence(self) -> tuple[Evidence, ...]:
        return tuple(
            Evidence(
                id=f"social-evidence-{post.id}",
                source=post.source,
                collected_at=post.timestamp,
                reliability=0.5,
                freshness=1.0,
                reference=post.reference,
                raw_data={"platform": post.platform, "text": post.text, "topic": post.topic},
                metadata={"author_id": post.author_id, "language": post.language, "sentiment": post.sentiment},
            )
            for post in self._latest_dataset.posts
        )

    def _insights(self, analysis: SocialAnalysis, observations: tuple[Observation, ...]) -> tuple[Insight, ...]:
        if not observations:
            return ()
        return (
            Insight(
                id="social-insight-attention",
                title="Social attention quality",
                explanation=(
                    f"Attention is {analysis.attention_level} and {analysis.attention_trend}; "
                    f"community quality is {analysis.community_quality}; sentiment is {analysis.sentiment_structure}."
                ),
                supporting_observations=observations,
                confidence=0.75,
                priority=0.8,
            ),
            Insight(
                id="social-insight-manipulation",
                title="Social manipulation risk",
                explanation=f"Manipulation assessment is {analysis.manipulation.level}: {analysis.manipulation.explanation}.",
                supporting_observations=observations,
                confidence=0.7,
                priority=0.7,
            ),
        )


@dataclass
class SocialIntelligencePlugin:
    metadata: PluginMetadata
    engine: SocialIntelligenceEngine

    def initialize(self, context: PipelineContext) -> None:
        context.record("social:intelligence:initialize")

    def validate(self, context: PipelineContext) -> None:
        context.record("social:intelligence:validate")

    def execute(self, context: PipelineContext) -> None:
        EngineRunner().run([self.engine], context)
        context.record("social:intelligence:execute")

    def shutdown(self, context: PipelineContext) -> None:
        context.record("social:intelligence:shutdown")


def create_plugin() -> SocialIntelligencePlugin:
    return SocialIntelligencePlugin(
        metadata=PluginMetadata(
            id="social-intelligence",
            name="Social Intelligence Engine",
            version="1.0.0",
            author="Project Hunter",
            description="Generates standardized social attention, influence, sentiment, and manipulation intelligence.",
            category="intelligence",
            capabilities=("social-intelligence", "intelligence"),
        ),
        engine=SocialIntelligenceEngine(),
    )


def _severity(value: float, direction: str) -> float:
    if direction == "negative":
        return round(value, 4)
    return round(abs(value - 0.5) * 2, 4)
