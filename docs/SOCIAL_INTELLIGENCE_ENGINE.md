# Social Intelligence Engine

## Purpose

The Social Intelligence Engine standardizes project-level social attention, influence, sentiment, community quality, and manipulation-risk analysis into Project Hunter Intelligence objects.

The engine does not produce recommendations, opportunity timing, trading signals, rankings, or scores. It emits evidence-backed intelligence for downstream orchestration.

## Architecture

The engine lives under `src/hunter/intelligence/engines/social/` and follows the permanent Intelligence Engine Framework.

Implemented components:

- `engine.py`: concrete engine and plugin integration.
- `models.py`: canonical social records and analysis models.
- `collectors.py`: provider-agnostic collector contract and context fixture collector.
- `normalization.py`: canonical normalization, duplicate suppression, repost filtering, spam filtering, and narrative alignment.
- `analyzers.py`: deterministic analysis over normalized records.
- `indicators.py`: social attention, community, influence, sentiment, narrative, and manipulation indicators.
- `influence.py`: influence quality and concentration model.
- `sentiment.py`: sentiment level, momentum, and dispersion model.
- `manipulation.py`: bot, spam, promotion, and coordination risk model.
- `confidence.py`: social confidence calculation.
- `configuration.py`: YAML-backed configuration.

## Collector Contract

Collectors implement `SocialCollector` and return canonical social records. Providers remain external to the engine contract. Future collectors may support social platforms, forums, governance spaces, or public datasets without changing orchestration.

The MVP includes `ContextSocialCollector`, which reads validated social records from `PipelineContext` for deterministic execution and tests.

## Canonical Inputs

Supported canonical records include:

- `SocialAuthor`
- `SocialAccount`
- `SocialPost`
- `SocialMention`
- `SocialEngagement`
- `SocialConversation`
- `SocialTopic`
- `SocialSentimentSnapshot`
- `SocialInfluenceSnapshot`
- `CommunitySnapshot`
- `SocialEvent`

Invalid canonical records are rejected at model construction.

## Normalization

Normalization is deterministic and provider-independent.

Implemented normalization behavior:

- Normalize platform, language, role, topic, and sentiment labels.
- Clamp probabilistic fields into `0.0` to `1.0`.
- Suppress duplicate posts when duplicate detection is enabled.
- Exclude spam posts.
- Exclude reposts unless configured otherwise.
- Preserve project-owned account classification.
- Track missing evidence groups.
- Integrate Narrative Intelligence metadata when available.

## Analysis

The analyzer generates:

- social indicators
- strengths
- risks
- missing evidence
- attention level
- attention trend
- community quality
- sentiment structure
- manipulation assessment

The analysis is deterministic and uses only normalized social records plus already-emitted Narrative Intelligence metadata.

## Confidence

Confidence depends on:

- evidence completeness
- author and engagement quality
- freshness
- platform coverage
- language coverage
- historical depth
- manipulation certainty

Confidence does not depend on optimism or price direction.

## Plugin Integration

The engine registers through the `hunter.plugins` entry point as `social-intelligence`.

The plugin lifecycle is:

1. validate
2. initialize
3. execute through `EngineRunner`
4. shutdown

The engine communicates only through `PipelineContext`.

## Current Limitations

- No live social providers are implemented.
- No paid integrations are implemented.
- No scoring, ranking, report, automation, scheduler, or recommendation behavior is implemented.
- Narrative alignment uses existing Narrative Intelligence metadata when present.
