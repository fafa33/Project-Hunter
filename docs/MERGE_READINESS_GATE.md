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

The following do not determine automated merge readiness (the single, narrow exception for owner-registered Issues is
defined in *Registered Issue acceptance* below; nothing else on this list gains authority from it):

- Issue identity (except through the owner registry below);
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

## Registered Issue acceptance

`docs/CODE_WRITE_POLICY.json` -> `review_progression.review_authority.acceptance_requirements` is an owner-authored **policy
registry**. It is not a proof authority: it only names which already-trusted evidence producer (a repository workflow file and
event) must have succeeded at the exact PR head for a governed Issue. It is read from the trusted default branch, so a candidate
cannot register, narrow or waive its own obligation. Evidence evaluation lives in the governance path
(`hunter_governance_review_v2.issue_acceptance_state`); Merge Readiness only consumes the `(state, detail)` result, like candidate
admission.

- **Binding.** A PR is bound to a registered Issue only by trusted changed-file evidence matching the entry's `governed_paths`.
  PR title/body, closing keywords, branch names, committed claims, reviewer or LLM verdicts, Issue closure and generic CI checks are
  never inputs. A PR that binds no registered Issue, or a repository with an empty registry, behaves exactly as before and costs
  no extra read.
- **States** (the first token of the detail): `ACCEPTANCE_PROVEN` (every required producer succeeded at the exact head),
  `ACCEPTANCE_MISSING` (required evidence absent or its latest exact-head run did not succeed), `ACCEPTANCE_STALE` (evidence
  exists only for another head or the exception no longer matches), `ACCEPTANCE_AMBIGUOUS` (the change binds more than one
  registered Issue), `ACCEPTANCE_UNRESOLVED` (criteria that cannot be verified mechanically, an unreadable registry, unavailable
  evidence, or a producer modified by the same change). `ACCEPTANCE_NOT_GOVERNED` means nothing is registered for the change.
- **Effect.** Only `PROVEN`, `NOT_GOVERNED` and a valid production-only exception allow a positive decision. Missing, stale and
  unavailable evidence is `pending`; ambiguous, structurally unresolved or malformed is `failure`. Unavailable evidence is never
  success.
- **Production-only exception.** An entry may carry one `production_only` record for evidence that can only exist after merge. It
  requires the owner's authorization and documented technical necessity, a bounded post-merge verification, one exact `head_sha`,
  a `trust_root` path and sha256 that must equal the base-branch file, an `expires_at`, and the explicit deferred evidence ids and
  criteria. All non-deferred evidence must still be proven before merge. The result is
  `ACCEPTANCE_PRODUCTION_ONLY_PENDING`: readiness may be positive but its published description leads with "Issue acceptance NOT complete" (so GitHub's 140-character status limit can never cut it), and
  the record stays in the registry until the owner removes it with production evidence. After `expires_at` it blocks again.
- **Three distinct facts.** PR merge readiness, Issue acceptance completion and post-merge production acceptance are separate;
  this section affects only the first and never reports the others as done. Closing an Issue is not evidence.

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
