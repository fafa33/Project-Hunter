from __future__ import annotations

from statistics import mean

from hunter.intelligence.engines.social.configuration import SocialEngineConfiguration
from hunter.intelligence.engines.social.models import ManipulationAssessment, SocialDataset


class SocialManipulationModel:
    def __init__(self, configuration: SocialEngineConfiguration | None = None) -> None:
        self.configuration = configuration or SocialEngineConfiguration()

    def assess(self, dataset: SocialDataset) -> ManipulationAssessment:
        bot_risk = self.bot_risk(dataset)
        spam_risk = self.spam_risk(dataset)
        promotion_risk = self.promotion_risk(dataset)
        coordination_risk = self.coordination_risk(dataset)
        maximum = max(bot_risk, spam_risk, promotion_risk, coordination_risk)
        if not dataset.posts:
            level = "insufficient_evidence"
        elif maximum >= 0.70:
            level = "detected"
        elif maximum >= 0.40:
            level = "suspected"
        else:
            level = "insufficient_evidence"
        return ManipulationAssessment(
            level=level,
            bot_risk=bot_risk,
            spam_risk=spam_risk,
            promotion_risk=promotion_risk,
            coordination_risk=coordination_risk,
            explanation=f"bot={bot_risk:.2f}; spam={spam_risk:.2f}; promotion={promotion_risk:.2f}; coordination={coordination_risk:.2f}",
        )

    def bot_risk(self, dataset: SocialDataset) -> float:
        if not dataset.authors:
            return 0.0
        return round(mean(author.bot_probability for author in dataset.authors), 4)

    def spam_risk(self, dataset: SocialDataset) -> float:
        total = len(dataset.posts) + len(dataset.filtered)
        if total == 0:
            return 0.0
        spam_posts = sum(1 for post in dataset.posts if post.spam)
        return round((spam_posts + len(dataset.filtered)) / total, 4)

    def promotion_risk(self, dataset: SocialDataset) -> float:
        if not dataset.posts:
            return 0.0
        promotional_posts = sum(1 for post in dataset.posts if post.promotional or post.giveaway)
        engagement_promo = mean(item.promotional_ratio for item in dataset.engagements) if dataset.engagements else 0.0
        return round(mean((promotional_posts / len(dataset.posts), engagement_promo)), 4)

    def coordination_risk(self, dataset: SocialDataset) -> float:
        if not dataset.posts:
            return 0.0
        coordinated = sum(1 for post in dataset.posts if post.coordinated)
        duplicate_pressure = len(dataset.duplicates) / max(len(dataset.posts), 1)
        repost_pressure = sum(1 for post in dataset.posts if post.repost) / len(dataset.posts)
        return round(min(mean((coordinated / len(dataset.posts), duplicate_pressure, repost_pressure)), 1.0), 4)
