# ADPR-0007: Project-Agnostic Prompt Intelligence Core

## Metadata

- ADPR ID: ADPR-0007
- Title: Project-Agnostic Prompt Intelligence Core
- Status: `READY_FOR_REVIEW`
- Author: OpenAI / repository owner-directed architecture work
- Reviewers: independent architecture review required
- Created: 2026-08-10
- Last updated: 2026-08-10
- Related Epic: not yet created
- Related Issue: #237
- Planned ADR: ADR 0032 — Project-Agnostic Prompt Intelligence Core and Project Adapter Boundary

## 1. Problem Statement

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

## 2. Problem Validation

ADR 0031 already states that generic Context/Prompt ownership is deferred until a second real AI consumer triggers a new ADPR/ADR and that any shared abstraction must be extracted only from proven common semantics while preserving Evidence Intelligence identities and consumer-specific authority.

Iran-OS provides that second consumer. Its agent architecture requires bounded, auditable AI execution with inputs, memory, decision logic, policies, operational records, and explicit authority constraints. These are not Evidence Intelligence semantics, so reusing Hunter-specific classes directly would create false ownership. At the same time, duplicating deterministic prompt construction, budgeting, provenance, and reconstruction logic would create divergent infrastructure.

The problem is therefore architectural rather than a convenience refactor.

## 3. Motivation

Without a shared boundary:

- Hunter-specific names and authority may leak into unrelated projects;
- Iran-OS may duplicate prompt/context logic with incompatible provenance and replay semantics;
- future projects would repeat the same infrastructure;
- provider-specific utilities could become de facto architecture;
- later extraction would require breaking changes across multiple consumers.

A correct shared core allows reuse while keeping domain authority local.

## 4. Existing Architecture

### Hunter

ADR 0031 owns Evidence Intelligence-specific concepts including `EvidenceExtractionIntent`, Evidence context resolution/selection, selection ledger, allocation result/package, prompt plan/artifact, and pre-model build record. It explicitly does not grant repository-wide generic ownership, model routing, or canonical authority.

### Iran-OS

The current Iran-OS `agents/README.md` establishes AI agents with:

- inputs;
- memory;
- decision engine;
- execution policies;
- operation logging;
- audit layer;
- bounded authority;
- emergency disablement;
- domain agents including welfare, health, education, tax, judicial, parliament oversight, constitutional trigger, and national security.

This evidence is sufficient to demonstrate a second consumer but not sufficient to make Iran-OS domain contracts authoritative inside Project Hunter. The shared core must therefore remain domain-neutral.

## 5. Constraints

### Constitutional and governance

- Project Hunter canonical authority remains with Hunter documents and accepted ADRs.
- Iran-OS authority remains in Iran-OS; this ADPR cannot establish governmental or constitutional authority there.
- ADR 0031 identities and lineage cannot be silently replaced.
- No implementation may promote itself to architecture.

### Technical

- Shared core must not import `hunter.*`.
- Shared core must not import Iran-OS domain packages.
- Consumer adapters may import the shared core.
- Core contracts must be provider-independent.
- Deterministic compilation and canonicalization must be testable without an LLM.

### Persistence and migration

- Shared core may define portable artifact schemas and identities, but each consumer owns persistence authority and storage integration.
- Migration from Evidence Intelligence records must be adapter-based and lossless.
- Existing Evidence Intelligence record identities remain valid historical identities.

### Replay and historical reconstruction

- Core artifacts must preserve exact source-reference identities, policy/template/compiler versions, canonicalization version, hashes, and size/accounting evidence sufficient for consumer-authorized reconstruction.
- Historical reconstruction availability must be explicit when source-retention policy prevents exact replay.

### Security and privacy

- Untrusted context classification and delimiting are core concerns.
- Data-handling policy remains consumer-owned.
- Core must not broaden source access or permissions.

## 6. Evidence Inventory

| Evidence | Authority/source | Relevance | Quality and limitations | Supports or challenges |
|---|---|---|---|---|
| ADR 0031 | Accepted Hunter ADR | Explicitly defers generic ownership until second consumer | Strong Hunter authority; Hunter-specific | Supports new ADPR trigger and preservation requirement |
| ADPR-0006 | Approved Hunter preparation record | Prior option analysis and pre-model boundaries | Hunter Evidence Intelligence scope | Supports proven Hunter semantics |
| Iran-OS `agents/README.md` | Iran-OS repository architecture overview | Demonstrates distinct AI-agent consumer and authority/audit needs | High-level; not yet a full prompt contract | Supports second-consumer trigger; limits how much can be generalized |
| PR #200 operational history recorded in ADR 0031 | Hunter governance evidence | Shows token budgets, provider quotas, and exact-review evidence matter | Governance-specific operational context | Supports deterministic pre-model artifacts, not shared domain authority |

Missing evidence: a production Iran-OS prompt runtime does not yet exist. Therefore the shared core must remain minimal and must not invent Iran-OS-specific prompt semantics.

## 7. Assumptions

| Assumption | Why required | Confidence | Falsification condition | Consequence if false |
|---|---|---|---|---|
| Hunter and Iran-OS both need deterministic pre-model construction | Basis for shared core | High | Iran-OS chooses no model-facing prompt/context path | Keep core usable by Hunter; do not force Iran-OS adoption |
| Domain authority differs materially | Prevent false generic ownership | High | Both consumers prove identical authority contracts | Adapters may shrink but remain harmless |
| Standalone service is premature | Avoid distributed-system cost before stable contracts | Medium-high | Multiple independent runtimes require remote shared execution | Revisit extraction/service decision |
| Portable package boundary is desirable | Enables future reuse | High | Consumer requirements require deep runtime coupling | Reassess module/package split |

## 8. Architectural Dimensions

The decision must explicitly cover authority, ownership, component boundaries, identity, persistence, versioning, provenance, replay, missingness, security, testability, extensibility, migration, rollback, observability, and dependency enforcement.

## 9. Candidate Options

### Option 1 — Keep all Prompt Intelligence inside Hunter

- Authority and ownership: Hunter Evidence Intelligence owns implementation.
- Advantages: lowest immediate implementation effort.
- Disadvantages: Iran-OS reuse requires Hunter dependency; domain leakage is likely.
- Failure mode: Hunter becomes accidental platform authority.
- Reversibility: increasingly expensive after additional consumers.

### Option 2 — Copy implementation into each project

- Authority and ownership: each project independently owns its copy.
- Advantages: maximum local autonomy.
- Disadvantages: duplicated budgeting, canonicalization, prompt hashing, provenance, security escaping, and evaluation; drift is expected.
- Failure mode: same concept acquires incompatible semantics and bugs.
- Reversibility: difficult once persisted artifacts diverge.

### Option 3 — Project-agnostic core plus project adapters

- Authority and ownership: shared core owns only domain-neutral pre-model mechanics and portable contracts; each adapter owns translation from project semantics and all domain authority.
- Persistence and replay: portable identities/artifacts, consumer-owned repositories.
- Compatibility: Evidence Intelligence adapter preserves ADR 0031 record identities and lineage; Iran-OS adapter maps its own intent/source/policy/authority concepts.
- Advantages: reuse without authority leakage, deterministic testing, future extraction path.
- Disadvantages: requires careful boundary design and adapter contracts.
- Failure modes: over-generalization or accidental import reversal; mitigated by dependency tests and narrow contracts.
- Reversibility: high; core can remain in monorepo or later be extracted.

### Option 4 — Standalone repository/service from day one

- Authority and ownership: separate product/service owns prompt intelligence APIs.
- Advantages: strong physical isolation and immediate multi-project consumption.
- Disadvantages: premature API freezing, release/version management, deployment/auth/network complexity, harder coordinated migration while contracts are still emerging.
- Failure mode: distributed monolith or lowest-common-denominator API.
- Reversibility: moderate but operationally expensive.

## 10. Comparative Evaluation

| Criterion | Hunter-only | Copy per project | Shared core + adapters | Standalone now |
|---|---|---|---|---|
| Correctness | Medium | Medium | High | Medium |
| Authority clarity | Low outside Hunter | High locally, low globally | High | Medium-high |
| Replay/provenance consistency | High in Hunter only | Low over time | High | High if correctly implemented |
| Maintainability | Low for multi-project use | Low | High | Medium |
| Migration risk | High later | High later | Low-medium | Medium-high now |
| Implementation effort | Low | Medium | Medium | High |
| Reversibility | Low over time | Low | High | Medium |
| Long-term extensibility | Low | Medium | High | High after contracts stabilize |

## 11. Falsification

Option 1 fails the second-consumer requirement because Iran-OS would need to depend on Hunter domain infrastructure or reimplement it.

Option 2 fails consistency and provenance goals: no mechanism prevents security, budgeting, canonicalization, or identity drift.

Option 3 survives current falsification if the common core is restricted to mechanics demonstrated in both consumers: intent envelopes, source-reference interfaces, deterministic selection/allocation mechanics, prompt planning/compilation, provenance, reconstruction metadata, and evaluation. It fails if it begins owning domain source authority, project permissions, downstream actions, or model-response truth.

Option 4 is not falsified as a future target, but is rejected for Phase 1 because only two consumers exist and Iran-OS does not yet expose a stable production prompt-runtime contract. A standalone service would freeze speculative interfaces and add operations before they are justified.

## 12. Rejected Options

- Hunter-only: rejected because it violates reuse and authority separation for a real second consumer.
- Copy per project: rejected because deterministic infrastructure would drift.
- Standalone service immediately: rejected for current phase; reconsider after at least two adapters run against stable versioned contracts and independent release cadence is justified.

## 13. Risks

### Over-generalization
Likelihood medium; impact high. Mitigation: extract only demonstrated common semantics; adapters retain domain policy.

### Authority leakage
Likelihood medium; impact critical. Mitigation: one-way imports, explicit prohibited dependencies, architecture tests, consumer-owned persistence and validation.

### Premature repository extraction
Likelihood medium; impact medium-high. Mitigation: begin as an isolated package boundary in Project Hunter, with zero `hunter.*` imports, then extract when release/runtime criteria are met.

### Identity migration errors
Likelihood medium; impact high. Mitigation: preserve ADR 0031 historical IDs, add adapter linkage rather than rewriting persisted identities.

## 14. Open Questions

Non-blocking for ADR draft:

- final neutral package name (`prompt_intelligence` is provisional);
- exact language-level protocol/class names;
- whether portable schemas live as dataclasses, typed mappings, or another implementation form;
- eventual standalone repository name.

Blocking implementation but not architecture:

- exact Iran-OS adapter source inventories and data-handling policies;
- model-adapter architecture;
- response-validation architecture.

## 15. Constitution Check

No shared core is allowed to become domain truth or decision authority. Domain projects retain their own constitutional/canonical owners. The proposed boundary strengthens explicit ownership and auditable evidence rather than weakening it.

## 16. Governance Check

ADR 0031's generic-ownership trigger is now satisfied by a second real consumer. This preparation preserves its requirement that shared abstraction be extracted only from common semantics, preserve Evidence Intelligence identities/lineage, define adapters and migration, and keep consumer authority local.

## 17. Architecture Readiness

- Outcome: `READY`
- Rationale: four materially distinct options were compared; the second-consumer trigger is evidenced; Option 3 satisfies reuse and authority boundaries without premature distributed architecture.
- Missing evidence: detailed Iran-OS production prompt runtime; deliberately excluded from generic semantics.
- Unresolved conflicts: none blocking the architecture boundary.

## 18. ADR Readiness

- Outcome: `READY_FOR_ADR`
- Proposed ADR title: Project-Agnostic Prompt Intelligence Core and Project Adapter Boundary
- Proposed ADR scope: shared mechanics, one-way dependency rule, adapter authority, ADR 0031 compatibility, persistence/replay boundaries, extraction criteria.
- Decisions the ADR must fix: ownership split, dependency direction, historical identity preservation, generic-core prohibited authority, extraction threshold.
- Matters the ADR must leave open: provider/model adapter, response validation, concrete Iran-OS implementation, standalone service timing.

## 19. Recommendation

Adopt Option 3: a project-agnostic Prompt Intelligence core with project-specific adapters. Initially place the core in the Project Hunter repository as a physically isolated package with a hard zero-import dependency on `hunter.*`. Treat this repository location as an incubation location, not Hunter ownership. Add a Hunter Evidence Intelligence adapter that preserves ADR 0031 identities and an eventual Iran-OS adapter in the Iran-OS repository. Extract the core into its own repository/package only after stable multi-consumer contracts and independent release/runtime needs are demonstrated.

## 20. Traceability

- Epic: not yet created
- Issue: #237
- Preparation record: ADPR-0007
- Checklist review: independent review pending
- ADR: planned ADR 0032
- Implementation plan: not authorized
- PR: not yet created
- Commit: created on `architecture/issue-237-generic-prompt-intelligence`
- Release: not applicable
- Supersedes: none
- Superseded by: none
