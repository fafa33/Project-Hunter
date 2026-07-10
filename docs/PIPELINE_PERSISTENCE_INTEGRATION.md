# Pipeline Persistence Integration

## Purpose

Pipeline Persistence Integration connects deterministic pipeline execution to persistence contracts and repository implementations.

It persists pipeline run lifecycle records, emitted analytical artifacts, and deterministic snapshots without adding analytical logic to the pipeline, engines, or repositories.

## Architecture

The integration package lives in `src/hunter/persistence/integration/`.

It contains:

- `PipelinePersistenceAdapter`
- lifecycle state validation
- persistence policy settings
- Intelligence-to-record conversion
- history helpers
- deterministic snapshot creation
- integration-specific exceptions

The adapter depends on UnitOfWork and repository contracts. It does not import SQLAlchemy or expose SQLAlchemy types.

## Adapter Boundary

`PipelinePersistenceAdapter` accepts a UnitOfWork factory. The factory supplies repository access and owns commit, rollback, and session closure.

`PipelineOrchestrator` accepts an optional persistence adapter. Without an adapter, existing in-memory behavior remains unchanged.

## Pipeline Lifecycle

Supported lifecycle states are:

- `pending`
- `running`
- `succeeded`
- `failed`
- `partial`
- `cancelled`

Valid transitions are explicit:

- `pending -> running`
- `pending -> cancelled`
- `running -> succeeded`
- `running -> failed`
- `running -> partial`
- `running -> cancelled`

Invalid transitions raise an explicit lifecycle error.

## Transaction Boundary

When persistence is enabled, the adapter opens a UnitOfWork before lifecycle persistence begins.

The normal boundary is:

1. Persist pending lifecycle state.
2. Persist running lifecycle state.
3. Execute the pipeline.
4. Validate run identity consistency.
5. Persist emitted artifacts.
6. Persist final lifecycle state.
7. Persist a deterministic snapshot when configured.
8. Commit.

Transaction failures roll back through the UnitOfWork.

## Persistence Policies

The default policy is `atomic`.

`atomic` commits analytical artifacts and final lifecycle state together. Artifact persistence failures roll back the transaction.

`run_durable` rolls back failed artifact persistence, then opens a separate UnitOfWork to persist a failed run lifecycle state for auditability.

Retry logic is not implemented.

## Run State Model

Pipeline lifecycle records are immutable persistence records. Pending and running records use deterministic lifecycle record identities linked to the canonical run identity through metadata.

The terminal run record uses the canonical `PipelineRun.run_id`.

Runtime timestamps such as `started_at` and `finished_at` are operational metadata and do not alter analytical run identity.

## Artifact Persistence

The adapter persists canonical records for:

- Evidence
- Signal
- Observation
- Insight
- Intelligence

It validates standardized Intelligence objects before conversion and preserves existing deterministic IDs. It does not generate replacement analytical IDs.

## Artifact Relationships

Records preserve:

- PipelineRun to Intelligence through `pipeline_run_id`
- Intelligence to Evidence through `evidence_ids`
- Intelligence to Signals through `signal_ids`
- Intelligence to Observations through `observation_ids`
- Intelligence to Insights through `insight_ids`
- project and engine references
- effective analytical time

## History Semantics

History does not mean mutable revisions of one identity.

Historical sequences are distinct immutable records linked by target, engine, effective time, run identity, and schema version.

The integration helpers distinguish:

- identity lookup
- run history
- target history
- engine history
- effective-time history
- artifact history
- snapshot history

## Snapshot Semantics

Snapshots are immutable `SnapshotRecord` objects.

A pipeline snapshot includes:

- deterministic snapshot identity
- target
- effective time
- pipeline run identity
- included artifact identities
- schema version
- metadata

Snapshots are deterministic for a completed persisted run and do not overwrite prior snapshots.

## Stale Identity Protection

When persistence begins, `PipelineContext` freezes identity-bearing inputs.

Before artifacts and final state are persisted, the adapter validates:

- target identity
- configuration fingerprint
- input fingerprint
- engine manifest fingerprint
- effective time

If they changed, persistence fails explicitly.

## Engine Manifest Enforcement

Persistence-enabled execution requires a declared engine manifest.

When strict enforcement is enabled, emitted Intelligence must come from declared engines unless plugin-hosted emission is the declared execution surface.

## Configuration

Default configuration lives in `configs/pipeline_persistence.yaml`.

It supports:

- enabled
- backend name
- persistence policy
- artifact toggles
- snapshot toggles
- history settings
- strict identity validation
- engine manifest enforcement

No secrets or database URLs are included.

## Failure Handling

Pipeline execution failures persist a failed lifecycle record and re-raise the original error.

Artifact persistence failures follow the configured policy.

Invalid lifecycle transitions, stale run identity, undeclared engines, and identity conflicts fail explicitly.

## Observability

The adapter records structured persistence events on `PipelineContext.persistence_events`, including:

- run persistence started
- run state changed
- artifact persisted
- artifact skipped as idempotent
- identity conflict
- transaction committed
- transaction rolled back
- persistence failed

## Backward Compatibility

Persistence is explicit and opt-in.

Running `PipelineOrchestrator` without an adapter preserves the existing in-memory execution contract.

Intelligence Engines remain storage-agnostic and direct engine calls remain possible without persistence.

## Future PostgreSQL Support

Future PostgreSQL support should implement the same repository and UnitOfWork contracts. Analytical code and engine code should not change.

## Known Limitations

- No retry logic.
- No migrations.
- No scheduler or automation.
- No async persistence.
- SQLite query behavior remains basic.
- Pending and running lifecycle states are represented as immutable lifecycle records linked to the canonical run identity.

## Relationship to Fusion

This integration is a prerequisite for Fusion because it provides durable run identity, artifact relationships, and snapshot history.

Fusion is not implemented here.

## Relationship to Opportunity Timing

Opportunity Timing will need persisted effective-time histories and snapshots. This milestone provides those foundations but does not implement timing logic.
