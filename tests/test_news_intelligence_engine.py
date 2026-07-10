from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from hunter.intelligence import Intelligence
from hunter.intelligence.engines.news import (
    NewsArticle,
    NewsEvent,
    NewsIntelligenceEngine,
    NewsSourceQuality,
    create_plugin,
)
from hunter.intelligence.engines.news.analyzers import NewsAnalyzer
from hunter.intelligence.engines.news.classifiers import NewsEventClassifier
from hunter.intelligence.engines.news.collectors import ContextNewsCollector
from hunter.intelligence.engines.news.confidence import NewsConfidenceModel
from hunter.intelligence.engines.news.exceptions import NewsValidationError
from hunter.intelligence.engines.news.normalization import NewsNormalizer
from hunter.pipeline import PipelineOrchestrator
from hunter.plugins.contracts import PipelineContext
from hunter.plugins.manager import PluginManager

NOW = datetime(2026, 7, 10, tzinfo=UTC)


def quality(
    *,
    credibility: float = 0.9,
    reliability: float = 0.85,
    freshness: float = 0.95,
    originality: float = 0.9,
    primary: bool = True,
) -> NewsSourceQuality:
    return NewsSourceQuality(
        credibility=credibility,
        historical_reliability=reliability,
        freshness=freshness,
        originality=originality,
        primary_source=primary,
    )


def article(
    article_id: str,
    title: str,
    *,
    domains: tuple[str, ...] = (),
    source_quality: NewsSourceQuality | None = None,
    rumor: bool = False,
    clickbait: bool = False,
    syndicated: bool = False,
    recycled: bool = False,
    days_ago: int = 1,
) -> NewsArticle:
    return NewsArticle(
        id=article_id,
        title=title,
        url=f"https://example.test/{article_id}",
        source="official_blog" if (source_quality or quality()).primary_source else "aggregator",
        published_at=NOW - timedelta(days=days_ago),
        collected_at=NOW,
        source_quality=source_quality or quality(),
        project="aave",
        summary=title,
        body=title,
        domains=domains,
        affected_projects=("aave",),
        affected_sectors=("defi",),
        rumor=rumor,
        clickbait=clickbait,
        syndicated=syndicated,
        recycled=recycled,
    )


def news_records() -> tuple[NewsArticle, ...]:
    return (
        article("a1", "Aave protocol upgrade approved by governance", domains=("protocol_upgrade",)),
        article("a2", "Aave institutional adoption expands", domains=("institutional_adoption",), days_ago=2),
        article(
            "a3",
            "Anonymous rumor claims Aave exploit",
            domains=("exploit",),
            source_quality=quality(credibility=0.4, reliability=0.35, originality=0.3, primary=False),
            rumor=True,
            days_ago=1,
        ),
    )


def test_context_collector_reads_replaceable_news_inputs() -> None:
    context = PipelineContext(values={"news_records": list(news_records())})

    collected = ContextNewsCollector().collect(context)

    assert len(collected) == len(news_records())
    assert isinstance(collected[0], NewsArticle)


def test_canonical_models_reject_invalid_articles() -> None:
    with pytest.raises(NewsValidationError):
        NewsArticle(
            id="",
            title="",
            url="https://example.test",
            source="source",
            published_at=NOW,
            collected_at=NOW,
            source_quality=quality(),
            project="aave",
        )


def test_source_quality_scores_primary_sources_above_secondary_sources() -> None:
    primary = quality(primary=True)
    secondary = quality(primary=False)

    assert primary.score() > secondary.score()


def test_classifier_detects_event_type_and_penalizes_rumors() -> None:
    classifier = NewsEventClassifier()
    official = classifier.classify(article("official", "Aave mainnet launch", domains=("mainnet_launch",)))
    rumor_event = classifier.classify(
        article(
            "rumor",
            "Anonymous rumor claims Aave exploit",
            domains=("exploit",),
            source_quality=quality(credibility=0.5, reliability=0.4, primary=False),
            rumor=True,
        )
    )

    assert official.event_type == "mainnet_launch"
    assert official.confidence > rumor_event.confidence
    assert rumor_event.event_type == "exploit"


def test_normalization_deduplicates_articles_and_filters_low_quality() -> None:
    duplicate = article("dup", "Aave protocol upgrade approved by governance", domains=("protocol_upgrade",))
    low_quality = article(
        "low",
        "Aave clickbait rumor",
        domains=("exploit",),
        source_quality=quality(credibility=0.2, reliability=0.2, originality=0.2, primary=False),
        clickbait=True,
    )

    dataset = NewsNormalizer().normalize((*news_records(), duplicate, low_quality))

    assert "dup" in dataset.duplicate_article_ids
    assert "low" in dataset.low_quality_article_ids
    assert all("low" not in event.article_ids for event in dataset.events)


def test_analyzer_detects_thesis_change_signal_quality_and_structural_change() -> None:
    dataset = NewsNormalizer().normalize(news_records())

    analysis = NewsAnalyzer().analyze(dataset)

    assert analysis.thesis_change in {"positive", "mixed"}
    assert analysis.signal_quality in {"high", "moderate"}
    assert analysis.structural_change in {"positive", "mixed"}
    assert "protocol_upgrade" in analysis.strengths


def test_confidence_uses_freshness_originality_and_primary_source_coverage() -> None:
    sparse = NewsNormalizer().normalize(
        (
            article(
                "weak",
                "Aave rumor",
                source_quality=quality(credibility=0.4, reliability=0.4, freshness=0.3, primary=False),
                rumor=True,
                days_ago=20,
            ),
        )
    )
    rich = NewsNormalizer().normalize(news_records()[:2])

    sparse_confidence = NewsConfidenceModel().calculate(sparse)
    rich_confidence = NewsConfidenceModel().calculate(rich)

    assert rich_confidence.score > sparse_confidence.score
    assert rich_confidence.evidence_quality > sparse_confidence.evidence_quality


def test_news_engine_generates_standardized_intelligence() -> None:
    context = PipelineContext(values={"news_records": list(news_records())})
    engine = NewsIntelligenceEngine()

    collected = engine.collect(context)
    analysis = engine.analyze(context, collected)
    intelligence = engine.generate_intelligence(context, analysis)

    assert isinstance(intelligence, Intelligence)
    assert intelligence.engine == "news-intelligence"
    assert intelligence.project == "aave"
    assert intelligence.signals
    assert intelligence.evidence
    assert intelligence.observations
    assert intelligence.insights
    assert intelligence.metadata.get("thesis_change") in {"positive", "mixed"}


def test_supplied_news_events_are_preserved() -> None:
    event = NewsEvent(
        id="manual-event",
        event_type="legal_event",
        title="Legal event",
        source="fixture",
        timestamp=NOW,
        source_quality=quality(),
        affected_projects=("aave",),
        severity=0.7,
        permanence=0.8,
        confidence=0.9,
    )

    dataset = NewsNormalizer().normalize((event,))

    assert dataset.events == (event,)


def test_news_plugin_registration_exposes_plugin_contract() -> None:
    plugin = create_plugin()

    assert plugin.metadata.id == "news-intelligence"
    assert "news-intelligence" in plugin.metadata.capabilities


def test_plugin_manager_loads_news_plugin_from_configuration(tmp_path) -> None:
    config = tmp_path / "plugins.yaml"
    config.write_text(
        """
enabled:
  news-intelligence: true
configuration: {}
load_order:
  - news-intelligence
priorities: {}
module_paths:
  - hunter.intelligence.engines.news:create_plugin
""",
        encoding="utf-8",
    )

    loaded = PluginManager().load(config_path=config)

    assert [plugin.metadata.id for plugin in loaded] == ["news-intelligence"]


def test_pipeline_executes_news_engine_plugin(tmp_path) -> None:
    config = tmp_path / "plugins.yaml"
    config.write_text(
        """
enabled:
  news-intelligence: true
configuration: {}
load_order:
  - news-intelligence
priorities: {}
module_paths:
  - hunter.intelligence.engines.news:create_plugin
""",
        encoding="utf-8",
    )
    context = PipelineContext(values={"news_records": list(news_records())})

    result = PipelineOrchestrator().run(context=context, config_path=config)

    assert len(result.intelligence) == 1
    assert result.intelligence[0].engine == "news-intelligence"
    assert "news:intelligence:execute" in result.events
