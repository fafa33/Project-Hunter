# ADR 0013: Governance Intelligence Engine

## Status

Accepted.

## Context

Governance Intelligence Engine Phase B4 implemented and verified the next production intelligence engine built on ADR 0010. The implementation analyzes persisted governance evidence and produces deterministic, evidence-backed findings without replacing the canonical production runtime documented in `docs/CANONICAL_RUNTIME_ARCHITECTURE.md`.

Hunter needs governance intelligence to remain descriptive, evidence-first, replay-safe, and separate from governance-quality scoring, decentralization scoring, recommendations, ranking, timing, portfolio logic, trading, and investment conclusions. Governance analysis must consume service-loaded evidence, never call providers, never access repositories or registries, never persist findings directly, and never infer findings from absent evidence. The implemented Governance Intelligence Engine follows the service-owned execution boundary from ADR 0010 and the repository-purity boundary from ADR 0009.

The engine is compatible with Discovery, the Candidate Registry, and Identity Resolution because it consumes persisted evidence produced by approved upstream evidence and registry flows. It does not bypass `DiscoveryObservation`, `DiscoveryEvidenceNormalizer`, candidate identity, or service-owned authority.

## Decision

Hunter accepts the implemented Governance Intelligence Engine architecture.

The Governance Intelligence Engine's purpose is to convert persisted governance evidence into deterministic, explainable findings about observed governance spaces, proposals, voting participation, quorum, delegates, governance parameters, execution metadata, and general governance observations. It does not score governance quality, score decentralization, rank, recommend, time, trade, compose engines, or make portfolio or investment decisions.

The engine consumes only immutable `EvidenceBundle` and immutable `EngineContext` supplied by `IntelligenceEngineService`. It does not receive providers, repositories, registries, mutable runtime state, or persistence handles.

`IntelligenceEngineService` remains the authority boundary. It owns evidence loading, replay cutoff enforcement, `EngineContext` creation, execution orchestration, finding validation, lineage validation, deterministic identity validation, and authorized persistence.

The implemented finding types are:

- `governance_activity`;
- `proposal_lifecycle`;
- `voting_participation`;
- `quorum_observation`;
- `delegate_distribution`;
- `governance_parameter_observation`;
- `governance_execution_observation`;
- `governance_observation`.

Governance context isolation is mandatory. Every governance finding is derived within one explicit deterministic governance context represented by persisted evidence, such as a governance space, proposal, vote, delegate, parameter, or execution record. Candidate identity alone is not sufficient to combine governance evidence. Proposal, vote, delegate, execution, parameter, and governance-space contexts remain isolated. Missing or ambiguous context identifiers suppress the affected context-specific finding. Multiple valid contexts may produce multiple findings of the same finding type.

Each finding is generated independently from persisted evidence. Findings may share supporting evidence, but no finding consumes, depends on, or derives from another finding.

Evidence sufficiency is mandatory. If the evidence required for a finding is absent within the relevant governance context, that finding is not produced. Missing evidence suppresses only the affected finding. The engine does not fabricate negative findings, default findings, or inferred findings from missing evidence.

Governance evidence contract semantics are shared with the service layer through the existing evidence-contract logic for `governance_evidence`. Evidence accepted by the engine must satisfy the same contract semantics used by `IntelligenceEngineService` for missing-evidence evaluation.

Execution is deterministic. The engine normalizes unordered evidence before analysis, uses deterministic ordering for payloads, governance context identifiers, evidence identifiers, lineage, conflicts, and findings, and produces stable output for the same `EvidenceBundle`, `EngineContext`, engine configuration, engine version, and `analysis_trace_version`.

Replay safety is preserved. Findings use service-owned `as_of` and `evaluated_at` timestamps, future evidence is excluded by `IntelligenceEngineService`, and finding identity includes replay-significant finding content, supporting evidence, evidence lineage, timestamps, engine identity, schema version, and `analysis_trace_version`. Governance finding explanations include deterministic evidence content fingerprints so replay-significant evidence changes change finding identity.

Persisted conflicts are preserved in findings but are not resolved by the engine. The engine records conflict context from persisted evidence and leaves conflict authority to upstream service-owned evidence and registry flows.

Repository purity is preserved. Repositories remain persistence-only under ADR 0009. The Governance Intelligence Engine never accesses repositories or registries and never persists findings directly.

The implemented engine is compatible with ADR 0011 and ADR 0012 because it follows the same production intelligence-engine pattern: evidence-only execution, service-owned authority, finding independence, deterministic replay, shared evidence-contract semantics, and canonical runtime preservation.

The canonical production runtime is preserved. This ADR records the implemented Governance Intelligence Engine; it does not introduce new architecture and does not replace existing experimental engine compatibility.

## Consequences

- Governance intelligence is now produced through the ADR 0010 service-owned engine foundation.
- Governance findings are evidence-backed, deterministic, explainable, and replay-safe.
- Governance context isolation prevents unrelated governance spaces, proposals, votes, delegates, parameters, or executions from being merged merely because they belong to the same candidate.
- Missing governance evidence suppresses unsupported findings instead of creating fabricated conclusions.
- Finding independence makes each governance finding easier to audit and regression-test.
- Shared evidence-contract semantics prevent divergence between service missing-evidence reporting and engine evidence acceptance.
- Persisted conflicts remain visible without giving the engine conflict-resolution authority.
- Repository purity remains intact because the engine has no repository, registry, or persistence access.
- Discovery, Candidate Registry, and Identity Resolution remain upstream evidence and identity authorities.
- The canonical production runtime remains unchanged.

## Alternatives Considered

- Let the Governance engine call governance providers directly. Rejected because engines must consume persisted evidence and must not own acquisition.
- Inject repositories or registries into the Governance engine. Rejected because repository or registry injection would bypass `IntelligenceEngineService` authority and violate ADR 0009 and ADR 0010.
- Combine governance evidence by candidate identity alone. Rejected because governance findings must be derived inside explicit governance contexts.
- Produce default findings when governance evidence is absent. Rejected because Hunter requires evidence-first outputs and explicit missing evidence.
- Allow findings to derive from other findings. Rejected because finding independence is required for deterministic replay and auditability.
- Resolve governance conflicts inside the engine. Rejected because the engine preserves persisted conflict context but does not own conflict authority.
- Add governance quality scoring or decentralization scoring. Rejected because the implemented engine records descriptive governance findings only.
- Replace the existing canonical production runtime. Rejected because the implemented engine is additive and preserves runtime compatibility.
