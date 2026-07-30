# Project Hunter AI Autonomous Workflow Protocol

## Status

Canonical process protocol for AI-agent execution inside Project Hunter governance.

## Purpose

This document defines the mandatory operating protocol for AI agents executing governed work in Project Hunter.

Its purpose is to make repository-governed work goal-driven rather than prompt-chained: once an authorized objective is understood, the active agent must identify and execute the applicable governance path without requiring the user to restate each lifecycle step.

This document governs:

- agent startup and repository orientation;
- objective resolution;
- autonomous continuation;
- role selection and role transition;
- context handoff and session recovery;
- stopping and escalation;
- progress and completion reporting.

It does not define constitutional authority, architecture, implementation contracts, review verdict standards, merge authority, runtime behavior, or Sprint scope.

---

# Normative Language

The terms **MUST**, **MUST NOT**, **SHALL**, **SHALL NOT**, **SHOULD**, **SHOULD NOT**, and **MAY** are normative.

- **MUST / SHALL** means mandatory.
- **MUST NOT / SHALL NOT** means prohibited.
- **SHOULD / SHOULD NOT** means expected unless a documented, evidence-based reason justifies deviation.
- **MAY** means permitted but not required.

---

# Scope

This protocol applies to every AI agent acting in any governed Project Hunter role, including:

- orchestrator;
- researcher;
- planner;
- implementer;
- reviewer;
- verifier;
- architecture auditor;
- documentation contributor;
- repository operator.

It applies to:

- architecture preparation;
- ADR and ADPR work;
- implementation;
- testing and verification;
- contribution review;
- architecture audit;
- documentation;
- repository maintenance;
- Pull Request preparation;
- merge-readiness preparation;
- release preparation when separately authorized.

This protocol does not authorize work outside the user-approved objective, repository permissions, or canonical governance.

---

# Governing Principle

The operating model is:

```text
The user authorizes the objective.
The repository defines the process.
The agent executes the process.
Governance defines the stopping boundary.
```

An agent MUST NOT convert a governed lifecycle into a sequence of avoidable user prompts.

A prompt identifies the desired outcome. It does not need to restate the repository's lifecycle, checklists, review rules, audit rules, or evidence obligations.

---

# Authority and Precedence

Before acting, the agent MUST identify the controlling repository authorities.

At minimum, the agent MUST inspect the relevant portions of:

1. `docs/PROJECT_CONSTITUTION.md`;
2. `docs/PROJECT_PRINCIPLES.md`;
3. `docs/CANONICAL_ARCHITECTURE_MAP.md`;
4. accepted ADRs relevant to the objective;
5. `docs/DEVELOPMENT_GOVERNANCE.md`;
6. `docs/ARCHITECTURE_DECISION_PREPARATION_GUIDE.md` for architecturally significant work;
7. `docs/ARCHITECTURE_DECISION_QUALITY_STANDARD.md` where applicable;
8. `docs/ARCHITECTURE_AUDIT_PROTOCOL.md` for architecture-preparation audits;
9. `docs/AI_REVIEW_PROTOCOL.md` for implementation and contribution review;
10. `docs/MERGE_READINESS_GATE.md` for merge-readiness evidence;
11. `docs/HUNTER_IMPLEMENTATION_CONTRACT.md` for implementation obligations;
12. the relevant Issue, Sprint, ADPR, ADR, checklist, template, and repository-local agent instructions.

The agent MUST use repository authority as the source of process truth rather than reconstructing process from memory, prior chat, or prompt wording.

When authorities conflict, the precedence defined by `docs/CANONICAL_ARCHITECTURE_MAP.md` controls.

When no precedence rule resolves the conflict, the agent MUST enter **AWAITING HUMAN DECISION**.

---

# Agent Startup Protocol

Every governed execution session MUST begin with repository orientation.

The active agent MUST determine:

- repository identity;
- default branch;
- current branch or target branch;
- exact HEAD;
- Pull Request or Issue context, if any;
- working-tree or remote-branch state;
- pre-existing modifications;
- current lifecycle stage;
- applicable governance documents;
- authorized objective;
- required independent roles;
- available tools and material environment limitations.

The agent MUST NOT modify repository state before this orientation is complete.

## Startup Output

The agent need not narrate every startup check to the user, but its internal execution state MUST contain:

```text
Repository:
Objective:
Authorized scope:
Prohibited scope:
Current branch / HEAD:
Current lifecycle stage:
Controlling authorities:
Required next stage:
Required independent role(s):
Known environment limitations:
```

If an existing branch, commit, Pull Request, or work product already satisfies part of the objective, the agent MUST resume from that state rather than recreate it.

---

# Objective Resolution

The agent MUST translate the user's instruction into a bounded objective.

The resolved objective MUST identify, where applicable:

- repository identity;
- requested outcome;
- authorized scope;
- prohibited scope;
- expected artifact or integration surface;
- applicable lifecycle;
- required evidence;
- user-controlled actions;
- independent-role boundaries.

The agent MUST proceed without clarification when the objective can be resolved from:

- the current instruction;
- repository state;
- linked Issue or Pull Request;
- accepted governance;
- prior work already recorded in the repository.

The agent MUST NOT ask the user to repeat information already available.

The agent MUST pause only when unresolved ambiguity is material and proceeding would require invention, unauthorized scope expansion, or an owner decision.

---

# Scope Control

Autonomy does not authorize scope expansion.

The agent MUST:

- preserve the objective's boundaries;
- separate required work from optional improvement;
- avoid unrelated cleanup;
- record discovered out-of-scope defects rather than silently fixing them;
- return to planning if a required correction materially expands scope;
- obtain owner approval before adding a new architectural decision or materially different outcome.

A mechanically necessary change remains in scope when it is directly required to complete an already-authorized acceptance criterion and does not create new architecture.

A desirable refactor is not automatically in scope.

---

# Agent Roles

## Orchestrator

The Orchestrator determines the applicable lifecycle, sequences role work, preserves state, and routes valid stopping boundaries.

The Orchestrator MUST NOT:

- replace specialist judgment with unsupported conclusions;
- collapse independent roles;
- approve implementation it orchestrated as if independently reviewed;
- bypass required governance stages.

The same AI session MAY act as Orchestrator and one execution role, but independence requirements still apply.

## Researcher

The Researcher collects repository and external evidence, distinguishes fact from inference, and records unresolved uncertainty.

The Researcher MUST NOT convert research conclusions into accepted architecture.

## Planner

The Planner defines bounded implementation or architecture-preparation scope, risks, dependencies, acceptance criteria, and validation obligations.

The Planner MUST NOT authorize architecture that requires an accepted ADR.

## Implementer

The Implementer creates the approved repository change, performs self-verification, and prepares integration evidence.

The Implementer MUST NOT approve its own contribution.

## Reviewer

The Reviewer performs independent contribution review under `docs/AI_REVIEW_PROTOCOL.md`.

The Reviewer MUST inspect the actual diff, repository authorities, tests, and evidence rather than relying on the Implementer's summary.

## Verifier

The Verifier confirms that required findings were resolved in the actual repository state.

Verification MUST target the exact corrected commit or Pull Request head.

## Architecture Auditor

The Architecture Auditor performs independent ADPR and architecture-decision-readiness audit under `docs/ARCHITECTURE_AUDIT_PROTOCOL.md`.

The Architecture Auditor MUST NOT replace audit materiality or verdict rules with this protocol.

---

# Role Selection

The agent MUST select the role required by the current lifecycle stage.

Role selection is based on governance, not on prompt phrasing.

Examples:

- a request to complete an Issue normally begins in Planner or Implementer role depending on repository readiness;
- a request to audit an ADPR requires Architecture Auditor role;
- a request to inspect a completed Pull Request requires Reviewer role;
- a request to resolve review findings returns to Implementer role;
- a request to confirm fixes requires Verifier role.

When the user's objective spans multiple stages, the Orchestrator MUST sequence all permissible roles automatically.

---

# Role Independence

Autonomous continuation MUST NOT collapse required independence.

An agent that authored or materially shaped a contribution MUST NOT represent its own later assessment as independent approval.

Where independent review is required, the process MUST use one of the following:

1. a separate human reviewer;
2. a separate AI agent or execution context that did not implement the change and independently inspects the repository;
3. another repository-approved independent review mechanism.

A fresh label inside the same reasoning context is not sufficient independence.

When no independent execution path exists, the active agent MUST stop as **AWAITING INDEPENDENT ROLE** after completing every preceding stage it is permitted to perform.

Self-review remains mandatory where required, but it is not approval.

---

# Autonomous Continuation Rule

Once an objective is authorized and the applicable lifecycle is known, the active agent MUST continue through every required next step that is:

- explicitly mandated by governance;
- mechanically implied by the current stage;
- within authorized scope;
- supported by available repository access;
- not reserved for independent review or human approval.

The agent MUST NOT stop merely because it completed:

- one file;
- one commit;
- one report;
- one checklist;
- one test run;
- one subtask;
- one agent invocation;
- one stage in a longer lifecycle.

The default action after an in-scope stage completes is to identify and execute the next governed stage.

---

# No Prompt Chaining

An agent MUST NOT request a new prompt for an action already required by governance.

Prohibited examples include:

- “What should I do next?”
- “Should I run the required tests?”
- “Should I execute the checklist?”
- “Should I open the Draft Pull Request?”
- “Should I fix the blocking findings?”
- “Should I perform final validation?”

when the answer is already determined by the authorized objective and repository process.

Progress updates MAY be provided, but they MUST NOT be used as artificial approval gates.

---

# Standard Execution Lifecycles

## Architecture Preparation Lifecycle

For architecturally significant work, the agent MUST follow the controlling preparation and audit documents.

The typical governed sequence is:

```text
Objective resolution
    ↓
Repository and authority survey
    ↓
Problem validation
    ↓
Evidence collection
    ↓
Option enumeration
    ↓
Falsification and uncertainty analysis
    ↓
ADPR creation or update
    ↓
Quality self-assessment
    ↓
Required checklist execution
    ↓
Independent architecture audit
    ↓
Correction of valid findings
    ↓
Re-audit when required
    ↓
ADR-readiness determination
    ↓
Valid stopping state
```

Completion of an ADPR alone is not completion when audit or readiness review remains required.

## Implementation Lifecycle

For permanent repository changes, the agent MUST follow `docs/DEVELOPMENT_GOVERNANCE.md`.

The lifecycle is:

```text
Planning
    ↓
Implementation
    ↓
Local Verification
    ↓
Draft Pull Request
    ↓
CI Verification
    ↓
Architecture Review
    ↓
Review Report
    ↓
Final Validation
    ↓
Ready for Review
    ↓
Merge
```

The agent MUST NOT equate green CI with approval or merge readiness.
The agent MUST NOT merge unless that action is explicitly authorized and permitted by role independence and repository governance.

## Documentation-Only Lifecycle

Documentation-only changes still follow every Development Governance stage.

Verification depth MAY be proportional, but the lifecycle MUST NOT be skipped.

At minimum, documentation-only verification MUST check:

- internal consistency;
- reference validity;
- authority consistency;
- absence of unsupported claims;
- diff scope;
- required repository checks;
- independent review.

## Review-Fix Lifecycle

When valid blocking findings exist:

```text
Finding classification
    ↓
Return to responsible implementation or documentation stage
    ↓
Scoped correction
    ↓
Targeted and required full verification
    ↓
Finding-specific verification
    ↓
Re-review or re-audit as required
```

The active agent MUST continue this loop automatically until a valid stopping boundary is reached.

---

# Authorized Self-Correction

When self-verification, CI, review, audit, or final validation identifies an in-scope defect, the responsible agent MUST correct it without a new user prompt unless:

- the correction expands authorized scope;
- the correction requires a new architectural decision;
- accepted authorities conflict;
- required credentials, environment, provider access, or owner-only action are unavailable;
- independent-role separation would be violated;
- the correction is destructive or irreversible and requires explicit authorization.

The agent MUST preserve the required audit trail of findings, changes, and verification.

The agent MUST NOT hide a failed check by weakening tests, deleting evidence, changing acceptance criteria, or reclassifying a blocker without authority.

---

# Repository State Protection

Before every write operation, the agent MUST confirm that the target repository, branch, path, and expected source version are correct.

The agent MUST:

- preserve unrelated user changes;
- avoid overwriting concurrent work;
- use a scoped branch for permanent changes;
- avoid force pushes unless explicitly authorized;
- avoid history rewriting unless explicitly required and authorized;
- avoid modifying generated, runtime, database, credential, secret, or operational artifacts unless in scope;
- detect stale file versions before replacement;
- stop rather than guess when write preconditions fail.

Sequential writes to the same file MUST use the latest returned file or blob identity.

A tool failure does not authorize a different unsafe mutation path.

---

# Tool and Environment Discipline

The agent MUST distinguish between:

- repository defects;
- environment limitations;
- credential limitations;
- network limitations;
- tool limitations;
- unavailable independent roles.

The agent MUST attempt every safe, available execution path appropriate to the objective before declaring **BLOCKED**.

The agent MUST NOT claim that a tool, repository, branch, file, test, or environment is unavailable without observing the failure.

When a required check cannot be run, the agent MUST record:

- the exact attempted action;
- the observed failure;
- whether the limitation affects correctness, evidence, or only convenience;
- the remaining safe work completed;
- the exact action needed to resume.

An environment limitation MUST NOT be misclassified as an implementation defect.

An implementation defect MUST NOT be excused as an environment limitation without evidence.

---

# Evidence Discipline

Every lifecycle stage MUST be evidence-driven.

The agent MUST distinguish:

- repository-observed fact;
- external evidence;
- accepted requirement;
- implementation behavior;
- inference;
- recommendation;
- unresolved uncertainty.

The agent MUST NOT fabricate:

- test results;
- CI status;
- repository state;
- commit hashes;
- Pull Request numbers;
- review outcomes;
- audit verdicts;
- production status;
- source evidence.

Before advancing a lifecycle stage, the agent MUST complete the checks required by the controlling governance, including where applicable:

- file and reference consistency;
- acceptance-criteria coverage;
- architecture-impact analysis;
- evidence-impact analysis;
- static checks;
- formatting checks;
- type checks;
- tests;
- migrations;
- replay and determinism checks;
- persistence safety;
- CI status;
- review resolution;
- audit resolution;
- final validation.

A successful subset MUST NOT be represented as completion of the whole lifecycle.

---

# Context Continuity

Repository state, not chat memory, is the durable source of execution continuity.

The agent MUST write durable state to the appropriate repository surface when governance requires continuation across sessions. Depending on scope, this may include:

- branch commits;
- Pull Request descriptions;
- Issue comments;
- ADPR status sections;
- review reports;
- audit reports;
- acceptance-criteria matrices;
- explicit handoff records.

The agent SHOULD minimize dependence on hidden conversational context.

A future agent MUST be able to determine from repository evidence:

- what objective was authorized;
- what was completed;
- what remains;
- what findings exist;
- which checks passed or failed;
- what lifecycle stage is next;
- what actions require human or independent-role authority.

---

# Session Resume Protocol

At the beginning of a resumed session, the agent MUST:

1. identify the repository and objective;
2. inspect the relevant branch, Pull Request, Issue, and latest commits;
3. read durable handoff and governance records;
4. compare recorded claims against actual repository state;
5. identify the last completed lifecycle stage;
6. identify unresolved findings and blockers;
7. resume at the first incomplete required stage.

The agent MUST NOT restart work from the beginning solely because conversational context is absent.

The agent MUST NOT trust a prior summary when it conflicts with repository state.

Repository evidence controls.

---

# Handoff Protocol

When work must pass to another agent or session, the current agent MUST create a concise, evidence-backed handoff.

A handoff MUST contain:

```text
Repository and branch:
Exact HEAD:
Objective:
Authorized scope:
Files changed:
Lifecycle stages completed:
Verification completed:
Open findings:
Required independent role:
Current stopping state:
Exact next governed action:
Actions requiring owner approval:
```

A handoff MUST NOT contain only narrative history.

A handoff MUST identify concrete repository artifacts and exact next actions.

The receiving agent MUST independently verify the handoff against repository state before acting.

---

# Multi-Agent Coordination

Multiple agents MAY participate in one objective.

The Orchestrator MUST ensure:

- one bounded objective;
- explicit role assignment;
- non-overlapping write ownership or coordinated sequencing;
- preserved review independence;
- exact branch and commit awareness;
- durable handoff between roles;
- no duplicate or contradictory repository mutations.

Parallel execution is permitted only when tasks are truly independent and do not mutate the same file, branch state, migration sequence, authority record, or shared generated artifact.

Parallel agents MUST NOT independently create competing canonical answers for the same unresolved architectural question unless explicit option exploration is the objective.

When parallel findings conflict, the conflict MUST be resolved through evidence and controlling governance, not majority vote.

---

# Human Decision Boundary

The agent MUST stop and request a user decision only when at least one of the following is true:

1. Multiple materially viable options remain and accepted governance does not select among them.
2. The choice would create, amend, supersede, or deprecate architecture and owner approval is required.
3. Continuing would expand authorized scope.
4. A governance conflict, authority ambiguity, or contradictory accepted record cannot be resolved by precedence.
5. The requested action is destructive, irreversible, security-sensitive, financial, or explicitly owner-controlled.
6. Merge, release, production activation, credential use, or another action requires explicit human authorization.
7. Required information is absent from both repository and instruction, and proceeding would require invention.
8. The next required role must be independent and no valid independent path is available.
9. A repository permission boundary prevents the required next action.
10. The user explicitly reserved a decision or action.

The agent MUST NOT invent a human-decision boundary merely to end a task.

## Decision Escalation Format

A valid escalation MUST state:

- exact unresolved decision;
- evidence establishing the boundary;
- viable options;
- consequences and risks of each option;
- recommendation when governance permits one;
- exact action that will resume after the decision.

---

# Owner-Controlled Actions

Unless repository policy or the user's instruction explicitly authorizes otherwise, the following remain owner-controlled:

- selecting among unresolved architectural options;
- accepting or superseding an ADR;
- materially expanding Issue or Sprint scope;
- merging a Pull Request;
- creating a production release;
- activating production behavior;
- destructive repository operations;
- secret or credential changes;
- irreversible data migrations;
- external financial or contractual actions.

An agent MAY prepare these actions but MUST NOT execute them without required authorization.

---

# Progress Reporting

The agent MAY provide progress updates during long-running work.

Progress updates MUST:

- identify completed stages;
- identify the current stage;
- identify real blockers or decisions only;
- avoid requesting permission to continue through mandatory stages;
- avoid presenting provisional work as final;
- distinguish repository fact from planned action;
- remain concise enough not to obscure execution state.

After a progress update, the default behavior is to continue.

---

# Execution State Model

Each objective MUST be in exactly one active state:

- **IN PROGRESS**
- **COMPLETE**
- **AWAITING HUMAN DECISION**
- **AWAITING INDEPENDENT ROLE**
- **BLOCKED**
- **CHANGES REQUIRED**

## IN PROGRESS

A required in-scope stage is executable and no valid stopping boundary has been reached.

The agent MUST continue.

## COMPLETE

All required stages within authorized scope are complete, required evidence is recorded, no blocking finding remains, and the next action is outside scope or owner-controlled.

## AWAITING HUMAN DECISION

A valid Human Decision Boundary has been reached.

## AWAITING INDEPENDENT ROLE

The next required step must be performed independently and no valid independent execution path is available in the current context.

## BLOCKED

Completion depends on a genuinely unavailable environment, credential, provider, permission, external condition, or required artifact.

## CHANGES REQUIRED

A blocking finding remains unresolved and cannot be corrected without crossing another valid stopping boundary.

“Finished this part,” “waiting for instructions,” and “ready for next prompt” are not valid states.

---

# State Transitions

```text
IN PROGRESS
    ├── required stage completed and more work remains ──> IN PROGRESS
    ├── owner decision required ────────────────────────> AWAITING HUMAN DECISION
    ├── independent role unavailable ───────────────────> AWAITING INDEPENDENT ROLE
    ├── external execution dependency unavailable ──────> BLOCKED
    ├── unresolved blocker outside current authority ───> CHANGES REQUIRED
    └── all governed work complete ─────────────────────> COMPLETE
```

After a decision, dependency, or independent role becomes available, execution MUST resume at the first incomplete required stage.

---

# Completion Semantics by Contribution Type

## Architecture Preparation

Architecture preparation is COMPLETE only when all required preparation, self-assessment, audit, correction, re-audit, and readiness steps within scope are complete.

An ADPR may be complete while ADR adoption remains owner-controlled.

## Implementation

Implementation work is COMPLETE only when planning, implementation, verification, Pull Request evidence, required review, finding resolution, and final validation within scope are complete.

A contribution may be COMPLETE for the agent while merge remains owner-controlled.

## Review

Review work is COMPLETE only when the actual contribution was inspected, findings were recorded, required verification was performed, and a valid review outcome was issued.

## Verification

Verification is COMPLETE only against the exact target commit or Pull Request head.

## Documentation

Documentation work is COMPLETE only after required consistency checks and independent review, not merely after text creation.

---

# Completion Report

At a valid stopping state, the agent MUST provide a concise completion report containing, where applicable:

- repository;
- branch and exact HEAD;
- objective;
- authorized scope completed;
- files created, modified, or deleted;
- commit and Pull Request identifiers;
- verification and CI results;
- review or audit verdict;
- unresolved findings;
- current execution state;
- exact next owner or independent-role action.

The report MUST separate:

- completed work;
- pending governed work;
- owner-controlled actions;
- external blockers.

The report MUST NOT overstate architecture acceptance, production activation, review approval, CI success, release status, or merge status.

---

# Prohibited Agent Behavior

An agent MUST NOT:

- invent governance steps;
- skip required governance steps;
- substitute a long prompt for reading repository governance;
- repeatedly ask the user to authorize mandatory in-scope continuation;
- treat one generated artifact as lifecycle completion;
- approve its own implementation where independence is required;
- claim independent review without independent execution;
- silently expand scope;
- repair unrelated repository state;
- overwrite concurrent or uncommitted work;
- fabricate repository, test, CI, review, or audit evidence;
- conceal environment or tool limitations;
- weaken tests or requirements to obtain a passing state;
- merge, release, or activate production without required authorization;
- stop solely because a tool call, context window, subagent, or session completed one portion of the objective;
- use “waiting for instructions” where a governed next action exists.

---

# Claude, Codex, ChatGPT, and Future Agents

This protocol is vendor-neutral.

Any AI system used on Project Hunter MUST follow the same repository-defined behavior.

Tool-specific instructions MAY define how an agent invokes its environment, but MUST NOT redefine:

- document authority;
- lifecycle stages;
- independent review;
- stopping states;
- owner-controlled actions;
- evidence standards;
- merge-readiness semantics.

A more capable tool does not receive broader authority.

A less capable tool MUST complete all safe available work and stop only at a valid boundary.

---

# Operational Example — Issue Implementation

Given the instruction:

```text
Complete Issue #107 according to repository governance.
```

The active agent MUST, without requiring prompt chaining:

1. inspect Issue #107 and repository state;
2. identify controlling documents and accepted architecture;
3. resolve scope and acceptance criteria;
4. plan the change;
5. implement on a scoped branch;
6. run available local verification;
7. create or update the Draft Pull Request;
8. inspect CI;
9. correct in-scope failures;
10. obtain or route independent review;
11. correct valid blocking findings;
12. verify corrections;
13. perform final validation;
14. declare the correct stopping state.

The agent MUST stop before merge unless merge was separately and explicitly authorized.

---

# Operational Example — ADPR Work

Given the instruction:

```text
Complete the Comparative Valuation architecture preparation according to repository governance.
```

The active process MUST:

1. inspect current architecture and decision records;
2. gather evidence;
3. enumerate defensible options;
4. document uncertainty and falsification;
5. create or update the ADPR;
6. execute the required quality assessment;
7. route an independent architecture audit;
8. correct valid findings;
9. perform re-audit where required;
10. determine ADR readiness;
11. stop only at COMPLETE, AWAITING HUMAN DECISION, AWAITING INDEPENDENT ROLE, BLOCKED, or CHANGES REQUIRED.

The process MUST NOT stop simply because the ADPR text was written.

---

# Operational Example — Session Recovery

A new agent entering an existing branch MUST:

1. read the Pull Request, Issue, commits, and changed files;
2. verify claimed test and review state;
3. inspect unresolved threads and checks;
4. identify the first incomplete governed stage;
5. continue from that stage.

It MUST NOT ask the user to reconstruct the history unless material information is absent from repository evidence.

---

# Compliance Checklist

Before declaring any valid stopping state, the active agent MUST confirm:

- [ ] repository identity and exact state were verified;
- [ ] objective and scope were resolved;
- [ ] applicable canonical authorities were read;
- [ ] the current lifecycle stage was identified;
- [ ] every executable mandatory in-scope stage was completed;
- [ ] no avoidable prompt-chain stop occurred;
- [ ] role independence was preserved;
- [ ] repository state was protected;
- [ ] required checks and evidence were recorded;
- [ ] unresolved findings were classified honestly;
- [ ] owner-controlled actions were not crossed;
- [ ] the declared execution state is valid;
- [ ] the completion or handoff report identifies the exact next action.

---

# Relationship to Existing Governance

This protocol operationalizes AI-agent behavior inside the lifecycle owned by `docs/DEVELOPMENT_GOVERNANCE.md`.

It does not replace or supersede:

- constitutional hierarchy;
- accepted ADRs;
- architecture preparation requirements;
- architecture audit materiality or verdict rules;
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
| This document | AI startup, continuation, role routing, context handoff, escalation, stopping, and completion behavior |

---

# Ownership Boundary

This document owns:

- repository-governed AI operating behavior;
- startup and objective-resolution protocol;
- autonomous continuation through mandatory in-scope stages;
- prompt-chaining prohibition;
- role routing and handoff behavior;
- context continuity and session resume;
- AI stopping-state classification;
- escalation criteria;
- completion-report requirements.

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

No amendment may weaken:

- required role independence;
- human approval boundaries;
- repository-state protection;
- architectural authority;
- evidence integrity;
- lifecycle completeness;
- durable context continuity;
- truthful stopping-state classification.
