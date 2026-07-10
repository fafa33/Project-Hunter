# Operational Attempts and Run Lifecycle

## Purpose

Operational attempts separate runtime execution state from immutable analytical run identity.

`PipelineRun` identifies a reproducible analytical execution. `OperationalAttemptRecord` records one operational attempt to execute that analytical run.

## Analytical Run Identity

`PipelineRun.run_id` remains deterministic and analytical.

It is based on:

- target identity
- effective analytical time
- configuration fingerprint
- input fingerprint
- engine manifest fingerprint
- run type where analytically relevant
- schema and identity version

Runtime timestamps do not alter analytical run identity.

## Operational Attempt Model

`OperationalAttemptRecord` is immutable and includes:

- `attempt_id`
- `run_id`
- `attempt_number`
- `requested_at`
- `started_at`
- `finished_at`
- `status`
- `error_summary`
- `warning_summary`
- `metadata`

An attempt records operational correlation, not analytical identity.

## Lifecycle State

Supported attempt states are:

- `pending`
- `running`
- `succeeded`
- `failed`
- `partial`
- `cancelled`

Valid transitions remain explicit:

- `pending -> running`
- `pending -> cancelled`
- `running -> succeeded`
- `running -> failed`
- `running -> partial`
- `running -> cancelled`

Each persisted state is an immutable attempt-state record linked by `attempt_id` and `run_id`.

## Repeated Attempts

The same `PipelineRun` may have multiple operational attempts.

This supports:

- failed attempt followed by successful attempt
- partial attempt followed by successful attempt
- repeated operational execution without changing analytical identity

Attempt numbers are assigned from existing persisted attempt history for the run.

## Persistence Boundary

The pipeline persistence adapter stores:

- one analytical `PipelineRunRecord`
- one or more immutable `OperationalAttemptRecord` state records
- emitted analytical artifacts
- deterministic snapshots when configured

Lifecycle transitions no longer mutate or replace the analytical run record.

## Stale Run Protection

If a `PipelineContext` already contains a `PipelineRun`, persistence validates it against current:

- target identity
- configuration fingerprint
- input fingerprint
- engine manifest fingerprint
- effective time

Stale runs fail before persistence begins.

## Engine Manifest Enforcement

Strict persistence mode rejects emitted Intelligence from undeclared engines unless a declared plugin identity is present.

Persisted artifacts preserve declaration metadata where available:

- engine ID
- engine version
- plugin ID
- plugin version

## Backward Compatibility

Pipeline execution remains in-memory when no persistence adapter is supplied.

Existing analytical IDs remain stable because operational attempt data is not part of analytical identity.

Engines remain storage-agnostic. SQLAlchemy remains inside `src/hunter/persistence/`.

## Known Limitations

- No retry scheduler is implemented.
- No timeout enforcement is implemented.
- No migrations are implemented.
- Attempt numbering is repository-backed and scoped to current persisted history.
