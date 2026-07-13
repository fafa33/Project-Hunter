# Automation and Scheduler

Status: Operational layer. The canonical v2.1.x production analytical runtime is the evidence-backed Market Validation path documented in `docs/CANONICAL_RUNTIME_ARCHITECTURE.md`. Automation may still invoke `PipelineOrchestrator` for experimental plugin jobs, but scheduler logic is not the production analytical architecture.

## Architecture

The Automation and Scheduler layer lives in `src/hunter/automation/`.

The scheduler controls when configured jobs are due. The job runner claims work, acquires locks, invokes the existing `PipelineOrchestrator`, records lifecycle state, and releases locks. Analytical execution remains inside the Pipeline, Intelligence Engines, Fusion, and Opportunity Timing.

## Scheduler vs Pipeline Responsibilities

The scheduler decides when a job should run. It does not score, classify, fuse, rank, collect data, or render analytical outputs.

The `PipelineOrchestrator` controls how the analytical pipeline executes.

Automation converts job configuration into a typed pipeline execution plan and passes that plan to the runner. The plan controls orchestrator inputs such as selected Intelligence Engines, Fusion enablement, Opportunity Timing enablement, persistence policy metadata, current-state mode, replay mode, and explicit `as_of`. It does not duplicate pipeline stage behavior.

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

The scheduler supports a single-process polling loop controlled by `polling_interval_seconds`. Polling repeatedly checks due jobs, executes due work through the job runner, and supports graceful shutdown through `stop()`.

Completed one-time jobs are suppressed after a terminal persisted run state. A one-time job that has succeeded, partially completed, failed, or been cancelled will not be executed again by the scheduler.

## Current-State Execution

Current-state jobs may omit explicit `as_of`. Opportunity Timing derives a safe current-state boundary from the latest aligned Fusion effective time.

## Replay and Backtest Execution

Replay and backtest jobs require explicit timezone-aware `as_of`. Jobs without explicit replay/backtest `as_of` are rejected before pipeline execution.

Replay information is passed as a typed automation replay context, not as a loose metadata string. The runner installs that context on `PipelineContext` and uses it to set the pipeline clock for replay execution, allowing Fusion and Opportunity Timing to operate against the replay boundary.

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

Locks are released on success, partial completion, failure, timeout, cancellation, and restart recovery.

## Persistence

Automation persistence uses `AutomationJobRecord` and `AutomationRunRecord` through existing repository and UnitOfWork boundaries. Operational timestamps are persisted as operational metadata and do not alter analytical pipeline identities.

`AutomationJobRecord` represents job definition only. Repeated saves of the same job definition are idempotent even when save timestamps differ. Material job definition changes produce a new deterministic job-definition identity.

`AutomationRunRecord` separates run state identity from operational timestamps. Repeated persistence of the same automation run state remains idempotent even when `created_at`, `started_at`, or `finished_at` differ.

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

`cancel` loads the target run from known runner state or persistence, validates lifecycle transition rules, persists the cancelled state, releases any held in-process lock for the job target, and emits a cancellation event. It does not terminate threads or processes.

## Observability

Structured events include scheduler start/stop, job scheduled, claimed, started, skipped, blocked, succeeded, partial, failed, cancelled, lock acquired, and lock released.

## Failure Handling

Failures are recorded as failed automation runs. Locks are released in a `finally` path. Failed work is not marked successful. Partial pipeline outcomes are supported through persistence error detection.

Timeouts are enforced after monitored execution exceeds the configured job timeout. Timed-out jobs transition to failed, persist the failed state when persistence is configured, emit a timeout event, and release locks. Automatic retries are not implemented.

## Restart Behavior

The scheduler is replaceable and supports durable restart recovery when repositories are available. On startup recovery, persisted `scheduled`, `claimed`, and `running` automation runs are inspected. Scheduled runs are blocked for explicit review. Claimed or running runs are marked failed as abandoned by restart recovery. Stale in-process locks are released and recovery events are emitted.

## Known Limitations

No dashboard, distributed workers, Redis, Celery, Kubernetes, external queues, automatic trading, portfolio execution, recommendations, price targets, live provider integrations, or external notifications are implemented.

Timeout enforcement is cooperative and checked after synchronous runner execution returns or raises; it does not terminate running threads or processes.

CLI state is process-local unless a future command surface wires durable repository construction into the CLI.

## Future Distributed Scheduling

Future distributed scheduling should implement the lock contract with a durable distributed backend and preserve the current job, lifecycle, and runner contracts.
