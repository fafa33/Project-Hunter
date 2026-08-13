# Hunter Implementation Contract

## Purpose

This document defines the mandatory implementation obligations for Project Hunter.

Its purpose is to ensure that every implementation conforms to the project's accepted architecture while remaining consistent, maintainable, testable, and deterministic.

This document governs implementation obligations only.

It does not define constitutional authority, engineering principles, architecture, development workflow, or Sprint planning.

---

# Scope

This contract applies to every permanent implementation affecting:

- runtime components;
- services;
- providers;
- repositories;
- persistence;
- analytical engines;
- orchestration;
- replay support;
- migrations;
- public implementation interfaces.

Every accepted implementation shall satisfy this contract.

---

# General Implementation Obligations

Every implementation shall:

- conform to the accepted architecture;
- respect architectural ownership boundaries;
- preserve deterministic behavior where required;
- preserve replay correctness where applicable;
- preserve evidence traceability where applicable;
- remain maintainable;
- remain testable;
- remain internally consistent.

No implementation may weaken an accepted architectural guarantee without explicit approval through the project's governance process.

---

# Component Responsibilities

Each implementation shall remain within the responsibilities assigned by the accepted architecture.

Components shall not assume responsibilities owned by other architectural components.

Architectural ownership is defined by:

- accepted ADRs;
- canonical architecture documents;
- implementation interfaces established by the project.

Implementation must follow those boundaries rather than redefine them.

---

# Service Contract

Business behavior shall be implemented only within the architectural components designated to own that behavior.

Authoritative state changes shall originate only through approved implementation paths.

Business behavior shall not be duplicated across multiple components.

---

# Provider Contract

Provider implementations shall remain responsible only for acquiring and normalizing external information.

Providers shall not introduce implementation that bypasses architectural authority.

---

# Repository Contract

Repository implementations shall remain persistence-oriented.

Repository implementations shall not introduce business behavior outside their approved responsibility.

---

# Engine Contract

Analytical engines shall implement only the analytical responsibilities assigned to them by the accepted architecture.

Engine implementations shall not bypass architectural ownership or introduce hidden execution paths.

---

# Dashboard Contract

Presentation implementations shall present existing authoritative information.

Presentation components shall not become authoritative producers of analytical state.

---

# Replay Contract

Whenever replay capability exists, implementations shall preserve deterministic historical reconstruction according to the accepted replay architecture.

Replay implementations shall not introduce future knowledge or nondeterministic behavior.

---

# Persistence Contract

Persistence implementations shall preserve:

- consistency;
- durability;
- traceability;
- compatibility where required.

Changes affecting persisted data require an approved migration strategy whenever compatibility cannot otherwise be preserved.

---

# Migration Contract

Schema or persistence changes shall include an implementation strategy for safe migration whenever migration is required.

Migration implementations shall be:

- deterministic;
- repeatable;
- verifiable.

Previously persisted information shall never be silently reinterpreted.

---

# Testing Contract

Every implementation shall include verification appropriate to its scope.

Verification may include:

- automated tests;
- integration tests;
- migration tests;
- replay verification;
- documentation updates.

The required depth depends on the complexity and risk of the implementation.

## Harness Fidelity

Fakes, stubs, and in-memory doubles shall reproduce the external semantics the
production logic under test actually depends on. A double that is more
convenient than the real service does not verify the code; it verifies the
double.

A test double shall not:

- silently ignore a query parameter the production code sends;
- return results already filtered in a way the real service does not filter them;
- provide stronger ordering guarantees than the real service provides;
- provide cleaner association, identity, or uniqueness semantics than the real
  service provides;
- resolve an ambiguity the real service leaves ambiguous.

For every external semantic the production code relies on, the double shall
record which behavior it is simulating, so that a divergence is reviewable rather
than invisible. Where practical, at least one contract-style test shall assert
the double against the real semantics being relied upon.

## Non-Vacuous Regression Tests

A regression test is not evidence merely because it passes.

For every release-blocking defect fix, the implementation shall answer:

> Would this test fail if the fix were removed?

For release-blocking fixes this shall be demonstrated rather than assumed — by
reverting or disabling the fix and observing the test fail, or by an equivalent
demonstration of the failing counterfactual. A test that still passes without the
fix does not prove the fix and shall be corrected before the fix is presented as
verified.

Tests that pass because a mock reproduces the implementation's internals do not
satisfy this contract.

---

# Compatibility Contract

Implementations shall preserve documented compatibility guarantees unless an approved architectural change explicitly authorizes otherwise.

Breaking changes require corresponding updates to:

- architecture documentation;
- affected ADRs;
- migration strategy;
- implementation documentation.

---

# Completion Criteria

An implementation satisfies this contract when:

- implementation obligations have been met;
- required verification has completed;
- required documentation has been updated;
- architectural consistency has been preserved;
- required compatibility has been maintained.

Project readiness and repository acceptance remain governed by `DEVELOPMENT_GOVERNANCE.md`.

---

# Relationship to Other Canonical Documents

| Document | Responsibility |
|----------|----------------|
| PROJECT_CONSTITUTION | Constitutional governance |
| PROJECT_PRINCIPLES | Engineering principles |
| CANONICAL_ARCHITECTURE_MAP | Canonical document authority |
| Architecture documents | System architecture |
| ADRs | Architectural decisions |
| DEVELOPMENT_GOVERNANCE | Development lifecycle |
| This document | Implementation obligations |

---

# Ownership Boundary

This document owns:

- implementation obligations;
- implementation boundaries;
- implementation consistency;
- implementation compatibility;
- implementation verification requirements.

This document does not own:

- constitutional governance;
- engineering principles;
- architecture;
- runtime design;
- development workflow;
- Sprint management;
- operational procedures.

Those responsibilities remain with their respective canonical documents.