# Opportunity Timing Engine

Status: Experimental for v2.1.x production architecture.

The Fusion-backed Opportunity Timing Engine in `src/hunter/opportunity/` is not the production timing implementation for v2.1.x. Production timing is `src/hunter/timing/` and is fed by persisted evidence through the canonical Market Validation runtime documented in `docs/CANONICAL_RUNTIME_ARCHITECTURE.md`.

## Purpose

The Opportunity Timing Engine deterministically evaluates whether a project, asset, protocol, chain, sector, narrative, or ecosystem is approaching, entering, remaining inside, or leaving an opportunity window.

## Boundaries

The engine consumes persisted `FusedIntelligenceRecord` objects and historical Opportunity Timing records or snapshots. It does not collect raw data, call external providers, bypass Fusion, predict prices, issue investment advice, or execute actions.

## Input Contracts

Inputs must be aligned to the requested Fusion target and include persisted Fusion explainability payloads: source Fusion IDs, source run IDs, canonical evidence groups, confidence, corroboration, contradiction, dependency, missing-evidence, unified signal, narrative, and graph payloads.

## As-Of Replay Semantics

Every assessment has an `as_of` boundary.

For current-state execution, callers may omit `as_of`; the engine uses the latest aligned Fusion effective time as the current analytical boundary.

For replay and backtest execution, callers must pass an explicit timezone-aware `as_of`. The engine excludes any Fusion records and historical timing records with `effective_at` later than `as_of`. Future data must never influence an earlier assessment.

## Phases

Supported phases are `too_early`, `forming`, `early_entry`, `confirmed_entry`, `expansion`, `mature`, `crowded`, `deteriorating`, `exit_risk`, `invalidated`, and `insufficient_evidence`.

Phase classification uses configured upper-bound thresholds from `configs/opportunity_timing.yaml`. Configuration is validated at startup for ordered 0-100 threshold ranges.

## Opportunity Windows

Supported windows are `closed`, `watch`, `opening`, `open`, `strengthening`, `weakening`, `closing`, and `invalid`.

Opportunity-window classification uses configured upper-bound thresholds from `configs/opportunity_timing.yaml`.

## Temporal Analysis

Temporal analysis compares current and historical fused records for the same target. It detects structural change, persistence, deterioration, reversal, and one-off events from deterministic confidence deltas.

Historical comparison excludes timing records later than the assessment `as_of` boundary.

## Confirmation

Confirmation uses categories and canonical evidence groups rather than hardcoded engine names. Categories may include macro, whale, developer, protocol, news, narrative, social, and on-chain, but configuration may change the required coverage.

Confirmation requires both configured category coverage and a configured count of independent canonical evidence groups. Dependent canonical evidence groups, including shared-lineage, shared-reference, and shared-id groups, are excluded from independent confirmation. Failed or partial confirmation records explain missing category coverage, insufficient independent groups, and excluded dependent groups.

## Acceleration

Acceleration classifies positive acceleration, negative acceleration, stable trend, stalled trend, reversal, or insufficient history from ordered historical Fusion confidence changes.

## Divergence

Divergence detects deterministic evidence mismatches such as social excitement without fundamentals, fundamentals without attention, whale accumulation without broad adoption, narrative acceleration without developer activity, adoption growth under macro headwinds, and social saturation while fundamentals remain early.

## Scoring

The timing score is a deterministic 0-100 score derived from Fusion confidence, confirmation, acceleration, persistence, contradiction severity, missing evidence, divergence, risk, and historical depth. Score labels and thresholds are configurable.

The model fingerprint includes every analytically material timing rule: phase thresholds, window thresholds, confirmation rules, acceleration rules, divergence rules, contradiction penalties, missing-evidence penalties, risk weights, confidence weights, horizon rules, historical-depth rules, invalidation rules, freshness windows, schema identity version, and model version.

## Confidence

Confidence accounts for historical depth, source diversity, category coverage, canonical evidence independence, persistence of change, contradiction severity, missing evidence, freshness, target alignment, Fusion confidence, and snapshot completeness.

Freshness is derived from Fusion effective time, effective windows where available, configured freshness windows, and the assessment `as_of` boundary. Freshness decays deterministically after the configured grace period.

## Risk

Timing-specific risks include narrative saturation, social manipulation, concentration, liquidity deterioration, declining developer activity, protocol weakness, negative capital flows, macro headwinds, incentive dependence, evidence dependency, and insufficient historical confirmation.

## Horizons

Expected horizons are deterministic bands only: `days`, `weeks`, `1-3 months`, `3-6 months`, `6-12 months`, `12-24 months`, `24-36 months`, and `indeterminate`. The engine does not predict exact dates.

## Invalidation

Every assessment includes invalidation conditions such as loss of independent confirmation, material contradiction increases, unresolved divergence, or sustained negative acceleration.

## Persistence

`OpportunityTimingAssessmentRecord` and `OpportunityTimingSnapshotRecord` preserve the full explainability payload, source Fusion IDs, source run IDs, canonical evidence references, configuration fingerprint, model fingerprint, historical window, phase, score, confidence, risk, and invalidation conditions.

## Pipeline Integration

Opportunity Timing is an optional post-Fusion stage. With persistence enabled, the adapter first persists Fusion records, then passes those records to the timing engine and persists the resulting assessment and snapshot in the same UnitOfWork.

## Explainability

Outputs preserve supporting factors, opposing factors, contradictions, missing evidence, confirmation state, acceleration state, divergence state, risk state, historical comparisons, confidence breakdown, and deterministic metadata.

## Known Limitations

The engine is rule-based and deterministic. It does not implement machine-learning prediction, scheduler automation, dashboards, live providers, portfolio allocation, expected returns, price targets, or order execution.

## Timing Assessment vs Investment Advice

An Opportunity Timing assessment is an evidence-backed analytical state. It is not a buy, sell, hold, allocation, expected return, or price-target recommendation.
