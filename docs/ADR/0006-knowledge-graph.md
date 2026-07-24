# ADR 0006: Future Knowledge Graph

## Status

Accepted.

## Context

Hunter tracks technology dependencies, economic dependencies, on-chain surfaces, developer evidence, narrative evidence, entity relationships, and candidate relationships.

Future competition intelligence, network-effect analysis, intrinsic-value analysis, ecosystem modeling, dependency analysis, and historical analogues require relationship-aware modeling that cannot be represented safely through isolated engine outputs or flat identifiers alone.

However, introducing a graph implementation before identity, evidence, trust, and registry foundations are stable would create a competing source of truth, duplicate canonical records, and increase the risk of incorrect relationships.

## Decision

Hunter adopts relationship-aware graph modeling as the approved future architecture for relationships between:

- economic entities;
- asset and protocol representations;
- technologies;
- dependencies;
- competitors;
- ecosystems;
- evidence;
- analytical findings;
- investment theses;
- historical analogues.

Graph implementation must not become an authoritative or competing runtime before identity resolution, trust, evidence, and canonical registry foundations are production-stable.

The architectural decision concerns relationship-aware graph modeling.

It does not mandate:

- a specific graph database;
- a specific storage engine;
- a specific query language;
- replacement of existing repositories;
- immediate implementation;
- transfer of authority from existing canonical records.

Until a later accepted ADR explicitly changes the boundary:

- the Candidate Registry remains authoritative for candidate identity and lifecycle;
- canonical entity services remain authoritative for identity decisions;
- evidence repositories remain authoritative for persisted evidence;
- service-owned analytical paths remain authoritative for findings and analytical outputs;
- graph projections remain derived from existing canonical records.

## Consequences

- Current implementations must preserve stable identifiers for entities, representations, evidence, sources, findings, and relationships.
- Relationship-ready metadata may be added when justified, provided it does not create a second source of truth.
- Future graph projections must reference canonical records rather than duplicate or reinterpret them.
- Graph relationships must preserve provenance, temporal validity, ambiguity, conflicts, and missing evidence where applicable.
- A graph projection must not silently resolve identity conflicts or merge ambiguous entities.
- Future graph implementation must reuse existing registry, entity, evidence, and analytical records.
- Graph storage must not become an independent authority merely because it contains derived relationships.
- Knowledge graph implementation remains deferred until identity resolution, trust, evidence, and canonical registry foundations are production-stable.
- A later accepted ADR is required before any graph implementation changes runtime authority, persistence ownership, or canonical data boundaries.

## Alternatives Considered

### Keep relationships embedded in isolated engine outputs

Rejected because relationships would remain duplicated, difficult to query, and disconnected across analytical domains.

### Introduce a graph database immediately

Rejected because premature graph infrastructure would add operational and architectural complexity before canonical identity and evidence foundations are sufficiently stable.

### Replace existing repositories with a graph runtime

Rejected because it would create unnecessary migration risk and could replace established canonical authorities with a competing persistence model.

### Model relationships only in reports

Rejected because report-only relationships would not provide durable, queryable, replay-safe, or reusable relationship modeling.

### Leave graph adoption undecided

Rejected because current models must preserve the identifiers, provenance, and metadata needed for future relationship-aware architecture.

## Reasoning

Relationship-aware modeling can improve competition analysis, ecosystem analysis, dependency analysis, historical comparison, network-effect analysis, and explainability.

The architectural value comes from connecting existing canonical records, not from replacing them.

Accepting relationship-aware graph modeling now allows current implementations to preserve compatible identifiers and metadata while preventing premature introduction of a competing runtime or source of truth.