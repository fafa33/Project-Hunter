from __future__ import annotations

from statistics import mean

from hunter.intelligence.engines.news.models import (
    NEGATIVE_DOMAINS,
    POSITIVE_DOMAINS,
    NewsAnalysis,
    NewsDataset,
    NewsEvent,
)


class NewsAnalyzer:
    def analyze(self, dataset: NewsDataset) -> NewsAnalysis:
        material_events = tuple(event for event in dataset.events if event.severity >= 0.45 and event.confidence >= 0.45)
        strengths = tuple(sorted({event.event_type for event in material_events if event.event_type in POSITIVE_DOMAINS}))
        risks = tuple(sorted({event.event_type for event in material_events if event.event_type in NEGATIVE_DOMAINS or event.rumor or event.conflicting}))
        missing = tuple(sorted(set(dataset.missing_fields)))
        return NewsAnalysis(
            events=material_events,
            thesis_change=self._thesis_change(material_events),
            signal_quality=self._signal_quality(dataset, material_events),
            structural_change=self._structural_change(material_events),
            strengths=strengths,
            risks=risks,
            missing_evidence=missing,
            metadata={
                "article_count": str(len(dataset.articles)),
                "event_count": str(len(material_events)),
                "duplicate_count": str(len(dataset.duplicate_article_ids)),
                "low_quality_count": str(len(dataset.low_quality_article_ids)),
            },
        )

    def _thesis_change(self, events: tuple[NewsEvent, ...]) -> str:
        if not events:
            return "none"
        positive = sum(event.severity * event.confidence for event in events if event.event_type in POSITIVE_DOMAINS)
        negative = sum(event.severity * event.confidence for event in events if event.event_type in NEGATIVE_DOMAINS)
        if negative > positive and negative >= 0.45:
            return "negative"
        if positive > negative and positive >= 0.45:
            return "positive"
        return "mixed"

    def _signal_quality(self, dataset: NewsDataset, events: tuple[NewsEvent, ...]) -> str:
        if not events:
            return "low"
        average_confidence = mean(event.confidence for event in events)
        quality_penalty = (len(dataset.duplicate_article_ids) + len(dataset.low_quality_article_ids)) / max(len(dataset.articles), 1)
        score = average_confidence * (1.0 - min(quality_penalty, 1.0))
        if score >= 0.70:
            return "high"
        if score >= 0.45:
            return "moderate"
        return "low"

    def _structural_change(self, events: tuple[NewsEvent, ...]) -> str:
        structural = [event for event in events if event.permanence >= 0.65 and event.severity >= 0.60]
        if not structural:
            return "none"
        if any(event.event_type in NEGATIVE_DOMAINS for event in structural):
            return "negative"
        if any(event.event_type in POSITIVE_DOMAINS for event in structural):
            return "positive"
        return "mixed"
