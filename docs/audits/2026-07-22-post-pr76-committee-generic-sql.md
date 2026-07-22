# Post-PR76 Committee Generic SQL Audit

Date: 2026-07-22

Audited main commit: `3609c22a0cf004cf522317e00294406f9fd8c378`

Governing issue: #61

## Verdict

**BLOCKED**

PR #76 materially improves the production persistence boundary, but issue #61 closure is not yet demonstrated.

## Verified

1. The installed production command no longer instantiates the standalone `InvestmentCommitteeRepository`.
2. Committee votes, ranked assessments, and champion snapshots are persisted through repositories obtained from the canonical `RepositoryFactory`.
3. Production output persistence shares the same SQL session used for authoritative input resolution.
4. Deterministic pipeline-run records are created with manifest and input fingerprints.
5. A focused test exists for generic-SQL committee output persistence.

## Blocking findings

### B1 — Required installed-CLI-to-real-dashboard end-to-end proof is absent

PR #76 adds focused persistence coverage, but it does not add the mandatory test that starts with authoritative records in the canonical SQL database, invokes the installed `hunter committee-authority` entry point from another working directory, then reads the exact persisted assessments through the real `DashboardDataProvider` investment-committee panel.

The required proof must also assert source-record traceability, dashboard read-only behavior, and absence of the standalone committee database.

### B2 — Failure-state persistence is not proven

The merged implementation records running and succeeded pipeline-run states. There is no demonstrated deterministic path proving that evaluation or persistence failure rolls back all committee votes, assessments, and champion output while leaving a durable failed run record and no partial ranking.

### B3 — Exact final-head quality evidence is unavailable

The PR was merged while its own description still required Ruff, Black, mypy, pytest, review resolution, and the complete E2E proof before merge. GitHub returns no combined commit statuses for the final PR head `6401fb9faa6be80cb075276cccb9d969bafb02a4`.

Therefore the following cannot be claimed as verified on the exact merged implementation head:

- `ruff check .`
- `black --check .`
- `mypy`
- `pytest`

### B4 — Issue #61 completion criterion is not yet satisfied

Issue #61 requires the complete authoritative chain through the actual dashboard read path and an independent post-merge `APPROVED` audit. This audit cannot return `APPROVED` while B1 through B3 remain open.

## Required closure work

1. Add the exact installed-CLI-to-real-dashboard E2E test.
2. Add a deterministic failure-path transaction test proving rollback of all outputs and durable failed run state.
3. Run all four quality gates on one exact final fix HEAD and obtain green CI evidence.
4. Perform another independent post-merge audit.
5. Close issue #61 only if that audit returns `APPROVED`.

## Scope protection

This audit does not authorize valuation, comparative valuation, mispricing, asymmetry, forward scenarios, portfolio allocation, trade execution, or dashboard redesign.
