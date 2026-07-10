from __future__ import annotations

from statistics import mean

from hunter.intelligence.engines.social.models import SocialAuthor, SocialDataset


class SocialInfluenceModel:
    def score_author(self, author: SocialAuthor) -> float:
        follower_score = min((author.follower_count or 0) / 100_000, 1.0)
        role_bonus = {
            "developer": 0.85,
            "researcher": 0.90,
            "investor": 0.75,
            "institutional": 0.90,
            "media": 0.65,
            "community": 0.45,
        }.get(author.role, 0.45)
        score = mean(
            (
                author.credibility,
                author.domain_expertise,
                author.attribution_quality,
                role_bonus,
                follower_score,
                1.0 - author.bot_probability,
            )
        )
        return round(min(max(score, 0.0), 1.0), 4)

    def concentration(self, dataset: SocialDataset) -> float:
        if not dataset.authors:
            return 0.0
        scores = [self.score_author(author) for author in dataset.authors]
        total = sum(scores)
        return 0.0 if total <= 0 else round(max(scores) / total, 4)
