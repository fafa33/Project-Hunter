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

## Creating A New ADR

1. Copy `TEMPLATE.md`.
2. Assign the next zero-padded number.
3. Use a lowercase hyphenated filename: `NNNN-short-title.md`.
4. State the decision in terms of Project Hunter architecture, not implementation convenience.
5. Cross-reference the ADR from any architecture document whose guidance depends on the decision.
6. Update this index in the same change.
