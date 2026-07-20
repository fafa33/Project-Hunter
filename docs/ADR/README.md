# Architecture Decision Records

## Purpose

This directory contains Project Hunter's Architecture Decision Records (ADRs).

ADRs record durable architectural decisions that require explicit context, consequences, alternatives, and lifecycle history.

They do not replace the Constitution, Principles, canonical architecture documents, development governance, implementation contract, or sprint specifications.

## Authority

ADRs are subordinate to:

- `docs/PROJECT_CONSTITUTION.md`;
- `docs/PROJECT_PRINCIPLES.md`; and
- all higher-authority documents identified by `docs/CANONICAL_ARCHITECTURE_MAP.md`.

The canonical document-authority hierarchy is owned and defined only by `docs/CANONICAL_ARCHITECTURE_MAP.md`.

Accepted ADRs are binding architectural decisions within that hierarchy until explicitly superseded or deprecated by a later accepted ADR.

An ADR may clarify or extend an earlier decision without superseding it. Supersession must be explicit.

## Required Structure

Every ADR must include:

- `Status`;
- `Context`;
- `Decision`;
- `Consequences`;
- `Alternatives Considered`.

An ADR may include additional sections such as `Reasoning`, `Implementation Status`, `Compatibility`, `Non-Goals`, or migration requirements when they improve auditability.

## Status Values

- `Proposed` — under discussion and not binding.
- `Accepted` — binding architectural decision.
- `Superseded` — explicitly replaced by a later accepted ADR.
- `Deprecated` — historically relevant but no longer preferred for new work.

Accepted architecture does not automatically imply completed implementation.

Where implementation status matters, the ADR must state it explicitly and must not represent unimplemented capabilities as complete.

## Amending an ADR

A later accepted ADR may amend a specific part of an existing ADR without replacing the entire decision.

The amendment must:

- identify the affected ADR and section;
- state the exact change;
- preserve unaffected decisions;
- describe compatibility, migration, and implementation consequences where applicable.

## Extending an ADR

A later ADR may extend an earlier decision by adding detail, authority boundaries, or implementation gates without changing the earlier decision.

An extension must state that the earlier ADR is reaffirmed and not superseded.

## Superseding an ADR

Supersession requires a later accepted ADR that explicitly:

- names the superseded ADR;
- defines the replacement decision;
- identifies migration, cutover, rollback, and compatibility effects where applicable;
- updates this index.

A code change, repository structure, persisted record, test, sprint document, or implementation report cannot supersede an ADR implicitly.

## Deprecating an ADR

Deprecation preserves historical context while declaring that the decision should not govern new work.

The deprecating ADR or governed documentation change must state the replacement guidance and update this index.

## Runtime and Implementation Changes

Runtime behavior must not be changed merely by editing an ADR.

Implementation, tests, migrations, configuration, reports, and dependent canonical documents must be updated through the normal development lifecycle.

Persistence, automation, presentation, or code existence does not establish architectural authority unless an accepted ADR grants it.

## Consistency Requirements

Every accepted ADR must remain consistent with:

- the Constitution;
- Project Principles;
- the Canonical Architecture Map;
- applicable architecture and runtime documents;
- previously accepted ADRs unless an explicit amendment or supersession is declared;
- Development Governance;
- the Implementation Contract.

Conflicts must be resolved before implementation proceeds.

## Index

| ADR | Title | Status | Primary Scope |
| --- | --- | --- | --- |
| [0001](0001-discovery-first.md) | Discovery-First Architecture | Accepted | Market-wide discovery before deep analysis |
| [0002](0002-evidence-first.md) | Evidence-First Outputs | Accepted | Provenance, traceability, missing evidence, replay safety |
| [0003](0003-candidate-registry.md) | Dynamic Candidate Registry | Accepted | SQL-backed market registry and lifecycle control |
| [0004](0004-trust-layer.md) | Trust Layer Before Intelligence | Accepted | Identity confidence, source reliability, conflicts, unavailable states |
| [0005](0005-entity-model.md) | Entity Model Separation | Accepted | Economic entities, representations, contracts, listings |
| [0006](0006-knowledge-graph.md) | Future Knowledge Graph | Accepted | Relationship modeling after identity and evidence foundations |
| [0007](0007-canonical-runtime-option-a.md) | Canonical Runtime Option A | Accepted | Current production analytical runtime classification |
| [0008](0008-plugin-sdk-architecture.md) | Plugin SDK Architecture | Accepted | Versioned extension boundary for plugins and external integrations |
| [0009](0009-repository-purification.md) | Repository Purification | Accepted | Provider, service, repository, and persistence authority boundaries |
| [0010](0010-intelligence-engine-foundation.md) | Intelligence Engine Foundation | Accepted | Service-owned intelligence engine execution over persisted evidence |
| [0011](0011-developer-intelligence-engine.md) | Developer Intelligence Engine | Accepted | Deterministic developer findings over persisted developer evidence |
| [0012](0012-tokenomics-intelligence-engine.md) | Tokenomics Intelligence Engine | Accepted | Deterministic tokenomics findings over persisted tokenomics evidence |
| [0013](0013-governance-intelligence-engine.md) | Governance Intelligence Engine | Accepted | Deterministic governance findings over persisted governance evidence |
| [0014](0014-security-intelligence-engine.md) | Security Intelligence Engine | Accepted | Deterministic security findings over persisted security evidence |
| [0015](0015-onchain-intelligence-engine.md) | On-chain Intelligence Engine | Accepted | Deterministic on-chain findings over persisted on-chain evidence |
| [0016](0016-runtime-analytical-authority.md) | Runtime Analytical Authority | Accepted | Canonical runtime, semantic owners, and experimental-output promotion rules |
| [0017](0017-experimental-opportunity-pipeline.md) | Experimental Opportunity Pipeline | Accepted | Controlled research and replay without production promotion |
| [0018](0018-experimental-opportunity-factor-sourcing.md) | Experimental Opportunity Factor Sourcing | Accepted | Factor-source eligibility and anti-double-counting gate |
| [0019](0019-prediction-evaluation-authority.md) | Prediction Evaluation Authority | Accepted | Evaluation lifecycle, correctness, accuracy, and calibration authority |
| [0020](0020-canonical-market-validation-input-authority.md) | Canonical Market Validation Input Authority and Strict-Known Replay | Accepted | Input ownership, missingness, anti-aliasing, and cutoff-safe replay |
| [0021](0021-canonical-valuation-evidence-authority.md) | Canonical Valuation Evidence Authority | Accepted | Valuation-family evidence and service-authority contracts |

## Creating a New ADR

1. Copy `TEMPLATE.md`.
2. Assign the next zero-padded number.
3. Use a lowercase hyphenated filename: `NNNN-short-title.md`.
4. State the decision in architectural terms rather than implementation convenience.
5. Identify affected authority, compatibility, replay, persistence, migration, and unavailable-state boundaries where applicable.
6. Cross-reference the ADR from dependent canonical documents.
7. Update this index in the same governed change.

## Ownership Boundary

This README owns:

- ADR format;
- ADR status semantics;
- ADR lifecycle rules;
- the accepted ADR index.

It does not own:

- canonical document precedence;
- constitutional governance;
- the architecture defined by individual ADRs;
- implementation scope;
- runtime behavior;
- development workflow.

Those responsibilities remain with their respective canonical owners.