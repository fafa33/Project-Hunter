# Project Hunter Vision

## Mission

Project Hunter exists to continuously discover, validate, prioritize, and deeply analyze the best long-term cryptocurrency investment opportunities across the entire market.

Hunter is not a price prediction system. Hunter is an evidence-backed market discovery and investment intelligence system. Its purpose is to identify where the largest gap may exist between current market value and intrinsic long-term value, then explain the evidence, assumptions, risks, and missing information behind that view.

## Vision

Hunter should become a durable personal investment intelligence platform that sees the crypto market broadly, understands it rigorously, and helps decide what deserves serious research before the opportunity becomes obvious.

The system must become useful before it becomes complete. Early versions should improve discovery, triage, and evidence quality. Later versions may estimate intrinsic value only after discovery, identity, evidence, historical validation, and explainability are strong enough to support it.

## Long-Term Objectives

- Maintain a dynamic market-wide candidate registry.
- Discover assets, protocols, networks, and ecosystems from independent public sources.
- Resolve candidate identity safely and deterministically.
- Preserve point-in-time evidence and avoid lookahead bias.
- Screen thousands of candidates cheaply.
- Prioritize what deserves deeper analysis and explain why.
- Acquire evidence across market, protocol, developer, social, macro, on-chain, and historical domains.
- Run deep evidence-backed analysis through the canonical runtime.
- Build future competition, network-effect, tokenomics, liquidity, revenue, and intrinsic-value layers only when prerequisite evidence exists.
- Improve real investment decisions through better coverage, evidence quality, prioritization, and explainability.

## Architecture Philosophy

Hunter is discovery-first, evidence-first, deterministic, and incrementally extensible.

The architecture must preserve clear boundaries:

- source adapters discover raw market observations;
- normalization converts source payloads into typed records;
- the Candidate Registry stores market-wide identity candidates;
- Identity Resolution determines what records refer to the same economic entity;
- screening and queueing prioritize research;
- acquisition gathers evidence;
- `EvidenceBackedProjectExecutor` remains the canonical deep-analysis scoring boundary;
- explainability links outputs back to evidence;
- historical validation preserves point-in-time truth.

Hunter evolves through small production-safe releases. It should reuse existing production contracts unless a change is necessary to remove a real architectural blocker.

## Investment Philosophy

Hunter seeks long-term asymmetric opportunities, not short-term trading signals.

A useful opportunity thesis eventually requires:

- a clearly identified asset or protocol;
- reliable evidence about market value;
- evidence about real usage, revenue, developer activity, liquidity, tokenomics, and on-chain flows;
- understanding of competition and substitutes;
- network-effect or moat analysis where applicable;
- historical calibration against both winners and failures;
- explicit assumptions, risks, missing evidence, and invalidation conditions.

Hunter must never convert popularity, narratives, current price movement, or incomplete data into unsupported investment conclusions.

## Success Metrics

Primary success is measured by whether Hunter improves real investment decisions.

Supporting metrics include:

- market coverage;
- source diversity;
- unique canonical candidates;
- identity coverage;
- evidence coverage;
- provider overlap and disagreement visibility;
- screening coverage;
- queue usefulness;
- freshness;
- historical point-in-time coverage;
- explainability completeness;
- reduction in false confidence;
- reduction in unsupported assumptions.

Architecture quality matters when it improves these outcomes, protects correctness, or enables future capability without rework.

## Future Evolution

Hunter's expected evolution is:

1. market-wide discovery;
2. identity resolution;
3. stronger candidate screening;
4. competition and peer intelligence;
5. tokenomics, liquidity, revenue, and economic quality;
6. network effects and moat;
7. historical pattern and failure calibration;
8. intrinsic value and asymmetric opportunity;
9. personal decision support;
10. productization only after sustained personal usefulness is proven.

Each phase depends on the trustworthiness of the previous phase. Hunter must not implement advanced investment intelligence on top of unresolved identity, weak evidence, missing provenance, or unvalidated historical assumptions.

## Canonical Source of Truth

The architecture documents define Hunter's governance hierarchy:

1. `docs/PROJECT_PRINCIPLES.md`
2. `docs/VISION.md`
3. `docs/HUNTER_ARCHITECTURE_MANIFEST.md`
4. `docs/HUNTER_ARCHITECTURE_SPEC.md`
5. `docs/HUNTER_ROADMAP.md`
6. `docs/SPRINTS/<version>.md`
7. `docs/CODEX_IMPLEMENTATION_GUIDE.md`

Sprint specifications define release scope, but they do not override the principles, vision, manifest, or architecture specification.
