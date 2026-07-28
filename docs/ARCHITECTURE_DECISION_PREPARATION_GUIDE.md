# Architecture Decision Preparation Guide

## Status

This document is a reference and implementation guide.

It does not define an independent governance authority, lifecycle, approval path, architectural authority, or review authority.

The mandatory Planning stage is owned by `docs/DEVELOPMENT_GOVERNANCE.md`. This guide elaborates only the preparation of significant architectural decisions within that stage.

Where this guide conflicts with `PROJECT_CONSTITUTION.md`, `PROJECT_PRINCIPLES.md`, `docs/DEVELOPMENT_GOVERNANCE.md`, an accepted ADR, or another canonical owner, the higher or canonical authority remains controlling.

---

## Purpose

Project Hunter depends on long-lived architectural decisions whose cost of correction grows rapidly after implementation, persistence, replay, migration, operationalization, and downstream analytical dependencies exist.

This guide standardizes how significant architectural decisions are prepared before implementation begins and, where applicable, before an ADR is drafted.

Its purposes are to:

- prevent solution-first planning;
- distinguish real architectural problems from local implementation symptoms;
- require traceable evidence for architectural claims;
- expose assumptions, uncertainty, conflicts, and missing information;
- discover constraints and architectural dimensions before option selection;
- ensure materially distinct options are considered on comparable terms;
- require deliberate attempts to falsify the preferred interpretation or option;
- improve the completeness, clarity, and auditability of ADRs and implementation plans;
- reduce avoidable architectural rework without imposing disproportionate process on routine changes.

This guide prepares decisions. It does not make or approve them.

---

## Scope

This guide applies to architectural decision preparation for proposed changes that are significant in scope, authority, persistence, replay, evidence, compatibility, operational risk, or long-term evolution.

Typical examples include:

- a new ADR or a material amendment to an accepted ADR;
- a new canonical analytical authority or evidence family;
- a change to ownership boundaries or source-of-truth rules;
- a new persistence, correction, replay, lineage, or historical reconstruction contract;
- a new cross-engine protocol or shared abstraction;
- a material change to canonical architecture, runtime architecture, governance, or implementation obligations;
- an architectural refactor affecting multiple bounded contexts;
- a decision whose failure would create costly migration, invalid historical output, duplicated authority, hidden correlation, or operational fragility;
- a major Epic whose implementation depends on unresolved architectural choices.

This guide normally does not apply to:

- routine bug fixes that preserve accepted behavior;
- narrow implementation changes fully determined by existing architecture and contracts;
- mechanical refactors with no boundary, authority, persistence, replay, or compatibility impact;
- test-only changes that do not alter production obligations;
- editorial documentation corrections;
- dependency updates with no architectural consequence.

A small change may still require this guide when its apparent size understates its authority, evidence, replay, or migration impact.

---

## Applicability Determination

During Stage 1 Planning, the planner determines whether architectural decision preparation is required.

Use this guide when one or more of the following conditions is true:

- the change requires a new architectural decision rather than application of an existing one;
- more than one materially distinct architecture appears plausible;
- the proposed change creates or moves canonical ownership;
- the change affects evidence authority, missingness, conflict, correction, provenance, strict-known replay, or deterministic reconstruction;
- the change introduces irreversible or expensive-to-reverse persistence or migration consequences;
- the change spans multiple architectural areas or bounded contexts;
- the change weakens, replaces, or qualifies an existing guarantee;
- the problem statement depends on uncertain, disputed, external, or incomplete evidence;
- implementation cannot be planned without first resolving architectural uncertainty;
- an independent reviewer would otherwise need to reconstruct why the architecture was chosen.

When applicability is uncertain, the planner records the uncertainty and uses the guide proportionally until the decision is resolved.

Not using this guide must never be used to bypass an ADR, governance requirement, architecture review, acceptance criterion, or operational validation that is required elsewhere.

---

## Relationship to Canonical Governance

### Project Constitution

`PROJECT_CONSTITUTION.md` remains the highest governing document.

Preparation performed under this guide must preserve, among other applicable rules:

- evidence authority;
- deterministic intelligence;
- explicit architectural boundaries and ownership;
- single canonical ownership;
- explainability;
- long-term integrity;
- the established governance hierarchy.

This guide cannot create an exception to a constitutional rule.

### Development Governance

`docs/DEVELOPMENT_GOVERNANCE.md` owns the development lifecycle and Stage 1 Planning.

This guide is subordinate to that ownership. It provides a proportional method for preparing significant architectural decisions inside Planning. It does not add a lifecycle stage, change stage order, or create a second planning authority.

Implementation begins only after the intended scope is understood and all architecture-blocking uncertainty has been resolved or explicitly declared unresolved under the governing process.

### Architecture Decision Records

ADRs own accepted architectural decisions within their applicable authority.

This guide may produce inputs to an ADR, but it does not replace the ADR process and does not grant decision authority to a preparation report.

A preparation outcome may conclude that:

- no new ADR is required because accepted architecture already governs the case;
- an existing ADR requires clarification or amendment;
- a new ADR is justified;
- the evidence is insufficient to make an architectural decision;
- the proposed change should not proceed.

The final ADR must state the accepted decision and authority in accordance with repository conventions. It must not treat an unaccepted preparation artifact as canonical architecture.

### AI Review Protocol

`docs/AI_REVIEW_PROTOCOL.md` owns independent review roles, responsibilities, reporting, and approval protocol.

This guide does not perform the post-implementation Architecture Review or approve a contribution.

Where independent critique is used during decision preparation, it is a planning-quality activity only. It does not replace the independent review required later in the development lifecycle.

### Merge Readiness

`docs/MERGE_READINESS_GATE.md` elaborates the Merge Readiness rule owned by Development Governance.

Decision-preparation evidence may support a later Pull Request, but it does not establish merge readiness. Code quality, acceptance criteria, operational validation, review, and the complete evidence package remain independently required where applicable.

---

## Preparation Principles

### Problem before solution

Preparation begins from a precise architectural problem, not from a preferred implementation, schema, service, provider, class, or technology.

A proposed solution may be recorded as a hypothesis, but it must not define the problem in terms that make itself inevitable.

### Default to existing governance and architecture

The initial hypothesis is that the repository's accepted architecture, ADRs, contracts, and governance are sufficient.

A new authority, abstraction, mechanism, document, record family, or workflow is justified only when a specific capability gap remains after serious attempts to use or extend the current owner.

### Evidence proportionality

Claims must be supported by evidence proportional to their importance and uncertainty.

Repository evidence is required for claims about current Hunter behavior, ownership, contracts, or gaps. External evidence is required when the decision depends on external systems, domain facts, standards, provider behavior, economic mechanisms, or scientific claims.

Evidence quantity does not substitute for relevance, independence, quality, or applicability.

### Preserve uncertainty

Unknown, missing, disputed, conditional, and conflicting information must remain explicit.

Preparation must not convert absence of evidence into evidence of absence, average unresolved conflicts into a synthetic answer, or silently choose a convenient interpretation.

### Implementation independence

Architectural requirements should be expressed independently of a specific implementation where possible.

Implementation details may be evaluated after the architectural dimensions, invariants, constraints, and authority boundaries are understood.

### Exhaustive material option coverage

All materially distinct options reasonably supported by the evidence must be identified before selection.

Options that differ only in naming, serialization, framework, or local implementation technique should not be presented as different architectures unless the distinction changes an architectural property.

### Falsification over confirmation

Preparation must actively seek evidence that the problem is misdiagnosed, the proposed mechanism is unnecessary, the preferred option violates a constraint, or an existing owner can absorb the requirement.

A decision is not ready merely because supporting arguments exist.

### No hidden selection

When the assigned task is investigation or option enumeration, the output must not silently rank, recommend, or select an option.

Selection occurs only when the authorized planning or ADR task explicitly requires it.

### Reproducibility

A qualified reviewer should be able to follow the evidence, assumptions, reasoning, option definitions, and falsification attempts and understand how the preparation outcome was reached.

---

## Required Preparation Activities

The depth of each activity is proportional to the decision's risk and complexity. Activities may be iterated, but none may be represented as complete when material uncertainty remains hidden.

### 1. Define the architectural question

State the question in implementation-independent terms.

Record:

- the observed problem or unmet capability;
- the affected architectural areas;
- why the question is architectural rather than purely local implementation work;
- what is explicitly outside scope;
- what decision is not yet being made;
- the consequences of leaving the question unresolved.

A valid question must be falsifiable enough that investigation could conclude that no architectural change is needed.

### 2. Validate that the problem exists

Attempt to prove that existing architecture and governance already solve the problem.

Inspect the relevant canonical documents, ADRs, contracts, runtime behavior, persistence, tests, operational procedures, and prior reviews.

Classify the result as one of:

- no architectural gap exists;
- an existing canonical owner is sufficient but under-specified;
- an existing owner can be extended without new authority;
- a genuinely unowned architectural capability remains;
- evidence is insufficient to determine whether a gap exists.

A new mechanism must not be proposed before this validation is complete.

### 3. Build the evidence base

Collect the evidence necessary to understand the question.

For each material item, record:

- source or repository location;
- claim supported;
- evidence type;
- authority or provenance;
- temporal applicability;
- limitations;
- conflicts with other evidence;
- whether it is observed, inferred, assumed, or proposed.

Repository summaries are not substitutes for direct inspection when the direct artifact is available.

### 4. Assess evidence quality

Evaluate whether the evidence is:

- relevant to the precise question;
- authoritative for the claim;
- independent where corroboration matters;
- current or historically applicable;
- reproducible;
- complete enough for the intended decision;
- affected by selection, survivorship, provider, implementation, or current-state bias.

Weak evidence may still be retained, but its weakness must constrain the conclusions drawn from it.

### 5. Discover constraints and invariants

Extract the conditions that every acceptable option must preserve.

Include, where applicable:

- constitutional constraints;
- canonical ownership boundaries;
- evidence authority and provenance;
- missingness and conflict semantics;
- deterministic reconstruction;
- strict-known historical replay;
- correction and lineage requirements;
- entity and representation scope;
- temporal semantics;
- compatibility and migration requirements;
- operational constraints;
- external domain invariants;
- economic or analytical invariants;
- prohibited correlations or duplicated evidence.

Distinguish unconditional constraints from assumptions and preferences.

### 6. Discover architectural dimensions

Identify the independent dimensions along which valid architectures may differ.

Dimensions should describe architectural properties rather than implementation artifacts.

Examples may include:

- authority location;
- evidence granularity;
- attribution basis;
- temporal model;
- correction model;
- conflict resolution authority;
- normalization boundary;
- calibration scope;
- persistence responsibility;
- replay semantics;
- compositionality;
- confidence representation;
- failure and missingness behavior.

Do not create a taxonomy before testing whether the proposed dimensions are necessary, independent, and sufficient to distinguish material alternatives.

### 7. Enumerate materially distinct options

Construct the option set from the validated dimensions and constraints.

Each option must state:

- its architectural rule;
- its canonical owner or ownership effect;
- the evidence it requires;
- the guarantees it provides;
- the guarantees it cannot provide;
- its behavior under missing, conflicting, corrected, and historical evidence;
- migration and compatibility implications;
- unresolved questions.

Include the option of making no architectural change when it remains viable.

Do not include an option that violates a fixed constitutional or accepted architectural requirement unless the explicit task is to evaluate an amendment to that authority.

### 8. Normalize options for comparison

Describe all options using the same comparison dimensions and terminology.

Do not compare one option at the architectural level and another at the class, schema, library, provider, or deployment level.

Separate:

- architectural properties;
- implementation consequences;
- operational consequences;
- evidence requirements;
- transition costs.

### 9. Falsify the problem interpretation and options

Perform explicit adversarial tests.

At minimum, attempt to determine:

- whether the stated problem is only a symptom;
- whether accepted architecture already implies a solution;
- whether extension of an existing owner is sufficient;
- whether an option depends on evidence that cannot exist or cannot be reproduced;
- whether an option creates duplicated or ambiguous authority;
- whether an option fails under missingness, conflict, correction, replay, or migration;
- whether current-state evidence has been substituted for point-in-time evidence;
- whether implementation convenience is being mistaken for architectural necessity;
- whether the option remains valid across representative domain cases rather than a single motivating example.

Record failed as well as successful falsification attempts.

### 10. Perform constitutional and governance checks

For each remaining option, verify consistency with the canonical authority hierarchy.

Explicitly examine:

- whether a canonical owner already exists;
- whether ownership would be duplicated or moved;
- whether a lower-level artifact would redefine a higher-level rule;
- whether a new lifecycle, gate, review authority, or approval state is being created;
- whether an ADR, constitutional amendment, governance amendment, or implementation-contract change would be required;
- whether the proposed artifact is authoritative or subordinate guidance.

Any unresolved ownership conflict blocks readiness.

### 11. Determine architecture-decision readiness

Preparation is ready for an architectural decision only when:

- the problem has been validated;
- scope and exclusions are explicit;
- material evidence and its limitations are recorded;
- fixed constraints and assumptions are distinguished;
- architectural dimensions are sufficiently complete;
- materially distinct viable options are enumerated on comparable terms;
- falsification attempts are documented;
- constitutional and governance conflicts are resolved;
- remaining uncertainty is explicit and acceptable for the decision being made;
- the required decision owner and artifact are identified.

When these conditions are not met, the outcome is not "choose the best available option." The outcome is that preparation remains incomplete, blocked, or unnecessary.

### 12. Determine ADR readiness

An ADR may be drafted when an architectural decision is required and the preparation record provides enough evidence to state:

- the decision context;
- the governing constraints;
- the options considered;
- the accepted decision;
- the reasons for acceptance;
- rejected alternatives and material consequences;
- unresolved risks and follow-up obligations;
- compatibility, migration, replay, persistence, evidence, and operational implications where applicable.

ADR readiness does not imply ADR acceptance.

---

## Preparation Outcomes

Every completed use of this guide records exactly one primary outcome:

- **NO ARCHITECTURAL GAP** — accepted architecture already governs the case;
- **CLARIFY EXISTING OWNER** — the capability exists but its canonical definition or guidance is under-specified;
- **EXTEND EXISTING OWNER** — a real gap exists, but the current canonical owner can absorb it without duplicated authority;
- **NEW ARCHITECTURAL DECISION REQUIRED** — a genuinely new decision is justified and must proceed through the appropriate ADR or canonical amendment process;
- **DO NOT PROCEED** — the proposed direction is unjustified, contradictory, or inferior to preserving the current architecture;
- **BLOCKED** — required evidence, authority clarification, environment, or external facts are unavailable;
- **INCOMPLETE** — material activities or falsification attempts remain unfinished.

The outcome must identify the next authorized action. It must not imply approval, implementation completion, or merge readiness.

---

## Minimum Preparation Record

For an applicable decision, the planning evidence should record, proportionally:

- architectural question;
- purpose and scope;
- governing documents and existing owners inspected;
- problem-validation result;
- evidence register with limitations and conflicts;
- constraints and invariants;
- architectural dimensions;
- normalized option set;
- falsification attempts and results;
- constitutional and governance analysis;
- unresolved uncertainty and risks;
- primary preparation outcome;
- required next artifact or action.

The record may exist in an Issue, planning document, ADR context section, or another repository-approved location. This guide does not create a mandatory new artifact type.

For high-risk decisions, a dedicated preparation report is preferred so that the evidence and option analysis remain independently reviewable.

---

## Proportionality

The guide must not turn routine development into unnecessary ceremony.

The depth of preparation scales with:

- architectural significance;
- number of affected owners or bounded contexts;
- irreversibility;
- migration cost;
- evidence uncertainty;
- historical and replay impact;
- operational risk;
- expected lifetime of the decision;
- difficulty of detecting an error after implementation.

A narrow decision may require a concise record. A new canonical analytical authority may require extensive evidence, option analysis, external validation, and multiple falsification rounds.

Proportionality reduces depth, not truthfulness. No record may claim that an activity was completed when it was omitted.

---

## Independence During Preparation

For high-risk or novel decisions, an independent critic should evaluate the preparation before option selection or ADR finalization.

The critic should test:

- whether the problem statement predetermines the answer;
- whether repository evidence was inspected directly;
- whether material options were omitted;
- whether dimensions are implementation-shaped;
- whether assumptions are presented as requirements;
- whether an existing owner was dismissed too quickly;
- whether falsification was genuine;
- whether the recommendation exceeds the evidence.

This planning critique is advisory unless another governing document assigns it formal authority. It does not replace the mandatory independent review of the resulting repository contribution.

---

## Prohibited Uses

This guide must not be used to:

- create a parallel development lifecycle;
- create a parallel architecture-review or approval process;
- bypass an ADR or canonical amendment;
- make a planning report authoritative architecture;
- justify implementation before the problem is validated;
- narrow acceptance criteria after implementation difficulties arise;
- convert environmental limitations into fabricated evidence;
- hide unresolved conflicts or missing evidence;
- present a preferred option as the only option by defining alternatives at unequal levels;
- redefine a concept owned by another canonical document;
- require the same preparation depth for every repository change.

---

## Relationship to Implementation

Implementation planning begins from the accepted architectural decision, not directly from an unaccepted preparation option.

The implementation plan should translate the accepted decision into:

- bounded scope;
- affected files and components;
- persistence and migration work;
- replay and correction obligations;
- test and verification requirements;
- operational validation;
- documentation updates;
- compatibility and rollback considerations;
- acceptance criteria.

If implementation reveals an architectural decision outside the accepted scope, work pauses and returns to Planning in accordance with `docs/DEVELOPMENT_GOVERNANCE.md`.

---

## Ownership Boundary

This document owns only:

- subordinate guidance for preparing significant architectural decisions during Stage 1 Planning;
- the preparation principles, activities, readiness criteria, and outcome vocabulary defined here.

This document does not own:

- constitutional rules;
- engineering principles;
- architecture or runtime behavior;
- canonical analytical or evidence authority;
- ADR acceptance;
- the development lifecycle;
- review roles or approval;
- implementation obligations;
- Sprint scope;
- merge readiness;
- operational procedures.

Those responsibilities remain with their respective canonical owners.

---

## Amendment

Changes to this guide follow the development lifecycle defined by `docs/DEVELOPMENT_GOVERNANCE.md`.

No amendment may create an independent governance authority or conflict with the repository's canonical authority hierarchy.
