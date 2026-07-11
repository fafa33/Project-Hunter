from __future__ import annotations

from hunter.intelligence.engines.social.configuration import SocialEngineConfiguration
from hunter.intelligence.engines.social.models import (
    CommunitySnapshot,
    SocialAccount,
    SocialAuthor,
    SocialConversation,
    SocialDataset,
    SocialEngagement,
    SocialEvent,
    SocialInfluenceSnapshot,
    SocialMention,
    SocialPost,
    SocialRecord,
    SocialSentimentSnapshot,
    SocialTopic,
)
from hunter.intelligence.intelligence import Intelligence


class SocialNormalizer:
    def __init__(self, configuration: SocialEngineConfiguration | None = None) -> None:
        self.configuration = configuration or SocialEngineConfiguration()

    def normalize(
        self, records: tuple[SocialRecord, ...], intelligence: tuple[Intelligence, ...] = ()
    ) -> SocialDataset:
        posts: dict[str, SocialPost] = {}
        authors: dict[str, SocialAuthor] = {}
        accounts: dict[str, SocialAccount] = {}
        mentions: dict[str, SocialMention] = {}
        engagements: dict[str, SocialEngagement] = {}
        conversations: dict[str, SocialConversation] = {}
        topics: dict[str, SocialTopic] = {}
        sentiment: dict[str, SocialSentimentSnapshot] = {}
        influence: dict[str, SocialInfluenceSnapshot] = {}
        communities: dict[str, CommunitySnapshot] = {}
        events: dict[str, SocialEvent] = {}
        duplicates: list[str] = []
        filtered: list[str] = []

        for record in records:
            if isinstance(record, SocialPost):
                key = record.duplicate_key or _post_key(record)
                if record.spam or (record.repost and not self.configuration.include_reposts):
                    filtered.append(record.id)
                    continue
                existing = posts.get(key)
                if existing and self.configuration.duplicate_detection:
                    duplicates.append(record.id)
                    continue
                posts[key] = record
            elif isinstance(record, SocialAuthor):
                authors[record.id] = record
            elif isinstance(record, SocialAccount):
                accounts[record.id] = record
            elif isinstance(record, SocialMention):
                mentions[record.id] = record
            elif isinstance(record, SocialEngagement):
                engagements[record.id] = record
            elif isinstance(record, SocialConversation):
                conversations[record.id] = record
            elif isinstance(record, SocialTopic):
                topics[record.id] = record
            elif isinstance(record, SocialSentimentSnapshot):
                sentiment[record.id] = record
            elif isinstance(record, SocialInfluenceSnapshot):
                influence[record.id] = record
            elif isinstance(record, CommunitySnapshot):
                communities[record.id] = record
            elif isinstance(record, SocialEvent):
                events[record.id] = record

        project = _project(records, self.configuration.project)
        missing = _missing(posts, authors, engagements, sentiment, communities)
        return SocialDataset(
            project=project,
            posts=tuple(sorted(posts.values(), key=lambda item: (item.timestamp.isoformat(), item.id))),
            authors=tuple(sorted(authors.values(), key=lambda item: item.id)),
            accounts=tuple(sorted(accounts.values(), key=lambda item: item.id)),
            mentions=tuple(sorted(mentions.values(), key=lambda item: item.id)),
            engagements=tuple(sorted(engagements.values(), key=lambda item: item.id)),
            conversations=tuple(sorted(conversations.values(), key=lambda item: item.id)),
            topics=tuple(sorted(topics.values(), key=lambda item: item.id)),
            sentiment=tuple(sorted(sentiment.values(), key=lambda item: item.timestamp.isoformat())),
            influence=tuple(sorted(influence.values(), key=lambda item: item.id)),
            communities=tuple(sorted(communities.values(), key=lambda item: item.timestamp.isoformat())),
            events=tuple(sorted(events.values(), key=lambda item: item.id)),
            duplicates=tuple(sorted(set(duplicates))),
            filtered=tuple(sorted(set(filtered))),
            missing_fields=missing,
            narrative_alignment=_narrative_alignment(intelligence, tuple(topics.values()), tuple(posts.values())),
        )


def _post_key(post: SocialPost) -> str:
    normalized_text = " ".join(post.text.lower().split())
    return f"{post.platform}|{post.project.lower()}|{normalized_text}|{post.timestamp.date().isoformat()}"


def _project(records: tuple[SocialRecord, ...], default: str) -> str:
    projects = {getattr(record, "project", "") for record in records if getattr(record, "project", "")}
    return sorted(projects)[0] if projects else default


def _missing(
    posts: dict[str, SocialPost],
    authors: dict[str, SocialAuthor],
    engagements: dict[str, SocialEngagement],
    sentiment: dict[str, SocialSentimentSnapshot],
    communities: dict[str, CommunitySnapshot],
) -> tuple[str, ...]:
    groups = {
        "posts": posts,
        "authors": authors,
        "engagements": engagements,
        "sentiment": sentiment,
        "communities": communities,
    }
    return tuple(sorted(name for name, values in groups.items() if not values))


def _narrative_alignment(
    intelligence: tuple[Intelligence, ...],
    topics: tuple[SocialTopic, ...],
    posts: tuple[SocialPost, ...],
) -> float:
    narrative_categories = {
        value
        for item in intelligence
        if item.engine == "narrative-intelligence"
        for value in str(item.metadata.get("lifecycle", "")).replace(":", ",").split(",")
        if value
    }
    topic_values = {topic.narrative or topic.topic for topic in topics}
    post_topics = {post.topic for post in posts}
    overlap = narrative_categories & (topic_values | post_topics)
    denominator = max(len(narrative_categories), 1)
    return min(len(overlap) / denominator, 1.0) if narrative_categories else 0.0
