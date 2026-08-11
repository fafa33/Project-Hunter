# ADPR-0007: Project-Agnostic Prompt Intelligence Core

## Metadata

- ADPR ID: `ADPR-0007`
- Status: `READY_FOR_REVIEW`
- Version: 2
- Author: OpenAI / repository owner-directed architecture work
- Reviewers: independent architecture review required
- Created: 2026-08-10
- Approved: not yet approved
- Related Epic: not yet created
- Related Issue: #237
- Planned or produced ADR: ADR 0032 — Project-Agnostic Prompt Intelligence Core and Project Adapter Boundary
- Supersedes: none
- Superseded by: none

## Executive Summary

Project Hunter needs a durable boundary between Hunter-specific semantics and any future reusable Prompt Intelligence infrastructure. This preparation does **not** claim that Hunter and Iran-OS already share concrete prompt/context contracts, and it does not use Iran-OS as authority for Hunter architecture. Iran-OS is only an example of a possible future consumer.

The recommended architecture is a project-neutral core boundary plus project-specific adapters, with a strict admission rule: **no Hunter mechanic may be promoted into the project-neutral core merely because it appears reusable. A concrete contract enters the shared core only after independent consumer evidence demonstrates that its semantics are genuinely cross-project.** Until then, Hunter continues to own and use its ADR 0031 Evidence Intelligence contracts.

This record therefore prepares the ownership boundary, dependency direction, admission rule, migration protections, and future extraction criteria without declaring any specific Hunter contract already shared with another project. No provider/model routing, LLM invocation, autonomous action, or runtime implementation is authorized.

## Problem Statement

### Current condition

ADR 0031 establishes Hunter's Evidence Intelligence-specific deterministic pre-model foundation and deliberately defers generic Prompt/Context ownership until a real second consumer provides enough evidence to compare actual boundaries.

Hunter nevertheless needs a clean architectural answer to a narrower question now: how can its future Prompt Intelligence work avoid becoming permanently coupled to `hunter.*` while still respecting ADR 0031's prohibition on prematurely generalizing Hunter semantics?

### Desired condition

Hunter has an explicit portability boundary that:

- keeps Hunter domain authority in Hunter;
- prevents future reusable infrastructure from importing Hunter domain packages;
- allows project-specific adapters;
- preserves ADR 0031 identities and lineage;
- requires evidence before any concrete contract is promoted to shared ownership;
- leaves unrelated projects fully independent.

### Decision required

Choose the architecture for **future portability and ownership separation**, not the concrete shared contract set.

### In scope

- project-neutral package/boundary rules;
- one-way dependency direction;
- project adapter authority;
- admission criteria for future shared contracts;
- ADR 0031 compatibility and migration constraints;
- persistence, replay, provenance, and security boundaries;
- criteria for later extraction into an independent package/repository.

### Out of scope

- declaring Hunter and Iran-OS contracts equivalent;
- defining Iran-OS contracts;
- implementing an Iran-OS adapter;
- provider credentials or model routing;
- model invocation;
- response truth validation;
- autonomous execution authority;
- Hunter Governance Review modification;
- runtime implementation.

## Problem Validation

ADR 0031 is authoritative for Hunter and requires evidence before generic ownership is inferred from Hunter-specific semantics. The current repository also has a practical portability need: if reusable infrastructure is allowed to grow inside Hunter domain packages without a boundary, later extraction becomes expensive and authority becomes ambiguous.

Those two facts create a real architectural problem even before a second consumer's concrete contracts are mature: Hunter needs to define **where generic ownership may exist and what evidence is required to put anything there**, while avoiding the unsupported claim that a specific set of contracts is already cross-project.

Iran-OS is not used as proof of common contract semantics. It is only a concrete example showing why future portability may matter.

## Motivation

Without an explicit boundary and admission rule:

- Hunter-specific semantics may become accidental platform APIs;
- future reuse may require breaking migrations;
- generic-looking names may conceal Hunter authority;
- a later second consumer may be forced to adopt Hunter-shaped contracts;
- provenance and replay identities may be rewritten during extraction.

With the boundary, Hunter can continue developing under ADR 0031 while preserving a safe path for evidence-based reuse later.

## Existing Architecture

### Hunter

ADR 0031 owns Evidence Intelligence-specific concepts such as extraction intent, context resolution and selection, allocation, prompt planning/compilation, and pre-model build records. Those remain Hunter-owned unless a later governed decision, backed by independent consumer evidence, promotes a proven common contract.

### Other projects

Other repositories, including Iran-OS, remain architecturally independent. This ADPR grants no authority over their contracts, persistence, policies, or runtime. A future project may choose to consume a shared core only through its own governed adapter.

### Current ownership boundary

No accepted Hunter architecture currently owns a populated project-neutral Prompt Intelligence contract set. ADR 0031 intentionally keeps concrete semantics Hunter-specific for now.

## Constraints

### Constitutional

- Project Hunter canonical authority remains inside Hunter's governing documents and accepted ADRs.
- No external project becomes subordinate to Hunter through this decision.
- No implementation may promote itself to architecture.

### Governance and accepted ADRs

- ADR 0031 remains binding.
- Generic ownership must not be inferred from Hunter-only evidence.
- A concrete contract may enter shared ownership only after independent consumer evidence demonstrates common semantics.
- Hunter Governance Review remains outside Prompt Intelligence authority.

### Technical

- A future shared core must not import `hunter.*` or any consumer domain package.
- Consumer adapters may depend on the shared core.
- The core must remain provider-independent.
- Cross-project admission must be explicit and testable.

### Operational

- Hunter may continue using Hunter-owned ADR 0031 contracts before any shared contract exists.
- No remote service is required.

### Persistence and migration

- Consumer persistence authority remains consumer-owned.
- Existing ADR 0031 identities remain historically valid.
- No historical Hunter record may be relabeled as having been produced by a generic core before that core owned the relevant contract.

### Replay and historical reconstruction

- Any future shared artifact must preserve exact source identity, policy/compiler versions, hashes, temporal coordinates, omission/missingness, and reconstruction availability.
- Strict replay must never substitute current/latest content for unavailable historical content.

### Compatibility

- ADR 0031 is reaffirmed, not superseded wholesale.
- Fields whose semantics are not proven cross-project remain consumer-owned.

### Security and privacy

- Consumer projects retain source eligibility, trust, sensitivity, permissions, and data-handling authority.
- A future shared core may own only mechanical enforcement that is proven consumer-neutral.

### Performance and scalability

- The boundary must permit deterministic budgeting and exact-size accounting without requiring provider availability.
- Distributed-service complexity is deferred.

### Evidence and provenance

- Promotion to shared ownership requires attributable evidence from at least two independent consumers.
- Assumptions and future reuse goals are not evidence of common semantics.

## Evidence Inventory

| ID | Evidence | Authority/source | Finding | Quality and limitations | Supports or challenges |
|---|---|---|---|---|---|
| E-001 | ADR 0031 | Accepted Hunter ADR | Concrete pre-model contracts are Evidence Intelligence-specific and generic ownership is deferred pending real cross-consumer evidence | Strong Hunter authority | Supports a strict admission rule and preservation requirement |
| E-002 | ADPR-0006 | Approved Hunter preparation record | Documents Hunter's current pre-model boundaries and reasons for keeping them domain-specific | Strong Hunter preparation evidence | Supports separation of Hunter semantics from future shared ownership |
| E-003 | Project-Hunter repository/package structure | Hunter implementation context | Future reusable code can become coupled if portability boundaries are not explicit | Direct Hunter engineering evidence | Supports defining dependency direction before extraction |
| E-004 | Iran-OS architecture overview | Separate repository; non-authoritative for Hunter | Demonstrates only that a distinct future consumer may exist | High-level and insufficient to prove common prompt/context contracts | Supports future-use possibility only; does **not** support contract promotion |

Missing evidence: concrete, versioned prompt/context contracts from a second independent consumer. Consequently, this ADPR does not identify any specific Hunter contract as already shared.

## Assumptions

| ID | Assumption | Rationale | Confidence | Falsification condition | Consequence if false |
|---|---|---|---|---|---|
| A-001 | Future reuse may be valuable | Motivates a portability boundary | Medium-high | Hunter remains the only consumer indefinitely | Boundary remains harmless; no shared contracts need be admitted |
| A-002 | Domain authority must remain consumer-owned | Prevents accidental cross-project authority | High | A future accepted architecture explicitly centralizes authority | Revisit through a new ADPR/ADR |
| A-003 | Standalone service is premature | Avoids operational cost before shared contracts exist | High | Multiple independent consumers require remote execution | Revisit deployment architecture |
| A-004 | A one-way dependency boundary is useful before extraction | Prevents future coupling | High | Static separation creates more cost than value | Reassess implementation location, not authority rules |

## Architectural Dimensions

The decision covers authority, ownership, dependency direction, identity, persistence, versioning, provenance, replay, missingness, security, testability, extensibility, migration, reversibility, and future extraction. The key distinction is between **designing a reusable boundary now** and **claiming shared semantics now**; only the former is supported by current evidence.

## Candidate Options

### Option 1 — Keep all future Prompt Intelligence permanently Hunter-specific

- Description: retain all contracts and mechanics under Hunter ownership indefinitely.
- Advantages: lowest immediate complexity.
- Disadvantages: portability becomes expensive and unrelated projects would have to depend on Hunter semantics or reimplement later.
- Failure mode: Hunter becomes accidental platform authority.
- Reversibility: decreases over time.

### Option 2 — Copy future mechanics into every project

- Description: each project independently duplicates similar infrastructure.
- Advantages: strong local autonomy.
- Disadvantages: canonicalization, provenance, replay, budgeting, and security mechanics may drift.
- Failure mode: incompatible duplicated infrastructure.
- Reversibility: poor after persisted histories diverge.

### Option 3 — Define a project-neutral core boundary plus project adapters, with evidence-gated contract admission

- Description: establish the neutral ownership boundary now, but populate it only with contracts whose cross-project semantics are independently demonstrated.
- Authority and ownership: Hunter retains Hunter semantics; each future consumer retains its semantics; only proven common mechanics may become core-owned.
- Boundaries: consumer adapter -> shared core; never shared core -> consumer domain package.
- Persistence and replay: consumer-owned persistence; shared artifacts only after evidence-backed admission.
- Compatibility: ADR 0031 identities remain Hunter-owned and historically valid.
- Advantages: preserves portability without premature generalization.
- Disadvantages: some initially duplicated or Hunter-owned mechanics may remain outside the core until evidence exists.
- Failure modes: premature admission; mitigated by the explicit two-consumer evidence gate.
- Reversibility: high.

### Option 4 — Create a standalone shared repository/service immediately

- Description: externalize Prompt Intelligence before shared contracts are proven.
- Advantages: strong physical isolation.
- Disadvantages: speculative APIs, versioning, deployment, authentication, and release management.
- Failure mode: generic infrastructure that is actually Hunter-shaped.
- Reversibility: moderate and costly.

## Comparative Analysis

| Criterion | Hunter-only | Copy per project | Boundary + evidence gate | Standalone now |
|---|---|---|---|---|
| Correctness under current evidence | Medium | Medium | High | Low-medium |
| Authority clarity | Low for future reuse | High locally | High | Medium |
| ADR 0031 compliance | High | High | High | Low until commonality is proven |
| Replay/provenance safety | High in Hunter only | Drifts | High | Speculative |
| Maintainability | Low long-term | Low | High | Medium |
| Operational complexity | Low | Medium | Low-medium | High |
| Migration risk | High later | High later | Low | Medium-high |
| Reversibility | Low over time | Low | High | Medium |
| Future extensibility | Low | Medium | High | High only after contracts stabilize |

## Falsification Results

Option 1 remains viable for Hunter-only operation but fails the portability goal over time.

Option 2 remains viable for independent projects but intentionally accepts duplication and drift.

Option 3 survives falsification because it does **not** assume any specific Hunter contract is already shared. It establishes only the ownership/dependency boundary and an evidence gate. It is invalidated if future implementation promotes Hunter-only semantics into the core without independent consumer evidence.

Option 4 is premature because current evidence does not justify a stable shared API or remote service.

## Rejected Options

### Hunter-only as the permanent architecture

Rejected as the long-term target because it makes future reuse increasingly expensive. Reconsider if Hunter is explicitly declared the sole permanent consumer.

### Copy per project as the default strategy

Rejected as the preferred target because it accepts avoidable drift. Reconsider for any mechanic that fails the cross-consumer evidence gate.

### Standalone service immediately

Rejected because no stable shared contract set exists. Reconsider only after at least two independent consumers use versioned shared contracts and independent deployment has a justified operational benefit.

## Risks

| Risk | Category | Likelihood | Impact | Mitigation | Residual uncertainty |
|---|---|---|---|---|---|
| Premature generalization | Architecture | Medium | High | Two-consumer evidence gate for every promoted contract | Future evidence may show less commonality than expected |
| Authority leakage | Governance | Medium | Critical | One-way imports and consumer-owned policy/persistence | Adapter misuse still requires tests |
| Over-engineering an empty boundary | Engineering | Low-medium | Medium | Keep initial shared surface minimal; do not extract speculative contracts | Exact first admitted contract remains unknown |
| Identity migration error | Data/replay | Medium | High | Preserve ADR 0031 identities and use additive mappings only | Concrete migration proof remains future work |
| Provider coupling | Architecture | Medium | High | Provider/model authority remains out of scope | Separate model-adapter architecture still needed |

## Open Questions

| Question | Blocking? | Owner | Required evidence or action | Status |
|---|---|---|---|---|
| Which concrete contract is first eligible for shared ownership? | Yes for populating the core; no for defining the boundary | future architecture review | Compare versioned contracts from at least two independent consumers | Open |
| Final neutral package name | No | implementation design | Choose before code lands | Open |
| Exact protocol/class names | No | implementation design | Derive only from admitted contracts | Open |
| Model/provider adapter architecture | Yes for model invocation; no for this boundary | future governed architecture | Separate ADPR/ADR | Open |
| Response-validation architecture | Yes for authoritative downstream use; no for this boundary | future governed architecture | Separate governed decision | Open |

## Constitution Review

The proposed boundary does not make the shared core a source of domain truth, permissions, constitutional authority, or downstream action authority. Hunter remains authoritative only for Hunter. Other projects remain authoritative only for themselves.

No constitutional conflict is identified at the preparation stage.

## Governance Review

ADR 0031 remains binding. This preparation no longer treats the existence of Iran-OS or any other project as proof that Hunter's current contracts are generic. Instead, it converts ADR 0031's deferral into an explicit admission rule: a concrete contract enters shared ownership only after independent consumer evidence demonstrates common semantics.

The preparation is architecture-only and authorizes no implementation or model invocation.

## Quality Assessment

| Dimension | Rating | Basis |
|---|---|---|
| Problem clarity | Strong | Portability boundary is separated from contract-commonality claims |
| Evidence quality | Strong for Hunter boundary; intentionally insufficient for shared contract promotion | Accepted Hunter architecture is authoritative; external-project evidence is treated only as illustrative |
| Option completeness | Strong | Four materially distinct strategies are compared |
| Authority clarity | Strong | Hunter, future consumers, adapters, and shared-core admission are distinct |
| Replay/provenance | Strong | Historical identities and additive migration are explicit |
| Security/privacy | Strong | Consumer classifications and permissions remain local |
| Falsifiability | Strong | Promotion without two-consumer evidence invalidates Option 3 |
| Migration/reversibility | Strong | Boundary-first, admission-later minimizes irreversible coupling |
| Implementation independence | Strong | No LLM/provider/runtime implementation is required |
| Residual uncertainty | Explicit | Concrete shared contracts remain intentionally unknown |

Overall preparation quality: `READY_FOR_REVIEW`, not yet `APPROVED`.

## Architecture Readiness

- Outcome: `READY`
- Rationale: the ownership/dependency boundary and evidence-gated admission rule can be decided from Hunter's own architecture without asserting external contract equivalence.
- Missing evidence: concrete second-consumer contracts are still required before any specific contract may be promoted into shared ownership.
- Unresolved conflicts: none identified for reviewing the boundary itself.

## ADR Readiness

- Outcome: `READY_FOR_ADR`
- Proposed ADR title: Project-Agnostic Prompt Intelligence Core and Project Adapter Boundary
- Proposed ADR scope: ownership/dependency boundary, evidence-gated contract admission, ADR 0031 compatibility, migration protections, persistence/replay rules, and future extraction criteria.
- Decisions the ADR must fix: no reverse consumer dependency; consumer-owned authority/persistence; no contract promotion without independent cross-consumer evidence; historical identity preservation.
- Matters the ADR must leave open: which concrete contracts are eventually admitted, external-project adapters, provider/model architecture, response validation, and standalone extraction timing.

## Final Recommendation

Adopt Option 3 **as a boundary and admission policy**, not as a declaration that Hunter and Iran-OS already share concrete contracts.

Project Hunter may reserve a physically isolated, project-neutral Prompt Intelligence boundary. Hunter's current ADR 0031 contracts remain Hunter-owned. A contract is promoted into the shared core only when at least two independent consumers provide concrete, versioned evidence that the contract's semantics are genuinely common. Iran-OS may become such a consumer later through its own adapter and governance, but it is not required by or subordinate to this ADPR.

## Decision History

| Date | State | Change | Author or reviewer |
|---|---|---|---|
| 2026-08-10 | READY_FOR_REVIEW | Initial preparation completed with four-option comparison and Option 3 recommendation | OpenAI / repository owner-directed architecture work |
| 2026-08-11 | READY_FOR_REVIEW | Reorganized into canonical ADPR structure | OpenAI / repository owner-directed architecture work |
| 2026-08-11 | READY_FOR_REVIEW | Corrected cross-project overclaim: Iran-OS is now illustrative only; concrete shared contracts require independent two-consumer evidence before admission | OpenAI / repository owner-directed architecture work |

## Traceability

- Epic: not yet created
- Issue: #237
- Preparation working document: this record
- Checklist review: independent review pending
- ADPR: ADPR-0007
- ADR: ADR 0032 (Proposed)
- Implementation plan: not authorized
- PR: #239
- Merge commit: not yet merged
- Release: not applicable

## Immutability and Supersession

This record is currently `READY_FOR_REVIEW`, not yet `APPROVED`. Until approval, review-driven corrections may amend the reasoning while remaining auditable in repository history.

After `APPROVED`, substantive changes to the decision basis require a new ADPR that explicitly supersedes ADPR-0007. Non-substantive traceability completion and typographical corrections must remain auditable.