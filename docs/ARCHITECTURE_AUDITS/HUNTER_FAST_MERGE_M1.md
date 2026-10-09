# Hunter Fast Merge — M1 decision-authority contract

Status: **proposed contract, not deployed**. Owner issue: #588; scoped delivery: #589. This document inventories the current authority boundaries and sets testable invariants. Do not remove required gates based on this document alone.

## Observed authorities (main at d1130e5, 2026-10-09)

| Role | Current evidence / implementation | Authority boundary |
| --- | --- | --- |
| Current PR and review state | `scripts/hunter_merge_readiness.py`: `unresolved_review_thread_ids`, `current_changes_requested_reviewers`, `all_check_runs`, `all_commit_statuses`, `CurrentState` | Reads live GitHub state; must bind to the exact PR head and current governance revision |
| Published merge readiness | `.github/workflows/hunter-merge-readiness.yml`, trusted default-branch checkout; `scripts/hunter_merge_readiness.py` | Derived status only, not permission to merge; event, workflow-run, schedule and manual wakeups |
| Candidate admission | `scripts/hunter_governance_review_v2.py`, `read_trusted_upgrade_status` | Verified ingress signatures plus hosted exact-head proof while connector ingress is active; fail closed |
| Canonical preflight | `.github/workflows/hunter-pre-pr-preflight.yml` and trusted preflight-upgrade workflow | Expensive proof bound to immutable head, not a PR branch name |
| Independent reviewer | Reviewer collector/orchestrator and governance reconcile workflows | Evidence and actionable findings, not an unbounded availability dependency |
| Final authorization | Repository owner | No automated merge without explicit approval |

## Safety invariants (M2–M7 implementation contract)

1. **One authoritative final decision:** candidate proof and governance are inputs; one controller computes READY / BLOCKED / WAITING / TIMED_OUT for an exact PR number and immutable HEAD. Other workflows may publish evidence, but must not independently grant READY.
2. **Live freshness:** before READY, re-fetch PR head, mergeability, current unresolved review threads, latest changes-requested reviews, relevant comments and exact-head checks. If the head or governance revision changes during evaluation, retry once against the new revision; if unstable, fail closed.
3. **Trust separation:** only default-branch trusted code may mint exact-head admission proof or final decision. Untrusted PR/review/comment payloads never execute privileged candidate code or directly publish READY. GitHub's approval-held bot events are not required gates.
4. **Bounded waiting:** every dependency has a named owner, deadline, terminal diagnostic and bounded retry. A missing external review response alone cannot block indefinitely; substantive safety findings must be resolved.
5. **No redundant full-suite rerun:** local fast targeted checks precede PR; one canonical hosted full proof per immutable head, with receipt reuse. Formatting, contract tests and changed-file review-target equality are enforced before publication.
6. **Owner-controlled merge:** READY is advisory to the owner, never authorization to merge. A later change invalidates READY and requires recomputation.
7. **Migration without bypass:** shadow old/new decisions on actual PRs and adversarial fixtures. Remove old gates only after measured parity and explicit owner cutover; retain fail-closed rollback.

## Adversarial acceptance matrix

| Case | Expected result |
| --- | --- |
| HEAD changes while checks are running | Old receipt invalid; no READY on new head |
| Human review dismissed or new changes-requested review | Live re-evaluation; no stale READY |
| Review thread becomes unresolved after last scheduled sweep | No READY until live state rechecked |
| Bot-authored review and inline comment | No manual approval-held required workflow |
| Hosted proof missing, stale, failed or wrong SHA | WAITING or BLOCKED, never READY |
| Collector retry stuck after success and bounded grace | Terminal timeout, no infinite pending |
| GitHub API unavailable or inconsistent during final read | Explicit WAITING/BLOCKED, never inferred success |
| Fork/untrusted review event | No privileged execution of candidate payload |
| Duplicate workflow reruns | Idempotent exact-head proof and status |
| Owner has not approved merge | Never merge |

## Delivery order and stop rule

M1 inventory/contract (#589) → M2 pre-push evidence gate → M3 trusted proof deduplication → M4 live decision authority → M5 event ingestion/wakeup → M6 bounded CI → M7 shadow rollout and cutover. Each milestone is an issue-bound, focused signed PR with its own verification and owner approval. **Do not add these changes to PR #584.** If a milestone exposes an unresolved safety dependency, stop and record its evidence rather than weakening an existing gate.
