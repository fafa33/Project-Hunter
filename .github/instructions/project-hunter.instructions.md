---
applyTo: "**"
---

# Project Hunter AI Agent Instructions

Treat the repository and current GitHub state as the source of truth.

## Work normally

- Understand the user-authorized objective and keep the change within that scope.
- Read the architecture/ADR material that actually governs the area being changed. Do not perform repository-wide ceremony for a local change unless the change genuinely crosses those boundaries.
- Prefer the smallest correct change. Do not invent missing architecture, weaken tests, or hide real failures.
- Before pushing a code-changing candidate, run `python scripts/hunter_pr_preflight.py` when the environment permits it.
- Link the relevant Issue/ADR when useful for traceability, but branch names, commit messages, PR titles/bodies, checkboxes, comments, reactions, and metadata formatting are not merge authority.

## Review and correction

- Treat review findings by materiality. Security, correctness, architecture, persistence/replay, evidence-integrity, and other substantive defects can block.
- Readability, refactoring, extra-test suggestions, style preferences, and other non-blocking recommendations do not force a commit and do not force another full review cycle.
- After fixing a blocking finding, verify that finding and the affected behavior. Re-run a broad review only when the substantive scope materially changed.
- Metadata-only edits and disposition of non-blocking recommendations do not invalidate completed code/security checks or require a new review.

## Merge control

The active merge path is current-state based. A PR is not ready while any of these are true:

- it is Draft;
- GitHub reports a merge conflict or unresolved mergeability;
- a substantive inline review thread remains unresolved;
- a current review is `CHANGES_REQUESTED`;
- `Quality Gates`, `dependency-review`, or `CodeQL` is missing, pending, or failed;
- `Hunter Governance Review` is failed;
- the head cannot be safely attributed to the PR.

Do not merge until every required gate on the final code head is green. Human merge approval remains required.

Do not run or recreate the retired `hunter_governance_preflight.py`, Draft Promotion, owner-`+1`, PR-body identity, or exact-pair hostile-review-attestation ceremony as merge prerequisites.
