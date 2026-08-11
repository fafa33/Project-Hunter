# ADPR-0007: Project-Agnostic Prompt Intelligence Core

## Metadata

- ADPR ID: `ADPR-0007`
- Status: `READY_FOR_REVIEW`
- Version: 1
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

ADR 0031 deliberately kept Hunter's pre-model foundation Evidence Intelligence-specific and deferred generic Prompt/Context ownership until a second real AI consumer existed. Iran-OS now provides that second materially distinct consumer. This ADPR evaluates four ownership models and recommends a project-agnostic Prompt Intelligence core with project-specific adapters. The shared core owns only deterministic, domain-neutral pre-model mechanics; each consumer retains canonical source authority, domain semantics, permissions, persistence authority, downstream validation, and action authority. The initial implementation may incubate as a physically isolated top-level package in the Project Hunter repository, but repository location does not grant Hunter domain ownership. No model/provider routing, LLM invocation, autonomous action, or runtime implementation is authorized by this preparation record.

## Problem Statement

### Current condition

ADR 0031 establishes an accepted, Evidence Intelligence-specific deterministic pre-model foundation inside Project Hunter. It deliberately defers generic Context/Prompt ownership until a second real AI consumer demonstrates materially distinct contracts.

That trigger now exists. Iran-OS has an independent AI-agent architecture in which agents have inputs, memory, decision engines, execution policies, bounded authority, operational logging, auditability, emergency disablement, and connections to national-ledger and smart-contract systems. Those concerns are materially different from Hunter Evidence Intelligence extraction while still requiring reusable prompt/context construction primitives.

### Desired condition

A reusable Prompt Intelligence foundation exists without making Hunter or Iran-OS domain semantics part of the shared core. Project-specific adapters retain authority over domain intent, canonical sources, validation, permissions, downstream actions, and promotion to authoritative state.

### Decision required

Determine whether reusable prompt intelligence should remain Hunter-specific, be copied per project, be extracted into a project-agnostic core with adapters, or become a standalone service/repository immediately.

### In scope

- project-independent intent envelope primitives;
- context candidate/reference interfaces without domain ownership;
- deterministic selection-plan interfaces and omission accounting;
- budgeting and capability constraints;
- prompt planning, deterministic compilation, canonicalization, hashes, reconstruction metadata, and provenance;
- project adapter boundary;
- dependency direction and import constraints;
- compatibility and migration from ADR 0031;
- criteria for later repository/package extraction;
- evaluation primitives for deterministic pre-model artifacts.

### Out of scope

- provider credentials;
- provider/model routing;
- model invocation;
- response truth validation;
- autonomous execution authority;
- Hunter Governance Review modification;
- Iran-OS governmental or constitutional decision authority;
- replacing project-owned persistence or canonical domain records.

## Problem Validation

ADR 0031 states that generic Context/Prompt ownership is deferred until a second real AI consumer triggers a new ADPR/ADR and that any shared abstraction must be extracted only from proven common semantics while preserving Evidence Intelligence identities and consumer-specific authority.

Iran-OS provides that second consumer. Its agent architecture requires bounded, auditable AI execution with inputs, memory, decision logic, policies, operational records, and explicit authority constraints. These are not Evidence Intelligence semantics, so reusing Hunter-specific classes directly would create false ownership. At the same time, duplicating deterministic prompt construction, budgeting, provenance, security, and reconstruction logic would create divergent infrastructure.

The unresolved problem is therefore architectural rather than a convenience refactor.

## Motivation

Without a shared boundary:

- Hunter-specific names and authority may leak into unrelated projects;
- Iran-OS may duplicate prompt/context logic with incompatible provenance and replay semantics;
- future projects would repeat the same infrastructure;
- provider-specific utilities could become de facto architecture;
- later extraction would require breaking changes across multiple consumers.

A correct shared core allows reuse while keeping domain authority local.

## Existing Architecture

### Hunter

ADR 0031 owns Evidence Intelligence-specific concepts including `EvidenceExtractionIntent`, Evidence context resolution/selection, selection ledger, allocation result/package, prompt plan/artifact, and pre-model build record. It explicitly does not grant repository-wide generic ownership, model routing, or canonical authority.

### Iran-OS

The current Iran-OS agent architecture establishes AI agents with inputs, memory, decision engines, execution policies, operation logging, auditability, bounded authority, emergency disablement, and multiple domain roles. This is sufficient to demonstrate a second consumer but not sufficient to make Iran-OS domain contracts authoritative inside Project Hunter. The shared core must therefore remain domain-neutral.

### Current ownership boundary

Hunter owns Hunter semantics and Evidence Intelligence authority. Iran-OS owns Iran-OS semantics and authority. No accepted architecture currently owns a project-neutral Prompt Intelligence core. ADR 0031 intentionally left that ownership unresolved pending a second consumer.

## Constraints

### Constitutional

- Project Hunter canonical authority remains with Hunter documents and accepted ADRs.
- Iran-OS authority remains in Iran-OS; this ADPR cannot establish governmental or constitutional authority there.
- No implementation may promote itself to architecture.

### Governance and accepted ADRs

- ADR 0031 identities and lineage cannot be silently replaced.
- Generic ownership may only be introduced through a new governed ADPR/ADR after the second-consumer trigger.
- Hunter Governance Review remains deterministic and outside the authority of Prompt Intelligence.

### Technical

- Shared core must not import `hunter.*`.
- Shared core must not import Iran-OS domain packages.
- Consumer adapters may import the shared core.
- Core contracts must be provider-independent.
- Deterministic compilation and canonicalization must be testable without an LLM.

### Operational

- The first implementation must remain usable in-process; a remote service is not required.
- The architecture must not require model/provider availability to validate pre-model behavior.

### Persistence and migration

- Shared core may define portable artifact schemas and identities, but each consumer owns persistence authority and storage integration.
- Migration from Evidence Intelligence records must be adapter-based and lossless.
- Existing Evidence Intelligence record identities remain valid historical identities.

### Replay and historical reconstruction

- Core artifacts must preserve exact source-reference identities, policy/template/compiler versions, canonicalization version, hashes, and size/accounting evidence sufficient for consumer-authorized reconstruction.
- Historical reconstruction availability must be explicit when source-retention policy prevents exact replay.
- Strict reconstruction must never substitute current/latest source content for unavailable historical content.

### Compatibility

- ADR 0031 remains historically valid and is not superseded wholesale.
- Consumer-specific fields that cannot be represented without semantic loss must remain adapter-owned rather than being forced into a neutral schema.

### Security and privacy

- Mechanical trusted/untrusted separation and deterministic escaping/delimiting may be core-owned.
- Trust, sensitivity, eligibility, and data-handling classifications remain consumer-owned.
- The core must not broaden source access or permissions.

### Performance and scalability

- Deterministic budgeting and exact-size accounting must be possible before model invocation.
- The initial architecture should avoid distributed-service latency and operational complexity until independent deployment is justified.

### Evidence and provenance

- Every selected, omitted, or unavailable context item must remain attributable to exact consumer-supplied source identity and policy/version coordinates.
- Neutral core artifacts must not erase domain provenance or consumer authority boundaries.

## Evidence Inventory

| ID | Evidence | Authority/source | Finding | Quality and limitations | Supports or challenges |
|---|---|---|---|---|---|
| E-001 | ADR 0031 | Accepted Hunter ADR | Explicitly defers generic ownership until a second consumer and requires preservation of Evidence Intelligence identities and consumer-specific authority | Strong Hunter authority; Hunter-specific | Supports new ADPR trigger and preservation requirement |
| E-002 | ADPR-0006 | Approved Hunter preparation record | Establishes prior option analysis and deterministic pre-model boundaries | Strong Hunter preparation evidence; scoped to Evidence Intelligence | Supports extraction only from proven Hunter semantics |
| E-003 | Iran-OS agent architecture | Iran-OS repository architecture overview | Demonstrates a materially distinct AI-agent consumer with authority, audit, memory, policy, and operational needs | High-level; not yet a production prompt-runtime contract | Supports second-consumer trigger while limiting how much may be generalized |
| E-004 | PR #200 operational history recorded in ADR 0031 | Hunter governance evidence | Shows token budgets, provider quotas, exact review inputs, and deterministic pre-model handling matter operationally | Governance-specific operational evidence | Supports deterministic budgeting/provenance mechanics, not shared domain authority |

Missing evidence: a mature production Iran-OS prompt runtime does not yet exist. Therefore the shared core must remain minimal and must not invent Iran-OS-specific prompt semantics.

## Assumptions

| ID | Assumption | Rationale | Confidence | Falsification condition | Consequence if false |
|---|---|---|---|---|---|
| A-001 | Hunter and Iran-OS both need deterministic pre-model construction | Basis for shared core | High | Iran-OS chooses no model-facing prompt/context path | Keep core usable by Hunter; do not force Iran-OS adoption |
| A-002 | Domain authority differs materially | Prevent false generic ownership | High | Both consumers prove identical authority contracts | Adapters may shrink but remain harmless |
| A-003 | Standalone service is premature | Avoid distributed-system cost before stable contracts | Medium-high | Multiple independent runtimes require remote shared execution | Revisit extraction/service decision |
| A-004 | Portable package boundary is desirable | Enables future reuse | High | Consumer requirements require deep runtime coupling | Reassess module/package split |

## Architectural Dimensions

The decision must cover authority, ownership, dependency direction, component boundaries, identity, persistence, versioning, provenance, replay, missingness, security, testability, performance, extensibility, migration, rollback, observability, and dependency enforcement. These dimensions matter because a reusable prompt layer can otherwise become accidental cross-project authority, lose historical provenance, or hard-code provider/runtime assumptions.

## Candidate Options

### Option 1 — Keep all Prompt Intelligence inside Hunter

- Description: keep reusable mechanics under Hunter/Evidence Intelligence ownership.
- Authority and ownership: Hunter Evidence Intelligence owns implementation.
- Boundaries: unrelated consumers depend on Hunter-specific packages or semantics.
- Persistence and replay: coherent only inside Hunter.
- Evidence and provenance: Hunter-native.
- Compatibility: lowest immediate Hunter migration cost.
- Advantages: lowest immediate implementation effort.
- Disadvantages: Iran-OS reuse requires Hunter dependency; domain leakage is likely.
- Failure modes: Hunter becomes accidental platform authority.
- Migration implications: later extraction becomes progressively harder.
- Reversibility: low over time.
- Open dependencies: none for Hunter-only use; unacceptable for a reusable cross-project architecture.

### Option 2 — Copy implementation into each project

- Description: independently implement similar prompt/context mechanics per project.
- Authority and ownership: each project owns its copy.
- Boundaries: strong local ownership but no shared behavioral contract.
- Persistence and replay: project-local and likely to diverge.
- Evidence and provenance: project-local.
- Compatibility: no shared compatibility guarantee.
- Advantages: maximum local autonomy.
- Disadvantages: duplicated budgeting, canonicalization, prompt hashing, provenance, security escaping, and evaluation; drift is expected.
- Failure modes: same concept acquires incompatible semantics and bugs.
- Migration implications: convergence later requires reconciliation of persisted artifacts and semantics.
- Reversibility: low once histories diverge.
- Open dependencies: duplicate maintenance and cross-project consistency work.

### Option 3 — Project-agnostic core plus project adapters

- Description: shared core owns domain-neutral pre-model mechanics; adapters own translation from consumer semantics and all domain authority.
- Authority and ownership: core owns only portable mechanics; adapters and projects own domain policy and authority.
- Boundaries: one-way dependency from consumer adapter to core; no reverse consumer import.
- Persistence and replay: portable identities/artifacts with consumer-owned persistence and reconstruction policy.
- Evidence and provenance: exact consumer identities and classifications flow through typed neutral views without transfer of authority.
- Compatibility: Hunter adapter preserves ADR 0031 identities; Iran-OS adapter maps its own semantics.
- Advantages: reuse without authority leakage, deterministic testing, future extraction path.
- Disadvantages: requires careful boundary design and adapter contracts.
- Failure modes: over-generalization or accidental import reversal; mitigated by dependency tests and narrow contracts.
- Migration implications: additive adapter-based migration; historical records are not relabeled.
- Reversibility: high; core can remain in monorepo or later be extracted.
- Open dependencies: detailed consumer adapter contracts and later model/provider architecture.

### Option 4 — Standalone repository/service from day one

- Description: create an independently versioned package/repository or remote service immediately.
- Authority and ownership: separate product/package owns prompt-intelligence APIs.
- Boundaries: strongest physical isolation.
- Persistence and replay: potentially portable but requires early versioning and compatibility policy.
- Evidence and provenance: can be strong if contracts are correct.
- Compatibility: speculative until two stable adapters exist.
- Advantages: immediate multi-project consumption and physical separation.
- Disadvantages: premature API freezing, release/version management, deployment/auth/network complexity.
- Failure modes: distributed monolith or lowest-common-denominator API.
- Migration implications: higher coordination cost while contracts are still emerging.
- Reversibility: moderate but operationally expensive.
- Open dependencies: packaging/deployment/versioning/service-security decisions not yet justified.

## Comparative Analysis

| Criterion | Hunter-only | Copy per project | Shared core + adapters | Standalone now |
|---|---|---|---|---|
| Correctness | Medium | Medium | High | Medium |
| Constitutional compliance | Medium | High locally | High | Medium-high |
| Governance compliance | Medium | Medium | High | Medium |
| Authority clarity | Low outside Hunter | High locally, low globally | High | Medium-high |
| Replayability | High in Hunter only | Low over time | High | High if correctly implemented |
| Evidence integrity | High in Hunter only | Medium | High | High if correctly implemented |
| Maintainability | Low for multi-project use | Low | High | Medium |
| Scalability | Low organizationally | Medium | High | High after contracts stabilize |
| Operational complexity | Low | Medium | Medium | High |
| Migration risk | High later | High later | Low-medium | Medium-high now |
| Implementation effort | Low | Medium | Medium | High |
| Reversibility | Low over time | Low | High | Medium |
| Long-term extensibility | Low | Medium | High | High after contracts stabilize |

## Falsification Results

Option 1 fails the second-consumer requirement because Iran-OS would need to depend on Hunter domain infrastructure or reimplement it.

Option 2 fails consistency and provenance goals because no common mechanism prevents security, budgeting, canonicalization, identity, or replay drift.

Option 3 survives current falsification only if the common core is restricted to mechanics demonstrated across consumers: intent envelopes, source-reference interfaces, deterministic selection/allocation mechanics, prompt planning/compilation, provenance, reconstruction metadata, budgeting, capability constraints, and pre-model evaluation. It is invalidated if it begins owning domain source authority, project permissions, downstream actions, provider routing, model invocation, or model-response truth.

Option 4 remains a plausible future deployment/extraction target but is rejected for Phase 1 because only two consumers exist and Iran-OS does not yet expose a stable production prompt-runtime contract. A standalone service would freeze speculative interfaces and add operational complexity before evidence justifies it.

## Rejected Options

### Hunter-only

- Rejection reason: violates reuse and authority separation for a real second consumer.
- Supporting evidence: E-001, E-003.
- Violated constraint or inferior trade-off: false cross-project Hunter ownership.
- Reconsideration conditions: only if the generic reuse goal is abandoned and Hunter becomes the sole consumer.

### Copy per project

- Rejection reason: deterministic infrastructure would drift.
- Supporting evidence: E-001, E-002, E-003.
- Violated constraint or inferior trade-off: weak cross-project provenance, replay, and maintenance consistency.
- Reconsideration conditions: only if consumers prove there is no stable common mechanic worth sharing.

### Standalone service immediately

- Rejection reason: premature interface freezing and distributed-system cost.
- Supporting evidence: E-003 and the absence of a mature Iran-OS production prompt contract.
- Violated constraint or inferior trade-off: unnecessary operational complexity during contract discovery.
- Reconsideration conditions: stable multi-consumer contracts, independent release cadence, and remote execution need are demonstrated.

## Risks

| Risk | Category | Likelihood | Impact | Mitigation | Residual uncertainty |
|---|---|---|---|---|---|
| Over-generalization | Architecture | Medium | High | Extract only demonstrated common semantics; keep domain policy in adapters | Exact neutral schema shape remains to be proven in implementation design |
| Authority leakage | Governance | Medium | Critical | One-way imports, explicit prohibited authority, architecture tests, consumer-owned validation/persistence | Adapter misuse remains possible without tests |
| Premature repository extraction | Operations | Medium | Medium-high | Incubate as isolated in-process package first | Future release cadence may justify extraction earlier than expected |
| Identity migration error | Data/replay | Medium | High | Preserve ADR 0031 historical IDs and add linkage rather than rewriting | Concrete persistence mappings require implementation proof |
| Provider coupling | Architecture | Medium | High | Keep provider/model routing and invocation outside the core | Later model-adapter design still open |

## Open Questions

| Question | Blocking? | Owner | Required evidence or action | Status |
|---|---|---|---|---|
| Final neutral package name | No | implementation design | Choose a project-neutral name before code lands | Open |
| Exact language-level protocol/class names | No | implementation design | Derive from accepted architecture without leaking consumer semantics | Open |
| Portable schema representation | No | implementation design | Compare typed representations under deterministic serialization requirements | Open |
| Exact Iran-OS adapter source inventories and data-handling policies | Yes for Iran-OS production use, no for this architecture decision | Iran-OS architecture | Produce consumer-owned adapter policy/evidence inventory | Open |
| Model/provider adapter architecture | Yes for model invocation, no for pre-model core | future governed architecture | Separate ADPR/ADR if model authority is introduced | Open |
| Response-validation architecture | Yes for authoritative downstream use, no for this pre-model core | future governed architecture | Separate governed decision | Open |

## Constitution Review

The proposed shared core does not become domain truth, constitutional authority, source authority, or decision authority. Project-specific constitutional and canonical owners remain unchanged. The one-way adapter boundary strengthens explicit ownership and auditability because the neutral core cannot import consumer domain packages or elevate its artifacts into authoritative project state.

No constitutional conflict is identified at the preparation stage. Any implementation that grants autonomous action, model/provider authority, or cross-project source authority would exceed this ADPR and require separate governance.

## Governance Review

ADR 0031's generic-ownership trigger is satisfied by a second materially distinct consumer. This preparation preserves its requirements that shared abstraction be extracted only from common semantics, preserve Evidence Intelligence identities/lineage, define adapters and migration explicitly, and keep consumer authority local.

The preparation record is intentionally architecture-only. It does not authorize implementation, model routing, provider selection, LLM invocation, or autonomous action. Independent architecture review remains required before ADR 0032 can become Accepted.

## Quality Assessment

Applied against `docs/ARCHITECTURE_DECISION_QUALITY_STANDARD.md` at the preparation stage:

| Dimension | Rating | Basis |
|---|---|---|
| Problem clarity | Strong | Current/desired state and decision required are explicit |
| Evidence quality | Adequate-to-strong | Accepted Hunter architecture plus a real second consumer; Iran-OS runtime evidence is still high-level and is treated as a limitation |
| Option completeness | Strong | Four materially distinct ownership/deployment options are compared |
| Authority clarity | Strong | Core, adapter, consumer, persistence, model/provider, and downstream authority are separated |
| Replay/provenance | Strong | Historical identity preservation and strict reconstruction constraints are explicit |
| Security/privacy | Strong | Mechanical separation is core-owned while classification and access remain consumer-owned |
| Falsifiability | Strong | Each option has explicit invalidation or reconsideration conditions |
| Migration/reversibility | Strong | Additive adapter migration and later extraction criteria are explicit |
| Implementation independence | Strong | Architecture does not depend on an LLM or provider implementation |
| Residual uncertainty | Explicit | Iran-OS production prompt contract, concrete schemas, model adapter, and response validation remain open and bounded |

Overall preparation quality: `READY_FOR_REVIEW`, not yet `APPROVED`.

## Architecture Readiness

- Outcome: `READY`
- Rationale: four materially distinct options were compared; the second-consumer trigger is evidenced; Option 3 satisfies reuse and authority boundaries without premature distributed architecture.
- Missing evidence: detailed Iran-OS production prompt runtime; deliberately excluded from generic semantics and required before Iran-OS production readiness can be claimed.
- Unresolved conflicts: none identified that block independent review of the architecture boundary.

## ADR Readiness

- Outcome: `READY_FOR_ADR`
- Proposed ADR title: Project-Agnostic Prompt Intelligence Core and Project Adapter Boundary
- Proposed ADR scope: shared mechanics, one-way dependency rule, adapter authority, ADR 0031 compatibility, persistence/replay boundaries, and extraction criteria.
- Decisions the ADR must fix: ownership split, dependency direction, historical identity preservation, generic-core prohibited authority, persistence boundary, and extraction threshold.
- Matters the ADR must leave open: provider/model adapter, response validation, concrete Iran-OS production integration, and standalone service timing.

## Final Recommendation

Adopt Option 3: a project-agnostic Prompt Intelligence core with project-specific adapters. Initially place the core in the Project Hunter repository as a physically isolated top-level package with a hard zero-import dependency on `hunter.*`. Treat repository location as incubation, not Hunter ownership. Add a Hunter Evidence Intelligence adapter that preserves ADR 0031 identities and an eventual Iran-OS adapter in the Iran-OS repository. Extract the core into its own repository/package only after stable multi-consumer contracts and independent release/runtime needs are demonstrated.

## Decision History

| Date | State | Change | Author or reviewer |
|---|---|---|---|
| 2026-08-10 | READY_FOR_REVIEW | Initial preparation completed with four-option comparison and Option 3 recommendation | OpenAI / repository owner-directed architecture work |
| 2026-08-11 | READY_FOR_REVIEW | Reorganized into the canonical ADPR structure; preserved architecture substance and made evidence, quality, falsification, history, and immutability sections explicit | OpenAI / repository owner-directed architecture work |

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

This record is currently `READY_FOR_REVIEW`, not yet `APPROVED`. Until approval, corrections required by review may amend the reasoning while remaining auditable in repository history.

After `APPROVED`, this record becomes historical evidence. Any correction that materially changes the decision basis, option analysis, or recommendation requires a new ADPR that explicitly supersedes ADPR-0007. Non-substantive traceability completion and typographical corrections may be made only with auditable repository history.
