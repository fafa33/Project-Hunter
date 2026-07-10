# Macro Intelligence Engine

## Architecture

The Macro Intelligence Engine is the first concrete Project Hunter Intelligence Engine.

It evaluates the global crypto environment and emits standardized Intelligence Layer objects. It does not produce investment advice, trading signals, recommendations, rankings, reports, or opportunity timing.

The engine is built on the Intelligence Engine Framework and communicates through `PipelineContext`.

## Data Flow

The macro data flow is:

1. Replaceable collectors provide `MacroDataPoint` objects.
2. The normalizer converts incoming data into a canonical `MacroDataset`.
3. Indicators derive reusable macro environment measurements.
4. The analyzer creates a `MacroAnalysis`.
5. The confidence model evaluates evidence quality, freshness, source reliability, completeness, and cross-source agreement.
6. The engine generates standardized `Intelligence`.
7. The `EngineRunner` validates and emits intelligence through `PipelineContext`.

## Indicators

Implemented reusable indicators include:

- Liquidity Expansion.
- Liquidity Contraction.
- Interest Rate Pressure.
- Inflation Pressure.
- Institutional Flow.
- Stablecoin Momentum.
- Risk Appetite.
- Market Cycle.
- Trend Strength.
- Sector Rotation.

Indicators are deterministic and evidence-backed.

## Intelligence Model

Generated macro intelligence contains:

- `Signal` objects for each macro indicator.
- `Evidence` objects for collected macro data points.
- `Observation` objects describing indicator direction.
- `Insight` objects describing risk regime and sector rotation.
- Standardized confidence.
- Metadata describing risk regime, liquidity flow, environment strength, and supported domains.

## Configuration

Configuration lives in `configs/macro_engine.yaml`.

The configuration controls:

- Whether the engine is enabled.
- Project scope.
- Engine id.
- Engine priority.
- Supported domains.
- Deterministic thresholds.

## Future Providers

Collectors are provider-agnostic and replaceable.

Future providers may include public macro, crypto market, stablecoin, ETF, regulatory, institutional, and sector data sources. Provider implementations must emit canonical `MacroDataPoint` objects and must not bypass the Intelligence Layer.

