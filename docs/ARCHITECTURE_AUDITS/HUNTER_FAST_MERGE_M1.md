# Hunter Fast Merge — M1 decision-authority contract

Status: **proposed contract, not deployed**. Owner issue: #588; scoped delivery: #589. This document inventories the current authority boundaries and sets testable invariants. Do not remove required gates based on this document alone.

## Observed authorities (main at d1130e5, 2026-10-09)

| Role | Current evidence / implementation | Authority boundary |
| --- | --- | --- |
| Current PR and review state | `scripts/hunter_merge_readiness_v2.py`: `ReadinessObservation`, live review-thread / CHANGES_REQUESTED and exact-head check readers; invoked by `scripts/hunter_governance_review/bootstrap_external_review_469.py::readiness_mode` | Reads live GitHub state; must bind to the exact PR head and current governance revision |
| Published merge readiness | `.github/workflows/hunter-merge-readiness.yml` → trusted default-branch checkout → `bootstrap_external_review_469.py readiness` → `hunter_merge_readiness_v2.main()` | Derived status only, not permission to merge; event, workflow-run, schedule and manual wakeups |
| Candidate admission | `scripts/hunter_governance_review_v2.py`, `read_trusted_upgrade_status` | Verified ingress signatures plus hosted exact-head proof while connector ingress is active; fail closed |
| Canonical preflight | `.github/workflows/hunter-pre-pr-preflight.yml` and trusted preflight-upgrade workflow | Expensive proof bound to immutable head, not a PR branch name |
| Independent reviewer | `.github/workflows/hunter-reviewer-collector.yml` is **workflow_dispatch-only**; trusted orchestration dispatches it. `.github/workflows/hunter-governance-reconcile.yml` wakes on `push` to main, `pull_request_target` (`ready_for_review`, `synchronize`), `pull_request_review` (`submitted`, `edited`, `dismissed`), `pull_request_review_comment` (`created`, `edited`, `deleted`), selected `workflow_run` completions, 30-minute cron and manual dispatch | Evidence and actionable findings, not an unbounded availability dependency; bot review event approval holds require separate M5 remediation |
| Final authorization | Repository owner | No automated merge without explicit approval |

## Active wakeup and status mapping (baseline; not a proposed implementation)

- **Merge Readiness wakeups:** `pull_request_target` (`opened`, `reopened`, `synchronize`, `ready_for_review`, `converted_to_draft`); `pull_request_review` (`submitted`, `edited`, `dismissed`); `pull_request_review_comment` (`created`, `edited`, `deleted`); `workflow_run` completion for CI, Dependency review, Governance Reconcile, Pre-PR Preflight, Trusted Preflight Upgrade and CodeQL bridge; every 5 minutes; `workflow_dispatch` with PR number. **These review-event triggers can be approval-held for bot authors**; the cron is a recovery path, not a freshness guarantee.
- **Governance Reconcile wakeups:** main push, selected `workflow_run` completions (Pre-PR Preflight, Trusted Preflight Upgrade, Governance Review, Reviewer Collector), `pull_request_target` (`ready_for_review`, `synchronize`), review and review-comment events listed above, 30-minute schedule and manual dispatch. It can dispatch privileged orchestration; reviewer collector itself has only `workflow_dispatch`.
- **Actual readiness status contract:** `hunter_merge_readiness_v2.Decision.state` is GitHub commit-status `success`, `failure` or `pending` in context `Hunter Merge Readiness`. `READY` maps to `success` only after exact-head safety prerequisites pass; `BLOCKED` maps to `failure` for verified safety defects; `WAITING` maps to `pending` for incomplete or unavailable required evidence. `TIMED_OUT` is **not** a fourth GitHub status: a timed-out required safety proof must produce `failure` with a bounded diagnostic, while external reviewer `REVIEW_TIMED_OUT` is advisory and may still yield `success` when all deterministic safety checks pass. The owner merge guard revalidates live state; no published status alone grants owner authorization.
- **M5 migration caveat:** the baseline above documents the code on `main` at the recorded SHA; #584 changes trigger behavior on its branch and was subsequently merged. Before implementing M5, re-read the current `main` trigger contract and write regression tests for the actual post-merge baseline rather than copying these historical triggers.

## Safety invariants (M2–M7 implementation contract)

1. **One authoritative final decision:** candidate proof and governance are inputs; one controller computes the READY / BLOCKED / WAITING policy and bounded timeout disposition for an exact PR number and immutable HEAD, projecting to the three GitHub states defined above. Other workflows may publish evidence, but must not independently grant READY.
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
