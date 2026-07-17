# ADR 0015 — On-chain Intelligence Engine

## Status

Accepted.

## Context

On-chain Intelligence Engine Phase B6 implemented and verified the next production intelligence engine built on ADR 0010. The implementation interprets persisted on-chain evidence and produces deterministic, evidence-backed findings without replacing the canonical production runtime documented in `docs/CANONICAL_RUNTIME_ARCHITECTURE.md`.

Hunter needs deterministic interpretation of persisted on-chain evidence while preserving the existing evidence, replay, authority, and persistence boundaries. On-chain analysis must consume service-loaded evidence, never call providers, never access repositories or registries, never persist findings directly, and never infer behavioral meaning beyond explicit persisted evidence. The implemented On-chain Intelligence Engine follows the service-owned execution boundary from ADR 0010 and the repository-purity boundary from ADR 0009.

Discovery, the Candidate Registry, Identity Resolution, providers, and repositories remain upstream authorities. The engine consumes persisted evidence produced by approved upstream flows. It does not bypass `DiscoveryObservation`, candidate identity, provider acquisition, registry authority, repository purity, or service-owned authority.

## Decision

Hunter accepts the implemented On-chain Intelligence Engine architecture.

The On-chain Intelligence Engine's purpose is to convert persisted `onchain_evidence` into deterministic, explainable findings about observed holder distribution, whale activity, treasury activity, bridge activity, liquidity activity, staking activity, validator activity, transaction patterns, contract interactions, and general on-chain observations. It does not score, rank, value, recommend, time, trade, compose engines, make portfolio decisions, or predict markets.

The engine consumes only immutable `EvidenceBundle` and immutable `EngineContext` supplied by `IntelligenceEngineService`. It does not receive providers, repositories, registries, mutable runtime state, file handles, or persistence handles.

`IntelligenceEngineService` remains the authority boundary. It owns evidence loading, replay cutoff enforcement, `EngineContext` creation, execution orchestration, finding validation, lineage validation, deterministic identity validation, and authorized persistence.

The engine produces deterministic `FindingBatch` output. Findings preserve supporting evidence, evidence lineage, conflicts, missing evidence, service-owned `as_of`, service-owned `evaluated_at`, schema version, and `analysis_trace_version`.

The implemented finding types are:

- `holder_distribution`;
- `whale_activity`;
- `treasury_activity`;
- `bridge_activity`;
- `liquidity_activity`;
- `staking_activity`;
- `validator_activity`;
- `transaction_pattern`;
- `contract_interaction`;
- `onchain_observation`.

Evidence sufficiency is mandatory. If the evidence required for a finding is absent within the relevant on-chain context, that finding is not produced. Context identifiers identify the analyzed context only. They never satisfy evidence sufficiency. Missing evidence suppresses only the affected finding. The engine does not fabricate negative findings or infer absent behavior from missing evidence.

On-chain context isolation is mandatory. Every on-chain finding is derived within one explicit deterministic context represented by persisted evidence, such as a wallet, holder, treasury, bridge, validator, staking position, liquidity pool, transaction, contract, or token. Candidate identity alone is not sufficient to combine on-chain evidence. Missing, malformed, or ambiguous context identifiers suppress only the affected context-specific finding.

Same-context on-chain observations may be aggregated descriptively when they belong to the same deterministic context. Cross-context aggregation is forbidden. Aggregation must never infer ownership, clustering, treasury attribution, profitability, manipulation, accumulation intent, distribution intent, strategy, market quality, scoring, ranking, recommendations, valuation, trading, timing, portfolio logic, or cross-engine conclusions.

Each finding is generated independently from persisted evidence. Findings may share supporting evidence, but no finding consumes, depends on, summarizes, or derives from another finding.

On-chain evidence contract semantics are shared with the service layer through the existing evidence-contract logic for `onchain_evidence`. Evidence accepted by the engine must satisfy the same contract semantics used by `IntelligenceEngineService` for missing-evidence evaluation.

Execution is deterministic. The engine normalizes unordered evidence before analysis, uses deterministic ordering for payloads, on-chain context identifiers, evidence identifiers, lineage, conflicts, and findings, and produces stable output for the same `EvidenceBundle`, `EngineContext`, engine configuration, engine version, and `analysis_trace_version`.

Replay safety is preserved. Findings use service-owned `as_of` and `evaluated_at` timestamps, future evidence is excluded by `IntelligenceEngineService`, and finding identity includes replay-significant finding content, supporting evidence, evidence lineage, timestamps, engine identity, schema version, and `analysis_trace_version`. On-chain finding explanations include deterministic evidence content fingerprints so replay-significant evidence changes change finding identity.

Persisted conflicts are preserved in findings but are not resolved by the engine. The engine records conflict context from persisted evidence and leaves conflict authority to upstream service-owned evidence and registry flows.

Repository purity is preserved. Repositories remain persistence-only under ADR 0009. The On-chain Intelligence Engine never accesses repositories or registries and never persists findings directly.

Provider purity is preserved. Providers continue to acquire and normalize source observations through approved upstream flows. The On-chain Intelligence Engine does not call providers or acquire fresh external data.

The implemented engine is compatible with ADR 0011, ADR 0012, ADR 0013, and ADR 0014 because it follows the same production intelligence-engine pattern: evidence-only execution, service-owned authority, finding independence, deterministic replay, shared evidence-contract semantics, context isolation, conflict preservation, repository purity, provider purity, and canonical runtime preservation.

The canonical production runtime is preserved. This ADR records the implemented On-chain Intelligence Engine; it does not introduce new architecture and does not replace existing experimental engine compatibility.

## Consequences

Positive consequences:

- On-chain intelligence is now produced through the ADR 0010 service-owned engine foundation.
- On-chain findings are evidence-backed, deterministic, explainable, and replay-safe.
- Context isolation prevents unrelated wallets, holders, treasuries, bridges, validators, staking positions, liquidity pools, transactions, contracts, or tokens from being merged merely because they belong to the same candidate.
- Same-context aggregation remains descriptive and does not create scoring, ranking, recommendations, valuation, timing, portfolio logic, trading logic, market prediction, or cross-engine composition.
- Missing on-chain evidence suppresses unsupported findings instead of creating fabricated conclusions.
- Context identifiers remain separate from evidence sufficiency and do not authorize findings by themselves.
- Finding independence makes each on-chain finding easier to audit and regression-test.
- Shared evidence-contract semantics prevent divergence between service missing-evidence reporting and engine evidence acceptance.
- Persisted conflicts remain visible without giving the engine conflict-resolution authority.
- Repository purity and provider purity remain intact.
- Discovery, Candidate Registry, and Identity Resolution remain upstream evidence and identity authorities.
- The canonical production runtime remains unchanged.

Tradeoffs:

- The engine requires persisted `onchain_evidence`; it cannot produce findings from provider payloads or live chain access.
- The engine is descriptive only and does not infer behavior beyond persisted evidence.
- Address, transaction, pool, bridge, validator, staking, contract, token, or treasury identifiers alone do not produce findings.
- Ownership, clustering, treasury attribution, profitability, manipulation, accumulation intent, distribution intent, strategy, and market quality remain unavailable unless explicitly persisted as evidence and still remain descriptive.

## Alternatives Considered

- Let the On-chain engine call blockchain providers directly. Rejected because engines must consume persisted evidence and must not own acquisition.
- Inject repositories or registries into the On-chain engine. Rejected because repository or registry injection would bypass `IntelligenceEngineService` authority and violate ADR 0009 and ADR 0010.
- Combine on-chain evidence by candidate identity alone. Rejected because on-chain findings must be derived inside explicit deterministic contexts.
- Treat context identifiers as evidence sufficiency. Rejected because identity identifies what is being analyzed; it does not prove what has been observed.
- Infer wallet ownership, wallet clustering, treasury attribution, accumulation intent, distribution intent, manipulation, profitability, strategy, or market quality from balances or transfers. Rejected because the implemented engine records descriptive observations only.
- Produce default findings when on-chain evidence is absent. Rejected because Hunter requires evidence-first outputs and explicit missing evidence.
- Allow findings to derive from other findings. Rejected because finding independence is required for deterministic replay and auditability.
- Resolve on-chain conflicts inside the engine. Rejected because the engine preserves persisted conflict context but does not own conflict authority.
- Add scoring, ranking, recommendations, valuation, timing, portfolio logic, trading logic, market prediction, or cross-engine composition. Rejected because the implemented engine records descriptive on-chain findings only.
- Replace the existing canonical production runtime. Rejected because the implemented engine is additive and preserves runtime compatibility.
