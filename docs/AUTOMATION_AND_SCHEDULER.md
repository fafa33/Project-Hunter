# Automation and Scheduler

## Architecture

The Automation and Scheduler layer lives in `src/hunter/automation/`.

The scheduler controls when configured jobs are due. The job runner claims work, acquires locks, invokes the existing `PipelineOrchestrator`, records lifecycle state, and releases locks. Analytical execution remains inside the Pipeline, Intelligence Engines, Fusion, and Opportunity Timing.

## Scheduler vs Pipeline Responsibilities

The scheduler decides when a job should run. It does not score, classify, fuse, rank, collect data, or render analytical outputs.

The `PipelineOrchestrator` controls how the analytical pipeline executes.

## Job Model

`AutomationJob` is immutable and includes job id, name, enabled state, schedule, timezone, target selection, run type, pipeline options, persistence policy, as-of policy, timeout, concurrency policy, job kind, and metadata.

Supported job kinds are:

- ingest/update project data
- current-state pipeline
- selected Intelligence Engines
- Fusion
- Opportunity Timing
- reports
- alerts
- historical replay
- backtest

Jobs do not duplicate pipeline logic.

## Scheduling

Supported schedules are hourly, every six hours, daily, weekly, cron expression, and one-time execution. All schedule calculations require timezone-aware datetimes.

## Current-State Execution

Current-state jobs may omit explicit `as_of`. Opportunity Timing derives a safe current-state boundary from the latest aligned Fusion effective time.

## Replay and Backtest Execution

Replay and backtest jobs require explicit timezone-aware `as_of`. Jobs without explicit replay/backtest `as_of` are rejected before pipeline execution.

## Run Lifecycle

Automation run lifecycle states are:

- scheduled
- claimed
- running
- succeeded
- partial
- failed
- cancelled
- skipped
- blocked

Invalid transitions raise explicit lifecycle errors.

## Concurrency

The default concurrency scope is job plus target. Overlapping execution for the same job and target is blocked before pipeline execution.

## Locking

`InProcessAutomationLock` provides local locking. The lock contract is replaceable for future distributed locks. Redis, external queues, distributed workers, and external lock services are not implemented.

## Persistence

Automation persistence uses `AutomationJobRecord` and `AutomationRunRecord` through existing repository and UnitOfWork boundaries. Operational timestamps are persisted as operational metadata and do not alter analytical pipeline identities.

## Configuration

Default configuration lives in `configs/automation.yaml`. It includes global enabled state, timezone, polling interval, jobs, schedules, target selectors, pipeline stage flags, run types, persistence policy, as-of rules, concurrency rules, timeouts, report flags, and alert flags.

## CLI

The CLI exposes:

- `hunter automation run-once JOB`
- `hunter automation start`
- `hunter automation status`
- `hunter automation list-jobs`
- `hunter automation show-job JOB`
- `hunter automation cancel RUN_ID`

`run-once` uses the same job runner as scheduled execution.

## Observability

Structured events include scheduler start/stop, job scheduled, claimed, started, skipped, blocked, succeeded, partial, failed, cancelled, lock acquired, and lock released.

## Failure Handling

Failures are recorded as failed automation runs. Locks are released in a `finally` path. Failed work is not marked successful. Partial pipeline outcomes are supported through persistence error detection.

## Restart Behavior

The scheduler is stateless and replaceable. Restart recovery is modeled by reloading configuration and checking due jobs again. Durable recovery can use persisted automation run records.

## Known Limitations

No dashboard, distributed workers, Redis, Celery, Kubernetes, external queues, automatic trading, portfolio execution, recommendations, price targets, live provider integrations, or external notifications are implemented.

## Future Distributed Scheduling

Future distributed scheduling should implement the lock contract with a durable distributed backend and preserve the current job, lifecycle, and runner contracts.
