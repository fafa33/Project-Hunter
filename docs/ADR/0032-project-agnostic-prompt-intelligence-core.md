# ADR 0032: Project-Agnostic Prompt Intelligence Core and Project Adapter Boundary

## Status

Accepted.

## Date

2026-08-10.

## Governing Preparation

[ADPR-0007](../architecture-records/ADPR-0007-project-agnostic-prompt-intelligence-core.md), created under Issue #237.

Independent architecture review: [ADR-0032 independent architecture review](../architecture-reviews/ADR-0032-independent-architecture-review.md), merged through PR #248 with verdict `READY_FOR_ADR`.

## Context

ADR 0031 accepted a Hunter Evidence Intelligence-specific deterministic pre-model foundation and deliberately deferred generic Prompt/Context ownership until actual cross-consumer evidence justified it.

Project Hunter still needs a portability boundary now so future reusable Prompt Intelligence does not become permanently coupled to `hunter.*`. Defining that boundary is different from claiming that Hunter's current contracts are already shared with another project.

This ADR therefore establishes **where shared ownership may exist and what evidence is required before any concrete contract enters it**. It does not declare Hunter and Iran-OS—or Hunter and any other project—to have equivalent prompt/context contracts. Iran-OS is only an illustrative possible future consumer and has no authority under this ADR.

## Decision

Project Hunter will maintain a **project-neutral Prompt Intelligence boundary** behind project-specific adapters.

The boundary is governed by a strict admission rule:

> A concrete Hunter contract or mechanic must not be promoted into shared/core ownership merely because it appears reusable. Promotion requires concrete, versioned evidence from at least two independent consumers demonstrating genuinely common semantics.

Until that evidence exists for a specific contract, the contract remains consumer-owned. Hunter's existing ADR 0031 contracts therefore remain Hunter-owned by default.

The intended dependency direction is one-way:

```text
Hunter domain / Evidence Intelligence
        -> Hunter Prompt Adapter
        -> Project-Neutral Prompt Intelligence Boundary

Future Consumer Domain
        -> Consumer Prompt Adapter
        -> Project-Neutral Prompt Intelligence Boundary
```

The project-neutral boundary must not import `hunter.*` or any future consumer domain package. Consumer adapters may depend on admitted shared contracts. The core may not infer or broaden consumer authority.

## Shared-Core Admission Rule

A contract may enter project-neutral ownership only when all of the following are satisfied:

1. at least two independent consumers expose concrete, versioned contracts for the relevant mechanic;
2. comparison demonstrates genuinely common semantics rather than similar naming;
3. consumer-specific authority, source eligibility, permissions, persistence, and downstream promotion remain adapter/consumer-owned;
4. identity, provenance, missingness, replay, and correction semantics can be represented without semantic loss;
5. deterministic compatibility tests prove the neutral representation does not erase consumer-owned meaning;
6. the admission is recorded through normal architecture/governance review.

If these conditions are not met, the mechanic remains consumer-owned and may be duplicated or separately implemented rather than prematurely generalized.

## Core Ownership

The project-neutral core may own only contracts that have passed the admission rule. Potential categories include deterministic pre-model mechanics such as budgeting, canonicalization, compilation, provenance containers, or other infrastructure, but **this ADR does not declare any specific current Hunter contract already admitted**.

The exact shared contract set is therefore initially empty or minimal until evidence-backed admissions occur.

## Core Prohibited Authority

The project-neutral core must not own or decide:

- Hunter canonical evidence or source authority;
- any other project's canonical records or domain truth;
- project permissions or user authorization;
- domain-specific source eligibility;
- consumer persistence authority;
- downstream promotion into canonical project state;
- autonomous action authorization;
- provider credentials;
- provider/model selection or routing;
- model invocation;
- response truth validation;
- Hunter Governance Review;
- any external project's constitutional, governmental, or domain decision authority.

No core artifact grants domain authority.

## Project Adapter Ownership

Each consumer adapter owns translation between consumer semantics and admitted neutral contracts.

A consumer adapter owns or validates, as applicable:

- domain task/intent semantics;
- canonical source inventory and ownership;
- exact project identities and temporal scope;
- context policy;
- trust, sensitivity, and data-handling classifications;
- permissions and capability ceiling;
- project-specific validation;
- persistence integration;
- downstream output/promotion authority.

Adapters may narrow capabilities but may not broaden authority beyond their governing project architecture.

## ADR 0031 Compatibility and Migration

ADR 0031 is reaffirmed and not superseded wholesale.

This ADR **does not claim that ADR 0031's current Evidence Intelligence contracts have become generic**. Instead, it adds the portability boundary and evidence-gated admission rule that any future shared ownership must satisfy.

Existing ADR 0031 identities remain historically valid. If a future contract is admitted to shared ownership:

1. existing Hunter records are not rewritten;
2. Hunter adapters must preserve exact source IDs, hashes, versions, policies, compiler identities, and lineage;
3. new records may link consumer identity to a neutral-core identity where needed;
4. no historical record may be relabeled as if the shared core owned it before admission;
5. round-trip compatibility tests must prove no authority, provenance, omission, budget, canonicalization, or reconstruction meaning is lost.

If semantic loss occurs, the field or contract remains consumer-owned.

## Persistence Boundary

Persistence authority remains consumer-owned.

A shared core must not directly own Hunter's canonical SQL database or any other project's authoritative store. Consumers persist admitted core artifacts through their own authorized repositories/services.

## Replay and Reconstruction

Any admitted shared artifact represented as replayable must preserve enough exact information for consumer-authorized reconstruction, including as applicable:

- consumer intent identity/version;
- exact source identities, revisions, ranges, hashes, and temporal coordinates;
- selection/omission decisions and reason codes;
- policy identities/versions;
- allocation/capability-constraint versions;
- template/compiler/canonicalization versions;
- exact prompt bytes/messages where retention policy permits;
- hashes and measured sizes;
- explicit reconstruction-unavailable reasons where exact replay is not possible.

Strict historical reconstruction must never substitute current/latest source content.

## Security Boundary

Consumer projects own trust, sensitivity, eligibility, permissions, and data-handling classifications.

A shared core may own mechanical enforcement—such as deterministic escaping or trusted/untrusted separation—only after the relevant contract has passed the admission rule.

## Model and Provider Boundary

This ADR grants no Model Adapter authority.

Provider selection, model capability discovery, routing, credentials, transport, retries, quotas, billing, invocation records, and response validation require separate architecture.

## Incubation Location

If implementation is later authorized, Project Hunter may reserve a physically isolated top-level package boundary, for example:

```text
src/
  prompt_intelligence/
  hunter/
    ...
```

Creating the package boundary does not itself authorize moving Hunter contracts into shared ownership. Admission remains evidence-gated.

The Hunter adapter may depend on admitted neutral contracts. The neutral core may not depend on Hunter.

## Standalone Extraction Criteria

Moving the project-neutral core into an independent repository/package/service is a later governed migration.

Extraction becomes eligible when:

1. at least two real consumer adapters use versioned admitted contracts;
2. shared contracts have survived independent consumer tests without domain leakage;
3. independent versioning provides operational or release-management value;
4. migration preserves artifact identities and provenance;
5. CI can test supported consumer/core compatibility matrices;
6. extraction costs less than continued isolated-package incubation.

A network service is not implied by repository extraction.

## Evaluation Requirements

Before any admitted contract is used in production, implementation must prove as applicable:

- deterministic identity for equal canonical inputs;
- deterministic output bytes/messages for equal versions;
- complete selection/omission accounting;
- budget-fit invariants;
- trusted/untrusted boundary invariants;
- strict-known reconstruction behavior;
- explicit unavailable reconstruction states;
- no reverse dependency from core to consumer packages;
- lossless adapter mapping for every consumer-owned field that carries authority or provenance.

These are pre-model evaluation requirements and do not evaluate model-response quality.

## Non-Goals

This ADR does not:

- claim that Hunter and Iran-OS already share concrete contracts;
- authorize an Iran-OS adapter;
- make Hunter architecture authoritative for another project;
- authorize a production LLM;
- authorize autonomous agents;
- define generic memory architecture;
- define generic retrieval infrastructure;
- require immediate standalone repository creation;
- authorize runtime implementation merely by being Accepted.

## Consequences

### Positive

- Hunter gains a clean future portability boundary without prematurely generalizing Hunter semantics.
- ADR 0031 remains authoritative for current Hunter contracts.
- future consumers can adopt admitted contracts without depending on Hunter domain packages.
- cross-project reuse becomes evidence-driven rather than assumption-driven.
- historical identity and provenance remain protected.

### Negative

- some mechanics may remain Hunter-owned or duplicated until a second consumer provides concrete evidence;
- shared-core population may progress more slowly;
- compatibility evidence is required for each promoted contract;
- an initially sparse neutral boundary may appear conservative.

## Alternatives Considered

### Keep Prompt Intelligence permanently Hunter-specific

Rejected as the long-term target because future reuse would become increasingly expensive and Hunter could become accidental platform authority.

### Copy Prompt Intelligence independently into every project

Rejected as the preferred strategy because deterministic infrastructure may drift, although duplication remains acceptable when a mechanic fails the shared-admission rule.

### Create a standalone service/repository immediately

Rejected because no stable admitted shared contract set exists. Reconsider after at least two consumers use versioned admitted contracts and independent deployment/versioning is justified.

## Implementation Status

Architecture accepted; runtime implementation is not yet authorized by this acceptance contribution.

No Hunter contract is promoted into shared ownership by this text alone. Concrete admissions, runtime behavior, provider/model integration, and external-project adapters require their own evidence and governed implementation steps.