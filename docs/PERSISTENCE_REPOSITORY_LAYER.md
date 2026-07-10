# Persistence Repository Layer

## Architecture

The Persistence Repository Layer is the first concrete storage implementation for Project Hunter persistence contracts.

It lives under `src/hunter/persistence/sql/` and provides a SQLite-backed SQLAlchemy 2.x implementation. Analytical engines remain storage-agnostic and continue to depend only on canonical models, execution identity, and repository contracts.

This layer does not implement Fusion, Opportunity Timing, Automation, Scheduler, dashboards, distributed execution, caching, async execution, search, or vector storage.

Pipeline persistence integration lives above this layer in `src/hunter/persistence/integration/` and uses repository and UnitOfWork contracts.

## Repository Implementation

Concrete repositories live under `src/hunter/persistence/sql/repositories/`.

Implemented repositories:

- `SQLPipelineRunRepository`
- `SQLOperationalAttemptRepository`
- `SQLEvidenceRepository`
- `SQLSignalRepository`
- `SQLObservationRepository`
- `SQLInsightRepository`
- `SQLIntelligenceRepository`
- `SQLSnapshotRepository`
- `SQLConfigurationRepository`
- `SQLEngineManifestRepository`

Repositories accept and return immutable persistence records from `src/hunter/persistence/records.py`.

## ORM Boundary

SQLAlchemy is internal to the persistence layer.

The internal ORM model is `PersistenceRecordModel` in `src/hunter/persistence/sql/base.py`. It stores deterministic serialized persistence records and storage metadata. ORM models do not replace canonical analytical or persistence records.

No Intelligence Engine imports or depends on SQLAlchemy, SQLite sessions, transactions, ORM models, or database connections.

## Session Lifecycle

Session support lives in `src/hunter/persistence/sql/session.py`.

It provides:

- `SessionFactory`
- `SessionManager`
- scoped session support
- context manager support

`SessionManager.session()` commits on success, rolls back on failure, and closes the session.

## UnitOfWork

`UnitOfWork` lives in `src/hunter/persistence/sql/unit_of_work.py`.

It owns:

- transaction boundary
- commit
- rollback
- repository factory access
- session lifetime

It does not expose SQLAlchemy outside the persistence layer.

## RepositoryFactory

`RepositoryFactory` lives in `src/hunter/persistence/sql/factory.py`.

It creates concrete repositories with an injected SQLAlchemy session. This isolates the storage implementation from analytical code and future pipeline integration.

## Transaction Model

The repository layer uses normal SQLAlchemy session transactions.

Records are immutable:

- saving the same record with the same identity and same deterministic payload is idempotent
- saving a different payload under an existing identity raises an identity conflict
- delete is logical and does not physically remove stored rows

## SQLite Backend

The current backend uses SQLite through SQLAlchemy 2.x.

Schema helpers:

- `create_schema(engine)`
- `drop_schema(engine)`

Engine helper:

- `create_sqlite_engine(...)`

## Future PostgreSQL Backend

PostgreSQL can be added later behind the same repository contracts and factory boundaries.

Analytical code should not change when a future backend is introduced.

## Relationship to Persistence Contracts

This layer implements the repository contracts introduced in `docs/PERSISTENCE_CONTRACTS.md`.

It reuses deterministic serialization from `src/hunter/persistence/serialization.py` and preserves analytical identities created by the Deterministic Execution Identity layer.

## Known Limitations

- No automatic pipeline persistence is implemented; persistence remains explicit through the integration adapter.
- No migrations are implemented.
- No PostgreSQL backend is implemented.
- No indexing strategy beyond basic ORM columns is implemented.
- No physical purge behavior is implemented.
- No performance optimization is implemented.
- No async execution is implemented.
