from __future__ import annotations

from datetime import UTC, datetime
from statistics import mean

from hunter.intelligence.confidence import Confidence
from hunter.intelligence.engines.news.configuration import NewsEngineConfiguration
from hunter.intelligence.engines.news.models import NewsDataset


class NewsConfidenceModel:
    def __init__(self, configuration: NewsEngineConfiguration | None = None) -> None:
        self.configuration = configuration or NewsEngineConfiguration()

    def calculate(self, dataset: NewsDataset) -> Confidence:
        completeness = self._completeness(dataset)
        quality = self._source_quality(dataset)
        freshness = self._freshness(dataset)
        uncertainty = 1.0 - mean(
            (
                completeness,
                quality,
                freshness,
                self._originality(dataset),
                self._primary_source_coverage(dataset),
                self._consistency(dataset),
            )
        )
        return Confidence.calculate(
            completeness=completeness,
            evidence_quality=quality,
            freshness=freshness,
            uncertainty=uncertainty,
        )

    def _completeness(self, dataset: NewsDataset) -> float:
        return mean((1.0 if dataset.articles else 0.0, 1.0 if dataset.events else 0.0))

    def _source_quality(self, dataset: NewsDataset) -> float:
        if not dataset.articles:
            return 0.0
        return mean(article.source_quality.score() for article in dataset.articles)

    def _freshness(self, dataset: NewsDataset) -> float:
        if not dataset.articles:
            return 0.0
        latest = max(article.published_at for article in dataset.articles)
        age_days = max((datetime.now(UTC) - latest).days, 0)
        return max(1.0 - (age_days / max(self.configuration.freshness_days, 1)), 0.0)

    def _originality(self, dataset: NewsDataset) -> float:
        if not dataset.articles:
            return 0.0
        duplicate_penalty = len(dataset.duplicate_article_ids) / max(len(dataset.articles), 1)
        recycled_penalty = sum(1 for article in dataset.articles if article.recycled or article.syndicated) / len(
            dataset.articles
        )
        return max(1.0 - mean((duplicate_penalty, recycled_penalty)), 0.0)

    def _primary_source_coverage(self, dataset: NewsDataset) -> float:
        if not dataset.articles:
            return 0.0
        primary = sum(1 for article in dataset.articles if article.source_quality.primary_source)
        return primary / len(dataset.articles)

    def _consistency(self, dataset: NewsDataset) -> float:
        if not dataset.events:
            return 0.0
        conflicting = sum(1 for event in dataset.events if event.conflicting or event.rumor)
        return 1.0 - (conflicting / len(dataset.events))
