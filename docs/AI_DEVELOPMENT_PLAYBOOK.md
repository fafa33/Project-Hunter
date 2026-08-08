# Project Hunter AI Development Playbook

## Purpose

This playbook defines the working protocol for Claude, Codex, and any future AI agent participating in significant Project Hunter changes.

It is subordinate to the Project Constitution, Project Principles, Canonical Architecture Map, accepted ADRs, Development Governance, AI Review Protocol, Architecture Audit Protocol, and Implementation Contract. When this playbook conflicts with a higher-authority document, the higher-authority document governs.

This playbook does not grant merge authority, architectural authority, or permission to bypass repository governance.

## Required Roles

### Implementer

The Implementer plans and changes code within the approved scope.

The Implementer must:

- verify repository identity and current governance before acting;
- state scope and non-goals;
- preserve architectural authority boundaries;
- add proportionate tests and documentation;
- report failures, limitations, and incomplete verification honestly;
- avoid approving its own implementation.

### Automated Governance

Automated Governance executes deterministic checks and governed model-assisted review.

It must:

- fail closed when required evidence or execution is incomplete;
- expose findings and rationale;
- preserve the exact reviewed source/base identity;
- never silently convert missing coverage into approval;
- remain subordinate to canonical policy.

### Independent Reviewer

The Independent Reviewer must not be the agent that implemented the reviewed change.

The Independent Reviewer must:

- review implementation and Technical Defense independently;
- treat implementer claims as untrusted until verified;
- identify false-approval paths, missing evidence, authority violations, replay gaps, and scope errors;
- separate current merge blockers from future architectural improvements;
- make no code changes during review unless explicitly reassigned as a later Implementer.

### Final Repository Authority

The repository owner or explicitly authorized human authority decides whether architectural evidence is sufficient and whether merge may proceed.

Human authority must not bypass failed governance merely by opinion. The defect must be resolved or the governing rule must be formally changed through the governed process.

## HDM Workflow

For significant changes, agents must follow ADR 0029:

1. Architecture
2. Design
3. Implementation
4. Verification
5. Technical Defense
6. Independent Review
7. Architecture Review
8. Knowledge Extraction
9. Canonical Integration
10. Merge

The workflow must be scaled proportionately to risk. Authority, persistence, replay, governance, security, model-runtime, and canonical architecture changes require the full workflow unless higher-authority governance explicitly permits otherwise.

## Technical Defense Artifact

After implementation and verification, the Implementer must produce a durable Technical Defense in the PR description, PR comment, or governed repository document.

It must include:

- root cause;
- selected design and rejected alternatives;
- provider/model assumptions when applicable;
- verification evidence;
- remaining temporary, medium-term, and long-term debt;
- known false-positive and false-negative risks;
- incomplete live verification;
- recommended independent-review focus;
- a statement that the Implementer is not approving its own work.

Private chat output alone is not sufficient.

## Independent Review Rules

The Independent Reviewer must not approve solely because:

- tests pass;
- CI is green;
- code is well formatted;
- the Implementer reports success;
- the change is already large or expensive to revise.

Approval requires evidence that the contribution is safe within its claimed scope.

Every review finding must be classified as one of:

- Merge Blocker — leaving it unresolved makes the contribution unsafe or materially contradicts its claimed behavior.
- Required Follow-up — needed to complete the claimed operating mode but may depend on an external action or separately governed cutover.
- Future Architecture — valid improvement that exceeds the current contribution's bounded mission.
- Non-blocking Improvement — improves quality without making the current contribution unsafe.

A reviewer must not turn an unrelated future platform ambition into a blocker. An implementer must not relabel a false-approval or authority defect as future work merely to obtain merge.

## Scope Control

Before adding work to an existing PR, answer:

- Is the finding a defect against the PR's explicit claim?
- Can it produce false approval, data loss, authority violation, unsafe persistence, non-replayable decisions, or misleading evidence?
- Does fixing it require a new canonical owner, new persistence family, new runtime, or substantially different milestone?

Defects against current claims stay in the PR. New reusable platforms and broader architecture move to separately scoped ADRs, issues, and PRs unless required to eliminate an immediate unsafe path.

## Model-Facing Work

Until the AI Intelligence architecture in ADR 0030 is implemented, agents must avoid presenting ad hoc prompt truncation as Prompt Intelligence.

Model-facing implementations must state explicitly:

- what evidence was available;
- what evidence was selected;
- what evidence was omitted and why;
- provider and model identity;
- budget and completion limits;
- coverage status;
- whether replay is exact, partial, or unavailable.

Any required but unreviewed evidence must remain explicit. It must not be represented as reviewed.

## Repair Workflow

Automated findings may be delegated to an implementation agent only after scope and authority are established.

A repair task must specify:

- exact finding;
- affected authority or contract;
- allowed files or subsystem boundary where practical;
- required tests and evidence;
- prohibited scope expansion;
- whether human approval is required before implementation.

The same agent may implement a repair, but may not perform the independent approval of that repair.

## Knowledge Extraction

After a significant review cycle, capture:

- the original failure;
- why earlier checks missed it;
- the architectural principle discovered;
- reusable components created or proposed;
- debt intentionally deferred;
- required ADR, issue, roadmap, or canonical-document updates.

PR #200 is the founding case study: the governance capability was reviewed by the governance it introduced, rejected, repaired, and independently challenged. The durable principle is:

> No implementation is exempt from the governance it introduces.

## Merge Conditions

A significant PR is ready to merge only when:

- required tests and verification pass;
- merge-blocking findings are resolved;
- Technical Defense is durable and complete;
- independent review is complete;
- required canonical integration is complete;
- external enforcement required for the claimed operating mode is enabled or the contribution is explicitly documented as not yet operational;
- the final repository authority authorizes merge.

Merge closes the current learning cycle. It does not erase deferred debt or authorize unimplemented future architecture.
