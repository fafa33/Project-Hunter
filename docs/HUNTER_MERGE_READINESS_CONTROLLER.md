# Hunter Merge Readiness Controller

## Status

Reference and implementation guide for the controller that publishes the
`Hunter Merge Readiness` commit status. It defines no independent governance
authority.

The mandatory Merge Readiness rule is owned by `docs/DEVELOPMENT_GOVERNANCE.md`
and elaborated by `docs/MERGE_READINESS_GATE.md`. This document describes only
*how the controller mechanically derives* the published status, and is
subordinate to both. Where anything here appears to restate the rule,
`docs/DEVELOPMENT_GOVERNANCE.md` is authoritative.

## Core invariant

    same current canonical state  =>  same readiness result

The published readiness status is a deterministic function of the *current
canonical GitHub state* of one pull request, and of nothing else. It does not
depend on which event triggered reconciliation, event delivery order, duplicate
or delayed delivery, cancelled predecessor runs, replayed `workflow_run`
payloads, status publication history, scheduler timing, or any prior published
state.

## Pipeline

    trigger
      -> identify pull request number(s)
      -> read current canonical state
      -> compute semantic revision
      -> resolve matching Governance Review evidence
      -> evaluate current blockers
      -> publish exactly one current readiness result

One canonical entry point, `reconcile_pr(pr_number)`, performs everything from
the second step onward. Every supported trigger delegates to it.

## Layers

| Layer | Function | Contract |
|---|---|---|
| I/O | `read_current_state(pr_number)` | Performs every GitHub read; returns an immutable `CurrentState` |
| Decision | `decide(state)` | Pure: no network, no clock, no environment, no event name |
| Reconciliation | `reconcile_pr(pr_number)` | Composes the two, publishes or confirms exactly one status |

`decide` being pure is what makes the convergence invariant testable rather than
asserted: the same snapshot cannot produce two different results.

## Authority boundaries

| Concern | Authority | Consumed by the controller as |
|---|---|---|
| Governance verdict for a pull request state | Hunter Governance Review | A commit status stamped with `(pull request, governance revision)` |
| Required check outcomes | Quality Gates, dependency-review, CodeQL | Check runs on the exact head SHA |
| Review threads, CHANGES_REQUESTED reviews, comment acknowledgement | GitHub, read live | Current state, evaluated directly |
| Implementer declarations and acceptance matrix | The pull request body | Current state, evaluated directly |
| Which pull request to reconcile | The trigger | A pull-request number, and nothing else |

A trigger's entire authority is identification. No event payload field reaches
`decide`, so a replayed, duplicated, delayed, or reordered delivery can only
cause a redundant reconciliation of current state.

## Semantic revision

Two related fingerprints, both defined in `scripts/hunter_governance_revision.py`
and `CurrentState`:

**Governance revision** — a digest of exactly the mutable facts the Hunter
Governance Review engine evaluates: pull request number, head SHA, base SHA,
base ref, title, body, draft flag, conflicting flag, and the sorted set of
changed paths. Repository content the engine resolves is addressed at the head
and base SHAs, so pinning those pins that content. Two governance inputs are
deliberately excluded: the author login (immutable for a pull request's life, and
represented differently by the two GitHub APIs involved) and changed-file
status/additions/deletions (read by no validator).

**Readiness semantic revision** — a digest of the complete readiness-relevant
state: everything above, plus required check outcomes, resolved Governance
evidence, unresolved review thread identifiers, CHANGES_REQUESTED reviewers, and
unacknowledged comment identifiers.

Deliberately excluded from both, because none of them changes what readiness
*is*: the controller's own published statuses, trusted repository-automation
advisory comments, every timestamp, and every run/status/check identifier.

## Governance evidence attribution

Hunter Governance Review stamps `[hgr:<pull request>:<revision>]` at the start of
its status description. The controller accepts a verdict only when the marker
names this pull request **and** the current governance revision. Everything else
fails closed:

- no marker (including every status published before this stamp existed) —
  unattributable, unusable;
- a marker naming another pull request — rejected, so
  `GovernanceEvidence(PR=A, HEAD=H)` can never satisfy
  `MergeReadiness(PR=B, HEAD=H)` when `A != B`;
- a marker naming another revision — rejected, so a verdict produced for
  superseded state can never satisfy current state.

Selection is by identity, never by recency, so a re-run of an older evaluation
landing after a newer one changes nothing. Where two qualifying verdicts for one
`(pull request, revision)` pair disagree, the conservative one wins: a terminal
`failure` outranks a `success`. That direction is deliberate — preferring
`success` would turn any weakness in the binding into a *permanent* bypass,
since the stale approval would outrank every later failure for that revision.
The liveness cost is bounded: any edit to the title, body, head, or base changes
the revision and clears the block.

The digest width is a security parameter. A pull request author controls the
title and body with unlimited entropy and can grind variants offline against a
public algorithm, so the marker must be wide enough that a cross-collision
between a governance-valid body and a governance-invalid one is infeasible —
`REVISION_DIGEST_LENGTH` is 32 hex characters (128 bits). The width is also part
of the wire format: markers of any other length do not parse and are treated as
unattributable evidence.

The `REVISION_SCHEMA_VERSION` constant exists so that changing the fingerprinted
input set invalidates every pre-existing stamp rather than silently
reinterpreting it under new semantics. A version bump fails closed.

## Feedback is not a Governance input

Unresolved review threads, CHANGES_REQUESTED reviews, and unacknowledged
comments are not read by Hunter Governance Review, so they cannot make its
verdict wrong and do not invalidate its evidence. They are evaluated live on
every reconciliation instead.

This is the correction of the defect that produced the repeated historical
fixes. Treating comment and review activity as governance invalidation meant a
pull request could enter a state where readiness waited for a Governance re-run
that no event would ever trigger — because reactions emit no event at all, and
Governance Review does not run on comment or review events. Resolving a thread
or applying an owner acknowledgement now converges on the next reconciliation
with no knowledge of how the change arrived.

## Concurrency

Per-PR concurrency with `cancel-in-progress: true`. Nothing depends on a
predecessor completing: a cancelled run leaves no state a successor reads, and
the successor re-reads everything. There is no global serialized state machine
and no distributed lock.

Green is the one irreversible direction — a stale green asserts mergeability that
may not hold, while a stale pending or failure is a liveness problem the next
reconciliation corrects — so green is guarded on **both sides of the write**, and
neither guard is a lock.

*Before* publishing `success`, current state is read again and the semantic
revision compared, rejecting a green computed from an already-overtaken read.
The exhaustion path below reads once more and writes to *that* snapshot's head
paired with *that* snapshot's published status: taking a head from an earlier
loop snapshot could write `pending` to a SHA the pull request has already left,
leaving an earlier iteration's `success` standing on the head that counts.

*After* publishing `success`, current state is read again and re-decided. A
pre-publish check alone cannot close the window between the check and the write:
a blocker can appear in that window, a concurrent reconciliation can publish
`failure` for it, and this run's already-decided `success` can land on top. The
post-publish pass closes that without serialization, because the blocker is
durable repository state rather than another writer's status — any read taken
after it exists observes it, so whichever run writes last also checks last. If
state is still moving after `SUCCESS_CONVERGENCE_ATTEMPTS` rounds, the run
publishes `pending` rather than leaving an unconfirmed green.

Every `success` — the first and every one re-decided during convergence — goes
through the same `_confirm_success` gate, so a converged green is never reached
by `decide` alone and a controller-upgrade candidate is held to the admission
kernel on each one.

When a run decides against green, it retracts the `success` statuses it
published, on every commit it wrote one to, without consulting which commit is
the head at that moment. Both exits take it: the withholding path, and any
convergence iteration that decides against green.

This is **defence in depth, not a guarantee**, and the distinction matters:

- a job can be cancelled between any two statements — `cancel-in-progress` is
  part of the design — so partial retraction is always reachable;
- it covers only greens the *same run* published and then decided against. A
  green left by an earlier run that ended cleanly is tracked by nothing and is
  not covered;
- correctness does not rest on it. A stale green on a commit that later becomes
  the head again is corrected by the next reconciliation, through exactly the
  convergence that corrects every other stale status — the head change raises an
  event, and the sweep runs regardless.

What it buys is narrowing the most common shape of the hazard: a run that
greened a commit and then, seconds later, learned better. Every tracked head is
attempted even when one write fails, so a transient error degrades to the
convergence behaviour above rather than skipping the rest silently.

A per-PR lock would not have been sufficient for this on its own: the lock this
design removes was released before the status write in the generation that had
it, so it never serialized the write either.

For a pull request that changes controller-owned paths, the pre-publish reading
must additionally satisfy `hunter_controller_admission.evaluate_admission`, an
independent re-derivation of every gate that shares no logic with `decide`.
Controller-upgrade candidacy is detected from both sides of a rename — GitHub
reports a renamed file with the destination in `filename` and the
controller-owned source in `previous_filename` — so renaming a controller-owned
path away from itself cannot escape admission. Previous filenames stay out of the
governance revision, which must cover exactly what the Governance Review engine
reads.

## Status guarantee

A reconciliation that evaluated an open pull request always publishes or
confirms. A write is skipped only when the already-published status is identical
to the decided one. Successful execution never leaves a stale status behind
unless pending or failure is the correct current result.

## Shared head SHAs fail closed

A commit status is keyed by `(SHA, context)`, not by pull request. When several
open pull requests have the same current head, branch protection evaluates the
*same status object* for all of them. A green published for one would therefore
assert mergeability for pull requests whose own blockers were never evaluated,
so readiness **withholds green whenever the head is shared** and publishes
`pending` naming the other pull requests instead. `hunter_controller_admission`
refuses independently, so the kernel never relies on the caller having refused.

Sibling detection uses the head-targeted commit-to-pulls endpoint, not the
base-scoped open-pull-request list. Both properties matter: the list is scoped to
the sweep's base branch and would miss a sibling targeting another protected
branch — which shares the same status slot — and scanning it on every state read
would make a sweep quadratic in the number of open pull requests.

This is the one piece of head-uniqueness logic that survives the redesign. #257
used head uniqueness to *attribute evidence*; that job is now done by the
identity marker. Head uniqueness is still required for a different reason —
whether a readiness result can be *published* at all — and the two must not be
confused.

The withholding is derived from current state, so it lifts by itself as soon as
the head is the pull request's alone again. Give each pull request its own head
commit.

## Governance freeze

Merge Readiness machinery is maintenance-mode infrastructure. Prefer current-state
reads, pure deterministic evaluation, explicit authority, fail-closed
attribution, and idempotent reconciliation over event bookkeeping, timestamp
comparison, migration markers, scheduler-specific branches, and replay-specific
special cases. Do not add a bespoke lifecycle state unless a demonstrated
requirement genuinely cannot be expressed as current canonical state.
