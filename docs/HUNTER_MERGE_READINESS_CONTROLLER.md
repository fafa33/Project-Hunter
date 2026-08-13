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
landing after a newer one changes nothing.

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

*After* publishing `success`, current state is read again and re-decided. A
pre-publish check alone cannot close the window between the check and the write:
a blocker can appear in that window, a concurrent reconciliation can publish
`failure` for it, and this run's already-decided `success` can land on top. The
post-publish pass closes that without serialization, because the blocker is
durable repository state rather than another writer's status — any read taken
after it exists observes it, so whichever run writes last also checks last. If
state is still moving after `SUCCESS_CONVERGENCE_ATTEMPTS` rounds, the run
publishes `pending` rather than leaving an unconfirmed green.

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

## Known limitation: several open pull requests sharing one head SHA

Commit statuses are keyed by SHA, so two open pull requests with the same current
head share one `Hunter Merge Readiness` status slot and will overwrite each
other. This is a property of GitHub's status model, not of this controller, and
it predates this design. Correctness is unaffected: each pull request is still
evaluated only against evidence naming it, so no verdict crosses the boundary —
only the displayed status is ambiguous. Give each pull request its own head
commit to avoid it.

## Governance freeze

Merge Readiness machinery is maintenance-mode infrastructure. Prefer current-state
reads, pure deterministic evaluation, explicit authority, fail-closed
attribution, and idempotent reconciliation over event bookkeeping, timestamp
comparison, migration markers, scheduler-specific branches, and replay-specific
special cases. Do not add a bespoke lifecycle state unless a demonstrated
requirement genuinely cannot be expressed as current canonical state.
