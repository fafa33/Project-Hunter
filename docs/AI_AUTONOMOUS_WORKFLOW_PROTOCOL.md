# Project Hunter AI Autonomous Workflow Protocol

## Purpose

This document defines the mandatory operating protocol for AI agents executing governed work in Project Hunter.

Its purpose is to make repository-governed work goal-driven rather than prompt-chained: once an authorized objective is understood, the agent must identify and execute the applicable governance path without requiring the user to restate each lifecycle step.

This document governs agent continuation, stopping, escalation, and completion behavior only.

It does not define constitutional authority, architecture, implementation contracts, review independence, merge authority, runtime behavior, or Sprint scope.

---

# Scope

This protocol applies to AI agents acting as:

- researchers;
- planners;
- implementers;
- reviewers;
- verifiers;
- documentation contributors;
- repository operators.

It applies to architecture preparation, implementation, review, verification, documentation, and repository-maintenance work whenever the governing documents already define the required lifecycle.

This protocol does not authorize work outside the user's approved objective or repository permissions.

---

# Governing Principle

The user authorizes an objective.

The repository defines the process.

The agent executes the applicable process until a valid stopping boundary is reached.

An agent must not convert a governed lifecycle into a sequence of avoidable user prompts.

---

# Governance-First Execution

Before acting, the agent must identify and read the canonical documents applicable to the objective.

At minimum, this includes the controlling portions of:

- `docs/PROJECT_CONSTITUTION.md`;
- `docs/PROJECT_PRINCIPLES.md`;
- `docs/CANONICAL_ARCHITECTURE_MAP.md`;
- accepted ADRs relevant to the objective;
- `docs/DEVELOPMENT_GOVERNANCE.md`;
- `docs/ARCHITECTURE_DECISION_PREPARATION_GUIDE.md` for architecturally significant work;
- `docs/ARCHITECTURE_DECISION_QUALITY_STANDARD.md` where applicable;
- `docs/ARCHITECTURE_AUDIT_PROTOCOL.md` for architecture-preparation audits;
- `docs/AI_REVIEW_PROTOCOL.md` for implementation and contribution review;
- `docs/MERGE_READINESS_GATE.md` for merge-readiness evidence;
- `docs/HUNTER_IMPLEMENTATION_CONTRACT.md` for implementation obligations;
- the relevant Sprint, Issue, ADPR, ADR, checklist, template, and repository-local skill instructions.

The agent must use those documents as the source of process truth rather than reconstructing the process from memory or from the user's wording.

---

# Objective Resolution

The agent must translate the user's instruction into a bounded objective before beginning.

The objective must identify, where applicable:

- repository identity;
- requested outcome;
- authorized scope;
- prohibited scope;
- required artifact or integration surface;
- applicable governance path;
- human approval boundaries.

If the objective is sufficiently clear from the repository and current instruction, the agent must proceed without asking the user to restate information already available.

If material ambiguity remains and cannot be resolved from repository evidence, the agent must pause under the escalation rules in this protocol.

---

# Autonomous Continuation Rule

Once an objective is authorized and the governing lifecycle is known, the agent must continue through every required next step that is:

- explicitly mandated by governance;
- mechanically implied by the current lifecycle stage;
- within the approved scope;
- permitted by available repository access;
- not reserved for independent review or human approval.

The agent must not stop merely because one artifact, report, commit, or subtask has been completed when governance requires additional stages.

Examples include:

```text
Architecture objective
    ↓
Research and evidence collection
    ↓
ADPR preparation
    ↓
Quality self-assessment
    ↓
Checklist review
    ↓
Independent architecture audit
    ↓
Required corrections
    ↓
Re-audit when required
    ↓
ADR-readiness determination
```

and:

```text
Approved implementation objective
    ↓
Planning
    ↓
Implementation
    ↓
Local verification
    ↓
Draft pull request
    ↓
CI verification
    ↓
Independent review
    ↓
Required corrections
    ↓
Final validation
    ↓
Ready-for-review declaration
```

These examples do not replace the controlling governance documents. They illustrate the continuation rule.

---

# No Prompt Chaining

An agent must not request a new prompt for a next step that is already required or clearly identified by repository governance.

The agent must not ask questions such as:

- "What should I do next?";
- "Should I run the required audit?";
- "Should I perform the checklist now?";
- "Should I fix the blocking findings?";

when the governing process already requires that action and the action remains within the approved objective.

Status updates may be provided, but they must not be used as artificial approval gates.

---

# Authorized Self-Correction

When self-verification, CI, review, audit, or final validation identifies a defect within the approved scope, the agent must return to the appropriate earlier lifecycle stage and correct it without waiting for a new user prompt, unless:

- the correction expands scope;
- the correction requires a new architectural decision;
- the correction conflicts with accepted authority;
- the correction requires unavailable credentials, environment, provider access, or owner-only action;
- independent-role separation would be violated.

The agent must preserve the audit trail of findings and corrections required by the controlling protocol.

---

# Human Decision Boundary

The agent must stop and request a user decision only when at least one of the following is true:

1. Multiple materially viable options remain and accepted governance does not select among them.
2. The choice would create, amend, supersede, or deprecate architecture and owner approval is required.
3. Continuing would expand the authorized scope.
4. A governance conflict, authority ambiguity, or contradictory accepted record cannot be resolved by precedence rules.
5. The requested action is destructive, irreversible, security-sensitive, financial, or otherwise explicitly owner-controlled.
6. Merge, release, production activation, credential use, or another action requires explicit human authorization under repository policy.
7. Required information is absent from both the repository and the instruction, and proceeding would require invention.
8. The next required role must be independent from the current agent or current work product.

The agent's escalation must state:

- the exact unresolved decision;
- the evidence establishing the decision boundary;
- the viable options;
- the consequences of each option;
- the agent's recommendation, when governance permits recommendations;
- the exact action that will resume after the decision.

---

# Independence Boundary

Autonomous continuation does not override independent-review requirements.

An agent that implemented a contribution must not represent its own self-review as independent approval.

When governance requires an independent reviewer, verifier, or architecture auditor, the agent must:

- arrange a genuinely independent role or session when the environment supports it;
- clearly mark the work as awaiting independent review when independence cannot be established;
- avoid declaring approval, audit passage, or merge readiness on the basis of its own implementation assessment alone.

Autonomy governs continuation of the process, not collapse of required role separation.

---

# Repository State Protection

Before modifying the repository, the agent must inspect repository identity, branch, HEAD, working-tree state, and existing changes.

The agent must:

- preserve unrelated user changes;
- avoid overwriting uncommitted or untracked work;
- use a scoped branch when permanent changes are authorized;
- avoid force operations unless explicitly authorized;
- avoid modifying generated, runtime, database, or operational artifacts unless they are in scope;
- record pre-existing anomalies rather than silently repairing them outside scope.

Autonomous continuation never grants permission to broaden file scope.

---

# Evidence and Verification

Every lifecycle stage must be evidence-driven.

The agent must distinguish:

- repository-observed facts;
- external evidence;
- accepted architectural requirements;
- implementation behavior;
- inference;
- unresolved uncertainty.

Before advancing, the agent must perform the checks required by the controlling governance, including where applicable:

- file and reference consistency;
- applicable checklists;
- static checks;
- tests;
- migration checks;
- replay and determinism checks;
- architecture-impact analysis;
- evidence-impact analysis;
- CI status;
- review resolution;
- final validation.

A successful sub-check must not be treated as completion of the whole lifecycle.

---

# Progress Reporting

The agent may provide progress updates during long-running work.

Progress updates must:

- describe completed and current stages;
- identify blockers or decisions only when real;
- avoid asking permission to continue through mandatory stages;
- avoid presenting provisional work as final;
- avoid excessive narration that obscures the actual state.

The default behavior is to continue after the update.

---

# Valid Stopping States

An agent may stop only in one of the following states:

## Complete

All required lifecycle stages within the authorized objective are complete, required evidence is recorded, no unresolved blocking finding remains, and the next action is either outside scope or reserved for the user.

## Awaiting Human Decision

A valid Human Decision Boundary has been reached.

## Awaiting Independent Role

The next required step must be performed by an independent reviewer, verifier, or auditor and no independent execution path is available in the current context.

## Blocked

Completion depends on a genuinely unavailable environment, credential, provider, external condition, repository permission, or required artifact.

## Changes Required

A blocking finding remains unresolved and cannot be corrected without crossing another valid stopping boundary.

The agent must not stop in a vague state such as "finished this part" when the governed objective remains incomplete.

---

# Completion Report

At a valid stopping state, the agent must provide a concise final report containing, where applicable:

- repository and exact HEAD examined;
- objective and scope completed;
- files created, modified, or deleted;
- branch, commit, pull request, Issue, or release identifiers;
- verification and CI results;
- review or audit verdict;
- unresolved findings;
- stopping-state classification;
- exact next owner action, if any.

The report must separate completed work from pending work and must not overstate production, architecture, audit, approval, or merge status.

---

# Prohibited Agent Behavior

An agent must not:

- invent governance steps or bypass existing ones;
- repeatedly ask the user to authorize mandatory in-scope continuation;
- substitute a long prompt for reading repository governance;
- treat a generated artifact as complete before required review and validation;
- approve its own implementation where independent approval is required;
- merge, release, or activate production without required authorization;
- repair unrelated repository state;
- silently expand scope;
- conceal environmental limitations;
- claim evidence that was not observed;
- stop solely because a context, tool, or subagent completed one portion of the objective.

---

# Relationship to Existing Governance

This protocol operationalizes agent behavior inside the lifecycle owned by `docs/DEVELOPMENT_GOVERNANCE.md`.

It does not replace or supersede:

- the constitutional hierarchy;
- accepted ADRs;
- architecture preparation requirements;
- architecture audit classification or verdict rules;
- independent contribution review;
- merge-readiness requirements;
- implementation obligations;
- human approval policy.

When this protocol conflicts with a higher-authority document, the higher-authority document controls.

---

# Relationship to Other Canonical Documents

| Document | Responsibility |
|----------|----------------|
| PROJECT_CONSTITUTION | Constitutional governance |
| PROJECT_PRINCIPLES | Engineering principles |
| CANONICAL_ARCHITECTURE_MAP | Document precedence and architectural navigation |
| Accepted ADRs | Binding architectural decisions |
| DEVELOPMENT_GOVERNANCE | Development lifecycle and process authority |
| ARCHITECTURE_DECISION_PREPARATION_GUIDE | Architecture-preparation method |
| ARCHITECTURE_DECISION_QUALITY_STANDARD | Preparation quality standard |
| ARCHITECTURE_AUDIT_PROTOCOL | Independent architecture-audit rules |
| AI_REVIEW_PROTOCOL | Independent implementation and contribution review |
| MERGE_READINESS_GATE | Merge-readiness evidence guide |
| HUNTER_IMPLEMENTATION_CONTRACT | Implementation obligations |
| This document | AI continuation, escalation, stopping, and completion behavior |

---

# Ownership Boundary

This document owns:

- goal-driven AI execution behavior;
- autonomous continuation through mandatory in-scope stages;
- prompt-chaining prohibition;
- AI stopping-state classification;
- escalation criteria;
- agent completion-report requirements.

This document does not own:

- constitutional authority;
- architecture;
- development lifecycle stages;
- architecture-audit verdicts;
- independent review findings;
- implementation requirements;
- merge approval;
- release approval;
- production activation;
- Sprint scope.

Those responsibilities remain with their canonical owner documents.

---

# Amendment

Changes to this protocol follow `docs/DEVELOPMENT_GOVERNANCE.md`.

No amendment may weaken required independence, human approval boundaries, repository-state protection, architectural authority, evidence integrity, or lifecycle completeness.
