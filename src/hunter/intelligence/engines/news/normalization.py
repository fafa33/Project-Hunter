from __future__ import annotations

from hunter.intelligence.engines.news.classifiers import NewsEventClassifier
from hunter.intelligence.engines.news.configuration import NewsEngineConfiguration
from hunter.intelligence.engines.news.models import NewsArticle, NewsDataset, NewsEvent, NewsRecord


class NewsNormalizer:
    def __init__(
        self,
        *,
        classifier: NewsEventClassifier | None = None,
        configuration: NewsEngineConfiguration | None = None,
    ) -> None:
        self.configuration = configuration or NewsEngineConfiguration()
        self._classifier = classifier or NewsEventClassifier(self.configuration)

    def normalize(self, records: tuple[NewsRecord, ...]) -> NewsDataset:
        article_by_key: dict[str, NewsArticle] = {}
        duplicates: list[str] = []
        low_quality: list[str] = []
        supplied_events: list[NewsEvent] = []
        for record in records:
            if isinstance(record, NewsArticle):
                key = _article_key(record)
                existing = article_by_key.get(key)
                if existing is None or record.source_quality.score() > existing.source_quality.score():
                    if existing is not None:
                        duplicates.append(existing.id)
                    article_by_key[key] = record
                else:
                    duplicates.append(record.id)
                if record.source_quality.score() < self.configuration.minimum_source_quality:
                    low_quality.append(record.id)
            elif isinstance(record, NewsEvent):
                supplied_events.append(record)

        articles = tuple(sorted(article_by_key.values(), key=lambda item: (item.published_at.isoformat(), item.id)))
        classified_events = tuple(
            self._classifier.classify(article) for article in articles if article.id not in low_quality
        )
        events = tuple(
            sorted((*classified_events, *supplied_events), key=lambda item: (item.timestamp.isoformat(), item.id))
        )
        missing = []
        if not articles:
            missing.append("articles")
        if not events:
            missing.append("events")
        project = (
            sorted(
                {article.project for article in articles}
                | {project for event in events for project in event.affected_projects}
            )[0]
            if articles or events
            else self.configuration.project
        )
        return NewsDataset(
            project=project,
            articles=articles,
            events=events,
            duplicate_article_ids=tuple(sorted(set(duplicates))),
            low_quality_article_ids=tuple(sorted(set(low_quality))),
            missing_fields=tuple(missing),
            metadata={"article_count": str(len(articles)), "event_count": str(len(events))},
        )


def _article_key(article: NewsArticle) -> str:
    normalized_title = " ".join(article.title.lower().split())
    return f"{normalized_title}|{article.project.lower()}|{article.published_at.date().isoformat()}"
