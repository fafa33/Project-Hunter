# Post-PR68 Committee Authority Audit

Date: 2026-07-22

Audited main commit: `6675550fba6a245e0e5fbd992c82f08f43f68c24`

Governing issue: #61

## Verdict

**BLOCKED**

PR #68 materially improves fail-closed committee input handling, but the completion criterion in issue #61 is not yet demonstrated on `main`.

## Verified

1. Generic snapshot payloads containing `valuation`, `comparative_valuation`, `mispricing`, `asymmetry`, `risk`, or `backtesting_reliability` are rejected before authoritative scoring.
2. Explicit non-current lifecycle metadata is rejected; only `active` and `current` are accepted when a lifecycle state is present.
3. Repository-backed resolution still checks persisted identity, freshness, known-at cutoff, correction lineage, and caller/persisted fingerprint equality.
4. Focused regression coverage exists for lifecycle rejection and unavailable generic snapshot metrics.

## Blocking findings

### B1 — No actual production execution wiring

`src/hunter/committee/runtime.py` introduces `ProductionCommitteeRuntime`, but PR #68 changed no CLI, scheduler, automation, orchestrator, or other installed production entry point. The new class is therefore a callable library surface, not demonstrated production wiring.

Issue #61 requires the authoritative path to operate through Hunter's real runtime architecture. That requirement is not satisfied merely by adding an unreferenced class.

### B2 — Required full end-to-end proof is absent

The changed tests exercise resolver/service behavior, but no test begins with records persisted to the approved SQL runtime, invokes an installed production entry point, persists committee assessments/rankings/champion, and then reads the same authoritative result through the real dashboard provider/API path.

The required chain remains unproven:

`authoritative SQL input -> installed production execution -> resolver -> service -> ranking/champion persistence -> dashboard read projection`

### B3 — Persistence origin is not independently approved

`ApprovedCommitteeRuntimePaths` checks only that two caller-provided paths are absolute and distinct. Any caller may construct it with arbitrary databases. `ProductionCommitteeRuntime` then creates schema in that caller-selected database.

This does not independently bind committee input resolution to Hunter's approved runtime database/session construction. It proves path shape, not approved origin.

### B4 — Temporary debug workflow was merged into main

`.github/workflows/pytest-debug.yml` remains in production history and runs an additional full pytest job on pull requests. It was introduced only to expose transient failures and is not part of the governed production design. It should be removed before closure.

### B5 — Exact final-head quality evidence is incomplete

PR #68 was merged after repeated failing pytest runs. No independent post-merge evidence currently demonstrates all four issue #61 gates on the merged main commit:

- `ruff check .`
- `black --check src tests config alembic`
- `mypy`
- `pytest`

The merge commit currently has no combined commit statuses returned by GitHub.

## Required closure work

1. Wire authoritative committee execution into an existing installed CLI/scheduler/orchestrator production path.
2. Bind repository/session construction to Hunter's canonical runtime configuration rather than arbitrary caller-supplied paths.
3. Add the exact SQL-input-to-dashboard end-to-end test required by issue #61, including source-record traceability and read-only dashboard assertions.
4. Remove `.github/workflows/pytest-debug.yml`.
5. Run all four quality gates on the exact final fix HEAD and obtain green CI.
6. Perform another independent post-merge audit. Close issue #61 only if that audit returns `APPROVED`.

## Scope protection

This audit does not authorize valuation, comparative valuation, mispricing, asymmetry, forward scenarios, portfolio allocation, trade execution, or dashboard redesign.