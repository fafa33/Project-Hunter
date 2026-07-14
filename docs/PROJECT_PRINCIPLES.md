# Project Hunter Principles

This document is the permanent engineering constitution for Project Hunter. It defines the principles that govern architecture, implementation, documentation, and release decisions.

## Canonical Purpose

Hunter is a market discovery engine before it is a project analysis engine.

Hunter exists to discover exceptional long-term cryptocurrency investment opportunities before they become obvious, then validate whether they deserve deeper analysis through evidence-backed workflows.

The long-term objective is not short-term price prediction. The long-term objective is to estimate the gap between current market value and intrinsic long-term value only when the evidence base is strong enough to support that judgment.

## Principle 1: Discovery First

Hunter must maintain a market-wide view before it narrows attention to deep analysis.

The system must not treat a manually configured project list as the complete investable universe. Configured projects are a compatibility seed and a validation baseline. The operating model is discovery, normalization, identity resolution, evidence validation, screening, prioritization, and then deep analysis.

## Principle 2: Evidence First

No candidate, source, metric, conclusion, or recommendation is trusted without provenance.

Hunter must preserve source identity, observation time, evidence links or ids, confidence, freshness, conflicts, missing evidence, and point-in-time availability. Unknown evidence remains unknown. Missing evidence remains missing.

## Principle 3: Trust Before Intelligence

Hunter must establish whether data can be trusted before converting it into intelligence.

Trust layers include source provenance, identity resolution, conflict detection, validation status, freshness, and unavailable-state handling. Analysis that bypasses trust validation is not production intelligence.

## Principle 4: Deterministic by Default

Hunter must produce repeatable outcomes from the same inputs.

Determinism applies to execution identity, candidate identity, persistence, screening outcomes, queue entries, evidence references, replay, validation, and explainability. Randomness, hidden state, implicit time assumptions, and silent provider substitution are not acceptable production behavior.

## Principle 5: Identity Before Valuation

Hunter must know what an entity is before estimating what it may be worth.

Ticker equality, similar names, popularity, market-cap rank, or social attention are not sufficient identity evidence. Identity resolution must precede intrinsic valuation, competition analysis, network-effect analysis, and any future investment thesis.

## Principle 6: Reuse Before Rewrite

Hunter evolves by extending proven production boundaries, not by replacing them casually.

Existing contracts, repositories, adapters, engines, CLI surfaces, and persistence mechanisms should be reused when technically sound. Refactoring is justified only when it removes a real blocker, reduces long-term complexity, or protects production correctness.

## Principle 7: Production Stability Before Feature Velocity

Hunter must remain useful and stable while it evolves.

Every release must preserve backward compatibility unless a migration is explicitly justified and verified. New capabilities must not weaken existing scoring, weighting, committee logic, timing, replay, historical validation, calibration, or explainability semantics.

## Principle 8: Explainability Is Mandatory

Every meaningful output must be explainable from evidence.

Hunter must show why a candidate was discovered, why it was screened, why it was queued, what evidence supports it, what evidence is missing, and what conflicts or unavailable states remain. Unsupported conclusions are not acceptable outputs.

## Principle 9: Market Coverage Before Market Understanding

Hunter optimizes for market coverage first, market understanding second, and investment intelligence third.

Coverage without trust is not intelligence, but Hunter cannot identify exceptional opportunities if it cannot see the market. Discovery breadth, source diversity, and candidate freshness are first-order architecture concerns.

## Principle 10: Investment Value Over Architectural Complexity

Success is measured by improving real investment decisions, not by increasing architecture, engine count, code volume, or report length.

Every new component must contribute measurable investment value or measurable market understanding. Components that cannot improve discovery quality, evidence quality, ranking quality, decision usefulness, reliability, or future extensibility should not be added.

## Principle 11: No Fabricated Evidence

Hunter must never fabricate evidence, mappings, coverage, identities, source results, repository links, contracts, domains, prices, valuations, or completeness.

When evidence is unavailable, Hunter must say so explicitly. When evidence conflicts, Hunter must preserve the conflict. When identity is ambiguous, Hunter must preserve ambiguity.

## Principle 12: Objective Evidence Over Assumptions

Hunter prefers objective evidence over assumptions and live verified observations over unverified claims.

Acceptable evidence comes from documented public sources, official project sources, verified provider records, persisted Hunter evidence, and reproducible point-in-time observations. Assumptions may guide research priorities, but they must not be persisted as facts.

## Principle 13: Ten-Year Maintainability

Hunter must be built as if it will remain actively developed and used for the next ten years.

Architectural decisions should make Hunter easier to extend, test, operate, and audit. Avoid unnecessary abstractions, speculative systems, duplicate runtimes, and irreversible schema choices. Prefer simple, indexed, incremental, evidence-preserving designs.

## Principle 14: Explicit Boundaries

Scheduler, pipeline orchestration, acquisition, discovery, identity, screening, deep analysis, scoring, committee, explainability, and future valuation are separate responsibilities.

Boundary violations create long-term risk. Scheduler remains operational. Pipeline orchestration owns analytical execution. `EvidenceBackedProjectExecutor` remains the canonical deep-analysis scoring boundary until explicitly changed by an approved architecture decision.

## Principle 15: Release Discipline

Every release must answer the release test:

> Does this version help the user make a materially better investment decision than the previous version?

If the answer is no, the release is not successful regardless of technical completeness.
