from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from hunter.intelligence import Evidence, Insight, Intelligence, Observation, Signal
from hunter.intelligence.engines.base import BaseIntelligenceEngine
from hunter.intelligence.engines.contracts import EngineMetadata
from hunter.intelligence.engines.news.analyzers import NewsAnalyzer
from hunter.intelligence.engines.news.collectors import ContextNewsCollector, NewsCollector
from hunter.intelligence.engines.news.confidence import NewsConfidenceModel
from hunter.intelligence.engines.news.configuration import NewsEngineConfiguration, NewsEngineConfigurationLoader
from hunter.intelligence.engines.news.exceptions import NewsCollectionError
from hunter.intelligence.engines.news.models import NEWS_DOMAINS, NewsAnalysis, NewsDataset
from hunter.intelligence.engines.news.normalization import NewsNormalizer
from hunter.intelligence.engines.runner import EngineRunner
from hunter.plugins.contracts import PipelineContext, PluginMetadata


class NewsIntelligenceEngine(BaseIntelligenceEngine):
    def __init__(
        self,
        *,
        collectors: tuple[NewsCollector, ...] | None = None,
        analyzer: NewsAnalyzer | None = None,
        normalizer: NewsNormalizer | None = None,
        confidence_model: NewsConfidenceModel | None = None,
        configuration: NewsEngineConfiguration | None = None,
    ) -> None:
        self.configuration = configuration or NewsEngineConfigurationLoader().load()
        self.metadata = EngineMetadata(
            id=self.configuration.engine_id,
            name="News Intelligence Engine",
            category="news",
            version="1.0.0",
            priority=self.configuration.priority,
            required_inputs=("news_records",),
            produced_outputs=("news_intelligence",),
            capabilities=("collect", "analyze", "generate-intelligence", "news-intelligence"),
        )
        self._collectors = collectors or (ContextNewsCollector(),)
        self._normalizer = normalizer or NewsNormalizer(configuration=self.configuration)
        self._analyzer = analyzer or NewsAnalyzer()
        self._confidence_model = confidence_model or NewsConfidenceModel(self.configuration)
        self._latest_dataset = NewsDataset(project=self.configuration.project)

    def validate(self, context: PipelineContext) -> None:
        if not self.configuration.enabled:
            raise NewsCollectionError("News Intelligence Engine is disabled")

    def collect(self, context: PipelineContext) -> NewsDataset:
        records = []
        for collector in self._collectors:
            records.extend(collector.collect(context))
        self._latest_dataset = self._normalizer.normalize(tuple(records))
        return self._latest_dataset

    def analyze(self, context: PipelineContext, collected: Any) -> NewsAnalysis:
        if not isinstance(collected, NewsDataset):
            raise NewsCollectionError("News engine expected a NewsDataset")
        return self._analyzer.analyze(collected)

    def generate_intelligence(self, context: PipelineContext, analysis: Any) -> Intelligence:
        if not isinstance(analysis, NewsAnalysis):
            raise NewsCollectionError("News engine expected NewsAnalysis")
        generated_at = datetime.now(UTC)
        confidence = self._confidence_model.calculate(self._latest_dataset)
        evidence = self._evidence()
        observations = tuple(
            Observation(
                id=f"news-observation-{event.id}",
                engine=self.id,
                project=self._latest_dataset.project,
                description=f"{event.event_type} event detected: {event.title}",
                evidence=evidence,
                importance=event.severity,
                metadata={"event_type": event.event_type, "scope": event.scope, "impact_horizon": event.impact_horizon},
            )
            for event in analysis.events
        )
        return Intelligence(
            id=f"{self.id}-{generated_at.isoformat()}",
            project=self._latest_dataset.project,
            engine=self.id,
            signals=tuple(
                Signal(
                    id=f"news-signal-{event.id}",
                    source=self.id,
                    timestamp=generated_at,
                    category=event.event_type,
                    strength=event.severity,
                    confidence=event.confidence,
                    severity=event.severity,
                    metadata={"scope": event.scope, "permanence": str(event.permanence)},
                )
                for event in analysis.events
            ),
            evidence=evidence,
            observations=observations,
            insights=self._insights(analysis, observations),
            confidence=confidence,
            generated_at=generated_at,
            metadata={
                "thesis_change": analysis.thesis_change,
                "signal_quality": analysis.signal_quality,
                "structural_change": analysis.structural_change,
                "strengths": ",".join(analysis.strengths),
                "risks": ",".join(analysis.risks),
                "missing_evidence": ",".join(analysis.missing_evidence),
                "supported_domains": str(len(NEWS_DOMAINS)),
            },
        )

    def health_check(self) -> bool:
        return self.configuration.enabled

    def _evidence(self) -> tuple[Evidence, ...]:
        return tuple(
            Evidence(
                id=f"news-evidence-{article.id}",
                source=article.source,
                collected_at=article.collected_at,
                reliability=article.source_quality.score(),
                freshness=article.source_quality.freshness,
                reference=article.url,
                raw_data={"title": article.title, "summary": article.summary},
                metadata={
                    "primary_source": str(article.source_quality.primary_source).lower(),
                    "rumor": str(article.rumor).lower(),
                    "clickbait": str(article.clickbait).lower(),
                },
            )
            for article in self._latest_dataset.articles
        )

    def _insights(self, analysis: NewsAnalysis, observations: tuple[Observation, ...]) -> tuple[Insight, ...]:
        if not observations:
            return ()
        return (
            Insight(
                id="news-insight-thesis",
                title="News thesis impact",
                explanation=(
                    f"News thesis change is {analysis.thesis_change}; signal quality is "
                    f"{analysis.signal_quality}; structural change is {analysis.structural_change}."
                ),
                supporting_observations=observations,
                confidence=0.75,
                priority=0.8,
            ),
            Insight(
                id="news-insight-quality",
                title="News quality controls",
                explanation=(
                    f"Strengths: {', '.join(analysis.strengths) or 'none'}; "
                    f"risks: {', '.join(analysis.risks) or 'none'}; "
                    f"missing evidence: {', '.join(analysis.missing_evidence) or 'none'}."
                ),
                supporting_observations=observations,
                confidence=0.7,
                priority=0.7,
            ),
        )


@dataclass
class NewsIntelligencePlugin:
    metadata: PluginMetadata
    engine: NewsIntelligenceEngine

    def initialize(self, context: PipelineContext) -> None:
        context.record("news:intelligence:initialize")

    def validate(self, context: PipelineContext) -> None:
        context.record("news:intelligence:validate")

    def execute(self, context: PipelineContext) -> None:
        EngineRunner().run([self.engine], context)
        context.record("news:intelligence:execute")

    def shutdown(self, context: PipelineContext) -> None:
        context.record("news:intelligence:shutdown")


def create_plugin() -> NewsIntelligencePlugin:
    return NewsIntelligencePlugin(
        metadata=PluginMetadata(
            id="news-intelligence",
            name="News Intelligence Engine",
            version="1.0.0",
            author="Project Hunter",
            description="Generates standardized crypto news event intelligence.",
            category="intelligence",
            capabilities=("news-intelligence", "intelligence"),
        ),
        engine=NewsIntelligenceEngine(),
    )
