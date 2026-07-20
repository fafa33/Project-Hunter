# Hunter Core Architecture Blueprint

Status: Proposed target architecture

## Authority Boundary

This document describes a proposed long-term target architecture and migration direction.

It does not create canonical runtime authority, production analytical authority, score ownership, persistence authorization, or implementation permission.

Current authority remains governed by the canonical document hierarchy and accepted ADRs, especially ADR 0007 and ADR 0016–0021.

Until a future accepted ADR explicitly promotes a defined output and satisfies the required evidence, replay, ownership, persistence, compatibility, and migration contracts:

- Market Validation remains the sole canonical production analytical runtime;
- Opportunity, general ranking, Probability, Pattern Matching, Technology Necessity, standalone Committee, Pipeline/Fusion, and fusion-backed Opportunity Timing remain experimental or research capabilities;
- valuation-family inputs remain unavailable under ADR 0021 unless their complete service-owned contracts are implemented and accepted;
- operational and presentation components remain downstream and non-analytical.

Where this blueprint uses terms such as "owner," "single boundary," or "authoritative," they describe proposed future ownership after explicit ADR approval and implementation. They do not override current accepted decisions.

## Mission

Hunter is a market-discovery and evidence-driven investment decision-support system. Its long-term mission is to continuously discover the crypto market, identify exceptional opportunities before broad recognition, explain why they exist, estimate their probability of success, and convert them into portfolio-level decision support.

Hunter is not a trading bot. Presentation and automation remain downstream consumers of approved runtime intelligence.

## Proposed Target Architecture

The proposed target reasoning pipeline is:

1. Market Discovery
2. Evidence Acquisition
3. Evidence Intelligence
4. Evidence Fusion
5. Opportunity Intelligence
6. Prediction Intelligence
7. Portfolio Intelligence
8. Operational Execution
9. Dashboard API
10. Hunter Terminal

Dependencies should point only forward through this proposed pipeline, except for historical-learning feedback that creates new versioned methodologies without rewriting prior outputs.

This pipeline is a target architecture, not a statement that every layer currently exists as a production authority.

## Proposed Layer Responsibilities

### 1. Market Discovery

Proposed responsibility:

- market-wide candidate discovery;
- canonical identity references;
- aliases;
- lifecycle;
- source coverage;
- candidate eligibility.

It must not perform investment scoring or recommendations.

### 2. Evidence Acquisition

Proposed responsibility:

- raw retrieval;
- normalization;
- validation;
- source registration;
- checkpoints;
- retries;
- acquisition provenance.

It must not interpret evidence or calculate analytical scores.

### 3. Evidence Intelligence

Proposed responsibility: domain-specific interpretation of acquired evidence.

Examples include macro, whale, developer, protocol, technology, tokenomics, unlocks, sell pressure, governance, adoption, revenue, competitive position, and capital formation.

Each domain engine should emit typed evidence claims, findings, or domain assessments under its accepted contract. It must not create a final opportunity score, probability, portfolio allocation, or UI state unless a future accepted ADR explicitly authorizes that output.

### 4. Evidence Fusion

Evidence Fusion is proposed as the future integration boundary between domain intelligence and decision intelligence.

Its proposed responsibilities include:

- evidence bundles per asset and cutoff;
- conflict and resolution state;
- evidence sufficiency;
- evidence freshness;
- source diversity;
- cross-engine agreement and disagreement;
- confidence composition;
- provenance-preserving unified evidence views;
- known-by-Hunter replay semantics.

It must preserve source-level evidence and must not erase disagreement through opaque averaging.

Future Opportunity, Prediction, and Portfolio services should consume approved Fusion outputs only after a future ADR establishes Fusion authority, inputs, persistence, replay, migration, and consumers.

Current experimental Fusion does not acquire production authority through this document.

### 5. Opportunity Intelligence

Opportunity Intelligence is proposed to answer:

> Is this asset an exceptional investment opportunity relative to alternatives?

A future approved implementation may consume a versioned fused-evidence snapshot and produce:

- `OpportunityAssessment`;
- `opportunity_score`;
- opportunity drivers;
- disconfirming evidence;
- risks;
- catalysts;
- evidence sufficiency state;
- methodology version;
- immutable assessment provenance.

Under the target design, `OpportunityAssessment.opportunity_score` would become the sole opportunity-score authority only after explicit production promotion through a future accepted ADR.

Until then, it remains experimental under ADR 0016–0018.

`TimingAssessment.entry_score`, Market Validation `hunter_score`, confidence, probability, and portfolio utility are separate concepts and must not substitute for `opportunity_score`.

Opportunity Intelligence must not acquire data, query Dashboard API, or read Terminal state.

### 6. Prediction Intelligence

Prediction Intelligence is proposed to unify:

- probability assessments;
- scenario-conditioned probabilities;
- historical similarity;
- pattern evidence;
- prediction hypotheses;
- prediction horizons;
- closure and correctness evaluation;
- calibration and accuracy aggregates.

Existing Probability, Pattern Matching, and historical-similarity capabilities may remain internal modules, but this blueprint does not promote them.

ADR 0019 separately defines canonical prediction-evaluation authority and its future service boundary. This blueprint must not collapse experimental prediction generation into canonical evaluation authority.

Future Prediction Intelligence should consume approved evidence and authorized Opportunity assessments. It must not redefine `opportunity_score`.

### 7. Portfolio Intelligence

Portfolio Intelligence is proposed as the final personal investment decision-support layer.

Its proposed responsibilities include:

- cross-asset comparison;
- ranking for a specific portfolio context;
- capital-allocation proposals;
- concentration limits;
- correlation and shared-risk analysis;
- replacement-opportunity analysis;
- entry sequencing;
- exit and review conditions;
- user-goal and constraint adaptation.

A market-wide ranking may belong here when it depends on comparative utility, portfolio constraints, or capital allocation.

Portfolio Intelligence must remain advisory, explainable, and non-custodial. Automated trade execution is outside the current core mission.

No production Portfolio authority is created by this document.

### 8. Operational Execution

Operational Execution owns operational concerns only:

- orchestration;
- automation;
- scheduling;
- jobs;
- retries;
- run records;
- health;
- operational corpus.

Operational records prove that work occurred. They do not establish analytical authority.

### 9. Dashboard API

Dashboard API is proposed as the read boundary between approved runtime outputs and presentation.

It must expose persisted authoritative records or explicit unavailable states.

It must not:

- calculate analytical scores;
- fuse evidence;
- infer missing values;
- create rankings;
- convert null into zero;
- promote experimental outputs.

### 10. Hunter Terminal

Hunter Terminal is visualization and user interaction only.

It must not access analytical repositories directly, calculate intelligence, reinterpret classifications, or become an analytical authority.

## Proposed Future Ownership Map

The following table describes proposed target ownership, subject to accepted ADR approval and completed implementation.

| Concept | Proposed future owner |
| --- | --- |
| Candidate identity | Market Discovery and canonical identity services |
| Raw and normalized source evidence | Evidence Acquisition |
| Domain interpretation | Corresponding approved Evidence Intelligence engine |
| Cross-domain confidence | Future Evidence Fusion authority |
| Evidence sufficiency | Future Evidence Fusion authority using approved sufficiency inputs |
| Opportunity score | Future Opportunity Intelligence authority |
| Timing signal | Its explicitly approved Timing authority; never inferred from naming |
| Prediction generation lifecycle | Future Prediction Intelligence authority |
| Prediction correctness and calibration | `PredictionEvaluationService` under ADR 0019 when implemented |
| Historical-pattern interpretation | Future Prediction Intelligence authority |
| Cross-asset ranking and allocation | Future Portfolio Intelligence authority |
| Operational health | Operational Execution |
| Presentation contracts | Dashboard API |
| UI state | Hunter Terminal |

Current accepted ADR classifications override this proposed map wherever implementation or promotion has not occurred.

No subsystem may create a second authority for an already governed concept.

## Committee

The target architecture proposes that Investment Committee behavior eventually become an explainable policy and decision-composition stage within Portfolio Intelligence rather than a competing top-level intelligence authority.

Specialist votes may remain internal decision artifacts, but they must not create competing probability, opportunity, risk, or allocation scores.

The current standalone Committee remains experimental under ADR 0016. This document does not migrate or promote it.

## Historical Learning

Historical Validation and Backtesting are cross-cutting learning capabilities.

They may evaluate analytical layers, but they must not silently mutate prior records or rewrite historical methodology results.

Improvements must create new versioned methodologies, calibration models, and outputs.

Replay must use information known by Hunter at the requested cutoff and prevent future leakage.

## Persistence and Authority Rules

Any future authoritative analytical output must be:

- typed;
- schema-versioned;
- methodology-versioned where applicable;
- immutable or correction-versioned;
- idempotently persisted;
- effective-time aware;
- recorded-time aware;
- known-by-Hunter aware;
- provenance-preserving;
- replayable;
- explicitly unavailable when required inputs are insufficient;
- authorized by its approved service boundary.

Operational JSON, process status, UI cache, downstream corpus summaries, repository reachability, model existence, or tests cannot substitute for authoritative analytical records.

## Migration Principles

Migration toward this target architecture must be incremental and governed.

1. Preserve current accepted authority until an explicit replacement ADR is accepted and implemented.
2. Freeze terminology and proposed ownership before changing runtime behavior.
3. Define Evidence Fusion contracts and isolated research persistence before considering promotion.
4. Adapt domain engines through explicit typed contracts without weakening existing authority.
5. Allow Opportunity research to consume only approved factor sources under ADR 0017–0018.
6. Promote Opportunity only after methodology, replay, calibration, persistence, compatibility, cutover, and retirement requirements are satisfied.
7. Keep prediction generation separate from canonical evaluation authority under ADR 0019.
8. Add Portfolio Intelligence only after Opportunity and Prediction authorities are durable and compatible.
9. Move Committee behavior only through an explicit migration decision.
10. Replace Dashboard fallbacks only after authoritative outputs exist.
11. Preserve compatibility until every legacy path has an approved replacement and migration tests.
12. At most one authority may exist for one semantic output at one effective boundary.

## Prohibited Shortcuts

The following are prohibited:

- using `TimingAssessment.entry_score` as `opportunity_score`;
- using Market Validation `hunter_score` as `opportunity_score`;
- informal field-name mapping between engines;
- treating model, test, repository, record, job, or UI existence as authority;
- Dashboard-side score calculation;
- Terminal-side analytical interpretation;
- treating missing data as zero without an approved methodology;
- using Operational Corpus as analytical authority;
- recomputing historical outputs with future knowledge;
- allowing multiple owners for confidence, probability, risk, ranking, or allocation;
- implementing production portfolio ranking before approved Opportunity and Prediction persistence;
- implementing this blueprint directly without the ADRs and sprint authorization required by the canonical hierarchy.

## Proposed Implementation Sequence

### Phase A — Target-Architecture Clarification

- maintain this blueprint as a non-authoritative target document;
- create or amend ADRs for each production promotion;
- publish an engine-to-layer inventory;
- mark current, experimental, unavailable, deferred, and planned authorities.

### Phase B — Evidence Fusion Research Foundation

- define research fused-evidence snapshots, conflicts, sufficiency, freshness, confidence, and provenance contracts;
- use isolated immutable persistence and strict-known replay;
- implement adapters only from approved source records;
- fail closed for unavailable or ambiguous sources;
- preserve experimental classification until a promotion ADR is accepted.

### Phase C — Opportunity Research and Possible Future Promotion

- continue under ADR 0017–0018;
- define exact factor methodology against approved sources;
- persist isolated experimental snapshots and assessments;
- prohibit arbitrary reads and unsupported factor substitution;
- require a future promotion ADR before any production authority.

### Phase D — Prediction Consolidation and Evaluation

- preserve the separation between experimental prediction generation and canonical evaluation;
- implement ADR 0019 only through its dedicated service and persistence boundary;
- add durable probability, pattern, historical similarity, prediction, closure, and calibration records only under approved contracts;
- expose one authority per exact semantic output.

### Phase E — Portfolio Intelligence

- define portfolio context, constraints, comparative utility, ranking, and allocation contracts;
- integrate approved opportunity, probability, timing, risk, and correlation inputs without collapsing their semantics;
- migrate Committee policy composition only through an accepted ADR;
- preserve advisory-only behavior.

### Phase F — Presentation Completion

- expose only appropriately classified persisted outputs through Dashboard API;
- complete Hunter Terminal pages;
- retain explicit unavailable, experimental, legacy, and insufficient states;
- prevent presentation from becoming an analytical authority.

## Definition of Target Architectural Completion

Hunter reaches this proposed target architecture only when:

- every discovered candidate can flow through a deterministic, replayable, authority-preserving pipeline;
- domain intelligence produces typed, evidence-backed outputs under accepted contracts;
- Evidence Fusion has an explicitly approved authority and provides a unified but provenance-preserving view;
- Opportunity Intelligence has an accepted methodology and explains and persists why an asset is or is not exceptional;
- Prediction Intelligence produces fully contracted predictions and ADR 0019 evaluation later measures them correctly;
- Portfolio Intelligence compares approved opportunities under real user constraints;
- Dashboard API and Hunter Terminal expose only classified, persisted outputs and explicit unavailable states;
- every decision can be reconstructed using only information known by Hunter at the historical cutoff;
- no semantic output has more than one authority;
- every production promotion has an accepted ADR, tested migration, cutover, rollback, and retirement plan.

Until those conditions are satisfied, this document remains a proposed target blueprint rather than a statement of completed production architecture.