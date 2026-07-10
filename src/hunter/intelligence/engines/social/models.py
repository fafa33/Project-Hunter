from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from hunter.intelligence.engines.social.exceptions import SocialValidationError

SOCIAL_DOMAINS = (
    "mention_volume",
    "mention_growth",
    "unique_author_growth",
    "engagement_growth",
    "engagement_quality",
    "discussion_quality",
    "sentiment",
    "sentiment_change",
    "sentiment_dispersion",
    "influencer_participation",
    "researcher_participation",
    "developer_participation",
    "institutional_participation",
    "community_growth",
    "community_retention",
    "platform_diversity",
    "geographic_diversity",
    "language_diversity",
    "topic_diversity",
    "narrative_alignment",
    "virality",
    "attention_persistence",
    "attention_saturation",
    "community_concentration",
    "influencer_concentration",
    "bot_risk",
    "spam_risk",
    "promotion_risk",
    "coordinated_campaign_risk",
    "social_manipulation_risk",
)

SENTIMENT_LEVELS = ("positive", "negative", "neutral", "mixed", "uncertain")
MANIPULATION_LEVELS = ("detected", "suspected", "insufficient_evidence")


@dataclass(frozen=True)
class SocialAuthor:
    id: str
    platform: str
    handle: str
    role: str = "community"
    credibility: float = 0.5
    domain_expertise: float = 0.0
    follower_count: int | None = None
    attribution_quality: float = 0.5
    bot_probability: float = 0.0
    created_at: datetime | None = None
    project_owned: bool = False
    metadata: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_text(self.id, "author id")
        _require_text(self.platform, "platform")
        _require_text(self.handle, "handle")
        object.__setattr__(self, "platform", _normalize(self.platform))
        object.__setattr__(self, "role", _normalize(self.role))
        object.__setattr__(self, "credibility", _clamp(self.credibility))
        object.__setattr__(self, "domain_expertise", _clamp(self.domain_expertise))
        object.__setattr__(self, "attribution_quality", _clamp(self.attribution_quality))
        object.__setattr__(self, "bot_probability", _clamp(self.bot_probability))
        object.__setattr__(self, "metadata", _string_metadata(self.metadata))


@dataclass(frozen=True)
class SocialAccount:
    id: str
    platform: str
    project: str
    handle: str
    project_owned: bool
    created_at: datetime | None = None
    metadata: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_text(self.id, "account id")
        _require_text(self.platform, "platform")
        _require_text(self.project, "project")
        _require_text(self.handle, "handle")
        object.__setattr__(self, "platform", _normalize(self.platform))
        object.__setattr__(self, "metadata", _string_metadata(self.metadata))


@dataclass(frozen=True)
class SocialPost:
    id: str
    platform: str
    project: str
    author_id: str
    timestamp: datetime
    text: str
    source: str
    reference: str
    language: str = "unknown"
    sentiment: str = "uncertain"
    topic: str = "general"
    reply: bool = False
    repost: bool = False
    promotional: bool = False
    spam: bool = False
    giveaway: bool = False
    coordinated: bool = False
    duplicate_key: str = ""
    metadata: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_text(self.id, "post id")
        _require_text(self.platform, "platform")
        _require_text(self.project, "project")
        _require_text(self.author_id, "author id")
        _require_text(self.text, "text")
        _require_text(self.source, "source")
        _require_text(self.reference, "reference")
        _require_datetime(self.timestamp, "timestamp")
        object.__setattr__(self, "platform", _normalize(self.platform))
        object.__setattr__(self, "language", _normalize(self.language))
        object.__setattr__(self, "sentiment", _normalize_sentiment(self.sentiment))
        object.__setattr__(self, "topic", _normalize(self.topic))
        object.__setattr__(self, "metadata", _string_metadata(self.metadata))


@dataclass(frozen=True)
class SocialMention:
    id: str
    platform: str
    project: str
    author_id: str
    timestamp: datetime
    source: str
    reference: str
    metadata: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_text(self.id, "mention id")
        _require_text(self.platform, "platform")
        _require_text(self.project, "project")
        _require_text(self.author_id, "author id")
        _require_datetime(self.timestamp, "timestamp")
        object.__setattr__(self, "platform", _normalize(self.platform))
        object.__setattr__(self, "metadata", _string_metadata(self.metadata))


@dataclass(frozen=True)
class SocialEngagement:
    id: str
    post_id: str
    platform: str
    timestamp: datetime
    likes: int = 0
    replies: int = 0
    reposts: int = 0
    quality: float = 0.5
    promotional_ratio: float = 0.0
    metadata: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_text(self.id, "engagement id")
        _require_text(self.post_id, "post id")
        _require_datetime(self.timestamp, "timestamp")
        object.__setattr__(self, "platform", _normalize(self.platform))
        object.__setattr__(self, "likes", max(self.likes, 0))
        object.__setattr__(self, "replies", max(self.replies, 0))
        object.__setattr__(self, "reposts", max(self.reposts, 0))
        object.__setattr__(self, "quality", _clamp(self.quality))
        object.__setattr__(self, "promotional_ratio", _clamp(self.promotional_ratio))
        object.__setattr__(self, "metadata", _string_metadata(self.metadata))


@dataclass(frozen=True)
class SocialConversation:
    id: str
    project: str
    topic: str
    post_ids: tuple[str, ...]
    started_at: datetime
    quality: float = 0.5
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class SocialTopic:
    id: str
    project: str
    topic: str
    narrative: str | None = None
    weight: float = 0.5
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class SocialSentimentSnapshot:
    id: str
    project: str
    timestamp: datetime
    level: str
    positive: float
    negative: float
    neutral: float
    confidence: float
    price_following: bool | None = None
    metadata: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_text(self.id, "sentiment id")
        _require_text(self.project, "project")
        _require_datetime(self.timestamp, "timestamp")
        object.__setattr__(self, "level", _normalize_sentiment(self.level))
        object.__setattr__(self, "positive", _clamp(self.positive))
        object.__setattr__(self, "negative", _clamp(self.negative))
        object.__setattr__(self, "neutral", _clamp(self.neutral))
        object.__setattr__(self, "confidence", _clamp(self.confidence))


@dataclass(frozen=True)
class SocialInfluenceSnapshot:
    id: str
    project: str
    timestamp: datetime
    author_id: str
    influence: float
    role: str
    credibility: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "influence", _clamp(self.influence))
        object.__setattr__(self, "credibility", _clamp(self.credibility))
        object.__setattr__(self, "role", _normalize(self.role))


@dataclass(frozen=True)
class CommunitySnapshot:
    id: str
    project: str
    platform: str
    timestamp: datetime
    members: int
    active_members: int
    retained_members: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "platform", _normalize(self.platform))
        object.__setattr__(self, "members", max(self.members, 0))
        object.__setattr__(self, "active_members", max(self.active_members, 0))
        object.__setattr__(self, "retained_members", max(self.retained_members, 0))


@dataclass(frozen=True)
class SocialEvent:
    id: str
    project: str
    event_type: str
    timestamp: datetime
    strength: float
    confidence: float


@dataclass(frozen=True)
class ManipulationAssessment:
    level: str
    bot_risk: float
    spam_risk: float
    promotion_risk: float
    coordination_risk: float
    explanation: str

    def __post_init__(self) -> None:
        if self.level not in MANIPULATION_LEVELS:
            raise SocialValidationError(f"Invalid manipulation level: {self.level}")
        object.__setattr__(self, "bot_risk", _clamp(self.bot_risk))
        object.__setattr__(self, "spam_risk", _clamp(self.spam_risk))
        object.__setattr__(self, "promotion_risk", _clamp(self.promotion_risk))
        object.__setattr__(self, "coordination_risk", _clamp(self.coordination_risk))


SocialRecord = (
    SocialPost
    | SocialAuthor
    | SocialAccount
    | SocialMention
    | SocialEngagement
    | SocialConversation
    | SocialTopic
    | SocialSentimentSnapshot
    | SocialInfluenceSnapshot
    | CommunitySnapshot
    | SocialEvent
)


@dataclass(frozen=True)
class SocialDataset:
    project: str = "global-crypto"
    posts: tuple[SocialPost, ...] = ()
    authors: tuple[SocialAuthor, ...] = ()
    accounts: tuple[SocialAccount, ...] = ()
    mentions: tuple[SocialMention, ...] = ()
    engagements: tuple[SocialEngagement, ...] = ()
    conversations: tuple[SocialConversation, ...] = ()
    topics: tuple[SocialTopic, ...] = ()
    sentiment: tuple[SocialSentimentSnapshot, ...] = ()
    influence: tuple[SocialInfluenceSnapshot, ...] = ()
    communities: tuple[CommunitySnapshot, ...] = ()
    events: tuple[SocialEvent, ...] = ()
    duplicates: tuple[str, ...] = ()
    filtered: tuple[str, ...] = ()
    missing_fields: tuple[str, ...] = ()
    narrative_alignment: float = 0.0


@dataclass(frozen=True)
class SocialIndicator:
    name: str
    value: float
    direction: str
    confidence: float
    description: str
    missing_evidence: tuple[str, ...] = ()
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class SocialAnalysis:
    indicators: tuple[SocialIndicator, ...]
    manipulation: ManipulationAssessment
    attention_level: str
    attention_trend: str
    community_quality: str
    sentiment_structure: str
    strengths: tuple[str, ...] = ()
    risks: tuple[str, ...] = ()
    missing_evidence: tuple[str, ...] = ()
    metadata: dict[str, str] = field(default_factory=dict)


def _normalize(value: str) -> str:
    return value.strip().lower().replace("-", "_").replace(" ", "_")


def _normalize_sentiment(value: str) -> str:
    normalized = _normalize(value)
    return normalized if normalized in SENTIMENT_LEVELS else "uncertain"


def _require_text(value: str, field_name: str) -> None:
    if not value or not value.strip():
        raise SocialValidationError(f"Missing {field_name}")


def _require_datetime(value: datetime, field_name: str) -> None:
    if not isinstance(value, datetime):
        raise SocialValidationError(f"Missing {field_name}")


def _clamp(value: float) -> float:
    return min(max(float(value), 0.0), 1.0)


def _string_metadata(metadata: dict[str, Any]) -> dict[str, str]:
    return {str(key): str(value) for key, value in metadata.items()}
