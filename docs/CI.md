# Continuous Integration

Project Hunter uses GitHub Actions to run deterministic quality and merge-risk checks.

## Quality Gates

The `CI` workflow installs the development environment and runs:

```text
python scripts/hunter_pr_preflight.py
```

That shared preflight covers the repository-approved lint, formatting, type, and test suite. A real failure fails CI.

## Required merge checks

The current merge path requires:

- `Quality Gates`;
- `dependency-review`;
- `CodeQL`;
- `Hunter Governance Review`;
- final `Hunter Merge Readiness` success.

CI and security checks evaluate code. Governance Review evaluates current mergeability sanity. Merge Readiness combines current Draft/conflict/review/check state.

## Developer workflow

Before pushing a code-changing candidate, run when available:

```text
python scripts/hunter_pr_preflight.py
```

Fix real failures in code or tests. Do not weaken quality gates or add unrelated runtime changes merely to make CI green.

PR prose, branch naming, Issue identity, comments, reactions, and metadata edits are not CI or merge-check authority.
