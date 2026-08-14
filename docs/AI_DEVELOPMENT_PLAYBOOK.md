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
or a governance decision, the Implementer should write a short design note
answering these ten questions concretely, before implementing:

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

Answers should be concrete. "Handled by retry" and "unlikely in practice" do not
answer questions 3, 4, or 7. The note is short by design — it belongs in the
Issue, the design comment, or the Technical Defense, and it is not a new
governance artifact.

**Authority of this section and the one that follows.** Both are working
guidance for agents. Neither creates a lifecycle stage, a precondition on
beginning implementation, a review requirement, or a merge condition.
`docs/CANONICAL_ARCHITECTURE_MAP.md` assigns development-process authority to
`docs/DEVELOPMENT_GOVERNANCE.md` and does not list this playbook in its hierarchy
at all. So nothing here blocks a change that repository governance otherwise
permits, and skipping a design note or a design review is not by itself a
governance violation.

Should either become a required precondition, that is for
`docs/DEVELOPMENT_GOVERNANCE.md` to establish through its own amendment process,
which is sufficient on its own and imposes no ADR condition. ADR 0029 is relevant
only to the design note, and only indirectly: the note elaborates that ADR's
stage 2, and only *accepted* ADRs are binding, so while ADR 0029 remains
`Proposed` the stage it defines is not binding either. The adversarial design
review below appears in no ADR and depends on none.

That limit is deliberate rather than reluctant: a subordinate document that
quietly creates gates is the same defect class as an implementation that quietly
creates authorities.

### Adversarial design review

Before implementing, the proposed model is worth putting through one focused
adversarial review. The goal is to invalidate a bad model while it is still free
to change.

The reviewer attacks the design, not the code, using every case below that
applies to it. A case that cannot apply — a design with no deliveries, no
provider, no external query, or no mutable execution state — is recorded as not
applicable, with one line saying why. Recording non-applicability is part of the
review; silently skipping a case is not.

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

If yes, prefer reusing or simplifying over adding. New machinery is worth having
only when its responsibility is one no existing mechanism already carries. Where
two mechanisms enforce the same invariant, prefer deleting one over maintaining
both, unless the redundancy is deliberate and its purpose is documented at the
mechanism.

Like the design guidance above, this is a preference for agents working under
this playbook. `docs/HUNTER_IMPLEMENTATION_CONTRACT.md` owns implementation
obligations; nothing here adds one.

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

The question that decides the classification is:

> Does this make the contribution **unsafe to merge today**?

The test is safety, exactly as `docs/AI_REVIEW_PROTOCOL.md` defines it, and that
document governs — including what a review report must record. Nothing here adds
a reporting obligation to it; answering this question explicitly is a habit worth
having, not a required report field. A finding is a Merge Blocker whenever it
makes the contribution unsafe — including a security defect, an
evidence-integrity failure, a replay
failure, a migration risk, an authority or implementation-contract violation, a
deterministic-behavior failure, or a documentation contradiction — **whether or
not** it appears in the PR's acceptance criteria, and whether or not it names an
architectural invariant. An acceptance-criteria matrix is not an exhaustive
safety specification, and a defect does not become safe by having been omitted
from it.

If the answer is no, the finding is one of the other three categories and becomes
a follow-up issue. "Could be more robust" is not a merge blocker, and does not
become one by repetition.

### When to stop hardening

Guidance for implementers on scope, not a lifecycle transition. Whether a PR is
ready, whether it leaves Draft, and whether it merges are decided by
`docs/DEVELOPMENT_GOVERNANCE.md` and `docs/AI_REVIEW_PROTOCOL.md`, and the
repository owner may always choose to make further changes. Nothing here bounds
review; independent review remains mandatory wherever those documents require it.

Once an independent review has returned clean on the exact reviewed **source-head
and target-commit pair**, and full validation passes on that same pair, the
default should be to stop:

- speculative hardening inside that PR has reached diminishing returns;
- non-blocking improvements are better recorded as follow-up issues;
- the PR is better presented for the merge decision than extended further.

The pair is the unit, never the source head alone. Per
`docs/AI_REVIEW_PROTOCOL.md`, any subsequent change to the source branch, **or
any advance of the target branch beyond the reviewed commit**, invalidates the
review and requires a new independent review before the PR may leave Draft or be
merged. Stopping under this guidance therefore never survives a base advance, and
never converts a stale review into merge readiness.

A reviewer identifying a possible future improvement is not by itself a reason to
reopen implementation. A finding that makes the contribution unsafe to merge
always is, under the safety test above — and that obligation comes from
`docs/AI_REVIEW_PROTOCOL.md`, not from this section.

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

PR #258 is the second case study, and the reason the design-note guidance and the
stop-hardening guidance above exist. It took eight independent review rounds and
fourteen findings to reach a clean result. The distribution is the lesson, not the
total:

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
