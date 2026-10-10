# Hunter Fast Merge — M1 decision-authority contract

Status: **proposed contract, not deployed**. Owner issue: #588; scoped delivery: #589. This document inventories the current authority boundaries and sets testable invariants. Do not remove required gates based on this document alone.

## Observed authorities (main at bd4cafb, 2026-10-09)

| Role | Current evidence / implementation | Authority boundary |
| --- | --- | --- |
| Current PR and review state | `scripts/hunter_merge_readiness_v2.py`: `ReadinessObservation`, live review-thread / CHANGES_REQUESTED and exact-head check readers; invoked by `scripts/hunter_governance_review/bootstrap_external_review_469.py::readiness_mode` | Reads live GitHub state; must bind to the exact PR head and current governance revision |
| Published merge readiness | `.github/workflows/hunter-merge-readiness.yml` → trusted default-branch checkout → `bootstrap_external_review_469.py readiness` → `hunter_merge_readiness_v2.main()` | Derived status only, not permission to merge; event, workflow-run, schedule and manual wakeups |
| Candidate admission | `scripts/hunter_governance_review_v2.py`, `read_trusted_upgrade_status` | Verified ingress signatures plus hosted exact-head proof while connector ingress is active; fail closed |
| Canonical preflight | `.github/workflows/hunter-pre-pr-preflight.yml` and trusted preflight-upgrade workflow | Expensive proof bound to immutable head, not a PR branch name |
| Independent reviewer | `.github/workflows/hunter-reviewer-collector.yml` is **workflow_dispatch-only**; trusted orchestration dispatches it. `.github/workflows/hunter-governance-reconcile.yml` wakes on `push` to main, `pull_request_target` (`ready_for_review`, `synchronize`), selected `workflow_run` completions, 5-minute cron and manual dispatch | Evidence and actionable findings, not an unbounded availability dependency; bot review-event approval holds were mitigated in #584 by removing direct review triggers |
| Final authorization | Repository owner | No automated merge without explicit approval |

## Active wakeup and status mapping (baseline; not a proposed implementation)

- **Merge Readiness wakeups:** `pull_request_target` (`opened`, `reopened`, `synchronize`, `ready_for_review`, `converted_to_draft`); `workflow_run` completion for CI, Dependency review, Governance Reconcile, Pre-PR Preflight, Trusted Preflight Upgrade and CodeQL bridge; every 5 minutes; `workflow_dispatch` with PR number. **Direct review-event triggers were removed in #584** to avoid approval-held bot runs; the cron is a recovery path, not a freshness guarantee.
- **Governance Reconcile wakeups:** main push, selected `workflow_run` completions (Pre-PR Preflight, Trusted Preflight Upgrade, Governance Review, Reviewer Collector), `pull_request_target` (`ready_for_review`, `synchronize`), 5-minute schedule and manual dispatch. It can dispatch privileged orchestration; reviewer collector itself has only `workflow_dispatch`.
- **Actual readiness status contract:** `hunter_merge_readiness_v2.Decision.state` is GitHub commit-status `success`, `failure` or `pending` in context `Hunter Merge Readiness`. `READY` maps to `success` only after exact-head safety prerequisites pass; `BLOCKED` maps to `failure` for verified safety defects; `WAITING` maps to `pending` for explicitly in-flight prerequisites and those transport/read exceptions that the active resolver treats as pending. For normal return paths, `hunter_governance_review_v2.candidate_admission` classifies malformed or absent admission evidence as `failure` (or an explicitly in-flight prerequisite as `pending`). **Current exception:** `hunter_merge_readiness_v2.candidate_admission_state` catches arbitrary exceptions from that call and returns `pending` with an evidence-unavailable diagnostic; this is fail-closed for READY but not a terminal `failure`. Do not treat the current exception mapping as a guaranteed bounded outage policy; changing it requires M2–M7 tests. Any alternative outage policy belongs to a separately tested M2–M7 change, not this baseline. `TIMED_OUT` is **not** a fourth GitHub status: a timed-out required safety proof must produce `failure` with a bounded diagnostic, while external reviewer `REVIEW_TIMED_OUT` is advisory and may still yield `success` when all deterministic safety checks pass. The owner merge guard revalidates live state; no published status alone grants owner authorization.
- **M5 migration caveat:** the baseline above documents the post-#584 `main` trigger contract at `bd4cafb`. Before implementing M5, re-read current `main` and write regression tests against its live triggers, not a historical pre-#584 snapshot.

## Confirmed current trust-boundary gap (M2/M4 hardening; not implemented by M1)

The live `.github/workflows/hunter-governance-review.yml` is triggered by `pull_request` and grants `statuses: write`. GitHub can evaluate a same-repository PR's candidate-authored workflow steps under that token, even when the Python bridge is checked out from the default branch. Consequently a candidate can add a status-publishing step and forge the protected `Hunter Merge Readiness` context. **The current deployment does not enforce invariant 3 below.** M2/M4 must eliminate candidate-reachable status-write permission (publish only from a default-branch trusted workflow), add an adversarial structural test of effective permissions across workflow/job declarations and all candidate-reachable trigger forms, and prove the malicious mutation fails before declaring the boundary enforced. Existing checkout-only tests are insufficient. This is a confirmed security gap, not evidence that PR #590 actually forged a status.

## Safety invariants (M2–M7 implementation contract)

1. **One authoritative final decision:** candidate proof and direct live governance evidence (review threads, CHANGES_REQUESTED, exact-head checks and trusted admission) are inputs; the legacy published `Hunter Governance Review` commit status is compatibility-only and is **not** a decision input or veto; one controller computes the READY / BLOCKED / WAITING policy and bounded timeout disposition for an exact PR number and immutable HEAD, projecting to the three GitHub states defined above. Other workflows may publish evidence, but must not independently grant READY.
2. **Live freshness:** before READY, re-fetch PR head, mergeability, current unresolved review threads, latest changes-requested reviews, relevant comments and exact-head checks. If the head or governance revision changes during evaluation, retry once against the new revision; if unstable, fail closed.
3. **Trust separation:** only default-branch trusted code may mint exact-head admission proof or final decision. Untrusted PR/review/comment payloads never execute privileged candidate code or directly publish READY. GitHub's approval-held bot events are not required gates.
4. **Bounded waiting:** every dependency has a named owner, deadline, terminal diagnostic and bounded retry. A missing external review response alone cannot block indefinitely; substantive safety findings must be resolved.
5. **No redundant full-suite rerun:** local fast targeted checks precede PR; one canonical hosted full proof per immutable head, with receipt reuse for unchanged validation definitions. **Exception:** if changed files intersect `PREFLIGHT_OWNED_PATHS`, `.github/workflows/hunter-trusted-preflight-upgrade.yml` must independently run its trusted full gate chain; candidate-authored proof cannot self-attest changed validation code or replace that trusted upgrade. Formatting, contract tests and changed-file review-target equality are enforced before publication.
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

## M2 trusted operator recovery (candidate change; not yet deployed)

Both Governance Reconcile and Merge Readiness accept `repository_dispatch`
with event type `hunter-trusted-recovery`. GitHub evaluates this event against
the default-branch workflow definition; there is no caller-selectable workflow
ref. An authorized operator can wake both controllers using a credential with
repository dispatch permission:

```sh
gh api -X POST repos/fafa33/Project-Hunter/dispatches -f event_type=hunter-trusted-recovery
```

This is a full current-state refresh, not permission to merge or to run a
candidate branch with a privileged token. Keep the five-minute schedule as
a fallback. The previous `workflow_dispatch` on-demand path is intentionally
not restored because a caller-selected candidate ref is not a trust boundary.
