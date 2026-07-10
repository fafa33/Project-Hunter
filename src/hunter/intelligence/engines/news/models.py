from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from hunter.intelligence.engines.news.exceptions import NewsValidationError

NEWS_DOMAINS = (
    "partnership",
    "integration",
    "mainnet_launch",
    "testnet_launch",
    "tokenomics_change",
    "governance_proposal",
    "governance_approval",
    "treasury_event",
    "security_incident",
    "exploit",
    "regulatory_action",
    "exchange_listing",
    "delisting",
    "major_funding_round",
    "institutional_adoption",
    "enterprise_adoption",
    "ecosystem_expansion",
    "developer_announcement",
    "protocol_upgrade",
    "roadmap_milestone",
    "community_announcement",
    "foundation_announcement",
    "ecosystem_grant",
    "strategic_acquisition",
    "legal_event",
)

POSITIVE_DOMAINS = {
    "partnership",
    "integration",
    "mainnet_launch",
    "testnet_launch",
    "governance_approval",
    "exchange_listing",
    "major_funding_round",
    "institutional_adoption",
    "enterprise_adoption",
    "ecosystem_expansion",
    "developer_announcement",
    "protocol_upgrade",
    "roadmap_milestone",
    "foundation_announcement",
    "ecosystem_grant",
    "strategic_acquisition",
}

NEGATIVE_DOMAINS = {
    "security_incident",
    "exploit",
    "regulatory_action",
    "delisting",
    "legal_event",
}


@dataclass(frozen=True)
class NewsSourceQuality:
    credibility: float
    historical_reliability: float
    freshness: float
    originality: float
    primary_source: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "credibility", _clamp(self.credibility))
        object.__setattr__(self, "historical_reliability", _clamp(self.historical_reliability))
        object.__setattr__(self, "freshness", _clamp(self.freshness))
        object.__setattr__(self, "originality", _clamp(self.originality))

    def score(self) -> float:
        primary_bonus = 1.0 if self.primary_source else 0.65
        value = (
            (0.30 * self.credibility)
            + (0.25 * self.historical_reliability)
            + (0.20 * self.freshness)
            + (0.15 * self.originality)
            + (0.10 * primary_bonus)
        )
        return round(_clamp(value), 4)


@dataclass(frozen=True)
class NewsArticle:
    id: str
    title: str
    url: str
    source: str
    published_at: datetime
    collected_at: datetime
    source_quality: NewsSourceQuality
    project: str
    summary: str = ""
    body: str = ""
    author: str = ""
    domains: tuple[str, ...] = ()
    affected_projects: tuple[str, ...] = ()
    affected_sectors: tuple[str, ...] = ()
    rumor: bool = False
    anonymous_sources: bool = False
    clickbait: bool = False
    syndicated: bool = False
    recycled: bool = False
    metadata: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_text(self.id, "article id")
        _require_text(self.title, "title")
        _require_text(self.url, "url")
        _require_text(self.source, "source")
        _require_text(self.project, "project")
        _require_datetime(self.published_at, "published_at")
        _require_datetime(self.collected_at, "collected_at")
        object.__setattr__(self, "domains", tuple(_normalize_domain(item) for item in self.domains))
        object.__setattr__(self, "affected_projects", tuple(str(item).strip().lower() for item in self.affected_projects))
        object.__setattr__(self, "affected_sectors", tuple(str(item).strip().lower() for item in self.affected_sectors))
        object.__setattr__(self, "metadata", _string_metadata(self.metadata))


@dataclass(frozen=True)
class NewsEvent:
    id: str
    event_type: str
    title: str
    source: str
    timestamp: datetime
    source_quality: NewsSourceQuality
    affected_projects: tuple[str, ...]
    affected_sectors: tuple[str, ...] = ()
    severity: float = 0.0
    scope: str = "project"
    permanence: float = 0.0
    impact_horizon: str = "unknown"
    confidence: float = 0.0
    article_ids: tuple[str, ...] = ()
    rumor: bool = False
    conflicting: bool = False
    metadata: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_text(self.id, "event id")
        _require_text(self.event_type, "event type")
        _require_text(self.title, "event title")
        _require_text(self.source, "event source")
        _require_datetime(self.timestamp, "event timestamp")
        object.__setattr__(self, "event_type", _normalize_domain(self.event_type))
        object.__setattr__(self, "severity", _clamp(self.severity))
        object.__setattr__(self, "permanence", _clamp(self.permanence))
        object.__setattr__(self, "confidence", _clamp(self.confidence))
        object.__setattr__(self, "affected_projects", tuple(str(item).strip().lower() for item in self.affected_projects))
        object.__setattr__(self, "affected_sectors", tuple(str(item).strip().lower() for item in self.affected_sectors))
        object.__setattr__(self, "article_ids", tuple(self.article_ids))
        object.__setattr__(self, "metadata", _string_metadata(self.metadata))


@dataclass(frozen=True)
class NewsDataset:
    project: str = "global-crypto"
    articles: tuple[NewsArticle, ...] = ()
    events: tuple[NewsEvent, ...] = ()
    duplicate_article_ids: tuple[str, ...] = ()
    low_quality_article_ids: tuple[str, ...] = ()
    missing_fields: tuple[str, ...] = ()
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class NewsAnalysis:
    events: tuple[NewsEvent, ...]
    thesis_change: str
    signal_quality: str
    structural_change: str
    strengths: tuple[str, ...] = ()
    risks: tuple[str, ...] = ()
    missing_evidence: tuple[str, ...] = ()
    metadata: dict[str, str] = field(default_factory=dict)


NewsRecord = NewsArticle | NewsEvent


def _normalize_domain(value: str) -> str:
    return value.strip().lower().replace("-", "_").replace(" ", "_")


def _require_text(value: str, field_name: str) -> None:
    if not value or not value.strip():
        raise NewsValidationError(f"Missing {field_name}")


def _require_datetime(value: datetime, field_name: str) -> None:
    if not isinstance(value, datetime):
        raise NewsValidationError(f"Missing {field_name}")


def _clamp(value: float) -> float:
    return min(max(float(value), 0.0), 1.0)


def _string_metadata(metadata: dict[str, Any]) -> dict[str, str]:
    return {str(key): str(value) for key, value in metadata.items()}
