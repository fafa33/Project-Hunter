# ADR 0011: Developer Intelligence Engine

## Status

Accepted.

## Context

Developer Intelligence Engine Phase B2 implemented and verified the first production intelligence engine built on ADR 0010. The implementation analyzes persisted developer evidence and produces deterministic, evidence-backed findings without replacing the canonical production runtime documented in `docs/CANONICAL_RUNTIME_ARCHITECTURE.md`.

Hunter needs developer intelligence to remain evidence-first and replay-safe. Developer analysis must consume service-loaded evidence, never call providers, never access repositories, never persist findings directly, and never infer findings from absent evidence. The implemented Developer Intelligence Engine follows the service-owned execution boundary from ADR 0010 and the repository-purity boundary from ADR 0009.

The engine is compatible with Discovery, the Candidate Registry, and Identity Resolution because it consumes persisted evidence derived from approved discovery and registry flows. It does not bypass `DiscoveryObservation`, `DiscoveryEvidenceNormalizer`, candidate identity, or service-owned authority.

## Decision

Hunter accepts the implemented Developer Intelligence Engine architecture.

The Developer Intelligence Engine's purpose is to convert persisted developer evidence into deterministic, explainable findings about observed repository and development activity. It does not score, rank, recommend, value, time, trade, compose engines, or make portfolio decisions.

The engine consumes only immutable `EvidenceBundle` and immutable `EngineContext` supplied by `IntelligenceEngineService`. It does not receive repositories, providers, mutable runtime state, or persistence handles.

`IntelligenceEngineService` remains the authority boundary. It owns evidence loading, replay cutoff enforcement, `EngineContext` creation, execution orchestration, finding validation, lineage validation, deterministic identity validation, and authorized persistence.

The implemented finding types are:

- `repository_activity`;
- `release_cadence`;
- `contributor_diversity`;
- `maintenance_state`;
- `archival_state`;
- `development_continuity`;
- `repository_migration`;
- `repository_health_observation`.

Each finding is generated independently from persisted evidence. Findings may share supporting evidence, but no finding consumes, depends on, or derives from another finding.

Evidence sufficiency is mandatory. If the evidence required for a finding is absent, that finding is not produced. The engine does not fabricate negative findings, default findings, or inferred findings from missing evidence.

Developer evidence contract semantics are shared with the service layer. The engine and `IntelligenceEngineService` use the same evidence-contract rules for `github_repository_profile`, so evidence accepted by the engine also satisfies service-derived missing-evidence evaluation. Legacy metric-based compatibility is allowed only through the same shared contract logic.

Execution is deterministic. The engine normalizes unordered evidence before analysis, uses deterministic ordering for payloads, evidence identifiers, lineage, and findings, and produces stable output for the same `EvidenceBundle`, `EngineContext`, engine configuration, engine version, and `analysis_trace_version`.

Replay safety is preserved. Findings use service-owned `as_of` and `evaluated_at` timestamps, future evidence is excluded by `IntelligenceEngineService`, and finding identity includes replay-significant finding content, supporting evidence, evidence lineage, timestamps, engine identity, schema version, and `analysis_trace_version`.

Repository purity is preserved. Repositories remain persistence-only under ADR 0009. The Developer Intelligence Engine never accesses repositories and never persists findings directly.

The canonical production runtime is preserved. This ADR records the implemented Developer Intelligence Engine; it does not introduce new architecture and does not replace existing experimental engine compatibility.

## Consequences

- Developer intelligence is now produced through the ADR 0010 service-owned engine foundation.
- Developer findings are evidence-backed, deterministic, explainable, and replay-safe.
- Missing developer evidence suppresses only unsupported findings instead of creating fabricated conclusions.
- Finding independence makes each finding easier to audit and regression-test.
- Shared evidence-contract semantics prevent divergence between service missing-evidence reporting and engine evidence acceptance.
- Repository purity remains intact because the engine has no repository or persistence access.
- Discovery, Candidate Registry, and Identity Resolution remain upstream evidence and identity authorities.
- The canonical production runtime remains unchanged.

## Alternatives Considered

- Let the Developer engine call GitHub or other providers directly. Rejected because engines must consume persisted evidence and must not own acquisition.
- Inject repositories into the Developer engine. Rejected because repository injection would bypass `IntelligenceEngineService` authority and violate ADR 0009 and ADR 0010.
- Produce default findings when developer evidence is absent. Rejected because Hunter requires evidence-first outputs and explicit missing evidence.
- Allow findings to derive from other findings. Rejected because finding independence is required for deterministic replay and auditability.
- Keep Developer-specific evidence sufficiency rules separate from service missing-evidence rules. Rejected because service and engine contract semantics must remain aligned.
- Replace the existing canonical production runtime. Rejected because the implemented engine is additive and preserves runtime compatibility.
