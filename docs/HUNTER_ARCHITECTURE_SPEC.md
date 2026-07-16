# Project Hunter Architecture Specification

## 1. Purpose

This document defines the target architecture for evolving Hunter from a static project-analysis platform into a discovery-first investment intelligence system.

The current production scoring boundary remains `EvidenceBackedProjectExecutor`. Discovery expands the market entry point; it does not replace the validated scoring runtime.

## 1.1 Architecture Decisions

This specification implements and expands the accepted ADRs in `docs/ADR/README.md`, especially:

- `docs/ADR/0001-discovery-first.md`;
- `docs/ADR/0002-evidence-first.md`;
- `docs/ADR/0003-candidate-registry.md`;
- `docs/ADR/0004-trust-layer.md`;
- `docs/ADR/0005-entity-model.md`;
- `docs/ADR/0006-knowledge-graph.md`.

Any future change that reverses those decisions or changes the production runtime boundary must be captured in a new ADR before this specification is rewritten around the new direction.

## 2. Target Operating Model

```text
Market-wide sources
  -> source adapters
  -> raw source records
  -> normalization
  -> identity resolution
  -> dynamic candidate registry
  -> lightweight screening
  -> prioritized candidate queue
  -> evidence acquisition
  -> EvidenceBackedProjectExecutor
  -> ranking / committee / explainability
  -> future intrinsic-value thesis
```

## 3. Discovery Layer

The discovery layer continuously enumerates assets and protocols from independent sources.

Responsibilities:

- discover new assets;
- update existing assets;
- detect renames, migrations, delistings, and abandoned projects;
- record source observation time;
- normalize source-specific records;
- create or reconcile candidate identities;
- produce coverage and conflict reports.

One source equals one adapter. Adapters must be independently configured, enabled, disabled, health-checked, rate-limited, retried, checkpointed, and tested.

Initial source priorities:

1. CoinGecko market discovery.
2. DefiLlama protocol discovery.
3. GeckoTerminal or DexScreener decentralized-market discovery.
4. GitHub enrichment only when official identity is verifiable.
5. Chain RPC and explorer adapters by network.

No single source is authoritative.

## 4. Source Adapter Contract

Each adapter emits typed `SourceAssetRecord` objects containing only normalized source observations and provenance.

Required fields should include:

- adapter id;
- source asset id;
- observed name and symbol;
- asset type;
- chain identifiers;
- contract addresses;
- official links when supplied;
- category labels;
- market fields when available;
- observation timestamp;
- retrieval timestamp;
- raw evidence reference;
- source confidence and availability status.

Source payloads must not leak into downstream domains.

## 5. Dynamic Candidate Registry

The registry is the durable canonical map of the investable market.

A registry entry may represent a protocol, network, native asset, token, or other supported economic entity.

Required registry concepts:

- canonical candidate id;
- canonical name;
- aliases and prior names;
- source-specific ids;
- symbols with collision awareness;
- asset and entity type;
- chains and deployments;
- verified contracts;
- wrapped or bridged representations;
- official domains;
- official repositories;
- categories and sectors;
- discovery timestamps;
- latest observation timestamp;
- lifecycle and validation status;
- evidence references;
- confidence;
- migration and archival history.

The existing 50-project universe is imported as a compatibility seed, not treated as the complete market.

## 6. Identity Resolution

Identity resolution must be deterministic and evidence-backed.

High-confidence evidence:

- exact verified contract plus chain;
- official domain;
- official repository;
- trusted provider id;
- verified migration record;
- protocol-owned documentation.

Resolution outcomes:

- exact;
- probable;
- ambiguous;
- conflict;
- rejected.

Ticker equality is never sufficient for a merge.

The resolver must handle:

- ticker collisions;
- duplicate listings;
- wrapped and bridged assets;
- native versus wrapped assets;
- token and protocol separation;
- contract migrations;
- project renames;
- forks and impersonators;
- chain-specific deployments.

Ambiguous records remain separate and unresolved.

## 7. Candidate Lifecycle

Canonical lifecycle states:

- `discovered`;
- `identified`;
- `evidence_pending`;
- `screenable`;
- `analyzable`;
- `ranked`;
- `deep_research`;
- `rejected`;
- `archived`.

Every transition records:

- prior state;
- new state;
- timestamp;
- reason;
- supporting evidence;
- discovery or screening run id.

Invalid transitions fail explicitly.

## 8. Lightweight Screening

Screening must be cheap enough to apply to thousands of candidates.

It is not intrinsic valuation and must not produce unsupported return forecasts.

Defensible screening dimensions include:

- canonical identity confidence;
- source agreement;
- market data availability;
- liquidity availability;
- verified contracts or native identity;
- active official domain;
- official repository availability;
- protocol data availability;
- evidence freshness;
- listing breadth;
- obvious impersonation or scam conflicts;
- minimum analyzability threshold.

Every result explains advancement, deferral, rejection, missing evidence, and confidence.

## 9. Candidate Queue

The queue answers:

> What should Hunter investigate next, and why?

Queue priority may consider:

- identity confidence;
- evidence coverage;
- freshness;
- analyzability;
- unusual cross-source activity;
- market-cap tier;
- sector representation;
- novelty;
- high-value missing evidence;
- previous screening outcome.

Popularity must not be equated with investment quality. Known assets and newly discovered assets must both be eligible.

## 10. Deep Analysis Integration

`EvidenceBackedProjectExecutor` remains the canonical production scoring boundary.

Only candidates in the `analyzable` state may enter the deep analysis path.

A typed conversion service maps qualified registry entries to the existing canonical project identity.

No second competing scoring runtime is created.

## 11. Persistence

SQL-backed repositories should become authoritative for:

- candidates;
- aliases;
- source identifiers;
- contracts;
- lifecycle history;
- discovery runs;
- checkpoints;
- conflicts;
- screening results;
- queue entries.

JSONL remains acceptable for raw immutable acquisition evidence, but not as the authoritative market-wide indexed registry.

All writes must be idempotent.

## 12. Automation

Discovery automation should install idempotent jobs for:

- source health;
- incremental discovery;
- registry reconciliation;
- lightweight screening;
- queue refresh;
- archival and delisting review.

Jobs require checkpoints, bounded retries, cooldowns, restart recovery, explicit unavailable states, and persisted run status.

Typed services are preferred over shelling through CLI commands.

## 13. Point-in-Time Semantics

Hunter must retain when it first knew:

- that a candidate existed;
- which evidence was available;
- when identity was resolved;
- when lifecycle states changed;
- when the candidate became analyzable.

Historical replay must not backfill future knowledge into earlier dates.

## 14. Data Quality Governance

Policies must cover:

- source reliability;
- conflicting values;
- stale observations;
- collisions;
- unverifiable links;
- abandoned projects;
- delistings;
- migrations;
- spam and impersonation;
- evidence expiry.

Every unresolved, rejected, or archived candidate has a machine-readable reason. No silent drops.

## 15. Coverage Semantics

Report coverage separately for:

- source discovery;
- canonical identity;
- contract identity;
- official-link verification;
- screening;
- analyzable candidates;
- deep analysis;
- historical point-in-time evidence.

Do not compress these dimensions into a misleading completeness score.

## 16. Practical Output

A live discovery cycle must produce a market-triage report with:

- source records observed;
- unique canonical candidates;
- new and updated candidates;
- ambiguity and conflict counts;
- lifecycle distribution;
- categories and sectors;
- market-cap tiers where available;
- evidence and source coverage;
- prioritized candidates;
- priority reasons;
- missing evidence;
- readiness for deep analysis.

This report is not an investment recommendation. It is the decision-useful answer to what deserves further research.

## 17. Future Intrinsic-Value Layer

Intrinsic value becomes a first-class domain only after discovery, registry, queue, and point-in-time evidence are stable.

The future thesis engine should combine:

- historical analogues;
- present fundamentals;
- future market size;
- attainable market share;
- token value capture;
- dilution and supply mechanics;
- competitive moat;
- network effects;
- adoption curve;
- scenario probabilities;
- failure conditions.

Outputs must be scenario-based, evidence-linked, and historically calibrated.

## 18. Non-Goals for the Discovery Release

Do not prematurely implement:

- 10x or 100x forecasts;
- portfolio construction;
- trade execution;
- speculative ML without validated training data;
- distributed workers unless required by proven scale;
- generic API or dashboard expansion before backend lifecycle stability.

## 19. Acceptance Standard

The architecture is functioning when Hunter can continuously maintain a dynamic market registry, resolve identities safely, screen thousands of candidates cheaply, produce a deterministic queue, and submit qualified candidates into the unchanged evidence-backed analysis path.
