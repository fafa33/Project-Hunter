# Hunter Core Architecture Blueprint

Status: Proposed architecture freeze

## Mission

Hunter is a market discovery and evidence-driven investment decision-support system. Its primary mission is to continuously discover the crypto market, identify exceptional opportunities before broad recognition, explain why they exist, estimate their probability of success, and convert them into portfolio-level decisions.

Hunter is not a dashboard, trading bot, screener, or portfolio tracker. Presentation and automation remain downstream consumers of authoritative runtime intelligence.

## Core Architecture

The authoritative reasoning pipeline is:

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

Dependencies may point only forward through this pipeline, except for historical-learning feedback that updates versioned methodologies without rewriting prior outputs.

## Layer Responsibilities

### 1. Market Discovery

Owns market-wide candidate discovery, canonical identity, aliases, lifecycle, source coverage, and candidate eligibility.

It must not perform investment scoring or recommendation.

### 2. Evidence Acquisition

Owns raw retrieval, normalization, validation, source registration, checkpoints, retries, and acquisition provenance.

It must not interpret evidence or calculate analytical scores.

### 3. Evidence Intelligence

Owns domain-specific interpretation of acquired evidence.

Examples include macro, whale, developer, protocol, technology, tokenomics, unlocks, sell pressure, governance, adoption, revenue, competitive position, and capital formation.

Each domain engine must emit typed evidence claims, findings, or domain assessments. It must not produce the final opportunity score, probability, portfolio allocation, or UI state.

### 4. Evidence Fusion

Evidence Fusion is the single integration boundary between domain intelligence and decision intelligence.

It owns:

- canonical evidence bundles per asset and cutoff;
- conflict detection and resolution state;
- evidence sufficiency;
- evidence freshness;
- source diversity;
- cross-engine agreement and disagreement;
- confidence composition;
- provenance-preserving unified evidence views;
- known-by-Hunter replay semantics.

It must preserve source-level evidence and must not erase disagreement through an opaque average.

Opportunity, Prediction, and Portfolio Intelligence must consume Fusion outputs rather than directly assembling arbitrary values from domain repositories.

### 5. Opportunity Intelligence

Owns the question: "Is this asset an exceptional investment opportunity relative to alternatives?"

It consumes a versioned fused evidence snapshot and produces:

- authoritative OpportunityAssessment;
- authoritative opportunity_score;
- opportunity drivers;
- disconfirming evidence;
- risks;
- catalysts;
- evidence sufficiency state;
- methodology version;
- immutable assessment provenance.

OpportunityAssessment.opportunity_score is the sole authoritative opportunity score.

TimingAssessment.entry_score, MarketValidation hunter_score, confidence, probability, and portfolio utility are independent concepts and must never substitute for opportunity_score.

Opportunity Intelligence must not acquire data, query Dashboard API, or read Terminal state.

### 6. Prediction Intelligence

Prediction Intelligence unifies probability, historical analogs, pattern matching, outcome hypotheses, calibration, and prediction lifecycle under one architectural owner.

It owns:

- probability assessments;
- scenario-conditioned probabilities;
- historical similarity;
- pattern evidence;
- prediction hypotheses;
- prediction horizons;
- closure and correctness evaluation;
- calibration and accuracy aggregates.

Existing Probability, Pattern Matching, and historical-similarity capabilities may remain internal modules, but they are not independent top-level decision authorities.

Prediction Intelligence consumes fused evidence and authoritative opportunity assessments. It must not redefine opportunity_score.

### 7. Portfolio Intelligence

Portfolio Intelligence is the final investment decision-support layer.

It owns:

- cross-asset comparison;
- ranking for a specific portfolio context;
- capital allocation proposals;
- concentration limits;
- correlation and shared-risk analysis;
- replacement opportunity analysis;
- entry sequencing;
- exit and review conditions;
- user-goal and constraint adaptation.

A market-wide opportunity ranking is owned here when ranking depends on comparative utility, portfolio constraints, or capital allocation. A context-free analytical ordering may be exposed by Opportunity Intelligence only if explicitly defined as such.

Portfolio Intelligence must remain advisory and explainable. Automated trade execution is outside the current core mission.

### 8. Operational Execution

Owns orchestration, automation, scheduling, jobs, retries, run records, health, and operational corpus.

Operational records prove that work occurred. They are not substitutes for analytical authority.

### 9. Dashboard API

Dashboard API is the sole read interface between runtime and presentation.

It exposes persisted authoritative outputs and explicit unavailable states. It must not calculate analytical scores, fuse evidence, infer missing values, rank assets, or convert null into zero.

### 10. Hunter Terminal

Hunter Terminal is visualization and user interaction only.

It must not access runtime repositories directly, calculate intelligence, or become an analytical authority.

## Architectural Ownership

Each major concept has one owner:

| Concept | Sole architectural owner |
| --- | --- |
| Candidate identity | Market Discovery |
| Raw and normalized source evidence | Evidence Acquisition |
| Domain interpretation | Corresponding Evidence Intelligence engine |
| Cross-domain confidence | Evidence Fusion |
| Evidence sufficiency | Evidence Fusion, using authoritative sufficiency inputs |
| Opportunity score | Opportunity Intelligence |
| Timing signal | Opportunity Timing module, consumed as independent evidence |
| Probability and prediction lifecycle | Prediction Intelligence |
| Historical pattern interpretation | Prediction Intelligence |
| Cross-asset ranking and allocation | Portfolio Intelligence |
| Operational health | Operational Execution |
| Presentation contracts | Dashboard API |
| UI state | Hunter Terminal |

No subsystem may create a second authority for one of these concepts.

## Committee

Investment Committee is not a separate top-level intelligence layer.

It becomes an explainable policy and decision-composition stage within Portfolio Intelligence. Specialist votes may remain as internal decision artifacts, but the Committee must not own competing probability, opportunity, risk, or allocation scores.

## Historical Learning

Historical Validation and Backtesting form a cross-cutting learning capability.

They may evaluate every analytical layer, but they must not silently mutate prior records or change historical methodology results. Improvements must create new versioned methodologies, calibration models, and outputs.

Replay must use information known by Hunter at the requested cutoff and must prevent future leakage.

## Persistence and Authority Rules

Every authoritative analytical output must be:

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
- explicitly unavailable when required inputs are insufficient.

Operational JSON, process status, UI cache, and downstream corpus summaries cannot substitute for authoritative analytical records.

## Migration Principles

The architecture must be migrated incrementally without a repository-wide rewrite.

1. Freeze ownership and terminology before changing runtime behavior.
2. Introduce Evidence Fusion contracts and persistence without deleting existing engines.
3. Adapt domain engines to publish typed fusion inputs.
4. Make Opportunity Intelligence consume only fused evidence snapshots.
5. Consolidate probability, pattern, historical similarity, and prediction lifecycle under Prediction Intelligence.
6. Add Portfolio Intelligence after opportunity and prediction authorities are durable.
7. Move Committee behavior into Portfolio Intelligence.
8. Replace Dashboard operational fallbacks only after authoritative outputs exist.
9. Preserve compatibility until each legacy path has an authoritative replacement and migration tests.

## Prohibited Shortcuts

The following are architecturally forbidden:

- using TimingAssessment.entry_score as opportunity_score;
- using MarketValidation hunter_score as opportunity_score;
- informal field-name mapping between engines;
- Dashboard-side score calculation;
- Terminal-side analytical interpretation;
- treating missing data as zero without explicit methodology;
- using operational corpus as analytical authority;
- recomputing historical outputs with future knowledge;
- allowing multiple owners for confidence, probability, risk, ranking, or allocation;
- implementing portfolio ranking before authoritative opportunity and prediction persistence.

## Implementation Sequence

### Phase A — Architecture Freeze

- adopt this blueprint;
- create ADRs for layer ownership and score semantics;
- publish an engine-to-layer inventory;
- mark legacy authorities and planned migrations.

### Phase B — Evidence Fusion Foundation

- define fused evidence snapshot, conflict, sufficiency, freshness, confidence, and provenance contracts;
- add immutable persistence and replay semantics;
- implement adapters from existing authoritative domain outputs;
- fail closed for unavailable or ambiguous sources.

### Phase C — Opportunity Authority

- define the exact Opportunity methodology against fused evidence fields;
- persist OpportunityMetricSnapshot and OpportunityAssessment;
- establish opportunity_score as sole authority;
- prohibit direct arbitrary reads from domain stores.

### Phase D — Prediction Intelligence Consolidation

- add durable probability, pattern, historical similarity, prediction, closure, and calibration records;
- preserve internal modularity while exposing one top-level authority;
- provide prediction accuracy aggregates.

### Phase E — Portfolio Intelligence

- define portfolio context, constraints, comparative utility, ranking, and allocation contracts;
- integrate opportunity, probability, timing, risk, and correlation without collapsing their semantics;
- move Committee policy composition into this layer.

### Phase F — Presentation Completion

- expose only authoritative Opportunity, Prediction, and Portfolio snapshots through Dashboard API;
- complete Hunter Terminal pages;
- retain explicit unavailable states where runtime authority is absent.

## Definition of Architectural Completion

Hunter reaches an evidence-driven investment decision-support architecture when:

- every discovered candidate can flow through a deterministic, replayable pipeline;
- domain intelligence produces typed, evidence-backed outputs;
- Evidence Fusion provides a unified but provenance-preserving view;
- Opportunity Intelligence explains and persists why an asset is or is not exceptional;
- Prediction Intelligence estimates and later evaluates outcome probabilities;
- Portfolio Intelligence compares opportunities under real user constraints;
- Dashboard API and Hunter Terminal only expose authoritative persisted outputs;
- every decision can be reconstructed using only information known by Hunter at the historical cutoff.
