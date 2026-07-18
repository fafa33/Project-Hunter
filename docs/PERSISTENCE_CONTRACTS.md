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
- `OpportunityTimingAssessmentRecord`
- `OpportunityTimingSnapshotRecord`
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

## Opt-In Bitemporal Analytical Record

`AnalyticalRecord` is the reusable envelope for a future domain whose accepted architecture explicitly opts into generic analytical persistence. The envelope does not authorize a semantic output. A domain service must first own the meaning, identity, timestamps, input selection, provenance, lifecycle decision, and complete `AuthorizedAnalyticalWrite` plan under ADR 0009 and ADR 0010.

The contract contains:

- `effective_at`: when the represented assessment or fact is effective;
- `created_at`, exposed as `recorded_at`: the immutable time Hunter recorded it, supplied by the authorizing service;
- `known_at`: the latest explicit known-by boundary represented by the record, or `null` with a required `known_time_limitation`;
- record schema version, optional model version, and optional methodology/configuration fingerprint;
- ordered source-record IDs paired one-to-one with immutable source versions;
- evidence references, confidence, and explicit missing evidence;
- a stable logical identity and a distinct immutable record identity;
- optional `supersedes_id` and required correction reason;
- the domain payload, canonically serialized with the envelope.

Repositories do not create any of these values. Direct `save` on the analytical repository is rejected. The repository accepts only an `AuthorizedAnalyticalWrite` with an explicit `create` or `correct` operation. The plan is a service-facing persistence instruction, not proof that its semantic type has production authority.

### Identity And Idempotency

The existing canonical JSON serialization and canonical hash cover every envelope field and payload field. Repeating the same authorized identity and canonical payload is idempotent. Reusing that identity for different canonical content raises `PersistenceIdentityConflictError`.

The generic `persistence_records` table already stores the serialized envelope, schema version, recorded time, effective time, and canonical hash. Therefore this opt-in contract needs no schema migration and does not change serialization or stored bytes for existing record types.

### Corrections And Supersession

Authoritative analytical rows are never updated in place. A correction is a new record with a new identity, the same logical identity, an explicit predecessor in `supersedes_id`, and a non-empty correction reason. The service authorizes that lifecycle transition; the repository only verifies referential consistency and appends the supplied successor. Both predecessor and successor remain loadable, and `lineage` returns the complete immutable history in recorded order.

### Strict-Known Replay

`AnalyticalReplaySpec` is a service-supplied mechanical query boundary containing logical identity, effective `as_of`, and `known_by` cutoff. Strict-known selection includes only records whose:

- effective time is on or before `as_of`;
- recorded time is on or before `known_by`;
- explicit `known_at` is on or before `known_by`; and
- known-time limitation is absent.

Within that eligible set, an eligible correction supersedes its eligible predecessor. A correction recorded after the cutoff cannot alter the historical selection. A record with unknown legacy known time is preserved and queryable through ordinary history, but is never strict-known eligible. The contract never synthesizes missing legacy timestamps.

### Domain Adoption

A future accepted domain opts in by defining its semantic owner and authority in an ADR/Authority Registry update, constructing `AnalyticalRecord` and `AuthorizedAnalyticalWrite` in its service boundary, and using `RepositoryFactory.analytical_records()` inside an existing UnitOfWork. Rich domain-specific schemas remain valid and need not adopt this envelope.

This foundation does not create Opportunity, ranking, Probability, Pattern Matching, Technology Necessity, Committee, prediction-evaluation, or Dashboard analytical authority, and it does not activate any runtime store.

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

## Relationship to Opportunity Timing

`OpportunityTimingAssessmentRecord` and `OpportunityTimingSnapshotRecord` store deterministic timing assessments derived only from persisted Fusion records and historical timing records or snapshots.

Timing records preserve source Fusion IDs, all source run IDs, canonical evidence references, configuration fingerprint, model fingerprint, historical window, phase, window, score, confidence, risk, invalidation conditions, and explainability payloads.

Assessment and snapshot conflict semantics exclude operational `created_at` variance in the SQL repository so repeated identical analytical timing outputs remain idempotent across operational attempts.

## Known Limitations

- No database backend is implemented.
- No repository implementation is implemented.
- No query engine is implemented.
- No migrations are implemented.
- No transaction or index model is implemented.
- Pipeline persistence is opt-in and requires an explicit adapter.
- Opportunity Timing is deterministic and persistence-backed, but it does not implement automation, live providers, recommendations, expected returns, price targets, or machine-learning prediction.
