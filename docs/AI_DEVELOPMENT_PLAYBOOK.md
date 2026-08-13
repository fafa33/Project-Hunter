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

## Design Notes for State-Coupled Changes

HDM stage 2 (Design) is not satisfied by starting to write code. For any change
that involves concurrency, asynchronous external state, retries, reconciliation,
persistence, replay, event ordering, distributed state, external API semantics,
or a governance decision, the Implementer must first write a short design note
answering these ten questions concretely:

1. What is the authoritative state?
2. What state is derived from it?
3. What can race?
4. What can become stale?
5. What must remain true under arbitrary event ordering?
6. What external system semantics are being assumed?
7. What happens after partial failure?
8. Which operations must be idempotent?
9. How does the design converge?
10. What simpler design would remove the race rather than shrink its window?

Concrete answers are required. "Handled by retry" and "unlikely in practice" are
not answers to questions 3, 4, or 7.

Implementation must not begin while any of the ten is unanswered. The note is
short by design — it belongs in the Issue, the design comment, or the Technical
Defense, and it is not a new governance artifact.

### Adversarial design review

Before implementation, the proposed model must survive one focused adversarial
review. The goal is to invalidate a bad model while it is still free to change.

The reviewer attacks the design, not the code, using at least:

- the head or underlying subject changing during execution;
- duplicate delivery;
- delayed delivery;
- reordered delivery;
- partial API failure;
- retry after a partial mutation;
- a stale read followed by a write;
- state shared across several subjects;
- exhaustion and no-more-provider states;
- external query semantics differing from the test harness;
- success followed by failure;
- failure followed by success.

One design review is cheaper than eight implementation review rounds.

### Prefer invariant-preserving designs

When choosing between a design that repeatedly observes mutable external state
and tries to time its mutations correctly, and a design whose correctness does
not depend on that state staying still, choose the second.

Narrowing a race window is not fixing a race. If a model exists that removes the
dependence entirely, adopting it is preferred over another round of tightening,
even when the tightening is smaller than the redesign.

### Complexity budget

Before adding another reconciliation loop, retry layer, cache, state machine,
fallback, or synchronization mechanism, answer: can an existing mechanism
already satisfy this invariant?

If yes, reuse or simplify rather than add. New machinery must have a unique
responsibility. Where two mechanisms enforce the same invariant, prefer deleting
one over maintaining both, unless the redundancy is deliberate and its purpose
is documented at the mechanism.

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

Every finding must answer one question explicitly before it is classified:

> Does this violate a required invariant or acceptance criterion **today**?

If yes, it is a Merge Blocker. If no, it is one of the other three categories and
becomes a follow-up issue. "Could be more robust" is not a merge blocker, and
must not become one by repetition.

### Review stopping rule

Independent review is mandatory and is not weakened by this rule. What it bounds
is the number of *implementation* rounds a single PR absorbs.

After an independent review returns clean on the exact final HEAD, and full
validation passes on that same HEAD:

- speculative hardening must not continue inside that PR;
- non-blocking improvements are deferred to follow-up issues;
- the PR is closed for merge authorization.

A reviewer identifying a possible future improvement is not sufficient reason to
reopen implementation. Only a concrete violation of the PR's acceptance criteria
or of an architectural invariant is release-blocking.

When a PR has absorbed repeated rounds whose findings are defects in that PR's
own earlier fixes, that is evidence the design was wrong rather than the code —
return to the design note and the adversarial design review rather than
continuing to patch.

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

PR #258 is the second case study, and the reason the design-note and stopping
rules above exist. It took eight independent review rounds and fourteen findings
to reach a clean result. The distribution is the lesson, not the total:

- rounds 1–5 were almost entirely defects in the PR's *own preceding fixes*,
  concentrated in one mechanism added during round 1 to close a race;
- each of those rounds narrowed a window instead of removing the dependence on
  it, so the next round found the residue;
- rounds 6 and 7 were the same defect class — a truncated digest used as a
  security identity over author-controlled text — in two different digests. The
  second existed only because the first was fixed narrowly, without auditing
  every other identity of the same kind;
- two findings were concealed by a test harness that modelled the external API
  *more helpfully than the real service behaves*, so the regression tests passed
  with the fix reverted and proved nothing.

The durable principles are:

> Narrowing a race window is not fixing a race.

> Fix the defect class, not the reported instance.

> A test harness that is kinder than the real service hides the bug it was
> written to catch.

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
