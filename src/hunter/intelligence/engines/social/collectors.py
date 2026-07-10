from __future__ import annotations

from typing import Protocol, runtime_checkable

from hunter.intelligence.engines.social.models import SocialRecord
from hunter.plugins.contracts import PipelineContext


@runtime_checkable
class SocialCollector(Protocol):
    id: str

    def collect(self, context: PipelineContext) -> tuple[SocialRecord, ...]:
        raise NotImplementedError


class ContextSocialCollector:
    id = "context"

    def collect(self, context: PipelineContext) -> tuple[SocialRecord, ...]:
        value = context.get("social_records", ())
        if isinstance(value, tuple | list):
            return tuple(item for item in value if _is_social_record(item))
        return (value,) if _is_social_record(value) else ()


def _is_social_record(value: object) -> bool:
    from hunter.intelligence.engines.social.models import (
        CommunitySnapshot,
        SocialAccount,
        SocialAuthor,
        SocialConversation,
        SocialEngagement,
        SocialEvent,
        SocialInfluenceSnapshot,
        SocialMention,
        SocialPost,
        SocialSentimentSnapshot,
        SocialTopic,
    )

    return isinstance(
        value,
        (
            SocialPost,
            SocialAuthor,
            SocialAccount,
            SocialMention,
            SocialEngagement,
            SocialConversation,
            SocialTopic,
            SocialSentimentSnapshot,
            SocialInfluenceSnapshot,
            CommunitySnapshot,
            SocialEvent,
        ),
    )
