from __future__ import annotations

from statistics import mean

from hunter.intelligence.engines.news.configuration import NewsEngineConfiguration
from hunter.intelligence.engines.news.models import (
    NEGATIVE_DOMAINS,
    NEWS_DOMAINS,
    POSITIVE_DOMAINS,
    NewsArticle,
    NewsEvent,
)


class NewsEventClassifier:
    def __init__(self, configuration: NewsEngineConfiguration | None = None) -> None:
        self.configuration = configuration or NewsEngineConfiguration()

    def classify(self, article: NewsArticle) -> NewsEvent:
        event_type = self._event_type(article)
        severity = self._severity(article, event_type)
        permanence = self._permanence(event_type)
        confidence = self._confidence(article, event_type)
        return NewsEvent(
            id=f"event-{article.id}",
            event_type=event_type,
            title=article.title,
            source=article.source,
            timestamp=article.published_at,
            source_quality=article.source_quality,
            affected_projects=article.affected_projects or (article.project,),
            affected_sectors=article.affected_sectors,
            severity=severity,
            scope=self._scope(article),
            permanence=permanence,
            impact_horizon=self._impact_horizon(severity, permanence),
            confidence=confidence,
            article_ids=(article.id,),
            rumor=article.rumor,
            metadata={"primary_source": str(article.source_quality.primary_source).lower()},
        )

    def _event_type(self, article: NewsArticle) -> str:
        if article.domains:
            for domain in article.domains:
                if domain in NEWS_DOMAINS:
                    return domain
        text = f"{article.title} {article.summary} {article.body}".lower()
        keyword_map = {
            "partnership": ("partner", "partnership"),
            "integration": ("integrat",),
            "mainnet_launch": ("mainnet",),
            "testnet_launch": ("testnet",),
            "tokenomics_change": ("tokenomics", "emission", "unlock"),
            "governance_proposal": ("proposal",),
            "governance_approval": ("approved", "passed"),
            "treasury_event": ("treasury",),
            "security_incident": ("security incident", "vulnerability"),
            "exploit": ("exploit", "hack"),
            "regulatory_action": ("sec", "regulator", "regulatory"),
            "exchange_listing": ("listing", "listed"),
            "delisting": ("delist",),
            "major_funding_round": ("funding", "series", "raise"),
            "institutional_adoption": ("institutional",),
            "enterprise_adoption": ("enterprise",),
            "ecosystem_expansion": ("ecosystem",),
            "developer_announcement": ("developer", "sdk"),
            "protocol_upgrade": ("upgrade",),
            "roadmap_milestone": ("roadmap", "milestone"),
            "community_announcement": ("community",),
            "foundation_announcement": ("foundation",),
            "ecosystem_grant": ("grant",),
            "strategic_acquisition": ("acquisition", "acquires"),
            "legal_event": ("lawsuit", "legal"),
        }
        for domain, keywords in keyword_map.items():
            if any(keyword in text for keyword in keywords):
                return domain
        return "community_announcement"

    def _severity(self, article: NewsArticle, event_type: str) -> float:
        base = 0.35
        if event_type in NEGATIVE_DOMAINS:
            base = 0.75
        elif event_type in POSITIVE_DOMAINS:
            base = 0.60
        if article.source_quality.primary_source:
            base += 0.10
        if article.clickbait or article.rumor:
            base -= 0.20
        return _clamp(base)

    def _permanence(self, event_type: str) -> float:
        long_lived = {
            "mainnet_launch",
            "tokenomics_change",
            "governance_approval",
            "security_incident",
            "exploit",
            "regulatory_action",
            "major_funding_round",
            "institutional_adoption",
            "protocol_upgrade",
            "strategic_acquisition",
            "legal_event",
        }
        medium = {"partnership", "integration", "exchange_listing", "ecosystem_grant", "roadmap_milestone"}
        if event_type in long_lived:
            return 0.85
        if event_type in medium:
            return 0.60
        return 0.35

    def _confidence(self, article: NewsArticle, event_type: str) -> float:
        penalties = [
            self.configuration.rumor_confidence_penalty if article.rumor else 0.0,
            0.20 if article.anonymous_sources else 0.0,
            0.15 if article.clickbait else 0.0,
            (
                self.configuration.secondary_source_confidence_penalty
                if not article.source_quality.primary_source
                else 0.0
            ),
            0.10 if event_type not in NEWS_DOMAINS else 0.0,
        ]
        return _clamp(article.source_quality.score() - sum(penalties))

    def _scope(self, article: NewsArticle) -> str:
        if len(article.affected_projects) > 3 or len(article.affected_sectors) > 1:
            return "ecosystem"
        return "project"

    def _impact_horizon(self, severity: float, permanence: float) -> str:
        average = mean((severity, permanence))
        if average >= 0.70:
            return "long"
        if average >= 0.45:
            return "medium"
        return "short"


def _clamp(value: float) -> float:
    return min(max(value, 0.0), 1.0)
