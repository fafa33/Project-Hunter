# Hunter Implementation Contract

## Purpose

This document defines the mandatory implementation contracts that every production implementation in Project Hunter must satisfy.

It translates the architectural design into implementation boundaries.

This document defines implementation obligations only.

Architecture, governance, engineering principles, and strategic direction are defined by their respective canonical documents.

---

## Scope

This contract applies to every implementation that introduces or modifies:

- runtime behavior;
- persistence;
- services;
- providers;
- repositories;
- engines;
- orchestration;
- replay behavior;
- migrations;
- public implementation contracts.

Every implementation must satisfy this contract before code is accepted.

---

# Implementation Layers

Project Hunter implementation follows these permanent architectural boundaries.

## Provider

Providers acquire external information.

Providers may:

- access external systems;
- normalize source data;
- expose acquisition metadata.

Providers must not:

- persist data;
- perform business decisions;
- determine trust;
- resolve identity;
- perform analysis.

---

## Service

Services own business behavior.

Services are responsible for:

- validation;
- authority;
- identity decisions;
- provenance;
- conflict handling;
- orchestration;
- transaction boundaries;
- replay ownership.

Every authoritative mutation originates from a service.

---

## Repository

Repositories own persistence only.

Repositories may:

- read;
- write;
- update;
- delete;
- migrate;
- index;
- manage transactions.

Repositories must not:

- implement business rules;
- resolve identity;
- determine trust;
- perform analysis;
- own replay logic.

---

## Engine

Engines consume persisted analytical inputs.

Engines produce analytical outputs.

Engines must remain:

- deterministic;
- explainable;
- replay-safe;
- independent.

Engines must not:

- access providers directly;
- own persistence;
- own orchestration;
- own scheduling.

---

## Dashboard

Dashboards present existing persisted information.

Dashboards may:

- filter;
- aggregate;
- visualize;
- explain.

Dashboards never create authoritative analytical state.

---

# Replay Contract

Replay implementations shall:

- use explicit timestamps;
- preserve historical correctness;
- exclude future information;
- remain deterministic.

Replay results must depend only on persisted historical state.

---

# Persistence Contract

Persistent state shall be:

- durable;
- deterministic;
- versioned where required;
- traceable;
- replayable.

Persistence changes require explicit migration support.

---

# Identity Contract

Canonical identity is determined only through approved service logic.

No individual identifier automatically establishes identity.

Similarity alone is never sufficient for entity merging.

Conflicts remain explicit until resolved.

---

# Transaction Contract

Authoritative writes follow this sequence:

```text
Validation
    ↓
Authority
    ↓
Transaction
    ↓
Persistence
    ↓
Projection
```

Related authoritative changes must commit atomically.

Partial authoritative state is prohibited unless explicitly modeled.

---

# Migration Contract

Every schema change requires:

- migration support;
- backward compatibility or an approved migration path;
- deterministic upgrades;
- migration tests;
- idempotent execution.

Existing evidence must never be silently reinterpreted.

---

# Implementation Requirements

Every implementation shall demonstrate:

- architectural consistency;
- deterministic behavior;
- replay safety;
- persistence correctness;
- evidence traceability;
- backward compatibility;
- test coverage.

Implementation may not weaken an existing documented guarantee without explicit governance approval.

---

# Definition of Done

An implementation is complete only when:

- implementation satisfies this contract;
- tests pass;
- documentation is updated;
- architecture remains consistent;
- replay remains correct;
- persistence remains safe;
- verification completes successfully.

---

## Relationship to Other Canonical Documents

| Document | Responsibility |
|----------|----------------|
| PROJECT_CONSTITUTION | Constitutional governance |
| PROJECT_PRINCIPLES | Engineering principles |
| DEVELOPMENT_GOVERNANCE | Development lifecycle |
| ADRs | Architectural decisions |
| Architecture documents | System design |
| This document | Implementation contracts |

This document defines implementation obligations only.

It does not redefine governance, architecture, engineering principles, or strategic direction.