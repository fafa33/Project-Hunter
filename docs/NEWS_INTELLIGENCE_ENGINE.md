# News Intelligence Engine

## Architecture

The News Intelligence Engine is a concrete Project Hunter Intelligence Engine.

It transforms crypto news records into standardized Intelligence Layer objects. It detects meaningful events rather than simply collecting headlines. It does not produce trading signals, recommendations, rankings, automation, scheduling, dashboards, reports, or Opportunity Timing.

## Mission

The engine evaluates whether news materially changes the investment or protocol thesis.

It distinguishes signal from noise, facts from rumors, primary sources from secondary reporting, and isolated events from structural changes.

## Data Flow

1. Replaceable collectors provide canonical news records.
2. The normalizer deduplicates articles, filters low-quality records, and preserves source metadata.
3. The classifier converts articles into canonical news events.
4. The analyzer detects material events, thesis change, signal quality, and structural change.
5. The confidence model evaluates source quality, freshness, originality, primary-source coverage, and consistency.
6. The engine generates standardized Intelligence.
7. The EngineRunner validates and emits intelligence through PipelineContext.

## Canonical Models

The canonical immutable records are:

- NewsSourceQuality
- NewsArticle
- NewsEvent
- NewsDataset
- NewsAnalysis

Each article includes source credibility, historical reliability, freshness, originality, primary-source status, project identity, source references, timestamps, affected projects, affected sectors, and quality-control flags.

## News Domains

Supported domains include partnerships, integrations, mainnet launches, testnet launches, tokenomics changes, governance proposals, governance approvals, treasury events, security incidents, exploits, regulatory actions, exchange listings, delistings, funding rounds, institutional adoption, enterprise adoption, ecosystem expansion, developer announcements, protocol upgrades, roadmap milestones, community announcements, foundation announcements, ecosystem grants, strategic acquisitions, and legal events.

## Quality Controls

The engine accounts for duplicate articles, syndicated news, recycled announcements, clickbait, rumors, anonymous sources, low-quality sources, conflicting reports, and outdated news.

Low-quality articles remain represented in the dataset metadata but do not become material events.

## Classification

Every event is classified by:

- Severity.
- Scope.
- Affected projects.
- Affected sectors.
- Permanence.
- Expected impact horizon.
- Confidence.

Classification is deterministic and evidence-backed.

## Confidence Model

Confidence depends on:

- Source credibility.
- Historical source reliability.
- Freshness.
- Originality.
- Primary-source coverage.
- Rumor and conflict handling.
- Duplicate and low-quality record handling.

Confidence measures evidence quality and uncertainty. It is not a ranking, forecast, or recommendation.

## Configuration

Configuration lives in `configs/news_engine.yaml`.

It controls enabled state, project scope, engine id, priority, freshness windows, minimum source quality, duplicate handling thresholds, structural severity thresholds, confidence penalties, confidence weights, and source priorities.

## Plugin Integration

The engine registers through the existing `hunter.plugins` entry point group as `news-intelligence`.

It does not introduce a parallel registration system.

## Pipeline Integration

The plugin executes the News Intelligence Engine through `EngineRunner` and emits Intelligence only through `PipelineContext`.

The pipeline does not hardcode the engine into execution order.

## Future Providers

Future collectors may include official project blogs, governance forums, foundation feeds, major crypto newsrooms, exchange announcements, regulator feeds, security incident databases, RSS feeds, and public disclosure sources.

Provider implementations must emit canonical news records and must not bypass the Intelligence Layer.

## Known Limitations

The MVP implementation is provider-agnostic and fixture/context-driven. It does not perform live network collection.

Semantic duplicate detection, full article fact extraction, source reputation history, conflict resolution across independent providers, and multilingual news normalization are represented by contracts and quality controls but require future provider-specific enrichment.
