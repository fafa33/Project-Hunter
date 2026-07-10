# Persistence Contracts

## Architecture

The Persistence Contracts layer defines how Project Hunter analytical records will be represented and addressed before any storage backend exists.

The layer lives under `src/hunter/persistence/` and provides immutable record models, repository interfaces, deterministic serialization helpers, identity-preservation helpers, and schema-version metadata.

It does not implement SQLite, PostgreSQL, SQLAlchemy, Alembic, database connections, transactions, indexes, caching, query execution, persistence repositories, Fusion, Opportunity Timing, Automation, or Scheduler behavior.

## Repository Interfaces

Repository interfaces live in `src/hunter/persistence/repositories.py`.

They define storage-agnostic contracts for:

- `PipelineRunRepository`
- `OperationalAttemptRepository`
- `EvidenceRepository`
- `SignalRepository`
- `ObservationRepository`
- `InsightRepository`
- `IntelligenceRepository`
- `FusedIntelligenceRepository`
- `SnapshotRepository`
- `ConfigurationRepository`
- `EngineManifestRepository`

Each repository contract supports:

- `save`
- `save_many`
- `load`
- `load_many`
- `exists`
- `delete`
- `query`
- `latest`
- `history`
- `snapshot`

These are interfaces only. No storage code is present.

## Canonical Records

Canonical immutable records live in `src/hunter/persistence/records.py`.

Implemented records:

- `PipelineRunRecord`
- `OperationalAttemptRecord`
- `EvidenceRecord`
- `SignalRecord`
- `ObservationRecord`
- `InsightRecord`
- `IntelligenceRecord`
- `FusedIntelligenceRecord`
- `SnapshotRecord`
- `ConfigurationRecord`
- `EngineManifestRecord`

Every record includes:

- stable identity
- schema version
- created time
- effective analytical time
- metadata
- validation
- deterministic serialization support

Records preserve analytical identities. They do not generate replacement analytical IDs.

## Versioning

Schema-version metadata lives in `src/hunter/persistence/versioning.py`.

Current persistence record schema:

- `persistence-record-v1`

The package includes version descriptors for future migration planning, but it does not implement migrations.

## Serialization

Serialization helpers live in `src/hunter/persistence/serialization.py`.

They support:

- deterministic record-to-dict conversion
- deterministic JSON serialization
- deserialization by record type
- identity preservation
- schema version preservation
- metadata preservation
- canonical byte generation

Serialization uses JSON-compatible structures and avoids Python object memory state.

## Identity

Persistence identity helpers live in `src/hunter/persistence/identity.py`.

Persistence records must preserve existing deterministic analytical identities from the Execution Identity and Intelligence layers. The persistence layer validates identities but does not invent new analytical IDs.

## Future Storage Backends

Future storage backends may implement the repository interfaces using SQLite, PostgreSQL, object storage, document stores, or other systems.

Those implementations must preserve:

- record immutability semantics
- deterministic identities
- schema versions
- effective-time semantics
- historical records
- serialization compatibility

Backend selection and implementation are outside this milestone.

## Relationship to Pipeline

Pipeline persistence integration now persists analytical `PipelineRunRecord` objects, operational attempt lifecycle records, and emitted Intelligence-related records through repository interfaces when an explicit adapter is supplied. Engines remain storage-agnostic.

## Relationship to Fusion

`FusedIntelligenceRecord` stores durable Fusion outputs for persisted-only downstream consumers.

The record preserves source Intelligence IDs, all source run IDs, canonical evidence groups, effective analytical window, confidence breakdown, configuration fingerprint, contribution-model fingerprint, contributions, provenance, corroboration, contradictions, dependencies, missing evidence, unified artifacts, narrative, and graph payloads.

Fused record conflict semantics exclude operational `created_at` variance in the SQL repository so repeated identical analytical Fusion outputs remain idempotent across operational attempts.

## Known Limitations

- No database backend is implemented.
- No repository implementation is implemented.
- No query engine is implemented.
- No migrations are implemented.
- No transaction or index model is implemented.
- Pipeline persistence is opt-in and requires an explicit adapter.
- No Fusion or Opportunity Timing behavior is implemented.
