from __future__ import annotations

from typing import Protocol, runtime_checkable

from hunter.intelligence.engines.news.models import NewsArticle, NewsEvent, NewsRecord
from hunter.plugins.contracts import PipelineContext


@runtime_checkable
class NewsCollector(Protocol):
    id: str

    def collect(self, context: PipelineContext) -> tuple[NewsRecord, ...]:
        raise NotImplementedError


class ContextNewsCollector:
    id = "context"

    def collect(self, context: PipelineContext) -> tuple[NewsRecord, ...]:
        value = context.get("news_records", ())
        if isinstance(value, NewsArticle | NewsEvent):
            return (value,)
        if isinstance(value, tuple | list):
            return tuple(item for item in value if isinstance(item, NewsArticle | NewsEvent))
        return ()
