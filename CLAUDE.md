# Claude Code Instructions — Project Hunter

## Source of truth

At the start of work, inspect the repository, current branch/HEAD, relevant Issue or PR, and the architecture/ADR material that actually governs the requested area. Repository state overrides remembered chat state.

Also inspect `docs/DEFECT_REGISTRY.json` for defect classes relevant to the work. A previously understood defect class is not a new one-off finding: if it recurs, treat the existing hardening as insufficient and strengthen the earliest reliable reusable guard.

## Scope and architecture

Implement only the user-authorized objective. Do not invent authorities, registries, persistence semantics, replay semantics, or architecture that the repository has not authorized.

For a materially architectural change, verify the applicable architecture before production implementation. For an ordinary local implementation or cleanup, do not perform a repository-wide ADR audit unless the change genuinely crosses those boundaries.

## Implementation and verification

Use the smallest correct change. Do not weaken tests or evidence requirements to make a check pass.

Before pushing an ordinary code-changing candidate, run when available:

```text
python scripts/hunter_pr_preflight.py --mode normal
```

Normal mode requires the deterministic Artifact Guard plus Ruff, Black, Mypy, and Pytest to pass. The Artifact Guard validates the canonical defect registry and changed governed artifacts. For an intentional tests-first RED commit only, create `.hunter-preflight-mode` containing exactly `tests-first-red` and commit that marker on the exact branch head being pushed. That mode is valid only when the Artifact Guard, Ruff, Black, and Mypy pass and Pytest exits with a real test-failure result. It is a Draft tests-first hygiene signal, never merge readiness. Before implementation or any green candidate resumes, remove `.hunter-preflight-mode`, commit that removal on the branch head being pushed, and return to normal mode.

Before treating a hosted Pre-PR run as proof or opening a PR based on it, verify that the run's commit SHA exactly equals the current branch HEAD. A run number, "latest" label, or remembered green result alone is not exact-head evidence.

CI failures are actionable: diagnose and fix real failures without asking for another prompt when the fix is in scope.

When independent review confirms a blocking systemic defect, update `docs/DEFECT_REGISTRY.json` in the correcting or immediately following governed contribution and add or strengthen a deterministic guard/test appropriate to the failure class. Do not rely on prose memory alone where a machine-checkable boundary exists.

A machine guard must itself be justified by canonical authority or a demonstrated recurring defect. Prefer semantic or structural invariants over brittle wording checks. Do not make TARGETED work satisfy FULL-review ceremony, do not block canonically valid equivalent wording, and do not add a merge-blocking check merely because it is easy to automate. A guard that rejects valid canonical output or creates false-positive merge blockage is itself a defect and must be corrected.

For any contribution that adds or materially changes a parser, validator, artifact guard, workflow guard, or merge-blocking gate, perform an adversarial bypass pass before requesting independent review. Test the semantic boundary, not only the happy-path text. Ask both: (1) can content that does not actually render/execute as the required construct still satisfy the guard, and (2) can a canonically valid equivalent construct be rejected? Where practical, add paired negative/positive fixtures for representation traps relevant to the parser or gate, including literal/example regions, fenced or otherwise non-semantic content, quoted/escaped text, negation, partial or duplicate declarations, wrong field/column/scope binding, and unrelated matching structure. This requirement applies to guard/parser/gate changes only; it is not ceremony for ordinary PRs.

## Pull requests and review

A Draft PR is optional for incomplete work; it is not a required ceremony for an already locally verified contribution.

Independent review is proportional to risk and scope. Treat findings as:

- **blocking** when they demonstrate a real correctness, security, architecture, persistence/replay, evidence-integrity, migration, or equivalent merge risk;
- **non-blocking** when they are maintainability, style, readability, optional refactoring, extra-test, or future-hardening suggestions that do not make the contribution unsafe.

Fix blocking findings and verify the affected behavior. Do not implement non-blocking suggestions merely to make review comment count reach zero. A metadata-only edit or non-blocking disposition does not require another full review. Re-review only the affected scope after a substantive code change; repeat a broad review only when the substantive scope materially changes.

## Merge readiness

The active merge controls are current GitHub state, not process history. A PR must not merge while Draft, conflicted/unresolved, carrying unresolved substantive review threads or current `CHANGES_REQUESTED`, or while required checks are not green.

Required checks are `Quality Gates`, `dependency-review`, `CodeQL`, and `Hunter Governance Review`, with `Hunter Merge Readiness` as the final current-state controller.

PR prose, Issue identity, branch naming, commit-message formatting, top-level PR comments, reactions, metadata-only edits, superseded historical runs, Draft Promotion signals, and hostile-review attestation markers are not merge authority.

Never merge until the final required gates are green. Human merge approval remains required.

## Continuation

When the next in-scope action is clear and permitted, continue without asking for another prompt. Stop only for a real scope/architecture decision, unavailable permission/environment, independent-role boundary, or unresolved blocking defect.
