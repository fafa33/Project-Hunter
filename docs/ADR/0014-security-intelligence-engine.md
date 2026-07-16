# ADR 0014: Security Intelligence Engine

## Status

Accepted.

## Context

Security Intelligence Engine Phase B5 implemented and verified the next production intelligence engine built on ADR 0010. The implementation analyzes persisted security evidence and produces deterministic, evidence-backed findings without replacing the canonical production runtime documented in `docs/CANONICAL_RUNTIME_ARCHITECTURE.md`.

Hunter needs security intelligence to remain descriptive, evidence-first, replay-safe, and separate from security scoring, ratings, trust levels, risk scores, recommendations, timing, portfolio logic, trading, and cross-engine composition. Security analysis must consume service-loaded evidence, never call providers, never access repositories or registries, never persist findings directly, and never infer findings from absent evidence. The implemented Security Intelligence Engine follows the service-owned execution boundary from ADR 0010 and the repository-purity boundary from ADR 0009.

The engine is compatible with Discovery, the Candidate Registry, and Identity Resolution because it consumes persisted evidence produced by approved upstream evidence and registry flows. It does not bypass `DiscoveryObservation`, candidate identity, or service-owned authority.

## Decision

Hunter accepts the implemented Security Intelligence Engine architecture.

The Security Intelligence Engine's purpose is to convert persisted security evidence into deterministic, explainable findings about observed contract security, ownership models, proxy configuration, privileged permissions, token control features, audit references, exploit history, vulnerability observations, and general security observations. It does not score security, rate safety, assign trust or risk levels, rank, recommend, time, trade, compose engines, or make portfolio or investment decisions.

The engine consumes only immutable `EvidenceBundle` and immutable `EngineContext` supplied by `IntelligenceEngineService`. It does not receive providers, repositories, registries, mutable runtime state, file handles, or persistence handles.

`IntelligenceEngineService` remains the authority boundary. It owns evidence loading, replay cutoff enforcement, `EngineContext` creation, execution orchestration, finding validation, lineage validation, deterministic identity validation, and authorized persistence.

The implemented finding types are:

- `contract_security`;
- `ownership_model`;
- `proxy_configuration`;
- `privileged_permissions`;
- `token_control_features`;
- `audit_observation`;
- `exploit_history`;
- `vulnerability_observation`;
- `security_observation`.

Security context isolation is mandatory. Every security finding is derived within one explicit deterministic security context represented by persisted evidence, such as a contract, token, proxy, ownership context, privilege context, audit, exploit, or vulnerability. Candidate identity alone is not sufficient to combine security evidence. Missing, malformed, or ambiguous context identifiers suppress only the affected context-specific finding.

Same-context security observations may be aggregated descriptively when they belong to the same deterministic security context. Cross-context aggregation is forbidden. Aggregation must never resolve conflicts, override persisted evidence, infer trust, infer security level, infer risk, or produce recommendations.

Evidence sufficiency is mandatory. If the evidence required for a finding is absent within the relevant security context, that finding is not produced. Context identifiers identify what is being analyzed; they are not evidence and do not satisfy evidence sufficiency. Missing evidence suppresses only the affected finding. The engine does not fabricate negative findings, default findings, safe or unsafe labels, trust conclusions, or risk conclusions from missing evidence.

Each finding is generated independently from persisted evidence. Findings may share supporting evidence, but no finding consumes, depends on, or derives from another finding.

Security evidence contract semantics are shared with the service layer through the existing evidence-contract logic for `security_evidence`. Evidence accepted by the engine must satisfy the same contract semantics used by `IntelligenceEngineService` for missing-evidence evaluation.

Execution is deterministic. The engine normalizes unordered evidence before analysis, uses deterministic ordering for payloads, security context identifiers, evidence identifiers, lineage, conflicts, and findings, and produces stable output for the same `EvidenceBundle`, `EngineContext`, engine configuration, engine version, and `analysis_trace_version`.

Replay safety is preserved. Findings use service-owned `as_of` and `evaluated_at` timestamps, future evidence is excluded by `IntelligenceEngineService`, and finding identity includes replay-significant finding content, supporting evidence, evidence lineage, timestamps, engine identity, schema version, and `analysis_trace_version`. Security finding explanations include deterministic evidence content fingerprints so replay-significant evidence changes change finding identity.

Persisted conflicts are preserved in findings but are not resolved by the engine. The engine records conflict context from persisted evidence and leaves conflict authority to upstream service-owned evidence and registry flows.

Repository purity is preserved. Repositories remain persistence-only under ADR 0009. The Security Intelligence Engine never accesses repositories or registries and never persists findings directly.

The implemented engine is compatible with ADR 0011, ADR 0012, and ADR 0013 because it follows the same production intelligence-engine pattern: evidence-only execution, service-owned authority, finding independence, deterministic replay, shared evidence-contract semantics, context isolation, conflict preservation, and canonical runtime preservation.

The canonical production runtime is preserved. This ADR records the implemented Security Intelligence Engine; it does not introduce new architecture and does not replace existing experimental engine compatibility.

## Consequences

- Security intelligence is now produced through the ADR 0010 service-owned engine foundation.
- Security findings are evidence-backed, deterministic, explainable, and replay-safe.
- Security context isolation prevents unrelated contracts, tokens, proxies, ownership contexts, privilege contexts, audits, exploits, or vulnerabilities from being merged merely because they belong to the same candidate.
- Same-context aggregation remains descriptive and does not create scoring, ratings, trust levels, risk scores, recommendations, timing, portfolio logic, trading logic, or cross-engine composition.
- Missing security evidence suppresses unsupported findings instead of creating fabricated conclusions.
- Context identifiers remain separate from evidence sufficiency and do not authorize findings by themselves.
- Finding independence makes each security finding easier to audit and regression-test.
- Shared evidence-contract semantics prevent divergence between service missing-evidence reporting and engine evidence acceptance.
- Persisted conflicts remain visible without giving the engine conflict-resolution authority.
- Repository purity remains intact because the engine has no repository, registry, or persistence access.
- Discovery, Candidate Registry, and Identity Resolution remain upstream evidence and identity authorities.
- The canonical production runtime remains unchanged.

## Alternatives Considered

- Let the Security engine call security providers directly. Rejected because engines must consume persisted evidence and must not own acquisition.
- Inject repositories or registries into the Security engine. Rejected because repository or registry injection would bypass `IntelligenceEngineService` authority and violate ADR 0009 and ADR 0010.
- Combine security evidence by candidate identity alone. Rejected because security findings must be derived inside explicit security contexts.
- Treat context identifiers as evidence sufficiency. Rejected because identity identifies what is being analyzed; it does not prove what has been observed.
- Produce default findings when security evidence is absent. Rejected because Hunter requires evidence-first outputs and explicit missing evidence.
- Allow findings to derive from other findings. Rejected because finding independence is required for deterministic replay and auditability.
- Resolve security conflicts inside the engine. Rejected because the engine preserves persisted conflict context but does not own conflict authority.
- Add security scoring, ratings, trust levels, risk scores, safe or unsafe labels, or recommendations. Rejected because the implemented engine records descriptive security findings only.
- Replace the existing canonical production runtime. Rejected because the implemented engine is additive and preserves runtime compatibility.
