# Hunter Implementation Contract

Status: Mandatory implementation contract.

This document is the permanent implementation contract for Codex, Claude, human contributors, and future automated contributors. It is not a replacement for architecture documentation. It is the pre-implementation contract that every future Sprint must satisfy before code is written.

This contract governs future implementation work and new architecture changes. It does not retroactively invalidate the current approved runtime. Existing production and experimental classifications remain governed by the canonical authority hierarchy and by `docs/CANONICAL_RUNTIME_ARCHITECTURE.md` until changed through the approved governance lifecycle.

## 1 Mission

Project Hunter is a long-term crypto research, intelligence, discovery, monitoring, and opportunity-hunting platform. Its permanent mission is to discover high-conviction opportunities before they become obvious by converting public evidence into deterministic, auditable, replay-safe intelligence.

Hunter must remain evidence-first, discovery-first, deterministic, extensible, and architecturally stable. Implementation speed, local convenience, or contributor preference must never override evidence integrity, replay safety, source authority, persistence safety, or documented runtime boundaries.

## 2 Authority Hierarchy

The canonical authority hierarchy is defined once in `docs/SPRINTS/README.md`. This contract follows that hierarchy and must not be used to create a parallel ordering.

`docs/PROJECT_CONSTITUTION.md` remains the highest architectural authority. `docs/PROJECT_PRINCIPLES.md` remains the permanent engineering constitution under it. Accepted ADRs remain binding architecture decision records under those documents until another ADR supersedes or deprecates them.

Higher documents always override lower documents. If two sources conflict, contributors must stop before implementation and report the conflict. Chat history, implementation summaries, and local verification are never architectural authority.

## 3 Universal Invariants

| Rule ID | Invariant | Contract |
| --- | --- | --- |
| INV-001 | Evidence before inference | No conclusion, score, lifecycle transition, candidate projection, or report claim may outrank its evidence. Missing evidence remains explicit. |
| INV-002 | Discovery before analysis | Hunter must discover what potentially exists before analyzing quality, value, rank, timing, or portfolio relevance. |
| INV-003 | Determinism before convenience | Same persisted inputs, configuration, timestamps, and replay cutoffs must produce the same outputs. |
| INV-004 | Replay safety | Historical replay must consume only evidence available at or before the replay cutoff. |
| INV-005 | Atomic persistence | Related authoritative writes must commit or roll back together. Partial authoritative state is a blocker unless explicitly modeled as a replayable pending state. |
| INV-006 | Explicit conflicts | Disagreement, ambiguity, collision, drift, and unavailable evidence must be persisted or reported explicitly. |
| INV-007 | No silent merges | Slug, ticker, alias, name, URL, provider listing, or source payload similarity must never silently merge identities. |
| INV-008 | No caller-controlled authority | Future authority boundaries cannot be supplied by a provider payload, arbitrary caller flag, mutable context object, fixture, or direct repository call. |
| INV-009 | No provider-owned persistence | Future providers acquire and normalize only. They must not persist, mutate registry state, advance checkpoints, or create analytical state. |
| INV-010 | No repository-owned business rules | Future repository work must keep repositories to persistence and loading. Repositories must not decide trust, identity, conflicts, lifecycle, replay, provenance, or business rules. |
| INV-011 | One canonical ingress path | Each new or changed authoritative mutation type must have exactly one service-owned ingress path. |
| INV-012 | Explicit provenance | Every evidence-bearing record must preserve source identity, provider identity where applicable, source record identity, timestamps, payload identity or evidence reference, confidence, and missing evidence. |
| INV-013 | Explicit timestamps | Replay-sensitive writes require explicit timestamps owned by the service or orchestration layer. Wall-clock fallback must not create replayable state. |
| INV-014 | Migration safety | Persistence changes must be additive or explicitly migrated, idempotent, backward compatible, and tested against pre-change data. |
| INV-015 | Backward compatibility | Existing persisted records, public reads, command behavior, and approved runtime contracts must remain readable and compatible unless a formal migration and governance update approve the break. |

## 4 Canonical Layer Responsibilities

### Provider

Providers own source access only:

- acquire external data;
- normalize source-specific payloads into typed observations;
- expose source availability, errors, freshness, and source references;
- return observations to the caller.

For future implementation work, providers must never persist, mutate registry state, advance checkpoints, validate authority, resolve identity, detect canonical conflicts, or create analytical state.

### Service

Services are the authority boundary.

Services own:

- authority;
- provenance;
- lineage;
- identity decisions;
- conflict decisions;
- replay;
- checkpoint ownership;
- orchestration;
- business rules.

For new or changed authoritative mutation paths, every authoritative mutation must originate from a service. A service validates authority, provenance, lineage, identity, conflict handling, replay timestamps, migration assumptions, and transaction scope before persistence.

### Repository

Repositories own only:

- persistence;
- loading;
- deterministic transactions;
- schema;
- migrations;
- indexes.

Repositories must never:

- detect conflicts;
- merge entities;
- infer identity;
- validate authority;
- validate provenance;
- validate lifecycle;
- create timestamps;
- create checkpoints;
- own business logic;
- own replay logic.

Future repository mutation APIs that would create authoritative state must reject direct use unless invoked through a service-owned persistence plan. Internal persistence primitives must contain storage mechanics only.

### Registry

The Registry is the durable canonical map of discovered candidates and their lifecycle state. It stores service-authorized projections, aliases, identifiers, sources, conflicts, observations, lifecycle history, screening state, queue state, and compatibility seed records.

The Registry does not decide identity by itself. Registry updates reflect service decisions and persisted evidence.

### Evidence

Evidence is immutable and first-class. Evidence records and discovery observations preserve provenance, timestamps, confidence, freshness where applicable, missing evidence, source identity, and conflict context.

Evidence must not be fabricated, silently completed, overwritten, or reinterpreted as stronger than the source supports.

### Engine

Engines consume persisted evidence, metrics, snapshots, registry records, or other approved persisted analytical records. Engines never consume provider payloads directly.

Engines produce structured, explainable, replay-safe outputs. Engines must not own persistence, command-line behavior, scheduling, provider access, or unrelated orchestration.

### Dashboard

Dashboards present persisted state. They may filter, sort, visualize, and explain records that already exist. They must not create authoritative analysis, mutate registry state, call providers directly, advance checkpoints, or introduce unsupported claims.

## 5 Service Contract

Every new or changed service must declare its authoritative mutation surface. For each new or changed authoritative write, the service must prove:

- authority source;
- accepted input contracts;
- provenance validation;
- evidence lineage validation;
- identity and conflict rules;
- replay timestamp ownership;
- transaction boundary;
- checkpoint ownership, if applicable;
- rollback behavior;
- backward compatibility and migration impact.

Future service authority boundaries must reject caller-supplied authority that is not derived from approved execution context, configuration, persisted evidence, or an explicitly trusted system boundary.

## 6 Repository Contract

Repositories are persistence adapters. They may implement tables, indexes, migrations, deterministic serialization, deterministic transactions, and read models.

Future repository work must not add domain decisions hidden inside helper methods, SQL conflict clauses, convenience upserts, default timestamps, source trust checks, identity inference, lifecycle transition validation, checkpoint advancement decisions, or replay cutoffs. Existing approved runtime code is not declared non-compliant by this contract; when repository code is changed, the change must move toward this boundary or explicitly preserve the current approved runtime under the governance lifecycle.

If future repository work adds or changes a method that mutates authoritative state, it must either:

- reject direct public use; or
- accept only a service-owned persistence plan that already contains all domain decisions.

## 7 Provider Contract

Providers only:

- acquire;
- normalize;
- return observations.

Providers never:

- persist;
- mutate registry;
- advance checkpoints;
- validate authority;
- create analytical state.

Provider-emitted fields are observations, not authority. A provider may state what it saw; it may not decide what Hunter trusts.

## 8 Engine Contract

Future engine work must consume persisted evidence or approved persisted analytical inputs. New or changed engines must not consume provider payloads directly.

Engines must make every output explainable from persisted inputs. Any engine that needs fresh external data must obtain it through the acquisition/provider layer and consume it only after the service/repository boundary has persisted the approved evidence.

## 9 Replay Contract

Timestamp semantics are:

- `observed_at`: when the source state was observed or was effective at the source.
- `acquired_at`: when Hunter acquired the record from the source.
- `effective_at`: when Hunter treats a service-authorized state change as effective.
- `as_of`: the replay cutoff used to select eligible persisted evidence and state.

No replayable state may depend on implicit wall-clock time. Wall-clock time may be used only at the outer orchestration boundary to create an explicit timestamp that is then passed through the service contract.

Replay queries must not leak future evidence into historical views. If a record is unavailable at `as_of`, the replay result must preserve unavailable, missing, stale, ambiguous, or conflict state rather than substituting present-day data.

## 10 Identity Contract

The following never establish canonical identity alone:

- slug;
- ticker;
- alias;
- name;
- URL.

Provider ids, contracts, official repositories, domains, and source records are evidence, not automatic identity. They may support identity decisions only under explicit service-owned rules.

Ticker equality is never sufficient for merge. Alias similarity is never sufficient for merge. URL similarity is never sufficient for merge. Conflicting identifiers, provider records, symbols, chains, contracts, aliases, or slugs must produce explicit conflict handling rather than silent winner selection.

## 11 Transaction Contract

The required authoritative write sequence is:

```text
Authority
↓
Validation
↓
Transaction
↓
Persistence
↓
Projection
```

Authority and validation happen before durable persistence. Related writes must execute in one transaction when they represent one authoritative mutation. Checkpoints advance only after the authoritative commit succeeds. If downstream processing is deferred, the persisted state must explicitly record that deferred status and remain replayable.

## 12 Migration Contract

Every persistence change requires:

- additive migration or an explicitly approved migration path;
- backward compatibility for existing persisted rows;
- migration tests with pre-change data;
- idempotency on repeated initialization, migration, retries, and restarts;
- index and uniqueness rules that preserve deterministic reads and writes;
- no destructive reinterpretation of existing evidence.

Schema initialization alone is not sufficient when existing databases require explicit upgrade behavior.

## 13 Implementation Checklist

Every Sprint must satisfy this checklist before implementation:

1. Repository audit: identify every repository touched, every mutation method, every read method, and every schema or migration impact.
2. Mutation path audit: find every path that can mutate authoritative state, including CLI, automation, tests, providers, engines, services, seed/config paths, migrations, and private helpers.
3. Authority audit: identify the service that owns authority and prove no caller can bypass it.
4. Transaction audit: define atomic write groups, rollback behavior, checkpoint timing, partial-state semantics, and retry behavior.
5. Replay audit: define `observed_at`, `acquired_at`, `effective_at`, `as_of`, cutoff behavior, and wall-clock restrictions.
6. Migration audit: define existing schema impact, upgrade path, backward compatibility, indexes, constraints, idempotency, and migration tests.
7. Test audit: define positive, negative, adversarial, migration, replay, retry, ordering, rollback, and backward-compatibility tests.

Implementation may begin only after the audit identifies no unresolved uncertainty about authority, ownership, transaction boundaries, replay behavior, migration behavior, or identity behavior.

## 14 Definition Of Done

A Sprint is complete only if:

- architecture is preserved;
- authority is preserved;
- replay remains deterministic;
- migration is safe;
- adversarial tests passed;
- independent audit passed;
- CI passed.

Passing local tests is not sufficient if architecture, authority, replay, migration, or independent audit requirements are unresolved.

## 15 Coding Rule

Future implementation prompts must begin with:

```text
Verify compliance with HUNTER_IMPLEMENTATION_CONTRACT.md before writing code.
```

If a prompt omits this sentence, Codex and other implementation agents must still verify the contract before writing code and must report the omission as a process issue when relevant.

## 16 Self-Compliance Matrix

| Rule | Owner | Verified By | Required Tests | Applicable Modules |
| --- | --- | --- | --- | --- |
| INV-001 Evidence before inference | Service, Engine | Evidence traceability review | Missing-evidence, provenance, unsupported-claim tests | Discovery, Registry, Evidence, Engines, Reports |
| INV-002 Discovery before analysis | Provider, Service, Engine | Architecture review | Discovery-ingress and no-analysis-in-discovery tests | Discovery, Registry, Engines |
| INV-003 Determinism before convenience | Service, Repository, Engine | Determinism review | Same-input same-output, ordering, retry tests | All runtime modules |
| INV-004 Replay safety | Service, Repository | Replay audit | `as_of`, future-evidence exclusion, historical replay tests | Discovery, Historical, Evidence, Engines |
| INV-005 Atomic persistence | Service, Repository | Transaction audit | Rollback, partial-failure, checkpoint-after-commit tests | Repositories, Services |
| INV-006 Explicit conflicts | Service | Conflict audit | Collision, drift, disagreement, duplicate-conflict tests | Discovery, Registry, Trust, Entity |
| INV-007 No silent merges | Service | Identity audit | Alias, slug, ticker, URL, identifier collision tests | Registry, Identity, Entity |
| INV-008 No caller-controlled authority | Service | Authority audit | Forged authority and direct-bypass tests | Services, CLI, Automation, Providers |
| INV-009 No provider-owned persistence | Provider, Service | Provider boundary review | Provider persistence attempt tests | Providers, Discovery, Acquisition |
| INV-010 No repository-owned business rules | Repository, Service | Repository audit | Repository primitive and business-rule absence tests | Repositories, Services |
| INV-011 One canonical ingress path | Service | Mutation path audit | Direct write rejection and service success-path tests | Services, Repositories, CLI, Automation |
| INV-012 Explicit provenance | Provider, Service, Evidence | Provenance review | Source identity, payload identity, missing-evidence tests | Providers, Evidence, Registry |
| INV-013 Explicit timestamps | Service | Replay audit | Explicit timestamp and wall-clock rejection tests | Services, Repositories, Engines |
| INV-014 Migration safety | Repository | Migration audit | Pre-change schema, idempotent migration, retry tests | Repositories, Migrations |
| INV-015 Backward compatibility | Service, Repository | Compatibility review | Old-row read, public read API, command compatibility tests | CLI, Repositories, Services |
| Service owns authority | Service | Authority review | Service-authorized success and forged-provenance rejection tests | Services |
| Repository persists only | Repository | Repository review | Persistence-plan, schema, read/write boundary tests | Repositories |
| Provider observes only | Provider | Provider review | Provider no-mutation and unavailable-state tests | Providers |
| Engine consumes persisted inputs only | Engine | Engine review | No-provider-payload and persisted-input contract tests | Engines |
| Checkpoint after commit | Service, Repository | Transaction review | Commit-failure, restart, retry, checkpoint tests | Services, Repositories, Automation |
