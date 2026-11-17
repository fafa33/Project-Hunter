# Hunter Merge Readiness Controller

## Status

Reference for `scripts/hunter_merge_readiness_v2.py`. It defines no independent policy; `docs/DEVELOPMENT_GOVERNANCE.md` owns merge-readiness semantics.

## Core invariant

```text
same current GitHub state => same readiness result
```

The controller re-reads current GitHub state. Event history, status history, metadata revision hashes, comment timestamps, and prior workflow ordering are not inputs to the decision.

## Inputs

For each candidate PR, the controller reads:

- open/Draft/mergeability state;
- current head SHA;
- unresolved inline review threads;
- current `CHANGES_REQUESTED` reviewers;
- the governance path's registered-Issue acceptance result (see `docs/MERGE_READINESS_GATE.md`); it is `success` with no extra read
  unless the trusted default-branch registry governs the changed paths;
- exact-head `Quality Gates`, `dependency-review`, and `CodeQL` results;
- other open PRs sharing the same head SHA.

## Decision

- Draft -> `pending`;
- conflict -> `failure`;
- unresolved mergeability -> `pending`;
- unresolved substantive review thread -> `failure`;
- current `CHANGES_REQUESTED` -> `failure`;
- registered-Issue acceptance missing, stale or unavailable -> `pending`; ambiguous, unresolved or malformed registry -> `failure`;
- missing/pending required check -> `pending`;
- failed/cancelled required check -> `failure`;
- failed Governance Review -> `failure`;
- shared head that prevents safe status attribution -> `pending`;
- otherwise -> `success`.

A stale Governance `pending` does not hold a resolved mergeable PR forever; current mergeability is authoritative for that sanity check.

## Explicit exclusions

The controller does not consume PR title/body, Issue identity (except the governance path's owner-registry result), branch naming, top-level comments, reactions, owner acknowledgements, acceptance-matrix formatting, Draft Promotion state, hostile-review markers, semantic revisions, or superseded workflow history.

## Reconciliation

Triggers only identify which PRs to inspect. Each reconciliation recomputes from live state, and scheduled sweeps recover missed events. Failure evaluating one PR does not stop the sweep from evaluating the others.

## Shared heads

Commit statuses are keyed by SHA, so the controller waits when multiple open PRs share a head and a success could be misattributed. Give each PR a unique head commit to remove the ambiguity.

## Maintenance rule

Keep the controller small. Add a new blocker only when a demonstrated merge risk cannot be expressed by the existing current-state signals. Do not recreate metadata state machines or timestamp/reaction ceremony.

## Issue #588 M1: bounded decision contract and independent review trigger

This is a contract inventory for the **existing** controller, not a new
authority or an instruction to relax required checks.

| Input / producer | Authority at decision time | Unknown or failure | Reconciliation |
| --- | --- | --- | --- |
| Live PR Draft, mergeability, exact HEAD | GitHub PR API | Draft/unknown mergeability: pending; conflict: failure | Event wake-up plus bounded trusted sweep |
| Unresolved threads and current changes-requested reviews | GitHub review API | Unresolved or changes requested: failure; API unavailable: no success | Review event plus trusted sweep |
| Candidate admission and trusted preflight | Existing trusted exact-HEAD status | Pending/missing: pending; failure: failure | Existing candidate/governance workflows |
| Knowledge Receipt and registered-Issue acceptance | Trusted exact-HEAD producer | Pending/missing: pending; failure: failure | Existing receipt/governance workflows |
| Required Quality Gates, dependency-review and CodeQL | Current exact-HEAD check runs | Missing/in-progress: pending; terminal failure: failure | GitHub check events and trusted sweep |
| Reviewer opportunity and native Codex availability | Advisory review orchestration only | Unavailable/timeout: not a merge blocker; authenticated findings still block via live threads | Existing reviewer collector and reconciliation |

**Decision owner:** `hunter_merge_readiness_v2.evaluate` consumes the
current observations; the trusted Merge Readiness publication is the single
final readiness decision. Neither a Codex summary nor an agent-authored
comment is a proof of merge eligibility. The owner alone authorizes merge.

**M1 adversarial parity:** Tests explicitly cover terminal required-check
failures, absent exact-head receipt, unresolved review threads despite
optional reviewer unavailability, and pending/failed candidate admission.
The existing controller must remain fail-closed for these inputs. A bot
Draft-to-Ready event is only a wake-up, not proof of a native Codex review.

**Issue #736 production observation:** The next legitimate issue-linked
Draft PR is the test vehicle, not a throwaway PR. Record its exact HEAD,
the actor/timestamp of the Ready transition, and the formal GitHub Codex
Review object (if any). Do not substitute a top-level summary comment for
a review object; do not install an owner PAT without evidence and explicit
authorization. M1 tests are not a claim that native automatic review works.
