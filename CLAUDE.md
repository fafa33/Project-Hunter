# Claude Code Instructions

## Mandatory Operating Protocol

For every Project Hunter task, Claude Code MUST follow:

`docs/AI_AUTONOMOUS_WORKFLOW_PROTOCOL.md`

The user authorizes the objective. Repository governance defines the process. Claude MUST continue through every mandatory, in-scope, executable governance stage without waiting for a new prompt.

Claude MUST NOT stop with statements such as:

- "What should I do next?"
- "Should I continue?"
- "Waiting for instructions."
- "I completed this part."

when repository governance already defines the next action.

Claude may stop only in one of the protocol's valid stopping execution states:

- **COMPLETE**
- **AWAITING HUMAN DECISION**
- **AWAITING INDEPENDENT ROLE**
- **BLOCKED**
- **CHANGES REQUIRED**

These execution states are coordination metadata only. They do not replace the canonical Pull Request declarations in `docs/DEVELOPMENT_GOVERNANCE.md`, including **Ready for Review**, and they do not replace review or audit verdicts.

Before stopping, Claude MUST complete every preceding stage that is safe, available, in scope, and permitted by role independence.

## Session Startup and Resume

At the start of every session, Claude MUST:

1. verify repository identity, branch, HEAD, and current work state;
2. identify the user-authorized objective and scope;
3. load the applicable canonical governance documents;
4. inspect any relevant Issue, Pull Request, ADPR, ADR, review, audit, and CI state;
5. determine the first incomplete required lifecycle stage;
6. continue from that stage.

Claude MUST treat repository state as the durable source of truth. Previous chat summaries are secondary and must not override actual repository evidence.

When resuming existing work, Claude MUST NOT restart from the beginning or ask the user to reconstruct history when the branch, commits, Pull Request, Issue, or durable handoff already contains the required information.

## Repository Authority

Before performing implementation, architecture analysis, ADR review, ADPR review, governance analysis, or repository review:

1. Load and follow the repository's latest accepted canonical governance documents.
2. Treat accepted canonical documents as the highest authority.
3. Never invent review criteria when canonical governance already defines them.
4. Follow the latest accepted governance rather than previous conversation context.
5. If multiple canonical documents apply, respect the repository's documented authority hierarchy.

At minimum, Claude MUST inspect the relevant portions of:

- `docs/PROJECT_CONSTITUTION.md`;
- `docs/PROJECT_PRINCIPLES.md`;
- `docs/CANONICAL_ARCHITECTURE_MAP.md`;
- applicable accepted ADRs;
- `docs/DEVELOPMENT_GOVERNANCE.md`;
- `docs/AI_AUTONOMOUS_WORKFLOW_PROTOCOL.md`;
- the applicable architecture-preparation, audit, review, implementation, merge-readiness, Sprint, Issue, checklist, and template documents.

## Architecture Reviews

Architecture reviews must:

- follow `docs/ARCHITECTURE_AUDIT_PROTOCOL.md`;
- use `docs/ARCHITECTURE_AUDIT_TEMPLATE.md` where an audit report is required;
- distinguish editorial and documentation-quality findings from decision-blocking and fundamental architecture findings;
- evaluate materiality and decision consequence before assigning a verdict;
- never use raw issue counts or simple PASS/FAIL totals as the basis for readiness;
- apply targeted re-audit rules after revisions unless the architecture scope materially changes.

Completing an ADPR is not the end of the objective when required quality assessment, independent audit, correction, re-audit, or readiness determination remains.

## Decision Preparation

For architecturally significant work:

- follow `docs/ARCHITECTURE_DECISION_PREPARATION_GUIDE.md`;
- assess quality using `docs/ARCHITECTURE_DECISION_QUALITY_STANDARD.md`;
- keep evidence, assumptions, unresolved conflicts, and missing information explicit;
- do not begin implementation while a material architectural decision remains unresolved;
- continue automatically through every preparation stage allowed by scope and role independence.

## Architecture Readiness Gate

Before planning or writing any production code, Claude MUST verify from repository evidence that:

1. the requested work is architecturally authorized;
2. every required production dependency already exists;
3. no unresolved implementation-critical ADR decision remains;
4. implementation will not require inventing new authorities, registries, methodologies, semantics, ownership boundaries, persistence rules, or replay rules;
5. all required ownership boundaries already exist and are singular.

If any answer is **NO** or **UNCERTAIN**, Claude MUST stop implementation work and perform an architecture investigation instead.

Claude MUST NOT write production code, reduce scope, invent temporary implementations, fabricate missing authorities, or open an implementation PR while architecture readiness is unproven.

If repository evidence proves implementation cannot proceed, Claude MUST produce a formal **BLOCKED** report with concrete repository, ADR, ownership, and dependency evidence.

## Mandatory Pre-PR Architecture Audit

Before creating any commit, Draft PR, implementation report, or completion report, Claude MUST perform a complete hostile architecture audit and assume the proposed work is incorrect until repository evidence proves otherwise.

### Cross-ADR consistency

Claude MUST review every Accepted ADR, not only the ADR being modified, and search for:

- hidden contradictions;
- ownership conflicts;
- replay conflicts;
- governance conflicts;
- activation conflicts;
- required ADR amendments.

Claude MUST never silently reinterpret accepted ADR text. If accepted ADR meaning must change, Claude MUST stop and prepare the required amendment first.

### Ownership proof

For every canonical concept, Claude MUST identify exactly one:

- canonical owner;
- service owner;
- repository owner;
- persistence owner;
- governance owner.

Claude MUST reject split ownership, shared ownership, ownership inversion, and downstream-owned canonical records.

### Architecture completeness

Claude MUST search for unresolved implementation-critical decisions, including:

- `TBD`;
- `future issue`;
- `implementation will decide`;
- unresolved alternatives.

If implementation depends on any such decision, Claude MUST declare that the architecture is not implementation-ready and stop.

### Runtime dependency audit

For every dependency, Claude MUST prove production existence of:

- service;
- repository;
- persistence;
- strict-known replay;
- composition root.

Test doubles never satisfy production readiness. If a required dependency exists only in tests, Claude MUST stop and report **BLOCKED**.

### Replay and record-identity audit

For every new record family, Claude MUST verify and document:

- immutable identity;
- logical identity;
- uniqueness;
- schema version;
- semantic version;
- effective/recorded/known coordinates where applicable;
- provenance;
- correction lineage;
- supersession;
- replay identity;
- replay selection;
- strict-known reconstruction.

Claude MUST not assume ADR compliance; it must demonstrate it with repository evidence.

### Governance audit

Claude MUST verify that the work does not invent:

- authorities;
- methodologies;
- registries;
- semantics;
- eligibility;
- compatibility;
- classifications.

Every such decision must originate from an existing governed production source or an accepted architecture decision.

### Dependency direction

Claude MUST verify:

- no circular ownership;
- no dependency inversion;
- no downstream-owned canonical types;
- no ownership leakage.

### Documentation and evidence audit

Before opening a PR, Claude MUST verify:

- SHAs refer to the correct code-bearing commit;
- CI claims match the referenced commit;
- test counts are current;
- implementation reports match the final branch state;
- PR descriptions match the final branch state;
- no wording becomes stale merely because a documentation-only commit is added later.

### Simulated hostile review

Before opening a PR, Claude MUST attempt to reject its own work.

Claude MUST identify the strongest materially plausible architecture findings an independent reviewer could raise. For each finding, Claude MUST:

1. attempt to reproduce it from repository evidence;
2. fix it if valid;
3. explain why it is unsupported if invalid, with repository evidence.

Claude MUST continue until it cannot identify additional materially supported findings.

### Final decision gate

Claude MUST choose exactly one outcome:

- **IMPLEMENTATION READY**
- **ARCHITECTURALLY BLOCKED**

`IMPLEMENTATION READY` may be declared only when repository evidence supports every implementation-critical architectural decision and no unresolved material choice remains.

If `ARCHITECTURALLY BLOCKED`, Claude MUST:

- stop;
- explain the blocker;
- avoid production code;
- avoid scope reduction;
- avoid inventing missing architecture;
- avoid opening an implementation PR unless the task is architecture preparation only.

## Implementation and Review Boundaries

- Implementation must not redefine architecture.
- Review must not invent new architecture or substitute reviewer preference for canonical requirements.
- If requested work conflicts with accepted governance, canonical architecture, or an accepted ADR, stop and report the conflict under the protocol's decision-boundary rules.
- Do not silently expand scope, weaken evidence requirements, bypass replay or provenance obligations, or treat unavailable evidence as neutral or successful.
- An implementer must not approve its own implementation.
- A fresh role label inside the same implementation context does not establish independent review.

## Development Lifecycle

All permanent repository changes must follow `docs/DEVELOPMENT_GOVERNANCE.md` and applicable review and merge-readiness documents.

Claude MUST automatically proceed through the applicable sequence, including planning, implementation, local verification, Draft Pull Request preparation, CI inspection, correction of in-scope failures, independent-review routing, finding resolution, verification, and final validation.

Claude MUST preserve the canonical Pull Request declaration required by `docs/DEVELOPMENT_GOVERNANCE.md`, including **Ready for Review** when its conditions are satisfied.

Whether Claude may merge, release, activate production, expand scope, use credentials, or perform a destructive action is determined only by the controlling repository governance, permissions, and explicit authorization applicable to that action. These are Claude operating constraints, not a new repository-wide authority policy.

## Self-Correction

When tests, CI, review, audit, or verification identify an in-scope defect, Claude MUST correct it and rerun the required checks without asking for another prompt, unless doing so would:

- expand scope;
- require a new architectural decision;
- violate independent-role separation;
- require unavailable credentials, permissions, environment, or provider access;
- cross a restriction imposed by controlling governance, repository permissions, or explicit user instruction.

Claude MUST NOT weaken tests, evidence requirements, acceptance criteria, or governance to produce a passing result.

## Handoff and Completion

When work must pass to another session or independent agent, Claude MUST leave a durable, evidence-backed handoff containing:

- repository and branch;
- exact HEAD;
- objective and authorized scope;
- files changed;
- lifecycle stages completed;
- verification performed;
- open findings;
- current protocol execution state;
- canonical PR, review, or audit declaration where applicable;
- exact next governed action.

Before reporting completion, Claude MUST verify the actual repository state and clearly distinguish implemented, tested, reviewed, approved, blocked, unavailable, Ready for Review, merged, released, and production-active outcomes.
