# ADR 0012: Tokenomics Intelligence Engine

## Status

Accepted.

## Context

Tokenomics Intelligence Engine Phase B3 implemented and verified the second production intelligence engine built on ADR 0010. The implementation analyzes persisted tokenomics evidence and produces deterministic, evidence-backed findings without replacing the canonical production runtime documented in `docs/CANONICAL_RUNTIME_ARCHITECTURE.md`.

Hunter needs tokenomics intelligence to remain descriptive, evidence-first, replay-safe, and separate from valuation or investment logic. Tokenomics analysis must consume service-loaded evidence, never call providers, never access repositories or registries, never persist findings directly, and never infer findings from absent evidence. The implemented Tokenomics Intelligence Engine follows the service-owned execution boundary from ADR 0010 and the repository-purity boundary from ADR 0009.

The engine is compatible with Discovery, the Candidate Registry, and Identity Resolution because it consumes persisted evidence produced by approved upstream evidence and registry flows. It does not bypass `DiscoveryObservation`, `DiscoveryEvidenceNormalizer`, candidate identity, or service-owned authority.

## Decision

Hunter accepts the implemented Tokenomics Intelligence Engine architecture.

The Tokenomics Intelligence Engine's purpose is to convert persisted tokenomics evidence into deterministic, explainable findings about observed supply, issuance, unlocks, vesting, allocations, treasury distribution, emissions, burns, protocol distribution, and general tokenomics observations. It does not score, rank, value, recommend, time, trade, compose engines, or make portfolio decisions.

The engine consumes only immutable `EvidenceBundle` and immutable `EngineContext` supplied by `IntelligenceEngineService`. It does not receive providers, repositories, registries, mutable runtime state, or persistence handles.

`IntelligenceEngineService` remains the authority boundary. It owns evidence loading, replay cutoff enforcement, `EngineContext` creation, execution orchestration, finding validation, lineage validation, deterministic identity validation, and authorized persistence.

The implemented finding types are:

- `supply_structure`;
- `issuance_schedule`;
- `unlock_schedule`;
- `vesting_schedule`;
- `allocation_structure`;
- `treasury_distribution`;
- `emission_profile`;
- `burn_activity`;
- `protocol_distribution`;
- `tokenomics_observation`.

Each finding is generated independently from persisted evidence. Findings may share supporting evidence, but no finding consumes, depends on, or derives from another finding.

Evidence sufficiency is mandatory. If the evidence required for a finding is absent, that finding is not produced. Missing evidence suppresses only the affected finding. The engine does not fabricate negative findings, default findings, or inferred findings from missing evidence.

Tokenomics evidence contract semantics are shared with the service layer through the existing evidence-contract logic for `tokenomics_evidence`. Evidence accepted by the engine must satisfy the same contract semantics used by `IntelligenceEngineService` for missing-evidence evaluation.

Execution is deterministic. The engine normalizes unordered evidence before analysis, uses deterministic ordering for payloads, evidence identifiers, lineage, conflicts, and findings, and produces stable output for the same `EvidenceBundle`, `EngineContext`, engine configuration, engine version, and `analysis_trace_version`.

Replay safety is preserved. Findings use service-owned `as_of` and `evaluated_at` timestamps, future evidence is excluded by `IntelligenceEngineService`, and finding identity includes replay-significant finding content, supporting evidence, evidence lineage, timestamps, engine identity, schema version, and `analysis_trace_version`. Tokenomics finding explanations include deterministic evidence content fingerprints so replay-significant evidence changes change finding identity.

Persisted conflicts are preserved in findings but are not resolved by the engine. The engine records conflict context from persisted evidence and leaves conflict authority to upstream service-owned evidence and registry flows.

Balance-only attribution safeguards are part of the implemented engine. Balance-only evidence must not produce ownership-sensitive findings for treasury, team, investor, exchange, or market maker attribution. Treasury-specific fields and normalized market-maker category variants are handled without inferring ownership from balance-only data.

Protocol fees, revenue, and TVL findings remain descriptive observations only. Fully unlocked supply does not imply low risk. Balance-only evidence does not infer team, investor, treasury, exchange, or market-maker ownership.

Repository purity is preserved. Repositories remain persistence-only under ADR 0009. The Tokenomics Intelligence Engine never accesses repositories or registries and never persists findings directly.

The canonical production runtime is preserved. This ADR records the implemented Tokenomics Intelligence Engine; it does not introduce new architecture and does not replace existing experimental engine compatibility.

## Consequences

- Tokenomics intelligence is now produced through the ADR 0010 service-owned engine foundation.
- Tokenomics findings are evidence-backed, deterministic, explainable, and replay-safe.
- Missing tokenomics evidence suppresses unsupported findings instead of creating fabricated conclusions.
- Finding independence makes each tokenomics finding easier to audit and regression-test.
- Shared evidence-contract semantics prevent divergence between service missing-evidence reporting and engine evidence acceptance.
- Persisted conflicts remain visible without giving the engine conflict-resolution authority.
- Balance-only attribution safeguards prevent ownership-sensitive claims from unsupported balance evidence.
- Repository purity remains intact because the engine has no repository, registry, or persistence access.
- Discovery, Candidate Registry, and Identity Resolution remain upstream evidence and identity authorities.
- The canonical production runtime remains unchanged.

## Alternatives Considered

- Let the Tokenomics engine call tokenomics providers directly. Rejected because engines must consume persisted evidence and must not own acquisition.
- Inject repositories or registries into the Tokenomics engine. Rejected because repository or registry injection would bypass `IntelligenceEngineService` authority and violate ADR 0009 and ADR 0010.
- Produce default findings when tokenomics evidence is absent. Rejected because Hunter requires evidence-first outputs and explicit missing evidence.
- Allow findings to derive from other findings. Rejected because finding independence is required for deterministic replay and auditability.
- Resolve tokenomics conflicts inside the engine. Rejected because the engine preserves persisted conflict context but does not own conflict authority.
- Infer ownership from balance-only evidence. Rejected because balance-only evidence cannot safely establish team, investor, treasury, exchange, or market-maker ownership.
- Treat protocol fees, revenue, or TVL as valuation inputs. Rejected because this engine records descriptive tokenomics findings only and does not perform valuation.
- Replace the existing canonical production runtime. Rejected because the implemented engine is additive and preserves runtime compatibility.
