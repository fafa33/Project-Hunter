# Merge Readiness Gate

## Status

Implementation guide for the Merge Readiness rule owned by `docs/DEVELOPMENT_GOVERNANCE.md`.

## Principle

Merge Readiness evaluates **current merge risk**. It does not grade process history or PR prose.

## Required current-state blockers

`Hunter Merge Readiness` blocks or waits when:

- the PR is Draft;
- mergeability is unresolved or the PR conflicts with `main`;
- an inline review thread with a substantive unresolved finding remains open;
- a current review is `CHANGES_REQUESTED`;
- `Quality Gates`, `dependency-review`, or `CodeQL` is missing, pending, cancelled, or failed;
- `Hunter Governance Review` is missing or non-stale pending, so readiness waits;
- `Hunter Governance Review` is failed or errored;
- the same head SHA is shared by another open PR in a way that makes status attribution unsafe.

When none of those conditions exists, the controller may publish success.

A pending `Hunter Governance Review` status may be ignored only when current mergeability is `true` and the controller treats that pending status as stale evidence from an earlier unresolved-mergeability observation.

## Explicit non-authority

The following do not determine automated merge readiness:

- Issue identity;
- branch naming;
- commit-message Issue references;
- PR title/body format or acceptance-matrix syntax;
- readiness checkboxes;
- top-level PR comments;
- reactions or owner `+1` acknowledgements;
- metadata-only edits;
- timestamps;
- cancelled or superseded historical runs;
- Draft Promotion signals;
- hostile-review attestation markers.

These may still be useful traceability, but they are not merge authority.

## Review findings

Current `CHANGES_REQUESTED` and unresolved substantive inline findings block. Non-blocking recommendations do not. Closing or dispositioning a non-blocking recommendation does not require a new code commit or another full review.

## Required product evidence

If the governing Issue genuinely requires live acquisition, migration, persistence/replay, or other operational behavior, that requirement must actually be satisfied before human merge approval. No particular PR-body formatting is required to prove it.

## Final rule

Do not merge until all required checks on the final code head are green and no real blocker remains. Automation reports readiness; a human performs the merge.
