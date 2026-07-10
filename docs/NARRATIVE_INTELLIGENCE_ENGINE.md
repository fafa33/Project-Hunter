# Narrative Intelligence Engine

## Architecture

The Narrative Intelligence Engine is a concrete Project Hunter Intelligence Engine.

It detects, models, tracks, and evaluates crypto market narratives. The engine produces standardized Intelligence Layer objects only. It does not produce sentiment scores, recommendations, trading signals, automation, scheduling, dashboards, reports, Fusion Engine outputs, or Opportunity Timing.

## Mission

The engine identifies structural narrative shifts before they become consensus.

It answers which narratives are emerging, accelerating, saturated, fading, ignored, institutional, retail-driven, and evidence-supported.

## Data Flow

1. Replaceable collectors provide canonical narrative evidence.
2. The normalizer removes duplicates, spam, paid promotion, unsupported categories, and low-quality evidence.
3. The clusterer groups related evidence into deterministic narrative clusters.
4. The evolution tracker calculates growth, acceleration, saturation, persistence, and ignored status.
5. The lifecycle model assigns a phase to each narrative.
6. The analyzer creates narratives, signals, trends, events, lifecycles, and relationships.
7. The confidence model evaluates evidence quality, freshness, diversity, cross-engine agreement, and historical persistence.
8. The engine generates standardized Intelligence.
9. The EngineRunner validates and emits intelligence through PipelineContext.

## Canonical Models

The canonical immutable records are:

- Narrative
- NarrativeSignal
- NarrativeCluster
- NarrativeTrend
- NarrativeEvidence
- NarrativeEvent
- NarrativeLifecycle
- NarrativeRelationship
- NarrativeDataset
- NarrativeAnalysis

## Supported Categories

Supported narrative categories include AI, DePIN, RWA, BitcoinFi, Layer-1, Layer-2, Rollups, Gaming, Oracle, DeFi, Privacy, Interoperability, Stablecoins, Tokenization, Restaking, Modular Chains, Consumer Crypto, Identity, Payments, Infrastructure, Cross-chain, Prediction Markets, and Data Availability.

## Lifecycle Model

Narratives are assigned one of the following phases:

- Unknown
- Emerging
- Early Expansion
- Expansion
- Acceleration
- Mainstream
- Crowded
- Saturation
- Decline
- Obsolete

Lifecycle assignment is deterministic and based on normalized evidence growth, acceleration, saturation, persistence, and ignored status.

## Clustering

Clustering is deterministic.

The current implementation groups evidence by normalized narrative category, suppresses duplicate evidence, supports hierarchical institutional and retail subclusters, and preserves parent-child relationships when a narrative has both institutional and retail evidence.

## Evolution

Evolution tracking evaluates:

- Birth through first evidence.
- Growth through recent evidence share.
- Acceleration through recent strength compared with older strength.
- Stagnation through low recent growth.
- Decline through persistence with weak recent growth.
- Death and obsolete states as future extensions.

## Relationships

The engine detects deterministic relationships including:

- Parent narratives.
- Child narratives.
- Competing narratives.
- Complementary narratives.
- Successor narratives.

Relationships are generated from cluster hierarchy and configured narrative adjacency rules.

## Quality Controls

The engine avoids marketing noise, duplicate stories, paid promotion, artificial hype, spam, and low-quality evidence through normalization filters.

Filtered and duplicate evidence remains available as dataset metadata but does not drive narrative intelligence.

## Confidence Model

Confidence depends on:

- Cross-engine agreement.
- News quality.
- Macro consistency.
- Developer support.
- Protocol evidence.
- Whale support.
- Historical persistence.
- Evidence diversity.
- Freshness.

Confidence measures evidence quality and uncertainty. It is not sentiment, prediction, ranking, or recommendation.

## Configuration

Configuration lives in `configs/narrative_engine.yaml`.

It controls enabled state, project scope, engine id, priority, freshness windows, minimum evidence quality, duplicate thresholds, lifecycle thresholds, and confidence weights.

## Plugin Integration

The engine registers through the existing `hunter.plugins` entry point group as `narrative-intelligence`.

It does not introduce a parallel registration system.

## Pipeline Integration

The plugin executes the Narrative Intelligence Engine through `EngineRunner` and emits Intelligence only through `PipelineContext`.

The pipeline does not hardcode the engine into execution order.

## Future Providers

Future collectors may include News, Social, GitHub, Funding, Conferences, Podcasts, Research, Governance, Market Data, and On-chain Observations.

Provider implementations must emit canonical narrative evidence and must not bypass the Intelligence Layer.

## Known Limitations

The MVP implementation is provider-agnostic and fixture/context-driven. It does not perform live provider collection.

Semantic topic merging, advanced topic splitting, cross-language narrative mapping, external social data ingestion, full cross-engine evidence extraction, and historical narrative backfills require future provider-specific enrichment.
