from __future__ import annotations

from datetime import timedelta
from statistics import mean

from hunter.intelligence.engines.social.configuration import SocialEngineConfiguration
from hunter.intelligence.engines.social.influence import SocialInfluenceModel
from hunter.intelligence.engines.social.manipulation import SocialManipulationModel
from hunter.intelligence.engines.social.models import SocialDataset, SocialIndicator
from hunter.intelligence.engines.social.sentiment import SocialSentimentModel


class SocialIndicatorCalculator:
    def __init__(self, configuration: SocialEngineConfiguration | None = None) -> None:
        self.configuration = configuration or SocialEngineConfiguration()
        self._influence = SocialInfluenceModel()
        self._sentiment = SocialSentimentModel()
        self._manipulation = SocialManipulationModel(self.configuration)

    def calculate(self, dataset: SocialDataset) -> tuple[SocialIndicator, ...]:
        return (
            self.mention_momentum(dataset),
            self.unique_author_growth(dataset),
            self.engagement_momentum(dataset),
            self.engagement_quality(dataset),
            self.discussion_quality(dataset),
            self.community_growth(dataset),
            self.community_retention(dataset),
            self.platform_diversity(dataset),
            self.author_diversity(dataset),
            self.influence_quality(dataset),
            self.influence_concentration(dataset),
            self.sentiment_momentum(dataset),
            self.sentiment_dispersion(dataset),
            self.attention_persistence(dataset),
            self.attention_acceleration(dataset),
            self.attention_saturation(dataset),
            self.organic_attention_ratio(dataset),
            self.promotional_activity_ratio(dataset),
            self.bot_risk(dataset),
            self.spam_risk(dataset),
            self.coordination_risk(dataset),
            self.manipulation_risk(dataset),
            self.narrative_alignment(dataset),
            self.social_acceleration(dataset),
            self.social_deterioration(dataset),
        )

    def mention_momentum(self, dataset: SocialDataset) -> SocialIndicator:
        return _indicator("mention_momentum", _recent_share([post.timestamp for post in dataset.posts]), "positive", "Recent mention share.")

    def unique_author_growth(self, dataset: SocialDataset) -> SocialIndicator:
        if not dataset.posts:
            return _missing("unique_author_growth", "posts")
        latest = max(post.timestamp for post in dataset.posts)
        recent = {post.author_id for post in dataset.posts if post.timestamp >= latest - timedelta(days=self.configuration.freshness_days)}
        all_authors = {post.author_id for post in dataset.posts}
        return _indicator("unique_author_growth", _ratio(len(recent), len(all_authors)), "positive", "Recent unique author share.")

    def engagement_momentum(self, dataset: SocialDataset) -> SocialIndicator:
        values = [item.likes + item.replies + item.reposts for item in dataset.engagements]
        return _growth_indicator("engagement_momentum", values, "Engagement growth across snapshots.")

    def engagement_quality(self, dataset: SocialDataset) -> SocialIndicator:
        if not dataset.engagements:
            return _missing("engagement_quality", "engagements")
        return _indicator("engagement_quality", mean(item.quality for item in dataset.engagements), "positive", "Average engagement quality.")

    def discussion_quality(self, dataset: SocialDataset) -> SocialIndicator:
        if dataset.conversations:
            return _indicator("discussion_quality", mean(item.quality for item in dataset.conversations), "positive", "Conversation quality.")
        if not dataset.posts:
            return _missing("discussion_quality", "posts")
        original = sum(1 for post in dataset.posts if not post.repost and not post.reply)
        return _indicator("discussion_quality", _ratio(original, len(dataset.posts)), "positive", "Original discussion share.")

    def community_growth(self, dataset: SocialDataset) -> SocialIndicator:
        values = [item.members for item in dataset.communities]
        return _growth_indicator("community_growth", values, "Community member growth.")

    def community_retention(self, dataset: SocialDataset) -> SocialIndicator:
        latest = dataset.communities[-1] if dataset.communities else None
        if latest is None or latest.active_members == 0:
            return _missing("community_retention", "communities")
        return _indicator("community_retention", _ratio(latest.retained_members, latest.active_members), "positive", "Retained active community members.")

    def platform_diversity(self, dataset: SocialDataset) -> SocialIndicator:
        platforms = {post.platform for post in dataset.posts} | {community.platform for community in dataset.communities}
        return _indicator("platform_diversity", min(len(platforms) / 4, 1.0), "positive", "Social platform diversity.")

    def author_diversity(self, dataset: SocialDataset) -> SocialIndicator:
        if not dataset.posts:
            return _missing("author_diversity", "posts")
        unique_authors = {post.author_id for post in dataset.posts}
        return _indicator("author_diversity", _ratio(len(unique_authors), len(dataset.posts)), "positive", "Unique authors relative to posts.")

    def influence_quality(self, dataset: SocialDataset) -> SocialIndicator:
        if not dataset.authors:
            return _missing("influence_quality", "authors")
        return _indicator("influence_quality", mean(self._influence.score_author(author) for author in dataset.authors), "positive", "Influence quality beyond follower count.")

    def influence_concentration(self, dataset: SocialDataset) -> SocialIndicator:
        concentration = self._influence.concentration(dataset)
        return _indicator("influence_concentration", 1.0 - concentration, "negative" if concentration >= 0.55 else "positive", "Lower influence concentration is healthier.")

    def sentiment_momentum(self, dataset: SocialDataset) -> SocialIndicator:
        return _indicator("sentiment_momentum", self._sentiment.momentum(dataset), "positive", "Sentiment momentum.")

    def sentiment_dispersion(self, dataset: SocialDataset) -> SocialIndicator:
        dispersion = self._sentiment.dispersion(dataset)
        return _indicator("sentiment_dispersion", dispersion, "negative" if dispersion >= 0.35 else "positive", "Sentiment dispersion and disagreement.")

    def attention_persistence(self, dataset: SocialDataset) -> SocialIndicator:
        if len(dataset.posts) < 2:
            return _missing("attention_persistence", "post_history")
        depth = (max(post.timestamp for post in dataset.posts) - min(post.timestamp for post in dataset.posts)).days
        return _indicator("attention_persistence", min(depth / self.configuration.minimum_historical_depth_days, 1.0), "positive", "Historical depth of attention.")

    def attention_acceleration(self, dataset: SocialDataset) -> SocialIndicator:
        return _indicator("attention_acceleration", self.mention_momentum(dataset).value, "positive", "Attention acceleration.")

    def attention_saturation(self, dataset: SocialDataset) -> SocialIndicator:
        saturation = min(len(dataset.posts) / 20, 1.0)
        return _indicator("attention_saturation", saturation, "negative" if saturation >= self.configuration.saturation_threshold else "positive", "High attention density can indicate saturation.")

    def organic_attention_ratio(self, dataset: SocialDataset) -> SocialIndicator:
        if not dataset.posts:
            return _missing("organic_attention_ratio", "posts")
        organic = sum(1 for post in dataset.posts if not post.promotional and not post.giveaway and not post.coordinated)
        return _indicator("organic_attention_ratio", _ratio(organic, len(dataset.posts)), "positive", "Organic post share.")

    def promotional_activity_ratio(self, dataset: SocialDataset) -> SocialIndicator:
        return _indicator("promotional_activity_ratio", self._manipulation.promotion_risk(dataset), "negative", "Promotional activity risk.")

    def bot_risk(self, dataset: SocialDataset) -> SocialIndicator:
        return _indicator("bot_risk", self._manipulation.bot_risk(dataset), "negative", "Bot cluster risk.")

    def spam_risk(self, dataset: SocialDataset) -> SocialIndicator:
        return _indicator("spam_risk", self._manipulation.spam_risk(dataset), "negative", "Spam risk.")

    def coordination_risk(self, dataset: SocialDataset) -> SocialIndicator:
        return _indicator("coordination_risk", self._manipulation.coordination_risk(dataset), "negative", "Coordinated campaign risk.")

    def manipulation_risk(self, dataset: SocialDataset) -> SocialIndicator:
        assessment = self._manipulation.assess(dataset)
        return _indicator("manipulation_risk", max(assessment.bot_risk, assessment.spam_risk, assessment.promotion_risk, assessment.coordination_risk), "negative", assessment.explanation)

    def narrative_alignment(self, dataset: SocialDataset) -> SocialIndicator:
        return _indicator("narrative_alignment", dataset.narrative_alignment, "positive", "Alignment with existing Narrative Intelligence.")

    def social_acceleration(self, dataset: SocialDataset) -> SocialIndicator:
        value = mean((self.mention_momentum(dataset).value, self.unique_author_growth(dataset).value, self.engagement_momentum(dataset).value))
        return _indicator("social_acceleration", value, "positive", "Combined social acceleration.")

    def social_deterioration(self, dataset: SocialDataset) -> SocialIndicator:
        value = 1.0 - mean((self.social_acceleration(dataset).value, self.community_retention(dataset).value, self.organic_attention_ratio(dataset).value))
        return _indicator("social_deterioration", value, "negative" if value >= 0.55 else "neutral", "Weakening social quality and momentum.")


def _growth_indicator(name: str, values: list[int], description: str) -> SocialIndicator:
    if len(values) < 2 or values[0] <= 0:
        return _missing(name, name)
    return _indicator(name, _clamp(0.5 + ((values[-1] - values[0]) / values[0])), "positive", description)


def _recent_share(timestamps) -> float:
    if not timestamps:
        return 0.0
    latest = max(timestamps)
    recent = sum(1 for timestamp in timestamps if timestamp >= latest - timedelta(days=30))
    return _ratio(recent, len(timestamps))


def _indicator(name: str, value: float, direction: str, description: str) -> SocialIndicator:
    return SocialIndicator(name=name, value=round(_clamp(value), 4), direction=direction, confidence=0.85, description=description)


def _missing(name: str, evidence_name: str) -> SocialIndicator:
    return SocialIndicator(name=name, value=0.0, direction="unknown", confidence=0.0, description=f"Missing {evidence_name} evidence.", missing_evidence=(evidence_name,))


def _ratio(numerator: int | float, denominator: int | float) -> float:
    if denominator <= 0:
        return 0.0
    return _clamp(float(numerator) / float(denominator))


def _clamp(value: float) -> float:
    return min(max(value, 0.0), 1.0)
