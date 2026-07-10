from __future__ import annotations

from statistics import mean

from hunter.intelligence.engines.social.configuration import SocialEngineConfiguration
from hunter.intelligence.engines.social.indicators import SocialIndicatorCalculator
from hunter.intelligence.engines.social.manipulation import SocialManipulationModel
from hunter.intelligence.engines.social.models import SocialAnalysis, SocialDataset, SocialIndicator
from hunter.intelligence.engines.social.sentiment import SocialSentimentModel


class SocialAnalyzer:
    def __init__(
        self,
        *,
        indicators: SocialIndicatorCalculator | None = None,
        manipulation: SocialManipulationModel | None = None,
        sentiment: SocialSentimentModel | None = None,
        configuration: SocialEngineConfiguration | None = None,
    ) -> None:
        self.configuration = configuration or SocialEngineConfiguration()
        self._indicators = indicators or SocialIndicatorCalculator(self.configuration)
        self._manipulation = manipulation or SocialManipulationModel(self.configuration)
        self._sentiment = sentiment or SocialSentimentModel()

    def analyze(self, dataset: SocialDataset) -> SocialAnalysis:
        indicators = self._indicators.calculate(dataset)
        by_name = {indicator.name: indicator for indicator in indicators}
        manipulation = self._manipulation.assess(dataset)
        strengths = tuple(
            sorted(
                indicator.name
                for indicator in indicators
                if indicator.direction == "positive" and indicator.value >= 0.6
            )
        )
        risks = tuple(
            sorted(
                indicator.name
                for indicator in indicators
                if indicator.direction == "negative" and indicator.value >= 0.45
            )
        )
        missing = tuple(sorted(set(dataset.missing_fields) | {item for indicator in indicators for item in indicator.missing_evidence}))
        return SocialAnalysis(
            indicators=indicators,
            manipulation=manipulation,
            attention_level=self._attention_level(by_name),
            attention_trend=self._attention_trend(by_name),
            community_quality=self._community_quality(by_name),
            sentiment_structure=self._sentiment.level(dataset),
            strengths=strengths,
            risks=risks,
            missing_evidence=missing,
            metadata={
                "platform_count": str(len({post.platform for post in dataset.posts})),
                "author_count": str(len(dataset.authors)),
                "manipulation_level": manipulation.level,
            },
        )

    def _attention_level(self, indicators: dict[str, SocialIndicator]) -> str:
        saturation = indicators.get("attention_saturation")
        if saturation and saturation.value >= self.configuration.saturation_threshold:
            return "saturated"
        momentum = indicators.get("mention_momentum")
        if momentum and momentum.value >= 0.65:
            return "elevated"
        if momentum and momentum.value <= 0.25:
            return "low"
        return "moderate"

    def _attention_trend(self, indicators: dict[str, SocialIndicator]) -> str:
        acceleration = indicators.get("social_acceleration")
        deterioration = indicators.get("social_deterioration")
        if acceleration and deterioration and acceleration.value > deterioration.value and acceleration.value >= 0.55:
            return "increasing"
        if deterioration and deterioration.value >= 0.55:
            return "declining"
        return "stable"

    def _community_quality(self, indicators: dict[str, SocialIndicator]) -> str:
        values = [
            indicators[name].value
            for name in ("engagement_quality", "discussion_quality", "community_retention", "organic_attention_ratio")
            if name in indicators and not indicators[name].missing_evidence
        ]
        if not values:
            return "unknown"
        score = mean(values)
        if score >= 0.7:
            return "high"
        if score >= 0.45:
            return "moderate"
        return "low"
