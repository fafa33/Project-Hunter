from __future__ import annotations

from statistics import pstdev

from hunter.intelligence.engines.social.models import SocialDataset


class SocialSentimentModel:
    def level(self, dataset: SocialDataset) -> str:
        if dataset.sentiment:
            return dataset.sentiment[-1].level
        counts = {level: 0 for level in ("positive", "negative", "neutral")}
        for post in dataset.posts:
            if post.sentiment in counts:
                counts[post.sentiment] += 1
        if not any(counts.values()):
            return "uncertain"
        if abs(counts["positive"] - counts["negative"]) <= 1 and counts["positive"] and counts["negative"]:
            return "mixed"
        return max(counts.items(), key=lambda item: (item[1], item[0]))[0]

    def momentum(self, dataset: SocialDataset) -> float:
        values = [item.positive - item.negative for item in dataset.sentiment]
        if len(values) < 2:
            return 0.0
        return round(_clamp(0.5 + ((values[-1] - values[0]) / 2)), 4)

    def dispersion(self, dataset: SocialDataset) -> float:
        if dataset.sentiment:
            latest = dataset.sentiment[-1]
            return round(pstdev((latest.positive, latest.negative, latest.neutral)), 4)
        mapped = [{"positive": 1.0, "neutral": 0.5, "negative": 0.0}.get(post.sentiment, 0.5) for post in dataset.posts]
        return round(pstdev(mapped), 4) if len(mapped) > 1 else 0.0


def _clamp(value: float) -> float:
    return min(max(value, 0.0), 1.0)
