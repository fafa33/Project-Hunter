# Post-PR71 Committee Production CLI Audit

Date: 2026-07-22

Audited main commit: `3af8d5c4ed6751f42edd6f6d14569c05e11e9dd5`

Governing issue: #61

## Verdict

**BLOCKED**

PR #71 creates an installed production command and materially improves runtime-path ownership, but issue #61 remains incomplete.

## Verified

1. `hunter committee-authority MANIFEST.json` is routed through the installed `hunter.__main__` entry point.
2. The command no longer accepts caller-provided database-path arguments, but its current paths are still resolved relative to the caller's working directory and are therefore not yet canonical runtime-owned locations.
3. Manifest inputs contain persisted record IDs; the command hydrates records through Hunter SQL repositories before invoking the authoritative composition root.
4. The authoritative service still performs identity, cutoff, freshness, lineage, lifecycle, authority-class, and persisted-record fingerprint validation before scoring.

## Blocking findings

### B1 — Runtime paths remain CWD-relative

`_canonical_path()` resolves both `data/data_ops.sqlite` and `data/committee/runtime/investment_committee.sqlite` under `Path.cwd()`.

When the installed command runs outside the repository root, it can create or read databases under the caller's working directory rather than Hunter's approved runtime root. Removing explicit database-path arguments therefore does not yet establish canonical runtime-path ownership.

### B2 — Dashboard reads a different persistence authority

The production command persists committee assessments and the champion through `InvestmentCommitteeRepository`, whose intended output path is:

`data/committee/runtime/investment_committee.sqlite`

The real `DashboardDataProvider`, however, reads `InvestmentCommitteeAssessmentRecord` rows through `RepositoryFactory.investment_committee_assessments()` from the generic Hunter persistence database.

PR #71 does not write committee assessments or champion snapshots to that SQL repository. Therefore, the installed command can complete successfully while the actual dashboard committee panel remains empty or stale.

### B3 — Required end-to-end test remains absent

No deterministic test proves the required production chain:

`persisted authoritative SQL input -> installed hunter CLI -> authoritative resolver/service -> ranked assessment/champion persistence -> real DashboardDataProvider read projection`

The PR description explicitly listed this as still required, but the PR was merged without it.

### B4 — Output persistence is not one atomic authority boundary

The authoritative service writes to the standalone committee SQLite repository while input hydration uses the generic SQL session. Because the dashboard authority resides in the generic repository, any later attempt to mirror outputs introduces a dual-write boundary unless both output forms are coordinated transactionally or one canonical persistence owner is selected.

Issue #61 requires service-owned evaluation, ranking, champion selection, and atomic persistence. The current production command does not demonstrate atomic persistence across the output consumed by the dashboard.

### B5 — Manifest file is caller-controlled content without a persisted run envelope

The caller may choose candidate membership, effective cutoff, identity fields, and record-ID combinations through an arbitrary local JSON manifest. Record-level policy validation is fail-closed, but the production cycle request itself is not persisted as an approved pipeline/run record before execution.

This does not necessarily permit forged records, but it leaves the production invocation outside the established orchestrator/pipeline execution provenance expected by ADRs 0009 and 0010.

### B6 — Final-head verification evidence was incomplete before merge

PR #71 was merged while its own description still stated that the end-to-end test, exact final-head gates, and independent review were required. Completion cannot be claimed from that merge.

## Required closure work

1. Resolve runtime paths from Hunter's approved application/repository root or canonical runtime configuration, never from `Path.cwd()`.
2. Select one canonical output persistence authority consumed directly by the dashboard.
3. Persist committee votes, assessments, and champion snapshots through that authority inside the service-owned transaction boundary.
4. Make the installed production command execute within an approved persisted pipeline/run envelope, or wire it through the existing orchestrator/automation path.
5. Add the exact installed-CLI-to-dashboard end-to-end test, including source-record traceability and dashboard read-only assertions.
6. Run on the exact final fix HEAD:
   - `ruff check .`
   - `black --check .`
   - `mypy`
   - `pytest`
7. Perform another independent post-merge audit. Close issue #61 only if it returns `APPROVED`.

## Scope protection

This audit does not authorize valuation, comparative valuation, mispricing, asymmetry, forward scenarios, portfolio allocation, trade execution, or dashboard redesign.
