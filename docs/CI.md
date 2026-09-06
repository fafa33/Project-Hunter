# Continuous Integration

Project Hunter uses GitHub Actions to run deterministic quality and merge-risk checks.

## Quality Gates

The `CI` workflow installs the development environment and validates the tree GitHub would actually merge with:

```text
python scripts/hunter_pr_preflight.py
```

That shared preflight covers the repository-approved lint, formatting, type, and test suite. A real failure fails CI.

When the integration tree is byte-for-byte the tree the trusted exact-head `Hunter / Pre-PR Preflight` run already validated, `CI` reuses that proof instead of re-running the identical suite. Reuse is decided by `scripts/hunter_validation_reuse.py` from immutable identity alone -- Git tree content identity, the validation-definition identity, and the pinned toolchain -- plus the trusted run record read by head SHA. A divergent integration tree, a missing or unsuccessful trusted run, or any unavailable evidence runs the full lane. See `docs/VALIDATION_STAGE_CONTRACT.md`.

## Required merge checks

The current merge path requires:

- `Quality Gates`;
- `dependency-review`;
- `CodeQL`;
- `Hunter Governance Review`;
- final `Hunter Merge Readiness` success.

CI and security checks evaluate code. Governance Review evaluates current mergeability sanity. Merge Readiness combines current Draft/conflict/review/check state.

## Developer workflow

Verify with focused tests and the relevant lint/type checks while implementing, then push through `.githooks/pre-push`. That boundary runs the deterministic push-safety gates; the authoritative full repository proof is the hosted exact-head `Hunter / Pre-PR Preflight` run of:

```text
python scripts/hunter_pr_preflight.py
```

Do not repeat a repository-wide validation on an unchanged HEAD. Fix real failures in code or tests. Do not weaken quality gates or add unrelated runtime changes merely to make CI green.

PR prose, branch naming, Issue identity, comments, reactions, and metadata edits are not CI or merge-check authority.
