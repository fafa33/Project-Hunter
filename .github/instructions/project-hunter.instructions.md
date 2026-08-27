---
applyTo: "**"
---

# Project Hunter AI Agent Instructions

Treat the repository and current GitHub state as the source of truth.

## Work normally

- Understand the user-authorized objective and keep the change within that scope.
- Read the architecture/ADR material that actually governs the area being changed. Do not perform repository-wide ceremony for a local change unless the change genuinely crosses those boundaries.
- Read `docs/DEFECT_REGISTRY.json` for defect classes relevant to the work. A repeated understood defect means the prior hardening was insufficient; strengthen the reusable guard instead of treating the recurrence as a new one-off correction.
- Prefer the smallest correct change. Do not invent missing architecture, weaken tests, or hide real failures.
- Ensure repository-owned Git hooks are enabled with `python scripts/install_hunter_git_hooks.py`. `.githooks/pre-push` is the machine-enforced local push boundary; agent instructions alone are not enforcement.
- Before pushing an ordinary code-changing candidate, run `python scripts/hunter_pr_preflight.py --mode normal`; the Artifact Guard, Ruff, Black, Mypy, and Pytest must all pass. The pre-push hook independently runs that same canonical command against the checked-out exact HEAD, requires a clean tree, and rechecks HEAD/tree state before authorizing the network push.
- A preflight result for commit A never authorizes commit B. Do not push a branch/ref whose local SHA is not the checked-out HEAD; checkout the candidate branch and rerun the machine boundary.
- Before treating a hosted Pre-PR run as proof or opening a PR based on it, verify that the run's commit SHA exactly equals the current branch HEAD. A run number, "latest" label, or remembered green result alone is not exact-head evidence.
- For an intentional tests-first RED commit, create `.hunter-preflight-mode` containing exactly `tests-first-red` and commit that marker on the exact branch head being pushed. The exact-head preflight may pass only when the Artifact Guard, Ruff, Black, and Mypy are green and Pytest is genuinely RED. This exception is for Draft tests-first work only; it never makes failing tests merge-ready.
- Before implementation or any normal candidate resumes, remove `.hunter-preflight-mode`, commit that removal on the branch head being pushed, and return to `python scripts/hunter_pr_preflight.py --mode normal`.
- CI and preflight are verification only. Do not auto-format, auto-commit, or hide failures merely to obtain a green status.
- A machine guard must be grounded in canonical authority or a demonstrated recurring defect. Prefer semantic/structural invariants over brittle wording checks; do not force TARGETED work through FULL-review ceremony or block canonically valid equivalent wording. A false-positive merge blocker is itself a defect and must be corrected.
- If a defect represented as prevented recurs on a pushed PR head or in independent review, classify that as a prevention-system failure and strengthen the earliest practical enforcement boundary; fixing only the feature symptom is insufficient.
- If a change adds or materially changes a parser, validator, artifact/workflow guard, or merge-blocking gate, perform an adversarial bypass pass before requesting review. Test whether non-semantic/literal/example content can impersonate real structure and whether valid equivalent structure is rejected. Use paired negative/positive fixtures where practical for relevant traps such as fenced or hidden content, quoted/escaped text, negation, partial/duplicate declarations, wrong field/column/scope binding, or unrelated matching structure. This is targeted to guard/parser/gate changes and must not become ceremony for ordinary PRs.
- Link the relevant Issue/ADR when useful for traceability, but Issue identity, branch names, commit messages, PR titles/bodies, checkboxes, top-level PR comments, reactions, metadata-only edits, and superseded historical runs are not merge authority.

## Review and correction

- Treat review findings by materiality. Security, correctness, architecture, persistence/replay, evidence-integrity, and other substantive defects can block.
- Readability, refactoring, extra-test suggestions, style preferences, and other non-blocking recommendations do not force a commit and do not force another full review cycle.
- After fixing a blocking finding, verify that finding and the affected behavior. Re-run a broad review only when the substantive scope materially changed.
- When independent review confirms a blocking systemic defect, update `docs/DEFECT_REGISTRY.json` and add or strengthen the earliest practical deterministic guard/test. Do not rely on prose memory alone where a machine-checkable boundary exists.
- Metadata-only edits and disposition of non-blocking recommendations do not invalidate completed code/security checks or require a new review.

## Merge control

The active merge path is current-state based. A PR is not ready while any of these are true:

- it is Draft;
- GitHub reports a merge conflict or unresolved mergeability;
- a substantive inline review thread remains unresolved;
- a current review is `CHANGES_REQUESTED`;
- `Quality Gates`, `dependency-review`, or `CodeQL` is missing, pending, cancelled, or failed;
- `Hunter Governance Review` is missing or non-stale pending;
- `Hunter Governance Review` is failed or errored;
- the head cannot be safely attributed to the PR.

`Hunter Merge Readiness` is the final current-state controller. Do not merge until it and every required gate on the final code head are green. Human merge approval remains required.

Do not run or recreate the retired `hunter_governance_preflight.py`, Draft Promotion, owner-`+1`, PR-body identity, or exact-pair hostile-review-attestation ceremony as merge prerequisites.
