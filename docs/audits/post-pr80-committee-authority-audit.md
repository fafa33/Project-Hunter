# Post-PR80 Committee Authority Audit

Date: 2026-07-23

Audited main commit: `1dca366e8ea0413c1dad55fd52995782a721abb6`

Governing issue: #61

## Verdict

**BLOCKED**

PRs #79 and #80 close the durable rollback, failed-retry idempotency, canonical SQL persistence, dashboard traceability, and exact final-head CI blockers. One required end-to-end proof remains incomplete, so issue #61 cannot yet be closed.

## Verified

1. Committee votes, assessments, champion snapshots, and pipeline-run records persist through canonical generic SQL repositories.
2. Failed committee execution rolls back votes, assessments, and champion output before persisting a durable failed run in a separate transaction.
3. Repeating the same invalid manifest preserves the original committee validation error and reuses one idempotent failed record.
4. The dashboard reads the persisted investment-committee assessment through the real `DashboardDataProvider` and exposes `source_record_ids` without writing new records.
5. Tests assert that neither the legacy standalone committee database nor a CWD-owned canonical database is created.
6. PR #80 final implementation HEAD `8d1d72328c7032dc71b5fb5330d03a0f8ef7594e` passed CI run #260, including Ruff, Black, mypy, and pytest.

## Blocking finding

### B1 — The installed console entry point is not actually executed by the E2E test

Issue #61 and the prior audit require an installed-CLI-to-real-dashboard proof from an unrelated working directory.

The merged E2E test imports:

```python
from hunter.__main__ import main as installed_main
```

and invokes:

```python
installed_main(["committee-authority", str(manifest_path)])
```

This directly calls the Python function inside the pytest process. It does not resolve and execute the installed `[project.scripts]` console command `hunter = "hunter.__main__:main"` through the operating-system command path.

Therefore the test does not prove that the packaged console script is installed, discoverable, receives arguments correctly, preserves `HUNTER_APPLICATION_ROOT`, works from an unrelated CWD, and reaches the same canonical persistence and dashboard path.

## Required closure work

1. Replace or supplement the direct-function E2E call with the actual installed `hunter` executable, resolved with `shutil.which("hunter")` and invoked by `subprocess` from the unrelated working directory.
2. Assert the process exit code and parse its real stdout result.
3. Preserve all existing assertions for canonical SQL persistence, source traceability, dashboard read-only behavior, absence of standalone/CWD databases, rollback, durable failed state, and repeated invalid-manifest behavior.
4. Run Ruff, Black, mypy, and pytest on one exact final fix HEAD and obtain green CI.
5. Perform another independent post-merge audit.
6. Close issue #61 only if that audit returns `APPROVED`.

## Scope protection

This audit does not authorize valuation, comparative valuation, mispricing, asymmetry, forward scenarios, portfolio allocation, trade execution, or dashboard redesign.
