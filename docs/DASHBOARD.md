# Dashboard

## Purpose

The Dashboard layer renders a deterministic, read-only operational view of persisted Project Hunter state.

It does not run Intelligence Engines, Fusion, Opportunity Timing, Automation, scoring, reporting logic, recommendations, trading logic, external providers, or live API calls.

## Architecture

The Dashboard package lives in `src/hunter/dashboard/`.

It contains:

- immutable dashboard view models
- configuration loading
- repository-facing data provider
- HTML renderer
- dashboard-specific exceptions

The dashboard consumes persistence repositories through contracts. SQLAlchemy remains inside `src/hunter/persistence/`.

## Data Sources

The current dashboard reads persisted:

- PipelineRun records
- OperationalAttempt records
- AutomationRun records
- FusedIntelligence records
- OpportunityTimingAssessment records

It displays existing persisted state only. Missing records produce empty panels rather than inferred data.

## Rendering

`HtmlDashboardRenderer` renders a deterministic static HTML document.

The renderer escapes all text values and does not execute scripts.

## Configuration

Default configuration lives in `configs/dashboard.yaml`.

Supported settings:

- `enabled`
- `title`
- `output_path`
- `sqlite_path`
- `max_rows`
- `include_automation`
- `include_pipeline`
- `include_fusion`
- `include_opportunity_timing`

## CLI

The CLI exposes:

- `hunter dashboard build --sqlite-path PATH --output dashboard.html`

The command opens the configured SQLite persistence store, builds a dashboard view from repositories, renders static HTML, and writes the output path.

The CLI also exposes `hunter dashboard-api [--pretty]` for the native operational console and other read-only operational consumers. Its sole public contract is `dashboard-api.v1`, with this deterministic top-level key order:

1. `schema_version`
2. `generated_at`
3. `system`
4. `scheduler`
5. `automation`
6. `jobs`
7. `providers`
8. `discovery`
9. `validation`
10. `predictions`
11. `corpus`
12. `database`
13. `logs`
14. `health`

Operational and validation corpus projections exist only below `corpus.operational` and `corpus.validation`; they are not duplicated as top-level fields. Each projection is explicitly `operational-only` and `read_only`, and contains `path`, `record_count`, `last_update`, `status`, and `error`. Status is one of:

- `available`: the source is present and contains one or more readable records;
- `empty`: the source is present and contains no readable records;
- `unavailable`: the source is absent;
- `error`: the source is present but cannot provide a valid record count, with the deterministic reason in `error`.

An unavailable or failed source retains `record_count: null`; the API never fabricates zero. These corpus projections report operational/audit-store state only. They do not establish analytical authority, correctness, ranking, scoring, or recommendation semantics. The API is a read-only projection governed by ADR 0016 and `docs/ANALYTICAL_AUTHORITY_REGISTRY.md`.

### Canonical prediction-evaluation projection

`predictions.canonical_evaluation` is the additive, nested
`canonical-prediction-evaluation-dashboard.v1` projection. The Dashboard API remains
`dashboard-api.v1`; its top-level keys and the existing `closed`, `due`, and `open`
operational counts are unchanged. The nested projection is read-only and labels its
authority as `canonical-evaluation`, owned by `PredictionEvaluationService` under ADR
0019. It never treats operational closure as correctness and never evaluates or
aggregates records itself.

The projection reads only the explicitly configured dedicated canonical store. A
missing or disabled configuration is `unavailable`; an absent store is `unavailable`;
a bootstrapped store with no canonical rows is `empty`; and an unreadable store is
`error`. These states retain `lifecycle: null` and `aggregates: null`, so absence is
never reported as zero. A populated store exposes strict-known, cutoff-eligible,
current lifecycle records and mutually compatible current accuracy/calibration
snapshot pairs. Superseded, future-effective, unknown-known-time, foreign, legacy
operational, and incompatible records are excluded. Multiple compatible cohorts stay
separate. Below the policy minimum, status is `insufficient-sample` and accuracy,
interval, and calibration values remain null as recorded by the canonical service.

Each aggregate preserves its canonical record IDs, schema version, aggregate policy,
cohort/filter/window, model/methodology/configuration versions, effective/recorded/
known times, exact source evaluation IDs and fingerprint, exclusions, sample counts,
accuracy interval, Brier score, and reliability bins. The provider performs no store
bootstrap, write, lifecycle transition, evaluation, aggregation, scheduling, network
call, or fallback to Operational Corpus, Market Validation, Opportunity, Timing,
Backtest, historical-validation files, or experimental stores.

### Analytical-store readiness

Dashboard API v1 exposes the operational `analytical-store-readiness.v1` projection at
`health.analytical_stores`. The API's top-level keys remain unchanged. The nested view
is identical to the additive `hunter status` projection and covers Tokenomics,
Evidence Intelligence, Sufficiency, canonical Market Validation, experimental derived
reasoning, experimental Opportunity, and canonical Prediction Evaluation.

States are `unavailable`, `absent`, `schema_only`, `populated`, `stale`,
`migration_required`, `unreachable`, or `failed`. Counts are operational facts and are
null when they cannot be read safely; they are never converted into scores or health,
quality, accuracy, calibration, sufficiency, or investment KPIs. No registered store
currently declares a store-wide freshness policy, so modification times and guessed or
unknown timestamps cannot produce `stale`. Inspection is read-only and never creates,
bootstraps, migrates, repairs, activates, or writes a store.

## Boundaries

The Dashboard is presentation-only.

It must not:

- mutate persisted analytical records
- trigger pipeline execution
- trigger automation jobs
- calculate new scores
- generate recommendations
- perform trading or portfolio actions
- call external providers

## Known Limitations

The current milestone implements a static HTML dashboard, not a web server.

There is no authentication, authorization, live refresh, REST API, websocket support, or dashboard editing UI.
