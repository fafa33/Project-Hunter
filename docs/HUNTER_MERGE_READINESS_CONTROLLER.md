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
- exact-head `Quality Gates`, `dependency-review`, and `CodeQL` results;
- current `Hunter Governance Review` status;
- other open PRs sharing the same head SHA.

## Decision

- Draft -> `pending`;
- conflict -> `failure`;
- unresolved mergeability -> `pending`;
- unresolved substantive review thread -> `failure`;
- current `CHANGES_REQUESTED` -> `failure`;
- missing/pending required check -> `pending`;
- failed/cancelled required check -> `failure`;
- failed Governance Review -> `failure`;
- shared head that prevents safe status attribution -> `pending`;
- otherwise -> `success`.

A stale Governance `pending` does not hold a resolved mergeable PR forever; current mergeability is authoritative for that sanity check.

## Explicit exclusions

The controller does not consume PR title/body, Issue identity, branch naming, top-level comments, reactions, owner acknowledgements, acceptance-matrix formatting, Draft Promotion state, hostile-review markers, semantic revisions, or superseded workflow history.

## Reconciliation

Triggers only identify which PRs to inspect. Each reconciliation recomputes from live state, and scheduled sweeps recover missed events. Failure evaluating one PR does not stop the sweep from evaluating the others.

## Shared heads

Commit statuses are keyed by SHA, so the controller waits when multiple open PRs share a head and a success could be misattributed. Give each PR a unique head commit to remove the ambiguity.

## Maintenance rule

Keep the controller small. Add a new blocker only when a demonstrated merge risk cannot be expressed by the existing current-state signals. Do not recreate metadata state machines or timestamp/reaction ceremony.
