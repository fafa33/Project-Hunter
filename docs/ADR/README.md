# Architecture Decision Records

This directory contains Project Hunter's Architecture Decision Records (ADRs).

ADRs are subordinate to `docs/PROJECT_CONSTITUTION.md` and `docs/PROJECT_PRINCIPLES.md`. They record durable architecture decisions made under those documents and are governed by `docs/DEVELOPMENT_GOVERNANCE.md`.

The canonical authority hierarchy is defined once in `docs/SPRINTS/README.md`. Accepted ADRs remain binding architecture decision records within that hierarchy until another ADR supersedes or deprecates them.

## Required Structure

Every ADR must include these sections:

- `Status`
- `Context`
- `Decision`
- `Consequences`
- `Alternatives Considered`

An ADR may include additional sections, such as `Reasoning`, when that improves auditability.

## Status Values

- `Proposed` - under discussion and not binding.
- `Accepted` - binding architecture guidance.
- `Superseded` - replaced by a later ADR.
- `Deprecated` - still historically relevant but no longer preferred for new work.

Accepted ADRs remain binding until another ADR supersedes or deprecates them. Runtime behavior must not be changed merely by editing an ADR; implementation, tests, migration notes, and related architecture documents must be updated through the normal governance lifecycle.

## Index

| ADR | Title | Status | Primary Scope |
| --- | --- | --- | --- |
| [0001](0001-discovery-first.md) | Discovery-First Architecture | Accepted | Market-wide discovery before deep analysis |
| [0002](0002-evidence-first.md) | Evidence-First Outputs | Accepted | Provenance, traceability, missing evidence, replay safety |
| [0003](0003-candidate-registry.md) | Dynamic Candidate Registry | Accepted | SQL-backed market registry and lifecycle control |
| [0004](0004-trust-layer.md) | Trust Layer Before Intelligence | Accepted | Identity confidence, source reliability, conflicts, unavailable states |
| [0005](0005-entity-model.md) | Entity Model Separation | Accepted | Economic entities, representations, contracts, listings |
| [0006](0006-knowledge-graph.md) | Future Knowledge Graph | Accepted | Relationship modeling after identity and evidence foundations |
| [0007](0007-canonical-runtime-option-a.md) | Canonical Runtime Option A | Accepted | v2.1.x production runtime classification |
| [0008](0008-plugin-sdk-architecture.md) | Plugin SDK Architecture | Accepted | Versioned extension boundary for plugins and external integrations |
| [0009](0009-repository-purification.md) | Repository Purification | Accepted | Provider, service, repository, and persistence authority boundaries |
| [0010](0010-intelligence-engine-foundation.md) | Intelligence Engine Foundation | Accepted | Service-owned intelligence engine execution over persisted evidence |
| [0011](0011-developer-intelligence-engine.md) | Developer Intelligence Engine | Accepted | Deterministic developer findings over persisted developer evidence |
| [0012](0012-tokenomics-intelligence-engine.md) | Tokenomics Intelligence Engine | Accepted | Deterministic tokenomics findings over persisted tokenomics evidence |
| [0013](0013-governance-intelligence-engine.md) | Governance Intelligence Engine | Accepted | Deterministic governance findings over persisted governance evidence |
| [0014](0014-security-intelligence-engine.md) | Security Intelligence Engine | Accepted | Deterministic security findings over persisted security evidence |
| [0015](0015-onchain-intelligence-engine.md) | On-chain Intelligence Engine | Accepted | Deterministic on-chain findings over persisted on-chain evidence |
| [0016](0016-runtime-analytical-authority.md) | Runtime Analytical Authority | Accepted | Canonical runtime, semantic owners, and experimental-output promotion rules |
| [0017](0017-experimental-opportunity-pipeline.md) | Experimental Opportunity Pipeline | Accepted | Controlled research/replay authorization without production promotion |
| [0018](0018-experimental-opportunity-factor-sourcing.md) | Experimental Opportunity Factor Sourcing | Accepted | Factor-by-factor persisted-source eligibility and anti-double-counting gate |
| [0019](0019-prediction-evaluation-authority.md) | Prediction Evaluation Authority | Accepted | Canonical evaluation lifecycle, correctness, accuracy, and calibration authority |
| [0020](0020-canonical-market-validation-input-authority.md) | Canonical Market Validation Input Authority and Strict-Known Replay | Accepted | Production input ownership, missingness, anti-aliasing, and cutoff-safe replay |
| [0021](0021-canonical-valuation-evidence-authority.md) | Canonical Valuation Evidence Authority | Accepted | Evidence contracts for valuation, comparative valuation, mispricing, and asymmetry |

## Creating A New ADR

1. Copy `TEMPLATE.md`.
2. Assign the next zero-padded number.
3. Use a lowercase hyphenated filename: `NNNN-short-title.md`.
4. State the decision in terms of Project Hunter architecture, not implementation convenience.
5. Cross-reference the ADR from any architecture document whose guidance depends on the decision.
6. Update this index in the same change.
