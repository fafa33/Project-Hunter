# Deterministic Execution Identity

## Purpose

Project Hunter requires reproducible analytical identities before persistence, Intelligence Fusion, historical replay, and Opportunity Timing can be implemented safely.

This foundation defines canonical pipeline-run identity, deterministic object identity, stable canonicalization, versioned hashing, and explicit clock usage. It does not implement database persistence, Fusion, Opportunity Timing, automation, scheduling, dashboards, scoring, ranking, or trading signals.

## Analytical Identity vs Operational Correlation

Analytical identity identifies reproducible analytical facts and outputs. It is derived from canonical inputs such as run identity, target identity, engine identity, engine version, effective time, configuration fingerprint, input fingerprint, schema version, source reference, and normalized payload.

Operational correlation identifies one runtime attempt or operational event. It may use wall-clock time when explicitly requested, but it must remain separate from reproducible analytical identity.

## PipelineRun Model

`PipelineRun` is the immutable execution-run model in `src/hunter/execution/run.py`.

It contains:

- `run_id`
- `run_type`
- `target_id`
- `target_type`
- `configuration_fingerprint`
- `input_fingerprint`
- `engine_manifest_fingerprint`
- `requested_at`
- `effective_at`
- `started_at`
- `finished_at`
- `status`
- `parent_run_id`
- `replay_of_run_id`
- `metadata`

`run_id` is deterministic by default and excludes runtime-only execution timestamps such as `requested_at`, `started_at`, and `finished_at`.

## Run Types

Supported run types are:

- `live`
- `scheduled`
- `manual`
- `replay`
- `backtest`
- `test`

Run type is part of canonical run identity. Replay runs may also carry `replay_of_run_id`.

## Effective Time Semantics

`requested_at` records when a run was requested on the in-memory `PipelineRun` model for compatibility, but persisted operational timing belongs to `OperationalAttemptRecord`.

`effective_at` records the analytical time used for reproducible outputs.

`started_at` and `finished_at` are runtime execution timestamps and are not part of deterministic analytical identity. Persisted run lifecycle state is represented by operational attempt records, not by changing analytical run identity.

Analytical object generation uses the run's `effective_at` through the shared identity path. Observation timestamps from source evidence remain separate from generated analytical timestamps.

When `PipelineContext.ensure_run(...)` must create an implicit run with the default `SystemClock`, it uses a stable epoch effective time rather than silently placing wall-clock time into analytical identity. Callers that need live analytical time should pass an explicit `PipelineRun` or inject an explicit clock.

## Canonicalization

Canonicalization lives in `src/hunter/execution/canonicalization.py`.

It:

- sorts mapping keys
- preserves list and tuple ordering
- sorts sets and frozensets deterministically
- normalizes enum values
- normalizes timezone-aware datetimes to UTC
- normalizes decimals and finite floats
- distinguishes null from missing keys
- rejects unsupported objects, naive datetimes, and non-finite numbers
- avoids Python memory representations

## Hashing

Hashing lives in `src/hunter/execution/hashing.py`.

The strategy uses SHA-256 over canonical serialized bytes with:

- a hash format version
- an explicit namespace
- a schema version
- normalized payload

Python built-in `hash()` is not used.

## Clock Injection

Clock contracts live in `src/hunter/execution/clock.py`.

Supported clocks:

- `SystemClock`
- `FixedClock`

Tests use `FixedClock`. Analytical generated timestamps should flow through `PipelineRun.effective_at` or an injected clock.

## Identity Namespaces

The identity layer uses explicit namespaces including:

- `pipeline-run`
- `evidence`
- `signal`
- `observation`
- `insight`
- `intelligence`

Namespace separation prevents two object types with equivalent payloads from sharing an identifier.

## Schema Versioning

Deterministic identity includes explicit schema versions:

- `pipeline-run-v1` for run identity
- `analytical-identity-v1` for analytical object identity

Future canonicalization or identity changes can introduce new schema versions without silently changing existing identities.

## PipelineContext Integration

`PipelineContext` now carries an optional `PipelineRun` and a `Clock`.

`PipelineContext.ensure_run(...)` creates one canonical run for the execution when none exists. The same run is shared by directly supplied engines and plugin-hosted engines during a pipeline execution.

Existing context fields remain compatible:

- `values`
- `plugin_config`
- `events`
- `intelligence`

## Intelligence Integration

`EngineRunner` stabilizes engine-emitted Intelligence through `IntelligenceIdentityFactory` before validation and context emission.

The factory creates deterministic IDs for:

- Evidence
- Signal
- Observation
- Insight
- Intelligence

It also sets reproducible `generated_at` and signal timestamps from `PipelineRun.effective_at` in the shared generation path. Explicit model IDs remain supported when constructing Intelligence Layer objects directly.

## Replay and Backtest Semantics

Replay and backtest runs use explicit run types. Replay identity can reference `replay_of_run_id`.

Reproducible replay should reuse the same effective time, configuration fingerprint, input fingerprint, and engine manifest fingerprint where the intent is to reproduce prior analytical outputs.

## Backward Compatibility

The public Intelligence Layer dataclasses still accept explicit IDs and timestamps.

Engine domain analysis logic was not rewritten. Deterministic stabilization is applied centrally by `EngineRunner` for engine-executed analytical outputs.

Direct calls to engine `generate_intelligence(...)` without `EngineRunner` can still produce legacy runtime IDs. Use `EngineRunner` for deterministic analytical emission.

## Known Limitations

- No database persistence is implemented.
- No evidence repository is implemented.
- No Intelligence repository is implemented.
- No full pipeline stage graph is implemented.
- Existing confidence models may still use wall-clock freshness calculations.
- Some concrete engines still construct legacy IDs and timestamps internally before `EngineRunner` stabilizes emitted Intelligence.
- Direct plugin calls to `PipelineContext.emit_intelligence(...)` are not automatically stabilized because engine version and identity policy may be unavailable.
- Implicit default runs use a stable epoch effective time. Production callers should provide an explicit run when a live effective analytical time is required.

## Relationship to Persistence

This milestone prepares identity and run metadata for future persistence. It does not write records to storage.

Persistence stores analytical `PipelineRunRecord` separately from operational attempts. Evidence, Intelligence, and snapshots continue to use deterministic analytical identities.

## Relationship to Fusion

Fusion should consume persisted or emitted Intelligence objects with stable IDs, schema versions, engine versions, and run identity metadata.

Fusion is not implemented in this milestone.

## Relationship to Opportunity Timing

Opportunity Timing requires reproducible historical intelligence, stable run identities, and effective-time semantics.

This foundation is a prerequisite, but Opportunity Timing is not implemented in this milestone.
