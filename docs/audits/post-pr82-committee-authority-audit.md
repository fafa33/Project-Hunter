# Post-PR82 Committee Authority Audit

Date: 2026-07-23

Audited main commit: `f16c250ed37179b9e2248484ac6766b50ae4f89a`

Governing issue: #61

## Verdict

**BLOCKED**

The installed-console-entry-point blocker is closed, but two contract requirements remain unproven.

## Verified

- The E2E test resolves the installed `hunter` console script with `shutil.which("hunter")`.
- The command is executed with `subprocess.run` from an unrelated working directory.
- Process exit code and JSON stdout are asserted.
- Committee assessment, champion snapshot, and pipeline-run state persist through canonical generic SQL repositories.
- `source_record_ids` remain traceable through the real `DashboardDataProvider`.
- Dashboard reads are proven not to add records.
- No standalone committee database or CWD-owned canonical database is created.
- Repeated invalid manifests preserve the original validation error without divergent failed records.
- Final implementation HEAD `0f39b7ce27eef02664987deb94d618b57030f79d` passed CI run #264 with Ruff, Black, mypy, and pytest.

## Blocking findings

### B1 — Persisted rank is not projected to the dashboard

The final contract requires the real dashboard projection to expose the exact persisted assessment rank. `DashboardDataProvider` committee rows omit `rank`, and the E2E test does not verify multi-candidate ranking through the dashboard.

### B2 — Post-evaluation persistence rollback is not proven

The current failure test uses duplicate project IDs and fails during input validation before evaluation and before committee output persistence. The final contract requires a forced output persistence failure after evaluation, followed by proof that all staged votes, assessments, champion output, and success records are rolled back while one durable failed run is committed.

## Required closure work

1. Project the exact persisted assessment `rank` into each dashboard committee row.
2. Add deterministic multi-candidate E2E assertions matching dashboard rank to persisted rank.
3. Force an output persistence failure after evaluation and after committee output has been staged.
4. Prove rollback of all committee output and success state, plus durable failed-run fingerprints and deterministic retry behavior.
5. Run Ruff, Black, mypy, and pytest on one exact final implementation HEAD and obtain green CI.
6. Perform another independent post-merge audit.

## Closure

Issue #61 remains open. It may be closed only after the remaining implementation is merged and a later independent post-merge audit returns `APPROVED`.

## Scope

This audit does not activate unavailable valuation families or authorize unrelated architecture changes.
