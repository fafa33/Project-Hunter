# Post-PR74 Committee Output Authority Audit

Date: 2026-07-22

Audited main commit: `467b654be735e6b9b79e35392b747030b68bca68`

Governing issue: #61

## Verdict

**BLOCKED**

PR #74 fixes only application-root resolution. It does not implement the remaining canonical output, atomic persistence, run-envelope, or end-to-end requirements stated in its own pull-request description.

## Verified

1. `Path.cwd()` is no longer used as the committee runtime authority.
2. Installed execution now requires `HUNTER_APPLICATION_ROOT`.
3. The configured application root must be absolute.
4. Committee runtime paths are resolved beneath that root and path escape is rejected.

## Blocking findings

### B1 — Standalone committee output remains authoritative

`src/hunter/committee/command.py` still constructs `InvestmentCommitteeRepository` using:

`data/committee/runtime/investment_committee.sqlite`

The real `DashboardDataProvider` reads committee assessments from `RepositoryFactory.investment_committee_assessments()` in the generic Hunter SQL database. The installed command therefore still writes to a different persistence authority than the dashboard reads.

### B2 — No atomic generic-SQL committee transaction

Votes, ranked assessments, and the champion are not persisted through the generic SQL repositories in one service-owned transaction. Input hydration and standalone committee output remain separate persistence boundaries.

### B3 — No persisted pipeline/run envelope

Candidate membership, cutoff, manifest fingerprint, input record IDs, and execution status are still not bound to a persisted production pipeline/run record before committee execution.

### B4 — Required end-to-end test remains absent

There is no deterministic proof of:

`authoritative SQL input -> installed CLI from another CWD -> persisted run envelope -> atomic committee output -> real DashboardDataProvider projection`

The test must also prove that no standalone committee database is created.

### B5 — PR #74 was merged while explicitly incomplete

PR #74 changed one production file and its own description listed generic SQL output, atomic persistence, run envelope, E2E testing, final quality gates, and independent review as still required before merge.

## Required closure work

1. Select the generic Hunter SQL database as the only canonical committee output authority.
2. Refactor the service-owned persistence boundary to write committee votes, ranked assessments, and champion snapshots through `RepositoryFactory` in one transaction.
3. Persist the production committee request/run envelope before execution and bind it to the resulting records.
4. Remove authoritative writes to `data/committee/runtime/investment_committee.sqlite`.
5. Add the exact installed-CLI-to-dashboard E2E test from the merged implementation contract.
6. Run `ruff check .`, `black --check .`, `mypy`, and `pytest` on one exact final HEAD.
7. Perform another independent post-merge audit. Close issue #61 only if it returns `APPROVED`.

## Scope protection

This audit does not authorize valuation, comparative valuation, mispricing, asymmetry, forward scenarios, portfolio allocation, trade execution, or dashboard redesign.
