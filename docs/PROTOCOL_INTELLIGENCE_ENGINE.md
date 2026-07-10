# Protocol Intelligence Engine

## Architecture

The Protocol Intelligence Engine is a concrete Project Hunter Intelligence Engine.

It evaluates operational health, usage, resilience, economics, adoption, and sustainability for crypto protocols. It produces standardized Intelligence Layer objects only. It does not produce recommendations, trading signals, rankings, portfolio decisions, automation, scheduling, reports, dashboards, or Opportunity Timing.

## Mission

The engine determines whether a protocol is genuinely used, economically relevant, technically operational, resilient, and improving.

It distinguishes durable protocol traction from temporary speculation, incentive-driven activity, duplicated metrics, bridge pass-through activity, token-price-driven TVL changes, isolated liquidity campaigns, and concentrated application usage.

## Data Flow

1. Replaceable collectors provide canonical protocol records.
2. The normalizer deduplicates provider records and groups them by protocol domain.
3. Indicators derive deterministic protocol measurements.
4. The analyzer creates a ProtocolAnalysis.
5. The confidence model evaluates source reliability, provider agreement, coverage breadth, chain and deployment coverage, freshness, historical depth, and completeness.
6. The engine generates standardized Intelligence.
7. The EngineRunner validates and emits intelligence through PipelineContext.

## Canonical Models

The canonical immutable records are:

- ProtocolSnapshot
- UsageSnapshot
- UserSnapshot
- TransactionSnapshot
- FeeSnapshot
- RevenueSnapshot
- TVLSnapshot
- LiquiditySnapshot
- ApplicationSnapshot
- ValidatorSnapshot
- IncidentSnapshot
- GovernanceSnapshot
- TreasurySnapshot
- IncentiveSnapshot
- ProtocolEvent

Each record includes explicit timestamps, source references, project and protocol identity, reliability, metadata, and optional chain or deployment context.

## Chain and Deployment Handling

Protocol records may include chain and deployment identifiers.

The normalizer preserves chain and deployment context and supports multiple chains and deployments in the same dataset. It distinguishes records by type, project, protocol, chain, deployment, and timestamp during deduplication.

## Indicators

Implemented deterministic indicators include:

- User growth.
- Returning-user ratio.
- Retention trend.
- Transaction growth.
- Transaction quality.
- Fee growth.
- Revenue growth.
- Fee-to-revenue conversion.
- TVL growth.
- Organic TVL ratio.
- Liquidity depth.
- Liquidity stability.
- Capital efficiency.
- Utilization.
- Application breadth.
- Application concentration.
- Network reliability.
- Incident frequency.
- Incident severity trend.
- Validator health.
- Governance participation.
- Treasury runway.
- Incentive dependence.
- Emissions dependence.
- Value capture efficiency.
- Ecosystem expansion.
- Protocol resilience.
- Protocol acceleration.
- Protocol deterioration.

Indicators consume normalized inputs only and never call external APIs.

## Quality Controls

The engine does not treat raw activity growth as sufficient proof of protocol strength.

It accounts for wash activity, bot activity, sybil activity, incentive farming, duplicate transactions, bridge pass-through activity, liquidity mining, token price effects on TVL, duplicated provider metrics, protocol migrations, chain migrations, incidents, emissions dependence, treasury stress, and application concentration.

## Confidence Model

Confidence depends on:

- Source reliability.
- Provider agreement.
- Coverage breadth.
- Chain coverage.
- Deployment coverage.
- Data freshness.
- Historical depth.
- Economic metric completeness.
- Incident reporting availability.
- Cross-source consistency.

Confidence measures evidence quality and uncertainty. It is not a ranking, forecast, or recommendation.

## Configuration

Configuration lives in `configs/protocol_engine.yaml`.

It controls enabled state, project and protocol scope, priority, chain and deployment classification, freshness windows, activity-quality thresholds, user-retention thresholds, TVL normalization thresholds, liquidity thresholds, concentration thresholds, incident severity thresholds, incentive and emissions dependence thresholds, treasury runway thresholds, minimum historical depth, confidence weights, and provider priorities.

## Plugin Integration

The engine registers through the existing `hunter.plugins` entry point group as `protocol-intelligence`.

It does not introduce a parallel registration system.

## Pipeline Integration

The plugin executes the Protocol Intelligence Engine through `EngineRunner` and emits Intelligence only through `PipelineContext`.

The pipeline does not hardcode the engine into execution order.

## Future Providers

Future collectors may include DefiLlama, chain explorers, protocol APIs, Dune, Token Terminal, Artemis, governance portals, validator dashboards, treasury dashboards, incident databases, and official protocol endpoints.

Provider implementations must emit canonical protocol records and must not bypass the Intelligence Layer.

## Known Limitations

The MVP implementation is provider-agnostic and fixture/context-driven. It does not perform live network collection.

Wash trading detection, sybil detection, token-price-adjusted TVL normalization, attribution reconciliation, protocol migration handling, and cross-provider metric reconciliation are represented by canonical fields and quality controls but require future provider-specific enrichment.
