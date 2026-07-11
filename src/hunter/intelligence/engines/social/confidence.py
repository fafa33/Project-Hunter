from __future__ import annotations

from datetime import UTC, datetime
from statistics import mean

from hunter.intelligence.confidence import Confidence
from hunter.intelligence.engines.social.configuration import SocialEngineConfiguration
from hunter.intelligence.engines.social.models import SocialDataset


class SocialConfidenceModel:
    def __init__(self, configuration: SocialEngineConfiguration | None = None) -> None:
        self.configuration = configuration or SocialEngineConfiguration()

    def calculate(self, dataset: SocialDataset) -> Confidence:
        completeness = self._completeness(dataset)
        quality = self._quality(dataset)
        freshness = self._freshness(dataset)
        uncertainty = 1.0 - mean(
            (
                completeness,
                quality,
                freshness,
                self._platform_coverage(dataset),
                self._language_coverage(dataset),
                self._historical_depth(dataset),
                self._manipulation_certainty(dataset),
            )
        )
        return Confidence.calculate(
            completeness=completeness,
            evidence_quality=quality,
            freshness=freshness,
            uncertainty=uncertainty,
        )

    def _completeness(self, dataset: SocialDataset) -> float:
        groups = (dataset.posts, dataset.authors, dataset.engagements, dataset.sentiment, dataset.communities)
        return sum(bool(group) for group in groups) / len(groups)

    def _quality(self, dataset: SocialDataset) -> float:
        if not dataset.posts:
            return 0.0
        author_quality = (
            mean(
                (author.credibility + author.attribution_quality + (1.0 - author.bot_probability)) / 3
                for author in dataset.authors
            )
            if dataset.authors
            else 0.0
        )
        engagement_quality = mean(item.quality for item in dataset.engagements) if dataset.engagements else 0.0
        duplicate_penalty = len(dataset.duplicates) / max(len(dataset.posts), 1)
        filtered_penalty = len(dataset.filtered) / max(len(dataset.posts), 1)
        return max(mean((author_quality, engagement_quality, 1.0 - duplicate_penalty, 1.0 - filtered_penalty)), 0.0)

    def _freshness(self, dataset: SocialDataset) -> float:
        timestamps = [post.timestamp for post in dataset.posts]
        if not timestamps:
            return 0.0
        age_days = max((datetime.now(UTC) - max(timestamps)).days, 0)
        return max(1.0 - (age_days / max(self.configuration.freshness_days, 1)), 0.0)

    def _platform_coverage(self, dataset: SocialDataset) -> float:
        platforms = {post.platform for post in dataset.posts}
        if self.configuration.required_platforms:
            required = set(self.configuration.required_platforms)
            return len(platforms & required) / len(required)
        return min(len(platforms) / 4, 1.0)

    def _language_coverage(self, dataset: SocialDataset) -> float:
        languages = {post.language for post in dataset.posts if post.language != "unknown"}
        if self.configuration.languages:
            required = set(self.configuration.languages)
            return len(languages & required) / len(required)
        return min(len(languages) / 3, 1.0) if languages else 0.0

    def _historical_depth(self, dataset: SocialDataset) -> float:
        if len(dataset.posts) < 2:
            return 0.0
        depth = (max(post.timestamp for post in dataset.posts) - min(post.timestamp for post in dataset.posts)).days
        return min(depth / max(self.configuration.minimum_historical_depth_days, 1), 1.0)

    def _manipulation_certainty(self, dataset: SocialDataset) -> float:
        if not dataset.posts:
            return 0.0
        evidence_count = len(dataset.posts) + len(dataset.authors) + len(dataset.engagements)
        return min(evidence_count / 20, 1.0)
