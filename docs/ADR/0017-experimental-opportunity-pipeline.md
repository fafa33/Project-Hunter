# ADR 0017: Experimental Opportunity Pipeline

## Status

Accepted.

## Context

ADR 0016 classifies `OpportunityAssessment.opportunity_score`, `rank_opportunities`, Probability, Pattern Matching, Technology Necessity, standalone Committee, Pipeline/Fusion, and fusion-backed Opportunity Timing as experimental. It keeps `EvidenceBackedProjectExecutor` and Market Validation as the sole canonical production analytical runtime.

The repository now has generic bitemporal analytical records, isolated experimental persistence for Probability, Pattern, Technology Necessity, and standalone Committee outputs, and a separate canonical Market Validation persistence boundary. Those storage capabilities do not authorize Opportunity input meaning, scoring execution, ranking, consumers, or production status.

The current Opportunity engine and sorter are pure deterministic research components over caller-supplied objects. The repository does not yet have an approved service that assembles every Opportunity factor from authoritative persisted records at a declared cutoff. Without a narrow architecture gate, Phase 3 work could accidentally treat a similarly named field, a latest record, an experimental assessment, a descriptive intelligence finding, or a reachable repository as factor authority.

This ADR decides whether controlled Phase 3 implementation may proceed without changing the production authority established by ADR 0016.

## Decision

Project Hunter authorizes a **staged, isolated, experimental Opportunity reasoning pipeline for research and replay validation only**.

This authorization permits implementation of research infrastructure after this ADR, subject to every condition below. It grants no production analytical authority.

### Classification and production boundary

- Opportunity assessment, `OpportunityAssessment.opportunity_score`, Opportunity ranking, and any Opportunity ranking snapshot remain **experimental**.
- They are not production decisions, investment recommendations, canonical scores, or authoritative rankings.
- They do not replace, modify, contribute to, reinterpret, or compete with Market Validation `hunter_score`, Market Validation ranking, canonical Timing, or canonical Market Validation committee fields.
- They must not be exposed as authoritative outputs through Dashboard API, desktop console, Operational Corpus, alerts, automation, scheduler jobs, public reports, user-facing decision surfaces, or other operational projections.
- Experimental Probability, Pattern, Technology Necessity, and standalone Committee records remain experimental inputs even when durably persisted. Their presence cannot make an Opportunity output production-ready.
- No general or cross-domain ranking authority is created.

ADR 0016 is reaffirmed and is not superseded. `EvidenceBackedProjectExecutor` / Market Validation remains the sole canonical production analytical runtime.

### Conditions for experimental Phase 3 implementation

Implementation may proceed only within all of these constraints:

1. A service-owned input-assembly boundary loads approved persisted records at an explicit effective and known-by cutoff. Engines, sorters, repositories, and presentation layers do not select or authorize inputs.
2. Opportunity input snapshots, assessments, and any later experimental ranking snapshots use a dedicated experimental persistence/configuration boundary physically and operationally isolated from canonical Market Validation, operational data-ops, Dashboard, Operational Corpus, and domain authority stores.
3. Every persisted record carries deterministic identity, schema/model/configuration/methodology version, effective time, recorded time, known-time policy, provenance, source versions, confidence, missing evidence, and correction/supersession lineage.
4. Strict-known replay selects only records effective, recorded, and known at or before the authorized cutoffs. Replay has no `latest` fallback and never substitutes a future, post-cutoff, unknown-known-time, or incompatible record.
5. Input snapshots are immutable and identify the exact records and fields used. Repositories store and retrieve service-authorized state only; they do not infer factor meaning, timestamps, missingness, normalization, confidence, or replay selection.
6. Before a factor is used, its contract declares one semantic owner; exact record, field, and version mapping; entity/representation scope; normalization and units; missingness behavior; confidence propagation; provenance; effective/recorded/known-time policy; and replay rule.
7. Unowned, unavailable, stale, incompatible, or insufficient factors remain explicitly missing and lower confidence. They never become zero-valued supportive evidence, neutral-to-positive defaults, inferred observations, or fabricated confidence.
8. The scoring engine remains a pure calculation over one authorized immutable input snapshot and performs no I/O. The sorter remains a pure deterministic ordering over authorized experimental assessments and performs no I/O.
9. Any experimental ranking later implemented consumes only compatible persisted Opportunity assessments produced under one declared methodology, configuration, entity scope, and cutoff. Mixed-methodology or mixed-cutoff ranking is prohibited.
10. Experimental consumers must be explicitly labeled research/replay tools and must preserve unavailable and experimental status. Persistence, tests, models, CLI existence, or deterministic output do not constitute promotion.

### Explicit exclusions

This ADR does not authorize:

- production promotion or a second canonical analytical runtime;
- any change to Market Validation scoring, weights, ranking, committee semantics, evidence selection, reports, or persistence;
- Tokenomics, unlock risk, catalysts, sell pressure, or Sufficiency as Opportunity factors unless a separate accepted scoring ADR defines semantic ownership, evidence contracts, normalization, missingness, correlation, and anti-double-counting treatment;
- automatic scheduling, alerts, Dashboard/UI/API exposure, desktop presentation, Operational Corpus authority, public recommendations, or investment-decision use;
- experimental Probability, Pattern, Technology Necessity, standalone Committee, Pipeline/Fusion, or fusion-backed Timing outputs as production inputs;
- descriptive ADR 0011–0015 findings automatically mapping to Opportunity factors or composing into a score;
- a broad, unified, general, or cross-domain ranking authority;
- database consolidation or reuse of canonical Market Validation or operational stores for experimental Opportunity records.

### Factor-contract gate

No current Opportunity factor is approved merely because a similarly named field exists. Each factor must pass the contract requirements above before it enters an authorized input snapshot. In particular, descriptive findings do not automatically establish developer momentum, future demand, valuation, whale accumulation, ownership, intent, strategy, risk, catalysts, or timing. A factor that has not passed the gate remains missing.

### Production-promotion gate

Experimental Opportunity outputs may be considered for production only through a future accepted ADR. That ADR must demonstrate and define:

1. A stable semantic contract, units, scope, and prohibited substitutes for the specific output.
2. Complete persisted authority for every factor, including owners, field/version mappings, entity/representation scope, provenance, conflicts, and correction lifecycle.
3. Effective, recorded, and known-time policy with strict-known historical replay and explicit leakage tests.
4. Historical validation, calibration, and backtesting evidence appropriate to the claimed use.
5. Missingness, staleness, incompatibility, sufficiency, and confidence policies validated under realistic absent-data conditions.
6. Correlation and anti-double-counting analysis across factors and derived inputs.
7. Compatibility, migration, cutover, rollback, and retirement plans relative to canonical Market Validation, proving that no parallel authority is introduced.
8. One production owner service/runtime and an explicit persistence authorization boundary under ADR 0009.
9. Explicit permitted consumers and versioned API, Dashboard, operational-read-model, and unavailable-state boundaries.
10. Security, operational, conformance, and report-parity evidence sufficient for the proposed claim.

Until that future ADR is accepted and implemented, Opportunity remains experimental regardless of model quality, persistence, replay coverage, tests, backtests, reports, or user-interface availability.

## Compatibility With Accepted ADRs

| ADR | Constraint applied by this decision |
| --- | --- |
| 0001 | Opportunity research does not bypass discovery-first candidate coverage or turn deep analysis into discovery authority. |
| 0002 | Every factor and output requires evidence provenance, explicit missingness, and leakage-safe replay. |
| 0003 | Input assembly must use canonical candidate identity and lifecycle rather than caller-created substitutes. |
| 0004 | Trust, conflicts, reliability, freshness, and unavailable states remain explicit inputs; Opportunity cannot repair or infer them. |
| 0005 | Economic entities, tokens, contracts, networks, wallets, and listings remain distinct; factor mappings must declare scope. |
| 0006 | No knowledge-graph runtime or graph authority is authorized; existing registry and persisted evidence boundaries remain authoritative. |
| 0007 | The canonical production runtime remains Option A/Market Validation; the authorized Opportunity pipeline is isolated research infrastructure. |
| 0008 | Plugin registration or orchestration cannot promote an input or output, and plugins cannot bypass factor authority contracts. |
| 0009 | Services authorize meaning, time, lineage, replay, and writes; repositories only persist and retrieve authorized records. |
| 0010 | Intelligence execution remains service-owned over persisted evidence; intelligence findings do not automatically become Opportunity factors. |
| 0011 | Developer findings remain descriptive and cannot equal `developer_momentum` without a separate approved factor mapping. |
| 0012 | Tokenomics findings remain descriptive; tokenomics, unlock, sell-pressure, catalyst, valuation, or risk mappings require a separate scoring ADR. |
| 0013 | Governance findings remain descriptive and non-scoring unless a future factor contract and scoring ADR explicitly authorize a mapping. |
| 0014 | Security findings remain descriptive and cannot automatically become an Opportunity risk or supportive factor. |
| 0015 | On-chain findings cannot establish ownership, intent, strategy, whale accumulation, smart-money positioning, or manipulation without an approved contract. |
| 0016 | Reaffirmed: Opportunity and its ranking remain experimental, Market Validation remains the sole production runtime, and promotion requires a future ADR. |

No accepted ADR 0001–0016 is superseded or weakened by this decision.

## Consequences

- Phase 3 research implementation may proceed behind a clear authority and persistence boundary.
- Factor ownership and replay contracts must be completed incrementally; incomplete factors remain missing rather than blocking honest experimental runs or being defaulted optimistically.
- Research can produce durable, leakage-tested snapshots and assessments without changing production behavior.
- Opportunity persistence, reports, or rankings will require conspicuous experimental classification and isolated consumers.
- Production Market Validation, Timing, committee fields, reports, Dashboard, automation, and operational stores remain unchanged.
- A future promotion proposal carries a high evidence and migration burden because parallel analytical authority is prohibited.

## Alternatives Considered

### Keep all Phase 3 implementation prohibited

Rejected because controlled persisted-input research and strict-known replay are necessary to validate whether Opportunity semantics are viable. Isolation and explicit factor gates address the authority risk without freezing research.

### Promote Opportunity directly to production

Rejected because factor authority, historical leakage controls, calibration, anti-double-counting evidence, and compatibility with Market Validation are not yet demonstrated.

### Allow experimental scoring over caller-supplied or latest records

Rejected because it would make replay non-reproducible, obscure missingness and provenance, and create leakage risk.

### Reuse Market Validation, data-ops, Dashboard, or Operational Corpus storage

Rejected because those stores have different semantic and operational ownership. Physical persistence convenience cannot create or merge analytical authority.
