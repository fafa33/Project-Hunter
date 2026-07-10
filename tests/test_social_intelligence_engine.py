from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from hunter.intelligence import Confidence, Intelligence
from hunter.intelligence.engines.social import (
    CommunitySnapshot,
    SocialAccount,
    SocialAuthor,
    SocialEngagement,
    SocialIntelligenceEngine,
    SocialPost,
    SocialSentimentSnapshot,
    create_plugin,
)
from hunter.intelligence.engines.social.analyzers import SocialAnalyzer
from hunter.intelligence.engines.social.collectors import ContextSocialCollector
from hunter.intelligence.engines.social.confidence import SocialConfidenceModel
from hunter.intelligence.engines.social.exceptions import SocialValidationError
from hunter.intelligence.engines.social.indicators import SocialIndicatorCalculator
from hunter.intelligence.engines.social.influence import SocialInfluenceModel
from hunter.intelligence.engines.social.manipulation import SocialManipulationModel
from hunter.intelligence.engines.social.models import SocialConversation, SocialTopic
from hunter.intelligence.engines.social.normalization import SocialNormalizer
from hunter.intelligence.engines.social.sentiment import SocialSentimentModel
from hunter.pipeline import PipelineOrchestrator
from hunter.plugins.contracts import PipelineContext
from hunter.plugins.manager import PluginManager

NOW = datetime(2026, 7, 10, tzinfo=UTC)


def author(
    author_id: str,
    *,
    role: str = "community",
    followers: int = 1_000,
    credibility: float = 0.7,
    expertise: float = 0.5,
    bot_probability: float = 0.05,
    project_owned: bool = False,
) -> SocialAuthor:
    return SocialAuthor(
        id=author_id,
        platform="x",
        handle=author_id,
        role=role,
        credibility=credibility,
        domain_expertise=expertise,
        follower_count=followers,
        attribution_quality=0.8,
        bot_probability=bot_probability,
        project_owned=project_owned,
    )


def post(
    post_id: str,
    author_id: str,
    days_ago: int,
    *,
    platform: str = "x",
    text: str | None = None,
    sentiment: str = "positive",
    topic: str = "ai",
    duplicate_key: str = "",
    repost: bool = False,
    spam: bool = False,
    promotional: bool = False,
    coordinated: bool = False,
) -> SocialPost:
    return SocialPost(
        id=post_id,
        platform=platform,
        project="api3",
        author_id=author_id,
        timestamp=NOW - timedelta(days=days_ago),
        text=text or f"{topic} social evidence {post_id}",
        source="fixture",
        reference=f"https://example.test/social/{post_id}",
        language="en",
        sentiment=sentiment,
        topic=topic,
        duplicate_key=duplicate_key,
        repost=repost,
        spam=spam,
        promotional=promotional,
        coordinated=coordinated,
    )


def engagement(post_id: str, days_ago: int, likes: int, replies: int, reposts: int, *, quality: float = 0.75) -> SocialEngagement:
    return SocialEngagement(
        id=f"engagement-{post_id}",
        post_id=post_id,
        platform="x",
        timestamp=NOW - timedelta(days=days_ago),
        likes=likes,
        replies=replies,
        reposts=reposts,
        quality=quality,
        promotional_ratio=0.1,
    )


def community(snapshot_id: str, days_ago: int, members: int, active: int, retained: int, *, platform: str = "discord") -> CommunitySnapshot:
    return CommunitySnapshot(
        id=snapshot_id,
        project="api3",
        platform=platform,
        timestamp=NOW - timedelta(days=days_ago),
        members=members,
        active_members=active,
        retained_members=retained,
    )


def sentiment(snapshot_id: str, days_ago: int, positive: float, negative: float) -> SocialSentimentSnapshot:
    return SocialSentimentSnapshot(
        id=snapshot_id,
        project="api3",
        timestamp=NOW - timedelta(days=days_ago),
        level="positive" if positive >= negative else "negative",
        positive=positive,
        negative=negative,
        neutral=max(0.0, 1.0 - positive - negative),
        confidence=0.8,
    )


def social_records():
    return (
        author("project", role="project", followers=20_000, credibility=0.8, expertise=0.8, project_owned=True),
        author("researcher", role="researcher", followers=15_000, credibility=0.9, expertise=0.9),
        author("builder", role="developer", followers=5_000, credibility=0.85, expertise=0.95),
        author("community", role="community", followers=1_000, credibility=0.7, expertise=0.4),
        SocialAccount(id="api3-x", platform="x", project="api3", handle="api3dao", project_owned=True),
        post("old-1", "project", 120, sentiment="neutral", topic="oracle"),
        post("recent-1", "researcher", 5, platform="x", sentiment="positive", topic="ai"),
        post("recent-2", "builder", 4, platform="reddit", sentiment="positive", topic="ai"),
        post("recent-3", "community", 3, platform="discord", sentiment="mixed", topic="oracle"),
        post("duplicate", "community", 2, text="duplicate social evidence", duplicate_key="same"),
        post("duplicate-low", "community", 2, text="duplicate social evidence", duplicate_key="same"),
        post("repost", "community", 1, repost=True),
        post("spam", "community", 1, spam=True),
        engagement("old-1", 120, 20, 3, 2, quality=0.55),
        engagement("recent-1", 5, 120, 30, 20),
        engagement("recent-2", 4, 100, 25, 18),
        SocialConversation(id="conversation-1", project="api3", topic="ai", post_ids=("recent-1", "recent-2"), started_at=NOW, quality=0.85),
        SocialTopic(id="topic-ai", project="api3", topic="ai", narrative="ai", weight=0.9),
        sentiment("sentiment-old", 90, 0.35, 0.25),
        sentiment("sentiment-new", 1, 0.68, 0.12),
        community("community-old", 90, 5_000, 1_000, 600),
        community("community-new", 1, 7_500, 2_000, 1_500),
    )


def dataset():
    return SocialNormalizer().normalize(social_records())


def test_context_collector_reads_social_records() -> None:
    context = PipelineContext(values={"social_records": list(social_records())})

    collected = ContextSocialCollector().collect(context)

    assert len(collected) == len(social_records())
    assert isinstance(collected[0], SocialAuthor)


def test_canonical_models_reject_invalid_social_posts() -> None:
    with pytest.raises(SocialValidationError):
        post("", "researcher", 1, text="")


def test_normalization_suppresses_duplicates_reposts_and_spam_and_preserves_owned_accounts() -> None:
    normalized = dataset()

    assert "duplicate-low" in normalized.duplicates
    assert "repost" in normalized.filtered
    assert "spam" in normalized.filtered
    assert any(account.project_owned for account in normalized.accounts)
    assert all(not item.repost and not item.spam for item in normalized.posts)


def test_influence_scores_quality_beyond_follower_count_and_concentration() -> None:
    model = SocialInfluenceModel()
    high_quality = author("expert", role="researcher", followers=1_000, credibility=0.95, expertise=0.95)
    low_quality = author("popular", followers=1_000_000, credibility=0.2, expertise=0.1, bot_probability=0.8)

    assert model.score_author(high_quality) > model.score_author(low_quality)
    assert 0.0 <= model.concentration(dataset()) <= 1.0


def test_indicators_cover_social_attention_community_influence_and_sentiment() -> None:
    indicators = {item.name: item for item in SocialIndicatorCalculator().calculate(dataset())}

    assert indicators["mention_momentum"].value > 0.0
    assert indicators["unique_author_growth"].value > 0.0
    assert indicators["engagement_quality"].value > 0.0
    assert indicators["community_growth"].value > 0.0
    assert indicators["influence_quality"].value > 0.0
    assert indicators["sentiment_momentum"].value > 0.0


def test_sentiment_model_detects_level_momentum_and_dispersion() -> None:
    normalized = dataset()
    model = SocialSentimentModel()

    assert model.level(normalized) == "positive"
    assert model.momentum(normalized) > 0.0
    assert model.dispersion(normalized) >= 0.0


def test_manipulation_model_detects_promotional_and_coordination_risk() -> None:
    noisy = SocialNormalizer().normalize(
        (
            author("promo", bot_probability=0.7),
            post("promo-1", "promo", 1, promotional=True, coordinated=True),
            post("promo-2", "promo", 1, promotional=True, coordinated=True, duplicate_key="promo"),
            post("promo-3", "promo", 1, promotional=True, coordinated=True, duplicate_key="promo"),
            engagement("promo-1", 1, 5, 1, 1, quality=0.2),
        )
    )

    assessment = SocialManipulationModel().assess(noisy)

    assert assessment.level in {"suspected", "detected"}
    assert assessment.bot_risk > 0.0
    assert assessment.promotion_risk > 0.0
    assert assessment.coordination_risk > 0.0


def test_narrative_intelligence_metadata_increases_alignment() -> None:
    narrative = Intelligence(
        id="narrative-fixture",
        project="api3",
        engine="narrative-intelligence",
        signals=(),
        evidence=(),
        observations=(),
        insights=(),
        confidence=Confidence.calculate(completeness=1.0, evidence_quality=1.0, freshness=1.0, uncertainty=0.0),
        generated_at=NOW,
        metadata={"lifecycle": "ai:acceleration"},
    )

    normalized = SocialNormalizer().normalize(social_records(), (narrative,))

    assert normalized.narrative_alignment > 0.0


def test_confidence_increases_with_richer_fresher_social_evidence() -> None:
    sparse = SocialNormalizer().normalize((post("single", "unknown", 1),))
    rich = dataset()

    sparse_confidence = SocialConfidenceModel().calculate(sparse)
    rich_confidence = SocialConfidenceModel().calculate(rich)

    assert rich_confidence.score > sparse_confidence.score
    assert rich_confidence.completeness > sparse_confidence.completeness


def test_analyzer_generates_strengths_risks_and_missing_evidence() -> None:
    analysis = SocialAnalyzer().analyze(dataset())

    assert analysis.indicators
    assert analysis.strengths
    assert analysis.attention_level in {"low", "moderate", "elevated", "saturated"}
    assert analysis.community_quality in {"unknown", "low", "moderate", "high"}


def test_social_engine_generates_standardized_intelligence() -> None:
    context = PipelineContext(values={"social_records": list(social_records())})
    engine = SocialIntelligenceEngine()

    collected = engine.collect(context)
    analysis = engine.analyze(context, collected)
    intelligence = engine.generate_intelligence(context, analysis)

    assert isinstance(intelligence, Intelligence)
    assert intelligence.engine == "social-intelligence"
    assert intelligence.project == "api3"
    assert intelligence.signals
    assert intelligence.evidence
    assert intelligence.observations
    assert intelligence.insights
    assert intelligence.metadata.get("attention_level") in {"low", "moderate", "elevated", "saturated"}


def test_social_plugin_registration_exposes_plugin_contract() -> None:
    plugin = create_plugin()

    assert plugin.metadata.id == "social-intelligence"
    assert "social-intelligence" in plugin.metadata.capabilities


def test_plugin_manager_loads_social_plugin_from_configuration(tmp_path) -> None:
    config = tmp_path / "plugins.yaml"
    config.write_text(
        """
enabled:
  social-intelligence: true
configuration: {}
load_order:
  - social-intelligence
priorities: {}
module_paths:
  - hunter.intelligence.engines.social:create_plugin
""",
        encoding="utf-8",
    )

    loaded = PluginManager().load(config_path=config)

    assert [plugin.metadata.id for plugin in loaded] == ["social-intelligence"]


def test_pipeline_executes_social_engine_plugin(tmp_path) -> None:
    config = tmp_path / "plugins.yaml"
    config.write_text(
        """
enabled:
  social-intelligence: true
configuration: {}
load_order:
  - social-intelligence
priorities: {}
module_paths:
  - hunter.intelligence.engines.social:create_plugin
""",
        encoding="utf-8",
    )
    context = PipelineContext(values={"social_records": list(social_records())})

    result = PipelineOrchestrator().run(context=context, config_path=config)

    assert len(result.intelligence) == 1
    assert result.intelligence[0].engine == "social-intelligence"
    assert "social:intelligence:execute" in result.events
