# Project Hunter Architecture Manifest

## Mission

Hunter is a market discovery engine before it is a project analysis engine.

Hunter exists to continuously inspect the entire crypto market, identify projects whose future intrinsic value may be materially greater than their current market valuation, validate that thesis with evidence, and prioritize what deserves deeper analysis.

Hunter is not limited to unknown projects. A widely known asset may still be a major opportunity if most of its economic potential remains unrealized.

## Primary Success Metric

A release is successful only when it meaningfully improves real investment decisions compared with the previous release.

Architectural completeness, engine count, code size, technical elegance, and feature volume are secondary to practical investment value.

## Practical Usefulness

Hunter must become useful long before it becomes complete.

Each release must provide immediate decision value while also moving the system toward the long-term architecture.

Never delay practical usefulness in pursuit of architectural perfection.

## Discovery-First Principle

Hunter must not depend on a manually maintained project list as its complete investable universe.

The operating model is:

1. Discover the market.
2. Normalize and resolve identities.
3. Validate evidence.
4. Screen candidates cheaply.
5. Prioritize what deserves deeper work.
6. Run evidence-backed deep analysis.
7. Estimate long-term intrinsic value only after the necessary evidence exists.

## Evidence-First Principle

No project, source, metric, or conclusion is trusted without provenance.

Hunter must preserve:

- source identity;
- observation time;
- evidence links or ids;
- confidence;
- freshness;
- conflicts;
- missing evidence;
- point-in-time availability.

Ambiguity must remain explicit. Hunter must never guess when identity, evidence, or state is unresolved.

## Market-Wide Scope

Hunter must support known and lesser-known assets across:

- layer 1 and layer 2 networks;
- DeFi;
- AI;
- DePIN;
- oracles;
- RWA;
- storage;
- interoperability;
- consumer and gaming;
- exchanges and market infrastructure;
- native assets, tokens, protocols, and networks.

## Long-Term Investment Thesis

Hunter's final goal is not short-term price prediction.

Hunter must estimate the gap between:

- current market value; and
- estimated intrinsic long-term value.

That estimate should eventually combine:

- historical evidence;
- current fundamentals;
- future market evolution;
- macro conditions;
- developer activity;
- protocol usage;
- on-chain capital flows;
- tokenomics;
- competition;
- network effects;
- market structure;
- revenue quality;
- risk;
- historical validation.

## Architectural Boundaries

The following principles are non-negotiable:

- deterministic execution identity;
- immutable evidence-backed records;
- idempotent persistence;
- historical replay cutoff discipline;
- explainability as a first-class output;
- separation of scheduling from analytical execution;
- no silent drops;
- no fabricated completeness;
- no unsupported price or return claims.

## Engineering Mindset

Think like the Chief Architect of the world's most advanced crypto investment intelligence platform, not like a feature implementer.

When multiple implementation choices exist, choose the option that:

- best supports the discovery-first mission;
- produces practical investment value in the shortest safe time;
- preserves production stability;
- maximizes sound code reuse;
- improves evidence quality;
- remains modular and extensible;
- enables future intrinsic-value estimation without implementing it prematurely.

## Release Test

Before approving any release, answer:

> Does this version help the user make a materially better investment decision than the previous version?

If not, the release is not successful regardless of technical complexity.