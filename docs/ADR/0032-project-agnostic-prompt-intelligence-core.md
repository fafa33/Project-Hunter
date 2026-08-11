# ADR 0032: Project-Agnostic Prompt Intelligence Core and Project Adapter Boundary

## Status

Proposed.

## Date

2026-08-10.

## Governing Preparation

[ADPR-0007](../architecture-records/ADPR-0007-project-agnostic-prompt-intelligence-core.md), created under Issue #237.

## Context

ADR 0031 accepted an Evidence Intelligence-specific deterministic pre-model foundation and explicitly deferred generic Context/Prompt ownership until a second real AI consumer triggered a new ADPR/ADR. It required any future shared abstraction to be extracted only from demonstrated common semantics, preserve Evidence Intelligence identities and lineage, define adapters and migration explicitly, and leave consumer-specific authority with its original owner.

Iran-OS is now a second real AI consumer. Its repository defines AI agents with inputs, memory, a decision engine, execution policies, operational logging, auditability, bounded authority, emergency disablement, and multiple governmental-domain roles. These requirements are materially distinct from Hunter Evidence Intelligence extraction while sharing a need for deterministic, auditable pre-model intent/context/prompt construction.

Keeping the reusable mechanics under Hunter domain ownership would create false coupling. Copying them into every project would create divergent budgeting, canonicalization, provenance, security, and replay behavior. Moving immediately to a remote standalone service would freeze speculative interfaces and add operational complexity before stable multi-consumer contracts exist.

## Decision

Project Hunter will incubate a **project-agnostic Prompt Intelligence core** behind a hard architectural boundary, with project-specific adapters as the only bridge between domain semantics and the shared core.

The initial repository location does not grant Hunter domain ownership over the generic core. The core is shared infrastructure incubated in this repository until extraction criteria are met.

The dependency direction is strictly one-way:

```text
Hunter domain / Evidence Intelligence
        -> Hunter Prompt Adapter
        -> Project-Agnostic Prompt Intelligence Core

Iran-OS domain
        -> Iran-OS Prompt Adapter
        -> Project-Agnostic Prompt Intelligence Core

Future Project
        -> Project Adapter
        -> Project-Agnostic Prompt Intelligence Core
```

The core must not import `hunter.*`, Iran-OS domain modules, or any future consumer package. This rule must be enforceable by architecture tests or equivalent static dependency checks.

## Core Ownership

The project-agnostic core may own only domain-neutral pre-model mechanics and portable contracts demonstrated as common across consumers:

- a provider-independent intent envelope with bounded objective, requested capability ceiling, replay/cutoff coordinates, and schema/version identity;
- source-reference and resolved-view interfaces that carry exact identities, versions, ranges/hashes, temporal coordinates, provenance, trust/data-handling classifications supplied by the consumer adapter;
- deterministic selection-plan and decision-ledger mechanics, including complete omission/missingness reason accounting;
- deterministic budget allocation and capability-constraint accounting;
- typed prompt-plan structures;
- deterministic prompt compilation, trusted/untrusted section separation, escaping/delimiting, canonicalization, exact bytes/messages, hashes, and measured size;
- portable build/result identities and reconstruction metadata;
- deterministic pre-model evaluation primitives such as coverage, fit, invariant, provenance-completeness, and reconstruction-capability outcomes.

The core may define neutral reason-code and outcome vocabularies only where their semantics are genuinely cross-consumer.

## Core Prohibited Authority

The generic core must not own or decide:

- which Hunter evidence is canonical;
- which Iran-OS records, laws, constitutional rules, policies, or state are authoritative;
- project permissions or user authorization;
- domain-specific source eligibility rules except as supplied through a versioned adapter-owned policy contract;
- domain truth;
- downstream persistence or promotion into canonical project state;
- autonomous action authorization;
- provider credentials;
- provider/model selection or routing;
- model invocation;
- response truth validation;
- Hunter Governance Review;
- Iran-OS constitutional or governmental decision authority.

No presence of a core artifact grants domain authority.

## Project Adapter Ownership

Each project adapter owns translation between the project's canonical semantics and the shared core.

A project adapter must own or validate, as applicable:

- domain task/intent semantics;
- the canonical source inventory and source-owner boundaries;
- exact project identities and temporal/repository scope;
- required and optional context policy;
- source authority, trust, sensitivity, and data-handling classifications;
- domain permissions and capability ceiling;
- downstream output contract;
- project-specific validation;
- project-specific persistence integration;
- any promotion from non-authoritative AI output into authoritative project state through an independently authorized boundary.

Adapters may narrow capabilities but may not broaden authority beyond their governing project architecture.

## ADR 0031 Compatibility and Migration

ADR 0031 is reaffirmed for Evidence Intelligence and is not superseded wholesale.

This ADR amends only ADR 0031's **generic ownership deferral**: the second-consumer trigger is now satisfied, so shared project-agnostic ownership may be introduced under the boundary defined here.

Existing ADR 0031 Evidence Intelligence concepts and persisted identities remain historically valid. Migration must be additive and adapter-based:

1. existing `EvidenceExtractionIntent`, Evidence selection/allocation records, `EvidencePromptPlan`, `EvidencePromptArtifact`, and `EvidencePreModelBuildRecord` identities are not rewritten;
2. the Hunter adapter may map Evidence Intelligence records to neutral core views/contracts while preserving exact source IDs, hashes, versions, policy/compiler identities, and lineage;
3. new builds may reference both the consumer-owned identity and the neutral core build identity when needed for audit/reconstruction;
4. no historical record may be relabeled as if it had been produced by the generic core before that core existed;
5. equivalence/migration tests must prove lossless round-trip of every ADR 0031 field that carries authority, provenance, selection, omission, budget, canonicalization, or reconstruction meaning.

If a neutral abstraction cannot preserve a consumer field without semantic loss, that field remains adapter/consumer-owned rather than being forced into the core.

## Persistence Boundary

The core may define immutable portable artifact schemas and deterministic identities, but **persistence authority remains consumer-owned** during incubation.

The core must not open or own Hunter's canonical SQL database, Iran-OS ledgers/databases, or any project-specific authoritative store directly.

A consumer integration supplies repository/persistence ports or persists returned immutable artifacts through its own authorized services.

This avoids turning shared prompt infrastructure into cross-project data authority.

## Replay and Reconstruction

For any build represented as replayable, the combined consumer adapter + core record set must preserve enough exact information to reconstruct the pre-model decision at the declared cutoff, including as applicable:

- consumer intent identity/version;
- source identities, revisions, ranges, hashes, and temporal coordinates;
- selection and omission decisions/reason codes;
- policy identities/versions;
- allocation and capability-constraint identity/version;
- prompt template/fragment/compiler identities/versions;
- canonicalization format/version;
- exact prompt bytes/messages where retention policy permits;
- exact hashes and measured sizes;
- explicit reconstruction-unavailable reasons where retention policy prevents exact replay.

No fallback to current/latest source content is permitted for strict historical reconstruction.

## Security Boundary

The core owns mechanical trusted/untrusted content separation, deterministic escaping/delimiting, and invariant checks. The consumer owns the classification that states which source is trusted, untrusted, sensitive, prohibited, or permitted.

The core must fail closed when required classifications or required context are absent.

## Model and Provider Boundary

This ADR grants no Model Adapter authority.

Provider selection, model capability discovery, routing, credentials, transport, retries, quotas, billing, invocation records, and response validation require separate architecture. A minimal versioned capability constraint may be supplied to budgeting/compilation as data; it does not authorize model selection or invocation.

## Incubation Location

Phase 1 implementation, when separately authorized, should place the core in a top-level package that is physically distinct from `hunter`, for example:

```text
src/
  prompt_intelligence/
  hunter/
    ...
```

The exact package name is an implementation detail, but the dependency boundary is architectural.

The Hunter adapter remains under Hunter ownership and may depend on the core. The core may not depend on Hunter.

## Standalone Extraction Criteria

Moving the generic core into its own repository/package/service is a later governed migration, not part of this ADR's Phase 1 implementation.

Extraction becomes eligible when all of the following are demonstrated:

1. at least two real consumer adapters use the versioned core contracts;
2. the shared contracts have survived independent consumer tests without consumer-specific fields leaking into the core;
3. release cadence or dependency management benefits from independent versioning;
4. migration can preserve artifact identities and provenance without rewriting history;
5. CI can test compatibility matrices for supported consumer/core versions;
6. repository extraction has lower operational and governance cost than continued isolated-package incubation.

A network service is not implied by repository extraction. In-process library/package deployment remains preferred until remote execution has an independently justified requirement.

## Evaluation Requirements

Before production use, implementation must prove at minimum:

- deterministic identity for equal canonical inputs;
- deterministic prompt bytes/messages for equal plan/package/compiler versions;
- complete selection/omission accounting;
- budget-fit invariants;
- exact-size preflight/final-compilation agreement;
- trusted/untrusted boundary invariants;
- strict-known reconstruction behavior where declared available;
- explicit unavailable reconstruction where source policy prevents it;
- no reverse dependency from core to consumer packages;
- lossless Hunter ADR 0031 adapter mapping;
- at least one distinct Iran-OS adapter contract test before claiming multi-project production readiness.

These are pre-model evaluation requirements. They do not evaluate model response quality.

## Non-Goals

This ADR does not:

- authorize a production LLM;
- authorize autonomous agents;
- define generic memory architecture;
- define generic retrieval infrastructure;
- define a universal knowledge graph;
- make Hunter architecture authoritative for Iran-OS;
- make Iran-OS architecture authoritative for Hunter;
- require immediate standalone repository creation;
- authorize implementation before normal architecture/governance review completes.

## Consequences

### Positive

- Hunter can implement Prompt Intelligence without trapping it inside Hunter domain ownership.
- Iran-OS and future projects have a clean reuse path.
- domain authority remains local to each project.
- deterministic security, budgeting, provenance, compilation, and replay mechanics can be tested once.
- standalone extraction remains possible without forcing distributed-system complexity today.

### Negative

- adapter design adds initial implementation work;
- neutral contracts must resist over-generalization;
- compatibility tests are required during migration from ADR 0031;
- repository location and architectural ownership are intentionally different during incubation and must be documented clearly.

## Alternatives Considered

### Keep Prompt Intelligence Hunter-specific

Rejected for generic use because it would require unrelated projects to depend on Hunter semantics and violates the purpose of the second-consumer trigger.

### Copy Prompt Intelligence into every project

Rejected because canonicalization, budget accounting, provenance, security boundaries, and replay behavior would drift.

### Create a standalone service/repository immediately

Rejected for the current phase because the contracts are not yet stable across two production consumers and Iran-OS has no mature production prompt-runtime contract. Reconsider under the extraction criteria above.

## Implementation Status

Not implemented or authorized by this Proposed ADR.

No runtime behavior, provider integration, model invocation, or Iran-OS integration is authorized until this ADR passes independent architecture review and becomes Accepted through the normal governed lifecycle.
