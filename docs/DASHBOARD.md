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
