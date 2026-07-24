# Committee Authority Final End-to-End Closure

Governing issue: #61

Base commit: `206f87e6aae11093b8d7b7ddf32a48bfb83c94f6`

This is the final scoped implementation contract for the remaining audited blockers.

## Required production behavior

1. An installed `hunter committee-authority` execution must persist valid outputs to canonical generic SQL and expose those same records through the real `DashboardDataProvider` without recomputation or alternate persistence.
2. On any validation, evaluation, or persistence failure, all votes, assessments, champion snapshots, and success records from that attempt must roll back.
3. After rollback, a durable failed `PipelineRunRecord` must be committed in a fresh transaction with no champion or partial ranking published.
4. The failed record must preserve the deterministic manifest/input fingerprints and reference the attempted run identity.
5. No standalone committee SQLite database may be created by production execution.

## Mandatory deterministic tests

### Installed CLI to dashboard

The test must:

- create an isolated absolute `HUNTER_APPLICATION_ROOT`;
- persist valid authoritative input records in `data/data_ops.sqlite`;
- invoke the installed CLI entry point from another working directory;
- load the resulting pipeline run, votes, assessments, and champion from generic SQL;
- construct the real `DashboardDataProvider` against the same repository factory;
- assert the dashboard committee projection contains the exact persisted assessment IDs, project IDs, decision, confidence, rank, and source-record IDs;
- compare persistence counts before and after dashboard reads to prove the dashboard path is read-only;
- assert the standalone committee database path does not exist.

### Failure rollback

The test must force an output persistence failure after evaluation and assert:

- no committee vote, assessment, champion, or succeeded run exists;
- one durable failed run exists;
- its fingerprints match the attempted manifest and input IDs;
- retry behavior remains deterministic.

## Final gates

One exact final HEAD must pass:

- `ruff check .`
- `black --check .`
- `mypy`
- `pytest`

After merge, a separate independent audit must return `APPROVED` before issue #61 is closed or the next roadmap phase begins.
