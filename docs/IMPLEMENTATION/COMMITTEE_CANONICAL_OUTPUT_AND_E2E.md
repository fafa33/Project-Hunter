# Committee Canonical Output and End-to-End Closure

Governing issue: #61

Base commit: `fe9e39eb4def50cc7d9df37a47bf4216b48e72ba`

This workstream closes the remaining blockers from the post-PR71 audit.

## Required production changes

1. Replace `Path.cwd()` runtime ownership with a Hunter-owned application root or canonical runtime configuration. Installed execution from any working directory must resolve the same approved persistence database.
2. Select the generic Hunter SQL persistence database as the single canonical committee output authority consumed directly by `DashboardDataProvider`.
3. Persist committee votes, assessments, and cycle champion snapshots through `RepositoryFactory` repositories in one service-owned SQL transaction.
4. Remove standalone committee SQLite output from the authoritative production command. No dual-write or mirroring boundary is allowed.
5. Persist a pipeline/run envelope before committee evaluation. The envelope must bind candidate membership, effective cutoff, manifest fingerprint, input record IDs, and execution status.
6. On failure, roll back committee output records and persist the failed run state without publishing a partial champion or ranking.
7. Preserve all existing fail-closed identity, cutoff, freshness, lineage, lifecycle, authority-class, repository-fingerprint, unavailable-family, and alert restrictions.

## Mandatory end-to-end proof

Add one deterministic test that:

1. creates the canonical Hunter SQL database under an isolated application root;
2. persists authoritative input records with valid identity, lineage, lifecycle, authority, effective-time, and recorded-time metadata;
3. invokes the installed `hunter committee-authority` entry point from a different current working directory;
4. verifies a persisted pipeline/run envelope;
5. verifies committee votes, ranked assessments, and cycle champion snapshot in the same canonical SQL database;
6. constructs the real `DashboardDataProvider` with `RepositoryFactory` against that database;
7. verifies the `investment-committee` panel exposes the same assessment IDs, project IDs, decision, confidence, and source-record traceability;
8. verifies the dashboard path performs no writes;
9. verifies no standalone `data/committee/runtime/investment_committee.sqlite` database is created.

## Mandatory verification

```text
ruff check .
black --check .
mypy
pytest
```

Do not mark the PR ready or close issue #61 until all four gates pass on the exact final HEAD and an independent post-merge audit returns `APPROVED`.

## Non-goals

- valuation implementation
- comparative valuation implementation
- mispricing implementation
- asymmetry implementation
- forward scenarios
- portfolio allocation
- trade execution
- dashboard redesign
