# Developer Intelligence Engine

## Architecture

The Developer Intelligence Engine is a concrete Project Hunter Intelligence Engine.

It evaluates developer activity, engineering health, delivery quality, ecosystem contribution, and development momentum. It produces standardized Intelligence Layer objects only. It does not produce recommendations, trading signals, rankings, reports, automation, scheduling, or Opportunity Timing.

## Mission

The engine determines whether a project is actively building, maintaining, shipping, and attracting meaningful engineering participation.

It distinguishes durable engineering progress from superficial repository activity by accounting for contributor concentration, bot activity, repository classification, release behavior, review activity, issue resolution, and historical depth.

## Data Flow

1. Replaceable collectors provide canonical developer records.
2. The normalizer filters bots, archived repositories, duplicates, and invalid records.
3. Indicators derive deterministic developer measurements.
4. The analyzer creates a DeveloperAnalysis.
5. The confidence model evaluates source quality, repository coverage, attribution quality, freshness, completeness, and historical depth.
6. The engine generates standardized Intelligence.
7. The EngineRunner validates and emits intelligence through PipelineContext.

## Canonical Models

The canonical immutable records are:

- DeveloperSnapshot
- RepositorySnapshot
- ContributorSnapshot
- ReleaseSnapshot
- PullRequestSnapshot
- IssueSnapshot
- DeveloperEvent

Each record uses explicit timestamps, source references, reliability, metadata, and validation.

## Repository Classification

Repositories can be classified as core or peripheral and active or archived.

Archived repositories are excluded by default. Core repository selection can be configured. Repository aliases are preserved for future provider reconciliation.

## Indicators

Implemented deterministic indicators include:

- Commit momentum.
- Contributor growth.
- Contributor concentration.
- Developer retention.
- Release cadence.
- Release consistency.
- Pull request throughput.
- Issue resolution efficiency.
- Code review health.
- Repository maintenance health.
- Ecosystem breadth.
- Roadmap delivery consistency.
- Engineering activity quality.
- Development acceleration.
- Development deterioration.

Indicators consume normalized inputs only and never call external APIs.

## Quality Controls

The engine does not treat raw commit counts as sufficient evidence of engineering strength.

It accounts for bot activity, automated commits, archived repositories, contributor concentration, duplicated records, non-core repository concentration, release stagnation, unresolved work, and missing evidence.

## Confidence Model

Confidence depends on:

- Source reliability.
- Repository coverage.
- Repository classification quality.
- Contributor attribution quality.
- Evidence freshness.
- Data completeness.
- Cross-repository coverage.
- Historical depth.
- Bot and automation filtering quality.

Confidence measures evidence quality and uncertainty. It is not a ranking, forecast, or recommendation.

## Configuration

Configuration lives in `configs/developer_engine.yaml`.

It controls enabled state, project scope, priority, core repository selection, archived repository handling, bot filtering, freshness windows, historical depth, concentration thresholds, indicator thresholds, and confidence weights.

## Plugin Integration

The engine registers through the existing `hunter.plugins` entry point group as `developer-intelligence`.

It does not introduce a parallel registration system.

## Pipeline Integration

The plugin executes the Developer Intelligence Engine through `EngineRunner` and emits Intelligence only through `PipelineContext`.

The pipeline does not hardcode the engine into execution order.

## Future Providers

Future collectors may include GitHub, GitLab, release feeds, package registries, official developer portals, SDK registries, and ecosystem development indexes.

Provider implementations must emit canonical developer records and must not bypass the Intelligence Layer.

## Known Limitations

The MVP implementation is provider-agnostic and fixture-driven. It does not perform live network collection.

Contributor identity resolution, roadmap verification, generated-code detection, repository mirror detection, and cross-provider reconciliation are represented by contracts and quality controls but require future provider-specific enrichment.
