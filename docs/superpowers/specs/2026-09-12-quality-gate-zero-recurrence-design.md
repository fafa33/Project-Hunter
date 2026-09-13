# Quality Gate and Zero-Recurrence Design

## Goal

Prevent known systemic review defects before publication, require live review-thread closure before Ready, and make Merge Readiness reconcile immediately after its dependencies become green.

## Boundaries

The existing validation-stage contract remains authoritative. Focused applicable defect tests belong at the pre-push safety boundary; the full repository suite remains exclusively owned by hosted Pre-PR Preflight. Merge Readiness continues to decide from current GitHub state and never treats an event payload as authority.

Historical findings are backfilled only when a review finding, its correction, and durable regression evidence can be linked. Duplicate findings map to an existing defect family. An entry is never promoted beyond `regression-tested` unless its enforcement is real at the declared boundary.

Review closure is fail-closed: current unresolved threads block Ready and merge. A fixed finding requires an evidence-bearing reply and a resolved GitHub thread. Automation may detect and report missing closure but must not resolve a thread itself.

The stale-pending repair adds `Hunter Governance Review Reconcile` as a dependency completion trigger. Because that workflow runs on the default-branch SHA, its completion triggers a sweep of open PRs; each decision is recomputed from the PR's live current head and statuses. The schedule remains recovery only.

## Delivery

Deliver three small reviewable changes: (1) stale-pending reconciliation, (2) review-thread closure at the pre-Ready authoring boundary, and (3) evidence-backed historical backfill plus applicable focused-test enforcement. Use TDD, adversarial positive/negative cases for gates, and the repository's normal push hook. Do not merge without fresh explicit owner approval.
