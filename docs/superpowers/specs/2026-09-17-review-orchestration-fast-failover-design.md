# Review Orchestration Fast-Failover Design

## Goal
Make exact-head hostile review orchestration progress automatically after every candidate HEAD change without repeatedly waiting on one unavailable reviewer.

## Trust boundary
The default-branch collector remains the trusted controller and never executes candidate code. Review authority remains authenticated, exact-head, claims-bound, and fail-closed. Candidate prose, generic `github-actions[bot]` comments, stale reviews, and malformed responses never create authority.

## Orchestration
For each exact HEAD, invoke each enabled reviewer at most once in strict priority order. A valid substantive response terminates orchestration. Explicit unavailability/rate-limit responses immediately fail over. Silence consumes only that reviewer's bounded timeout, then fails over. A changed HEAD invalidates the run and requires a fresh orchestration for the new HEAD.

Codex native authenticated exact-head clear output is normalized to the current trusted collector invocation/claims instead of requiring a second JSON-format response. Native output must be by the configured Codex bot, after the matching trusted trigger, and name the exact reviewed commit. Findings remain blockers.

## Reviewer pool
The policy is capability-driven. Only integrations with an authenticated GitHub identity and an installed trigger are enabled. Copilot, Jules, OpenCode, or another reviewer can join the ordered pool when those facts are configured; names alone are never treated as availability.

Current Codex timeout is reduced from 900 seconds to 300 seconds and retries are removed. Explicit unavailable/rate-limited replies bypass the remaining wait immediately. The same one-invocation rule applies to every enabled reviewer.

## Verification
TDD covers: one invocation per reviewer; immediate failover on explicit unavailability; timeout failover; exact-head invalidation; native Codex clear after trusted trigger accepted; stale/pre-trigger/wrong-author/wrong-head/finding native responses rejected; structured acknowledgement remains supported; collector receipt remains immutable and default-branch-bound.

Success means Candidate Admission, Hunter Governance Review, and Hunter Merge Readiness can consume the resulting authority on the same final HEAD without manual duplicate reviewer comments.
## Automatic exact-head trigger
The existing trusted `hunter-reviewer-collector.yml` also handles `pull_request_target` opened/reopened/synchronize events on `main`. It derives PR number and candidate SHA from the trusted event payload, checks out only the default-branch controller, and therefore starts exactly one collector run automatically for every new candidate HEAD without granting candidate code write authority.
