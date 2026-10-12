# Hunter Governance Review

## Status

Implementation specification for the active lightweight `Hunter Governance Review` status.

## Objective

Detect only current governance defects that materially affect mergeability. The check is not a second CI suite and is not a metadata-compliance grader.

## Controller

`scripts/hunter_governance_review_v2.py`

The trusted workflow checks out the repository default branch and runs that controller. PR-controlled governance code is not executed with status-write authority.

## Current decision

Before publishing any status, the controller verifies that the open pull request targets `main`. The workflow runs only for pull requests targeting `main`; manual dispatch of this status-writing workflow is disabled.

For an open PR targeting `main`:

- `mergeable == false` -> publish `failure` for a real merge conflict;
- `mergeable == null` -> publish `pending` until GitHub resolves mergeability;
- otherwise -> publish `success`.

The controller fails closed on GitHub transport/unhandled execution failure.

## Non-authority

This check does not judge:

- PR prose or template completeness;
- branch/commit naming;
- Issue identity;
- reactions or owner acknowledgements;
- review-history ceremony;
- exact-pair hostile-review attestations;
- metadata-only changes.

Code quality, dependency security, CodeQL, unresolved review feedback, `CHANGES_REQUESTED`, Draft state, and shared-head attribution are handled by their own current-state gates and by `Hunter Merge Readiness`.

## Triggers

The primary workflow runs via trusted `pull_request_target` on PR open/reopen/synchronize/ready/draft, on completion of the exact-head preflight proofs (`workflow_run`), and on every push to `main`, which re-evaluates every open pull request against the new base (a push-to-`main` run always executes the pushed trusted definition). It has no manual-dispatch entry point and no schedule; there is no separate reconciliation workflow. Recovery from a missed or failed run is GitHub\'s own "Re-run all jobs" or a new push.

## Security boundary

The workflow uses minimal read permissions plus `statuses: write`, checks out the trusted default branch, and uses no external LLM/provider. GitHub itself is the only network dependency.
