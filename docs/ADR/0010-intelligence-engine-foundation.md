# ADR 0010: Intelligence Engine Foundation

## Status

Accepted.

## Context

Foundation Sprint Phase B1 implemented and verified the common foundation for Hunter Intelligence Engines. The implementation established a service-owned execution boundary for intelligence analysis while preserving the canonical production runtime documented in `docs/CANONICAL_RUNTIME_ARCHITECTURE.md`.

Hunter's evidence-first architecture requires intelligence engines to consume persisted evidence, produce explainable findings, and remain replay-safe without owning persistence, repository access, provider access, authority, or orchestration. The implemented foundation provides that boundary through immutable execution contracts, deterministic finding identity, service-owned validation, and repository purity consistent with ADR 0009.

The foundation is compatible with Discovery evidence because `DiscoveryObservation` remains the upstream canonical discovery evidence record and `DiscoveryEvidenceNormalizer` remains the normalization boundary. Intelligence execution consumes persisted evidence derived from approved evidence flows; it does not bypass Discovery, identity resolution, registry authority, or repository purification.

## Decision

Hunter accepts the implemented Intelligence Engine Foundation architecture.

The engine execution flow is:

```text
Persisted Evidence
-> IntelligenceEngineService
-> EvidenceBundle
-> EngineContext
-> Intelligence Engine
-> Findings
-> IntelligenceEngineService Validation
-> Repository Persistence
```

`IntelligenceEngineService` is the authority boundary for intelligence engine execution. It owns:

- evidence loading;
- replay cutoff enforcement;
- `EngineContext` creation;
- engine execution orchestration;
- finding validation;
- evidence lineage validation;
- deterministic finding identity validation;
- persistence authorization.

Engines consume only service-supplied immutable `EvidenceBundle` and immutable `EngineContext`. Engines never access repositories, never receive repositories through constructor injection or execution context, and never persist findings or load evidence directly.

`EvidenceBundle` is the immutable evidence input contract. It contains the candidate reference, service-loaded evidence, evidence identifiers, missing evidence, and evidence lineage.

`EngineContext` is the immutable execution context contract. It contains replay-sensitive execution state including `as_of`, `evaluated_at`, replay fingerprint, engine configuration fingerprint, engine version, and execution metadata.

`Finding` is the immutable engine output contract. Each finding includes deterministic identity, finding type, explanation, supporting evidence, evidence lineage, deterministic confidence, confidence basis, `evaluated_at`, `as_of`, schema version, and `analysis_trace_version`.

`FindingBatch` is the immutable batch output contract for findings produced by one engine for one candidate under one service-owned execution context.

`finding_identity` is deterministic. Finding identity includes replay-significant finding content, supporting evidence, evidence lineage, timestamps, engine identity, schema version, and `analysis_trace_version`.

`analysis_trace_version` is part of explainability and replay compatibility. Changes to the trace schema participate in deterministic identity and replay behavior.

Replay fingerprints include canonical evidence content fingerprints, not only evidence identifiers. Replay identity changes when replay-significant evidence content changes and remains stable when canonical evidence is unchanged.

`HunterIntelligenceEngineBuilder` is definition-only. It creates immutable engine definitions and declares:

- engine metadata;
- capabilities;
- evidence contracts;
- supported evidence types;
- analysis stages;
- finding types;
- output schema version;
- analysis trace version;
- deterministic execution contract.

Builders never create runtime state, execute engines, load evidence, persist findings, own authority, call providers, open repositories, or mutate registry state.

Hunter records two permanent platform primitives:

- Hunter Data Source Builder;
- Hunter Intelligence Engine Builder.

Both builders are definition-only platform primitives. Data source construction remains separate from intelligence execution. Intelligence engine construction remains separate from service-owned runtime authority.

The provider, service, repository, and persistence boundaries remain:

```text
Provider
-> Service
-> Repository
-> Persistence
```

Providers acquire and normalize source data. Services own authority, replay, validation, and persistence orchestration. Repositories remain persistence-only and store service-authorized state without business logic. This preserves ADR 0009 Repository Purification.

The canonical production runtime is preserved. This ADR records the implemented Intelligence Engine Foundation; it does not replace the current production runtime and does not introduce scoring, ranking, recommendations, portfolio logic, trading, cross-engine composition, or timing behavior.

## Consequences

- Intelligence engine execution has a single service-owned authority boundary.
- Engines remain isolated from repositories and persistence concerns.
- Finding outputs are immutable, explainable, evidence-backed, and replay-safe.
- Replay behavior is tied to canonical evidence content, not just record identifiers.
- Builder responsibilities are limited to immutable definition construction.
- Repository purity is preserved because repositories do not validate authority, infer findings, compute replay state, or own engine decisions.
- Discovery evidence remains compatible with intelligence execution through persisted evidence lineage and normalization boundaries.
- Existing experimental engine compatibility is preserved while the canonical production runtime remains unchanged.
- Future implementation work must keep intelligence execution service-owned and must not add repository access, provider access, persistence, scoring, ranking, recommendation, timing, portfolio, or trading behavior to engines.

## Alternatives Considered

- Allow engines to access repositories directly. Rejected because it would bypass service-owned replay filtering, validation, lineage checks, and persistence authorization.
- Inject repositories into engine constructors or contexts. Rejected because repository injection would create an authority bypass and weaken engine/repository separation.
- Let builders execute engines or load evidence. Rejected because builders are definition-only primitives and must not own runtime state or authority.
- Base replay identity only on evidence identifiers. Rejected because replay-significant evidence content changes must change replay identity.
- Persist findings directly from engines. Rejected because persistence must remain service-authorized and repository-pure under ADR 0009.
- Replace the canonical production runtime with the new foundation. Rejected because the implemented foundation is additive and preserves the current canonical runtime.
