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
- the same head SHA is shared by another open PR in a way that makes status attribution unsafe.

When none of those conditions exists, the controller may publish success.

`Hunter Governance Review` is a compatibility status during the authority cutover, not a second merge prerequisite. Merge Readiness directly observes the current mergeability, exact-head review authority/findings, and required code/security checks that matter to its decision.

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

## Native enforcement and the retained controller (#592)

Native GitHub enforcement (ruleset "Protect main") currently covers the required `Quality Gates`, `dependency-review` and `CodeQL` checks and review-thread resolution. `Hunter Merge Readiness` is **not** removed yet: it still aggregates conditions GitHub cannot express (exact-head Hunter review authority and candidate admission, owner-approval binding). It is retired only after the owner applies and verifies the ruleset changes below; until then nothing in this change weakens the merge contract.

Owner-only ruleset settings still required (not performed from automation; the available token is read-only):

1. require the `Hunter Merge Readiness` and `Hunter Governance Review` status checks;
2. require an approving review from a code owner for `.github/workflows/**` and the root-of-trust paths in `docs/CODE_WRITE_POLICY.json`;
3. keep review-thread resolution required.

A generic GitHub approval is not treated as the repository owner's approval, and a published status is never merge authority. The one reviewer-result publisher is `Hunter Collector Result Publisher`; the collector certifies nothing and a missing result stays pending (fail closed). Recovery is GitHub's native "Re-run all jobs" or a new push (see `docs/ARCHITECTURE_AUDITS/HUNTER_FAST_MERGE_M1.md`).

Known legacy decisions: the retained five-minute schedule is a recovery path, not a freshness guarantee; the tracked `hunter-governance-shadow` workflow keeps comparing old and new decisions without being a gate, and any disagreement it reports is treated as a defect to explain, not as authority.

## Final rule

Do not merge until all required checks on the final code head are green and no real blocker remains. Automation reports readiness; a human performs the merge.
