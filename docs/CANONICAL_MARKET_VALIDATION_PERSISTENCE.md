# Canonical Market Validation Persistence

## Approved store decision

Canonical Market Validation uses a dedicated analytical SQLite store at the configured default path `data/market_validation/runtime/canonical_market_validation.sqlite`. The boundary is disabled by default in `configs/market_validation_persistence.yaml` and must be explicitly bootstrapped and enabled.

No previously approved Market Validation analytical store existed. `data/data_ops.sqlite` is operational scheduler/run storage and therefore is not an analytical authority store. The experimental derived-reasoning store is restricted to experimental semantic types. Dashboard/API and desktop models are read-only projections; Tokenomics, Evidence Intelligence, and Sufficiency retain separate schemas and authority boundaries. A dedicated store is consequently the smallest boundary compatible with ADR 0016.

`bootstrap_canonical_market_validation_store(path)` creates the existing generic persistence schema at an explicitly supplied path. It creates no Market Validation records, does not run validation, and performs no acquisition or network work. Opening a missing store does not create it.

## Service authorization and transaction

`MarketValidationPersistenceAuthorizationService` converts an already-computed `MarketValidationRun` into one `AuthorizedMarketValidationWrite`. It does not execute engines or calculate scores. The plan contains:

- one complete `MarketValidationRunRecord`;
- one `MarketValidationProjectResultRecord` per existing runtime result;
- production authority classification;
- deterministic record identities and canonical run/project identities;
- effective, recorded, and explicitly known or unknown-known-time context;
- schema, model, configuration, and methodology fingerprints;
- source record identities/versions and evidence references;
- the existing Hunter score, final score, ranks, score components/contributions, canonical committee fields, confidence, missing/stale evidence, warnings, and complete native payload;
- deterministic hashes of the unchanged CSV, JSON, and Markdown report bytes.

`CanonicalMarketValidationStore.persist()` uses one UnitOfWork for the complete plan. Project records are staged before the complete run record, and any exception rolls back the entire transaction. Repositories mechanically store authorized records and never calculate `hunter_score`, ranking, committee fields, timestamps, source versions, or replay cutoffs.

## Identity, correction, and replay

Record identities use separate `canonical-market-validation-run` and `canonical-market-validation-project` namespaces. Identical authorized plans are idempotent; different content under the same identity is rejected. Correction requires an explicit predecessor run, one predecessor for every project, and a reason. The canonical `validation_run_id` and project identity remain stable while predecessor records remain immutable and queryable.

Reads support exact run/project identity, projects for a run, ordered run/project lineage, and strict-known as-of selection. Strict-known selection requires effective time, recorded time, and explicit known time to be at or before their caller-authorized cutoffs. Records with unknown known time remain durable but are excluded from strict-known replay.

## Semantic and consumer boundaries

The persisted project fields are canonical Market Validation fields owned by `EvidenceBackedProjectExecutor` / Market Validation. Standalone experimental Committee records cannot substitute for them and use a different store, semantic type, identity, and authority classification.

Persistence is not wired into Dashboard API, Operational Corpus, Opportunity, Pipeline/Fusion, Timing, automation, scheduler, data-ops storage, or desktop console. It does not change Market Validation scoring, weights, ranking, committee decisions, evidence selection, reports, or existing CLI output. Future read projections require separate authorization and must remain consumers rather than owners.
