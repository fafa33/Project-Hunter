# On-chain Intelligence Engine

## Architecture

The On-chain Intelligence Engine is a concrete Project Hunter Intelligence Engine built on the existing Intelligence Engine Framework and Plugin Architecture.

It evaluates blockchain activity, capital flows, holder structure, transaction behavior, contract activity, cross-chain movement, decentralization, concentration, and anomalous on-chain behavior. It emits standardized Intelligence objects only.

## Mission

The engine evaluates what is happening on-chain and distinguishes organic usage from artificial activity, durable capital movement from circular flows, broad adoption from concentrated control, and productive contract use from spam or wash activity.

It does not produce Opportunity Timing, trading signals, recommendations, rankings, portfolio actions, automation, dashboards, or reports.

## Difference from Whale Intelligence

Whale Intelligence focuses on large-capital wallet behavior and smart-money movement.

On-chain Intelligence focuses on network-level activity, address quality, capital-flow structure, holder distribution, contract usage, validator and governance participation, and anomaly controls. It does not duplicate wallet-attribution responsibilities owned by Whale Intelligence.

## Difference from Protocol Intelligence

Protocol Intelligence evaluates operational health, protocol usage, resilience, economics, and sustainability.

On-chain Intelligence evaluates chain-observed behavior and normalized blockchain activity. It does not duplicate protocol operational-health responsibilities owned by Protocol Intelligence.

## Data Flow

1. Provider-agnostic collectors return canonical on-chain records.
2. The normalizer deduplicates records, detects overlapping aggregation windows, preserves chain and asset identity, and groups records by domain.
3. Reusable analyzers calculate flows, holder structure, contract behavior, and anomaly risk.
4. Indicators produce deterministic measurements.
5. The analyzer creates an on-chain analysis.
6. The confidence model evaluates completeness, quality, freshness, chain coverage, asset coverage, historical depth, duplicate filtering quality, and cross-engine alignment.
7. The engine generates standardized Intelligence.
8. The pipeline emits Intelligence through `PipelineContext`.

## Canonical Models

Implemented immutable canonical records:

- `AddressSnapshot`
- `TransactionSnapshot`
- `TransferSnapshot`
- `CapitalFlowSnapshot`
- `ExchangeFlowSnapshot`
- `BridgeFlowSnapshot`
- `StakingFlowSnapshot`
- `HolderSnapshot`
- `SupplyDistributionSnapshot`
- `ContractActivitySnapshot`
- `ContractDeploymentSnapshot`
- `ApplicationActivitySnapshot`
- `TreasuryActivitySnapshot`
- `MintBurnSnapshot`
- `ValidatorDistributionSnapshot`
- `GovernanceActivitySnapshot`
- `OnchainEvent`
- `AnomalyAssessment`

Records include explicit timestamps, project, asset, chain, source, reference, reliability, optional block height, optional transaction hash, optional contract address, optional token denomination, attribution quality, entity-label quality, and metadata.

## Flow Analysis

Implemented deterministic flow analysis includes:

- net capital flow
- exchange netflow
- bridge netflow
- staking netflow
- capital retention
- circular-flow risk
- bridge pass-through risk

The engine distinguishes gross and net flows where canonical records provide the necessary fields. Missing values remain missing and do not get fabricated.

## Holder Analysis

Implemented holder analysis includes:

- holder growth
- holder retention
- long-term holder growth
- holder concentration
- supply-distribution quality
- accumulation breadth
- distribution breadth
- dormancy

Insider or treasury concentration is represented only when attribution evidence is supplied.

## Contract Analysis

Implemented contract analysis includes:

- active contract growth
- contract diversity
- deployment growth
- interaction breadth
- application concentration
- spam or generated contract risk

Provider-specific contract classification is not hardcoded into the engine.

## Indicators

Implemented deterministic indicators include address momentum, transaction growth, adjusted volume growth, capital flow, exchange flow, bridge flow, staking flow, holder quality, supply quality, contract activity, token velocity, dormancy, churn, validator concentration, governance participation, anomaly risks, acceleration, and deterioration.

Indicators consume normalized inputs only and never call external APIs.

## Anomaly Controls

The anomaly model represents:

- circular flows
- wash activity
- sybil activity
- bot activity
- bridge pass-through

It assigns `detected`, `suspected`, or `insufficient_evidence` according to configured thresholds.

## Confidence Model

Confidence depends on:

- evidence completeness
- source reliability
- attribution quality
- entity-label quality
- data freshness
- chain coverage
- asset coverage
- historical depth
- duplicate and overlap filtering quality
- cross-engine alignment when supplied

Confidence is not a ranking, forecast, recommendation, or price signal.

## Configuration

Configuration lives in `configs/onchain_engine.yaml`.

It controls enabled state, project scope, priority, freshness windows, historical depth, thresholds, anomaly levels, duplicate and overlap handling, denomination preservation, chain priorities, provider priorities, and confidence weights.

## Cross-engine Integration

The engine may consume existing standardized Intelligence objects from `PipelineContext.intelligence`.

It does not directly invoke Whale, Protocol, News, Narrative, Social, Macro, or Developer engines.

## Plugin Integration

The engine registers through the existing `hunter.plugins` entry point as `onchain-intelligence`.

It does not introduce another registration system.

## Pipeline Integration

The plugin executes the engine through `EngineRunner` and emits Intelligence only through `PipelineContext`.

The core pipeline does not hardcode this engine.

## Future Providers

Future collectors may support chain explorers, archival nodes, indexers, Dune, Nansen, Arkham, Glassnode, CryptoQuant, Allium, Flipside, SubQuery, The Graph, protocol-specific indexers, bridge dashboards, and staking dashboards.

Provider implementations must emit canonical records and must not bypass the Intelligence Layer.

## Known Limitations

- No live providers are implemented.
- Tests use deterministic fixtures and no network access.
- Advanced attribution, identity resolution, fiat conversion, provider reconciliation, and chain-specific semantics require future provider-specific collectors.
- No scoring, ranking, report, automation, scheduler, dashboard, trading, or recommendation behavior is implemented.
