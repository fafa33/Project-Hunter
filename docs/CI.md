# Continuous Integration

Project Hunter uses GitHub Actions to run the same deterministic quality gates expected before merge.

## Purpose

The CI workflow verifies that every pull request to `main` and every push to `main` preserves formatting, linting, typing, and test correctness. CI is infrastructure only; it does not call external services, require API keys, or change runtime behavior.

## Execution Order

The workflow runs in this order:

1. Checkout the repository.
2. Set up Python 3.11.13.
3. Restore the pip dependency cache from `pyproject.toml` and `requirements/ci-constraints.txt`.
4. Install the project with development dependencies constrained by `requirements/ci-constraints.txt`.
5. Run `ruff check .`.
6. Run `black --check .`.
7. Run `mypy`.
8. Run `pytest`.

Each step must pass before the next step runs. A failed quality gate fails the workflow.

## Required Quality Gates

The required gates are:

- Ruff linting.
- Black formatting check.
- Mypy type checking.
- Pytest test suite.

Tests in CI must remain deterministic and must not require live network access, external services, credentials, or API keys.

The workflow pins GitHub Actions by commit SHA and uses pinned Python dependency constraints so quality-gate behavior does not drift silently.

## Developer Workflow

Before opening or updating a pull request, run:

```text
ruff check .
black --check .
mypy
pytest
```

Fix real failures in the relevant code or tests. Do not weaken quality gates, hide failures, or add unrelated runtime changes to satisfy CI.
