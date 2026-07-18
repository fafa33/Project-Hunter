# Analytical Store Readiness and Safe Bootstrap

## Scope and authority

This operational workflow covers every registered analytical-store boundary: Tokenomics, Evidence Intelligence, Sufficiency, canonical Market Validation, experimental derived reasoning, experimental Opportunity, and canonical Prediction Evaluation. Readiness is operational metadata only. It does not create or establish analytical authority, evidence sufficiency, correctness, scoring, ranking, accuracy, calibration, or recommendations. The classifications and owners in ADR 0016, ADR 0019, and `ANALYTICAL_AUTHORITY_REGISTRY.md` remain unchanged.

The registry excludes `data_ops.sqlite`, Operational Corpus, Dashboard/read-model caches, automation state, discovery/candidate storage, and generic filesystem artifacts. Experimental derived reasoning and experimental Opportunity are separate logical boundaries in one configured physical store and are counted only by their authorized semantic namespaces.

Bootstrap authorization is separate from analytical-write authorization. Bootstrap creates only parent directories, a SQLite database, tables, indexes, and schema changes already defined by the applicable package migration/schema. It performs no acquisition, provider or network calls and writes no observations, evidence, classifications, assessments, results, candidates, scores, or placeholders.

## Commands

Inspection never creates a missing store and accepts an intentionally omitted path:

```text
hunter analytical-store status tokenomics [--path PATH]
hunter analytical-store status evidence-intelligence [--path PATH]
hunter analytical-store status sufficiency [--path PATH]
```

Bootstrap requires an explicit path:

```text
hunter analytical-store bootstrap tokenomics --path PATH
hunter analytical-store bootstrap evidence-intelligence --path PATH
hunter analytical-store bootstrap sufficiency --path PATH
```

Both workflows emit deterministic JSON containing `store`, `state`, `reason`, `path`, `schema_id`, `schema_status`, `analytical_record_count`, and per-table structural counts. Bootstrap is idempotent: rerunning it applies the existing idempotent schema and preserves existing records. It exits successfully only when the resulting state is `schema_only` or `populated`.

## State definitions

| State | Meaning |
|---|---|
| `unavailable` | No path was intentionally supplied; no claim is made about a store. |
| `absent` | The configured file does not exist. Inspection does not create it. |
| `schema_only` | The expected schema is present and every inspected data-bearing table is empty. This is not analytical availability or sufficiency. |
| `populated` | At least one stored row exists. Readiness counts records but does not validate, interpret, or authorize them. |
| `stale` | A documented store-specific freshness policy and reliable recorded time prove that a populated store exceeds its threshold. No currently registered store declares such a policy. |
| `migration_required` | The file is readable SQLite but one or more required schema objects are missing. |
| `unreachable` | The configured path cannot be used as a store file. |
| `failed` | Deterministic configuration, inspection, or bootstrap validation failed, including a corrupt/non-SQLite file. |

The `schema_status` field independently reports `unavailable`, `absent`, `current`, `mismatch`, or `unreadable`. Empty, missing, corrupt, and unreachable states therefore remain distinct.

## Unified status projection

`hunter status` adds `analytical_stores`, versioned as `analytical-store-readiness.v1`. Text output includes an `ANALYTICAL STORES` section; JSON preserves the complete typed projection. Dashboard API v1 exposes the identical structure only below `health.analytical_stores`; its top-level schema is unchanged.

Each entry contains its identifier, authority classification, state and reason, enabled/configured flags, safe configured path, schema and migration status, data-bearing count, per-boundary table count, latest reliable recorded time when available, freshness policy/status, inspection time, and projection version. Counts are null when inspection cannot safely obtain them. `schema_only` is the only state that truthfully reports zero after successful schema inspection.

Tokenomics, Evidence Intelligence, and Sufficiency have no documented store-wide freshness threshold or uniformly reliable recorded-time field, so freshness is `not_applicable` and latest recorded time is unavailable. Canonical Market Validation, experimental derived reasoning, experimental Opportunity, and canonical Prediction Evaluation preserve reliable `created_at` when matching records exist, but likewise declare no store-wide freshness policy. Unknown or legacy time, filesystem modification time, empty state, and record counts never produce `stale`.

## Safe failure and compatibility

Inspection uses read-only SQLite access and never repairs a store. A failed bootstrap is reported as `failed` or `unreachable`; it is never labeled healthy. Operators may correct the path or schema and explicitly rerun bootstrap.

Unified inspection never invokes bootstrap. It opens existing SQLite paths in read-only mode, performs structural checks and scoped reads, and makes no migration, repair, provider, network, automation, scheduler, acquisition, evaluation, aggregation, scoring, or ranking call. Disabled stores remain `unavailable`; inspection does not change default enablement or create their path.

The existing explicit bootstrap workflow remains a dedicated CLI namespace. The unified view is additive to `hunter status` and nested under Dashboard health. It does not alter Dashboard API v1 top-level keys, desktop OperationalConsole inputs, automation schedules, package analytical behavior, default enablement, or runtime paths. Sufficiency bootstrap creates its schema only; it does not invent registry metadata when none was explicitly configured and creates no sufficiency assessment or result.
