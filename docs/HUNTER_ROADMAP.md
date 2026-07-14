# Project Hunter Roadmap

## Roadmap Rule

Every release must improve real investment decisions. Features that do not improve discovery quality, evidence quality, ranking quality, or decision usefulness move later.

## Phase 0 — Preserve the Production Baseline

Status: completed foundation.

Preserve:

- deterministic evidence-backed Market Validation;
- `EvidenceBackedProjectExecutor`;
- explainability;
- historical replay discipline;
- automation lifecycle;
- existing acquisition and intelligence engines;
- regression tests.

## Phase 1 — Investor-Useful Global Discovery

### v2.7.0 — Global Discovery and Candidate Registry

Primary result:

> Hunter continuously tells the user what deserves deeper analysis next and why.

Deliverables:

- independent discovery adapter contract;
- live CoinGecko and DefiLlama discovery;
- one decentralized-market source when operationally reachable;
- seeded import of the existing 50 projects;
- dynamic SQL-backed Candidate Registry;
- identity-ready registry structures;
- candidate lifecycle;
- lightweight market-wide screening;
- persistent prioritized Candidate Queue;
- discovery automation;
- point-in-time candidate existence;
- practical market-triage report.

Success test:

Hunter sees materially more than the static universe and produces a defensible prioritized list from live market data.

### v2.7.1 — Discovery Hardening and Production Verification

Delivered hardening:

- provider retry and failure isolation;
- registry merge corrections;
- deterministic screening gates;
- batch registry writes;
- expanded coverage reporting.

### v2.8.0 — Global Market Expansion

Primary result:

> Hunter materially expands market visibility through additional public discovery sources.

Deliverables:

- GeckoTerminal discovery;
- DexScreener discovery;
- chain-plus-contract identifiers for DEX-market candidates;
- provider overlap and uniqueness reporting;
- chain, ecosystem, and category coverage reporting;
- source-health automation job;
- deterministic tests for adapter normalization and contract-based overlap.

Success test:

Hunter discovers substantially more candidates than the seed universe while preserving evidence provenance and avoiding unsupported identity claims.

## Phase 1.5 — Trust and Identity Foundation

### v2.9.0 — Identity Resolution Foundation

Primary result:

> Hunter safely determines which discovered records refer to the same economic entity and which must remain ambiguous.

Deliverables:

- deterministic Identity Resolution service;
- exact, probable, ambiguous, conflict, rejected, and unresolved outcomes;
- chain-aware contract matching;
- official domain and repository matching when verified;
- conflict persistence and reporting;
- lifecycle integration for identified candidates;
- identity coverage reporting.

Success test:

Hunter reduces duplicate and ambiguous candidates without merging by ticker equality, popularity, or unsupported assumptions.

## Phase 2 — Better Candidate Selection

### Competitive and Peer Intelligence

- direct and indirect competitors;
- substitute technologies;
- centralized incumbents;
- peer-set discovery;
- competitive density;
- switching costs;
- differentiation and replaceability.

### Tokenomics and Supply Mechanics

- circulating float;
- unlock schedules;
- emissions;
- inflation;
- staking concentration;
- treasury supply;
- holder concentration;
- dilution pressure.

### Revenue and Economic Quality

- fees and revenue;
- sustainability;
- incentive dependence;
- user-paid versus subsidized activity;
- value capture by the token;
- sector-normalized economic quality.

### Market Structure and Liquidity

- exchange and DEX coverage;
- depth and slippage;
- float-adjusted market cap;
- liquidity concentration;
- manipulation risk;
- tradability constraints.

Success test:

The top candidate list becomes more selective and more useful than simple market-cap, narrative, or momentum rankings.

## Phase 3 — Network Effects and Moat

Build only after reliable peer sets and time series exist.

- developer ecosystem growth;
- user and liquidity graphs;
- integration density;
- validator/miner/provider networks;
- data and distribution advantages;
- protocol composability;
- community and governance durability;
- moat strengthening or deterioration over time.

Success test:

Hunter can explain why a leader is likely to retain its position or why an apparently strong project is replaceable.

## Phase 4 — Historical Pattern and Failure Calibration

Expand historical validation beyond price outcomes.

- point-in-time candidate universes;
- survivor and failed-project cohorts;
- early Bitcoin, Ethereum, Solana, Chainlink and other winners;
- projects with similar narratives that failed;
- feature calibration by sector and market regime;
- false-positive and false-negative tracking;
- thesis invalidation history.

Success test:

Hunter's opportunity judgments are calibrated against both historical winners and failures without lookahead bias.

## Phase 5 — Intrinsic Value and Asymmetric Opportunity

The final investment thesis engine combines past, present, and future.

Required inputs:

- addressable market evolution;
- plausible market share;
- adoption curve;
- competitive position;
- network effects;
- economic output;
- token value capture;
- supply and dilution;
- macro regime;
- historical calibration;
- execution and regulatory risks.

Required outputs:

- current market value;
- bear, base, and bull intrinsic-value ranges;
- expected upside multiple by horizon;
- confidence and evidence coverage;
- assumptions;
- invalidation conditions;
- historical analogues;
- reasons the market may be mispricing the asset.

Hunter may state that an asset has 10x, 100x, or greater plausible upside only when the number follows from explicit scenarios, defensible market-size assumptions, token value capture, and evidence-linked uncertainty.

Success test:

Hunter identifies the largest gaps between current valuation and evidence-supported long-term value, regardless of whether the asset is famous or obscure.

## Phase 6 — Personal Decision Support

After discovery and thesis quality are validated through real personal use:

- watchlists and thesis monitoring;
- alerts when assumptions strengthen or break;
- opportunity timing integration;
- portfolio context and concentration warnings;
- exit and re-evaluation triggers;
- personal dashboard.

Success test:

Hunter improves actual entry, sizing, hold, review, and exit decisions.

## Phase 7 — Productization

Only after tracked personal results demonstrate sustained value:

- stable personal UI;
- external API if needed;
- multi-user permissions;
- subscription product;
- mobile or public web applications;
- distributed infrastructure.

## Explicitly Deferred

Until discovery and practical decision value are proven, defer:

- generic dashboard expansion;
- cosmetic report redesign;
- broad REST API;
- distributed workers;
- additional deep engines without clear marginal value;
- public product complexity.

## Release Decision Checklist

Before approving the next milestone:

1. What real investment decision improves?
2. What new market coverage becomes available?
3. What false conclusion becomes less likely?
4. What evidence becomes stronger or more timely?
5. Can the user benefit immediately after release?
6. Is this the smallest production-safe implementation?
7. Does it preserve point-in-time truth and explainability?

If these questions cannot be answered clearly, the milestone should not be next.
