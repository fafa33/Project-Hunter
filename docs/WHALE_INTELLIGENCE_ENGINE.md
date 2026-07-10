# Whale Intelligence Engine

## Architecture

The Whale Intelligence Engine detects and standardizes large-capital behavior across the crypto ecosystem.

It is a concrete Intelligence Engine built on the Intelligence Engine Framework. It produces standardized Intelligence Layer objects only. It does not produce buy/sell recommendations, trading signals, rankings, reports, automation, scheduling, or Opportunity Timing.

## Data Flow

The whale data flow is:

1. Replaceable collectors provide `WhaleEvent` objects.
2. The normalizer converts events into a canonical `WhaleDataset`.
3. The analyzer detects large-capital behavior patterns.
4. The confidence model evaluates source reliability, wallet attribution quality, confirmation, evidence agreement, and freshness.
5. The engine generates standardized `Intelligence`.
6. The `EngineRunner` validates and emits intelligence through `PipelineContext`.

## Signal Types

Supported signal types include:

- Accumulation.
- Distribution.
- Exchange Flow.
- Smart Money.
- Treasury Movement.
- VC Activity.
- Foundation Activity.
- Liquidity Rotation.
- Cross-chain Flow.
- Long-term Holder Activity.

## Intelligence Model

Generated whale intelligence contains:

- `Signal` objects for each whale behavior category.
- `Evidence` objects for collected whale events.
- `Observation` objects describing event direction and strength.
- `Insight` objects describing exchange flow and large-capital behavior.
- Standardized confidence.
- Metadata describing exchange flow, smart money activity, accumulating assets, distributing assets, and supported signal types.

## Configuration

Configuration lives in `configs/whale_engine.yaml`.

The configuration controls:

- Whether the engine is enabled.
- Project scope.
- Engine id.
- Engine priority.
- Supported signal types.
- Deterministic thresholds.

## Future Providers

Collectors are provider-agnostic and replaceable.

Future providers may include on-chain indexers, public blockchain APIs, public labeled-wallet datasets, bridge activity feeds, exchange wallet datasets, staking movement sources, and ecosystem treasury disclosures. Provider implementations must emit canonical `WhaleEvent` objects and must not bypass the Intelligence Layer.

