# Architecture Decision Records

This directory contains the Architecture Decision Records (ADRs) for Project Hunter.

ADRs preserve durable architectural decisions and the reasoning behind them.

They are subordinate to:

- `docs/PROJECT_CONSTITUTION.md`
- `docs/PROJECT_PRINCIPLES.md`
- `docs/CANONICAL_ARCHITECTURE_MAP.md`

The authoritative relationship between ADRs and the other canonical documents is defined exclusively by `docs/CANONICAL_ARCHITECTURE_MAP.md`.

This document does not redefine that hierarchy.

---

## Purpose

An ADR records a specific architectural decision that has long-term consequences for Project Hunter.

ADRs exist to:

- preserve architectural intent;
- document the context behind important decisions;
- record the chosen decision and its consequences;
- preserve the alternatives that were considered;
- prevent accidental architectural drift;
- support future architectural audits.

An ADR records a decision.

It does not replace canonical architecture documents, implementation contracts, Sprint specifications, development procedures, or runtime documentation.

---

## Authority

Only ADRs with the status `Accepted` are binding.

An accepted ADR remains binding until another accepted ADR explicitly supersedes or deprecates it.

An ADR must not silently override:

- the Project Constitution;
- Project Principles;
- the Canonical Architecture Map;
- an architecture responsibility owned by another canonical document.

When an accepted ADR changes an architectural responsibility documented elsewhere, all affected canonical documents must be updated through the normal governance lifecycle.

Editing an ADR alone does not change runtime behavior.

Implementation, tests, migrations, operational documentation, and affected architecture documents must be updated separately where applicable.

---

## Required Structure

Every ADR must contain the following sections:

- `Status`
- `Context`
- `Decision`
- `Consequences`
- `Alternatives Considered`

Additional sections may be included when they improve clarity, traceability, or auditability.

An ADR must describe one coherent architectural decision.

Implementation plans, Sprint tasks, verification output, temporary work instructions, and operational procedures do not belong in an ADR.

---

## Status Values

### Proposed

The decision is under consideration and is not binding.

### Accepted

The decision has been approved and is architecturally binding.

### Superseded

The decision has been replaced by a later accepted ADR.

A superseded ADR must identify the ADR that replaced it.

### Deprecated

The decision remains historically relevant but is no longer approved for new architecture.

A deprecated ADR must explain why it was deprecated and identify the current governing authority where applicable.

---

## ADR Index

| ADR | Title | Status | Primary Scope |
| --- | --- | --- | --- |
| [0001](0001-discovery-first.md) | Discovery-First Architecture | Accepted | Market-wide discovery before deep analysis |
| [0002](0002-evidence-first.md) | Evidence-First Outputs | Accepted | Provenance, traceability, missing evidence, and replay safety |
| [0003](0003-candidate-registry.md) | Dynamic Candidate Registry | Accepted | SQL-backed market registry and lifecycle control |
| [0004](0004-trust-layer.md) | Trust Layer Before Intelligence | Accepted | Identity confidence, source reliability, conflicts, and unavailable states |
| [0005](0005-entity-model.md) | Entity Model Separation | Accepted | Economic entities, representations, contracts, and listings |
| [0006](0006-knowledge-graph.md) | Future Knowledge Graph | Accepted | Relationship modeling after identity and evidence foundations |
| [0007](0007-canonical-runtime-option-a.md) | Canonical Runtime Option A | Accepted | Production runtime classification |
| [0008](0008-plugin-sdk-architecture.md) | Plugin SDK Architecture | Accepted | Versioned extension boundary for plugins and external integrations |
| [0009](0009-repository-purification.md) | Repository Purification | Accepted | Provider, service, repository, and persistence authority boundaries |
| [0010](0010-intelligence-engine-foundation.md) | Intelligence Engine Foundation | Accepted | Service-owned intelligence execution over persisted evidence |
| [0011](0011-developer-intelligence-engine.md) | Developer Intelligence Engine | Accepted | Deterministic developer findings over persisted developer evidence |
| [0012](0012-tokenomics-intelligence-engine.md) | Tokenomics Intelligence Engine | Accepted | Deterministic tokenomics findings over persisted tokenomics evidence |
| [0013](0013-governance-intelligence-engine.md) | Governance Intelligence Engine | Accepted | Deterministic governance findings over persisted governance evidence |
| [0014](0014-security-intelligence-engine.md) | Security Intelligence Engine | Accepted | Deterministic security findings over persisted security evidence |
| [0015](0015-onchain-intelligence-engine.md) | On-chain Intelligence Engine | Accepted | Deterministic on-chain findings over persisted on-chain evidence |

The index must contain only ADR files that exist in this directory.

Every new ADR must be added to this table in the same change that introduces the ADR.

---

## Creating a New ADR

1. Copy `TEMPLATE.md`.
2. Assign the next unused four-digit number.
3. Use the filename format `NNNN-lowercase-hyphenated-title.md`.
4. Record one durable architectural decision.
5. Include every required ADR section.
6. Identify affected canonical architecture documents.
7. Verify consistency with `docs/CANONICAL_ARCHITECTURE_MAP.md`.
8. Update affected canonical documents when the decision changes their owned responsibilities.
9. Add the ADR to this index in the same change.
10. Complete the lifecycle defined by `docs/DEVELOPMENT_GOVERNANCE.md`.

---

## Superseding or Deprecating an ADR

An existing ADR must not be deleted merely because its decision is no longer current.

To replace an ADR:

1. create a new ADR;
2. identify the previous ADR in the new ADR;
3. change the previous ADR status to `Superseded`;
4. add a link from the previous ADR to its replacement;
5. update this index;
6. update affected canonical architecture documents.

To deprecate an ADR without replacing it:

1. change its status to `Deprecated`;
2. document the reason;
3. identify the current governing authority where applicable;
4. update this index.

This preserves the historical decision record while keeping current authority explicit.

---

## ADR Ownership Boundary

This directory owns:

- architectural decision records;
- ADR status;
- ADR numbering;
- ADR lifecycle;
- the ADR index.

This directory does not own:

- constitutional rules;
- engineering principles;
- the canonical document hierarchy;
- system-wide architecture specifications;
- runtime classification;
- implementation contracts;
- development workflow;
- Sprint scope;
- operational procedures.

Those responsibilities remain with their respective canonical documents.