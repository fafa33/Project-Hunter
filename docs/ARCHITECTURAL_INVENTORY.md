# Project Hunter — Complete Architectural Inventory

**Inventory date:** 2026-07-18
**Repository:** `/Users/farhadafshari/Documents/GitHub/Project-Hunter`
**Scope:** all tracked and currently present untracked repository files, excluding `.git` internals, bytecode caches, and the opaque contents of compiled Mach-O executables.
**Method:** static inspection of source, configuration, documentation, tests, persisted runtime artifacts, packaging metadata, CI, and desktop bundles. No implementation or runtime data was changed.

## 1. Executive architecture summary

Project Hunter is a Python 3.11+ evidence-backed crypto-market discovery and investment-intelligence system. Its current repository contains four overlapping architectural strata:

1. **Canonical production runtime (v2.1.x):** CLI → acquisition/validation repositories → `EngineValidationSource` adapters → `EvidenceBackedProjectExecutor` → weights → production timing → committee fields → explainability → reports.
2. **Experimental plugin runtime:** `PipelineOrchestrator` → plugin manager → Intelligence Engine runner → optional Fusion → experimental Opportunity Timing → persistence adapter.
3. **Operational and trust expansion:** discovery/candidate registry, evidence intelligence, competitive intelligence, data sufficiency, tokenomics, on-chain capital flow, scheduler/data-ops, operational corpus, dashboard API, and desktop status console.
4. **Historical and validation systems:** historical acquisition, point-in-time replay, bias controls, market validation, backtesting/calibration, scenarios, graphs, probability, pattern matching, necessity, and committee assessment.

The authoritative production classification is in `docs/CANONICAL_RUNTIME_ARCHITECTURE.md` and ADR 0007. It explicitly classifies `PipelineOrchestrator`, plugin Intelligence Engines, Fusion, `src/hunter/opportunity/`, and the standalone committee package as experimental for v2.1.x. Several newer operational/trust packages are implemented and tested but are not yet classified in that canonical-runtime document.

### Architectural invariants

- Evidence-first records with provenance, freshness, confidence, conflicts, and explicit missingness.
- Deterministic execution identity and canonical serialization.
- Immutable/idempotent persistence; conflicting payloads under one identity are rejected.
- Point-in-time replay cutoffs and anti-look-ahead controls.
- Scheduling is separate from analytical execution.
- Explainability is a first-class output.
- No automatic trading or portfolio execution exists.

## 2. Repository and build topology

| Area | Inventory |
| --- | --- |
| Python package | `src/hunter/`; 32 top-level subpackages, 8 top-level modules, 430 Python files total (including package initializers) |
| Tests | `tests/`; 79 pytest modules |
| Configuration | `configs/`; 38 YAML files |
| Runtime data | `data/`; JSONL, JSON, CSV, and SQLite stores organized by subsystem |
| Architecture/docs | `docs/`, 15 ADRs, sprint/release material, plus top-level strategy/engine specifications |
| Packaging | `pyproject.toml`; Hatchling with custom backend `build_backend/project_hunter_build.py` |
| Runtime dependencies | PyYAML 6.x and SQLAlchemy 2.x |
| Developer dependencies | pytest, Ruff, Black, mypy, types-PyYAML |
| CI | `.github/workflows/ci.yml` |
| Desktop surface | Objective-C macOS console under `desktop/OperationalConsole/`; built `.app` bundles under `desktop/.../dist` and repository `dist/` |

The custom build backend delegates to Hatchling after temporarily moving non-package top-level project material out of the build context. The installed console command is `hunter = hunter.__main__:main`.

## 3. Runtime entrypoints

### 3.1 Python and package entrypoints

| Entrypoint | Role |
| --- | --- |
| `python -m hunter` | Executes `hunter.__main__.main`, which delegates to `hunter.cli.main` |
| `hunter` | Installed console script; same CLI path |
| `hunter.cli:main(argv)` | Single, large argparse command router and composition root for operational commands |
| `hunter.pipeline:PipelineOrchestrator.run()` | Experimental plugin/intelligence/fusion pipeline API |
| `hunter.market_validation.runner:MarketValidationRunner` | Canonical market-validation run coordinator |
| `hunter.market_validation.runner:EvidenceBackedProjectExecutor` | Canonical evidence-to-project scoring executor |
| `hunter.automation.scheduler:AutomationScheduler` | In-process polling scheduler |
| `hunter.automation.runner:AutomationJobRunner` | Claims, locks, executes, times, and persists automation runs |
| `hunter.data_ops:DataOpsExecutor` | Executes configured ingestion/rebuild operations by invoking CLI subprocesses |
| `hunter.dashboard_api:build_dashboard_api` | Produces the operational dashboard JSON contract |
| `hunter.operational_status:build_status` | Aggregates process, scheduler, provider, corpus, DB, logs, and health status |
| `desktop/OperationalConsole/build.sh` | Compiles the native macOS operational console |
| `desktop/.../HunterOperationalConsole` | Native app executable; invokes bundled `hunter_status.py`/CLI status data |

### 3.2 Plugin entrypoints

`pyproject.toml` publishes eight `hunter.plugins` entrypoints: `developer-intelligence`, `macro-intelligence`, `narrative-intelligence`, `news-intelligence`, `onchain-intelligence`, `protocol-intelligence`, `social-intelligence`, and `whale-intelligence`. Each resolves to its engine package's `create_plugin` factory. Funding, governance, security, and tokenomics foundation engines are not registered as package entrypoints.

### 3.3 Canonical production flow

```text
hunter CLI
  -> acquisition (CoinGecko / DefiLlama / GitHub / narrative / macro / whale)
  -> validation + file repositories
  -> market_validation.acquisition_sources
  -> EvidenceBackedProjectExecutor
  -> WeightEngine
  -> timing.OpportunityTimingEvidenceEngine
  -> ProjectValidationResult committee fields
  -> DecisionExplainabilityEngine
  -> MarketValidation/Evidence renderers
```

### 3.4 Experimental orchestration flow

```text
AutomationJobRunner (optional)
  -> AutomationPipelineExecutor
  -> PipelineOrchestrator
       -> PluginManager lifecycle
       -> EngineRunner
       -> optional FusionEngine
       -> optional opportunity.OpportunityTimingEngine
       -> optional PipelinePersistenceAdapter / UnitOfWork
```

## 4. Complete package and module inventory

Module lists below include every source module. Package `__init__.py` files primarily define/re-export the public surface unless otherwise noted.

### 4.1 Top-level `hunter` modules

| Module | Responsibility |
| --- | --- |
| `__init__.py` | Package marker/version-facing surface |
| `__main__.py` | `python -m hunter` bridge |
| `cli.py` | All CLI parsing, command dispatch, dependency composition, and text/JSON output |
| `pipeline.py` | Experimental `PipelineOrchestrator` |
| `data_ops.py` | Data-operation job installation/execution/status/history/failure reporting |
| `dashboard_api.py` | Aggregated operational API payload and JSON renderer |
| `operational_status.py` | Machine and human status/health aggregation with exit-code policy |
| `operational_corpus.py` | Predictions, opportunities, outcomes, closures, validation samples, readiness, and recovery recording |

### 4.2 Acquisition, identity, and authentication

**`hunter.acquisition` (17 files):** `collector`, `configuration`, `contracts`, `exceptions`, `models`, `normalizer`, `pipeline`, `project_identifiers`, `registry`, `repositories`, `scheduler`, `validator`; providers `coingecko`, `defillama`, `github`; and both package initializers. Provides typed collection requests/evidence/runs/checkpoints, provider registry, canonical normalization, validation, retry/cache configuration, in-memory and JSONL repositories, scheduling, and project/provider identifier resolution.

**`hunter.auth` (7 files):** `configuration`, `contracts`, `credentials`, `providers`, `registry`, `validation`, `__init__`. Resolves environment-backed credentials, reports capabilities/quota, validates provider authentication, and avoids embedding secrets in configuration.

**`hunter.discovery` (8 files):** `automation`, `configuration`, `engine`, `identity`, `models`, `providers`, `repository`, `__init__`. Discovers market candidates from seed/CoinGecko/DefiLlama/GeckoTerminal/DexScreener, merges lifecycle state, resolves aliases/contracts/repositories/domains, records conflicts/checkpoints/coverage, and persists the candidate registry in SQLite.

**`hunter.narrative` (5 files):** `configuration`, `discovery`, `provider`, `repository`, `__init__`. Production-facing narrative source discovery, provider normalization/validation, statistics, and JSONL persistence. This is distinct from the experimental narrative Intelligence Engine.

**`hunter.historical_acquisition` (5 files):** `models`, `pipeline`, `providers`, `repository`, `__init__`. Point-in-time evidence collection from CoinGecko, DefiLlama, GitHub activity, governance archives, RSS announcements, Internet Archive, and reconstructed sources, with snapshot/run/validation persistence.

### 4.3 Production evidence and analytical packages

**`hunter.macro` (7):** `configuration`, `engine`, `models`, `providers`, `repository`, `validation`, `__init__`. Production macro evidence acquisition and snapshot engine with required metrics, provider registry, normalization/validation, failures, and JSONL storage.

**`hunter.whale` (7):** `configuration`, `engine`, `models`, `providers`, `repository`, `validation`, `__init__`. Derivatives/whale evidence from Binance, Bybit, and OKX adapters; metric validation, disagreement handling, snapshots/failures, and JSONL storage.

**`hunter.onchain` (8):** `adapters`, `automation`, `configuration`, `engine`, `models`, `registry`, `repository`, `__init__`. EVM JSON-RPC transport, configured chain/asset/surface registry, raw observation normalization, capital-flow records/snapshots, provider cooldown state, automation management, and JSONL storage.

**`hunter.market_validation` (9):** `acquisition_sources`, `configuration`, `contracts`, `evidence`, `models`, `renderer`, `repositories`, `runner`, `__init__`. Canonical runtime bridge. Converts persisted source families into `EngineValidationSource` inputs, assesses evidence coverage, executes deterministic/evidence-backed project validation, compares runs, and renders reports. Includes in-memory run repositories.

**`hunter.weights` (5):** `configuration`, `engine`, `models`, `renderer`, `__init__`. Applies configured engine weights, normalizes contributions, calculates evidence coverage, and recommends—not automatically activates—weight changes.

**`hunter.timing` (4):** `engine`, `models`, `repository`, `__init__`. Canonical production timing engine. Combines latest acquisition, graph, macro, and whale dependencies into assessments, rebuild/freshness state, classifications, reasoning chains, and JSONL history.

**`hunter.explainability` (4):** `engine`, `models`, `renderer`, `__init__`. Converts market-validation results into contribution breakdowns, evidence traces, decision trees, sensitivity/invalidation conditions, and auditable rendering.

### 4.4 Graphs, scenarios, and derived decision engines

**`hunter.graph` (4):** `engine`, `models`, `repository`, `__init__`. Technology dependency nodes/edges, cycle/path validation, centrality/criticality/replacement/switching metrics, and JSONL graph runs.

**`hunter.economic` (4):** `engine`, `models`, `repository`, `__init__`. Economic dependency graph with revenue/capital impact, moat/criticality/path analysis, validation, and JSONL runs.

**`hunter.scenario` (4):** `engine`, `models`, `repository`, `__init__`. Applies configured scenarios across technology/economic paths, records impacts/results/runs, and compares scenarios.

**`hunter.probability` (9):** `configuration`, `contracts`, `engine`, `metrics`, `models`, `ranking`, `renderer`, `repositories`, `__init__`. Derived probability assessment from engine consensus/conflict, evidence quality/freshness, and historical reliability; in-memory repositories.

**`hunter.patterns` (9):** `configuration`, `contracts`, `engine`, `metrics`, `models`, `ranking`, `renderer`, `repositories`, `__init__`. Matches current evidence dimensions against configured historical project patterns and reports similarity/confidence/missing evidence.

**`hunter.necessity` (9):** `configuration`, `contracts`, `engine`, `metrics`, `models`, `ranking`, `renderer`, `repositories`, `__init__`. Technology necessity/gap/rotation assessment over technology graph inputs; in-memory repositories.

**`hunter.committee` (10):** `backtesting`, `configuration`, `contracts`, `engine`, `metrics`, `models`, `ranking`, `renderer`, `repositories`, `__init__`. Experimental standalone persisted investment-committee voting, eligibility, decisions, champions, rankings, backtest summaries, and record conversion. Canonical production committee output instead lives on market-validation project results.

**`hunter.backtest` (4):** `engine`, `models`, `repository`, `__init__`. Calibration and project/engine backtest metrics, historical reliability, rank correlation, scenario reliability, comparison, and JSONL persistence.

**`hunter.historical` (14):** `benchmarks`, `bias_controls`, `configuration`, `contracts`, `cutoff`, `models`, `outcomes`, `performance`, `renderer`, `replay`, `repository`, `snapshot_builder`, `validation`, `__init__`. Historical cases/snapshots/replays, point-in-time cutoff enforcement, leakage and survivorship controls, benchmark outcomes, decision outcomes, performance/calibration/committee challenges, and comprehensive JSONL persistence.

### 4.5 Trust, evidence, competitive, sufficiency, and tokenomics layers

**`hunter.evidence_intelligence` (11):** `automation`, `claims`, `conflicts`, `intake`, `models`, `provider`, `relationships`, `reporting`, `repository`, `validation`, `__init__`. Versioned evidence documents/spans/entities/claims/conflicts/relationships; source-authority and lifecycle events; secure AI extraction boundary with prompt-injection checks; literal-support validation; deterministic confidence; SQLite persistence and replay-aware reporting.

**`hunter.competitive` (13):** `algorithmic`, `automation`, `conflicts`, `identity`, `inputs`, `models`, `peer_sets`, `policies`, `predicates`, `relationships`, `reporting`, `repository`, `__init__`. Selects trusted evidence/claims, builds direct and algorithmic competitive relationships and peer sets, applies cutoff-aware conflict detection, reports assessments, and persists versioned SQLite records.

**`hunter.sufficiency` (12):** `assessor`, `automation`, `evaluator`, `identity`, `migrations`, `models`, `policies`, `registry`, `reporter`, `repository`, `validation`, `__init__`. Data-requirement registry, source availability/directness/quality/freshness evaluation, cross-source compatibility/disagreement, degraded-mode policy, conclusion gating, lineage links, reports, migrations, and SQLite persistence.

**`hunter.tokenomics` (7):** `identity`, `ingestion`, `migrations`, `models`, `providers`, `repository`, `__init__`. Canonical token/representation identity, evidence artifacts/claims, supply/allocation/vesting/unlock/holder/venue/flow observations, conflicts and sufficiency links; official disclosure, public aggregator, and EVM ERC-20 supply adapters; immutable SQLite persistence. This operational package is distinct from the experimental tokenomics foundation engine.

### 4.6 Intelligence model and experimental engine framework

**Core `hunter.intelligence` modules (13):** `aggregator`, `confidence`, `contracts`, `evidence`, `exceptions`, `insight`, `intelligence`, `metadata`, `observation`, `registry`, `signal`, `validator`, `__init__`. Defines typed Evidence → Signal → Observation → Insight → Intelligence records, provenance/metadata/confidence, aggregation, validation, and registries.

**Engine framework modules (11):** `engines.base`, `builder`, `capabilities`, `categories`, `contracts`, `evidence_contracts`, `exceptions`, `factory`, `registry`, `runner`, `service`, plus `engines.__init__`. Defines engine lifecycle/contracts, categories/capabilities, construction/registration, evidence contracts, sequential execution, and service composition.

Engine packages:

| Engine | Modules | Status/role |
| --- | --- | --- |
| Developer | `analyzers`, `collectors`, `confidence`, `configuration`, `engine`, `exceptions`, `foundation`, `indicators`, `models`, `normalization`, `__init__` | Full experimental plugin; GitHub/developer signals |
| Macro | `analyzers`, `collectors`, `confidence`, `configuration`, `engine`, `exceptions`, `indicators`, `models`, `normalization`, `scoring`, `__init__` | Full experimental plugin; distinct from production `hunter.macro` |
| Narrative | `analyzers`, `clustering`, `collectors`, `confidence`, `configuration`, `engine`, `evolution`, `exceptions`, `lifecycle`, `models`, `normalization`, `__init__` | Full experimental plugin |
| News | `analyzers`, `classifiers`, `collectors`, `confidence`, `configuration`, `engine`, `exceptions`, `models`, `normalization`, `__init__` | Full experimental plugin |
| On-chain | `analyzers`, `anomalies`, `collectors`, `confidence`, `configuration`, `contracts`, `engine`, `exceptions`, `flows`, `foundation`, `holders`, `indicators`, `models`, `normalization`, `__init__` | Full experimental plugin/foundation; distinct from operational `hunter.onchain` |
| Protocol | `analyzers`, `collectors`, `confidence`, `configuration`, `engine`, `exceptions`, `indicators`, `models`, `normalization`, `__init__` | Full experimental plugin |
| Social | `analyzers`, `collectors`, `confidence`, `configuration`, `engine`, `exceptions`, `indicators`, `influence`, `manipulation`, `models`, `normalization`, `sentiment`, `__init__` | Full experimental plugin |
| Whale | `analyzers`, `collectors`, `confidence`, `configuration`, `engine`, `exceptions`, `models`, `normalization`, `__init__` | Full experimental plugin; distinct from production `hunter.whale` |
| Funding | `foundation`, `__init__` | Foundation-only engine |
| Governance | `foundation`, `__init__` | Foundation-only engine |
| Security | `foundation`, `__init__` | Foundation-only engine |
| Tokenomics | `foundation`, `__init__` | Foundation-only engine; distinct from operational `hunter.tokenomics` |

**`hunter.intelligence.fusion` (17):** `alignment`, `confidence`, `configuration`, `contracts`, `contradiction`, `corroboration`, `deduplication`, `dependencies`, `engine`, `exceptions`, `graph`, `models`, `narrative`, `normalization`, `weighting`, `__init__`. Experimental deterministic fusion pipeline for temporal alignment, lineage/dependency graphs, deduplication, corroboration/contradiction, weighting/confidence, fused narratives, and target-specific results.

**`hunter.opportunity` (22):** `acceleration`, `confidence`, `configuration`, `confirmation`, `contracts`, `divergence`, `engine`, `exceptions`, `history`, `metrics`, `models`, `momentum`, `persistence`, `phases`, `ranking`, `renderer`, `repositories`, `risk`, `scoring`, `temporal`, `windows`, `__init__`. Experimental fusion-backed opportunity timing and assessment model; explicitly not the canonical production `hunter.timing` implementation.

### 4.7 Plugins, automation, dashboards, and execution infrastructure

**`hunter.plugins` (9):** `contracts`, `discovery`, `exceptions`, `lifecycle`, `loader`, `manager`, `registry`, `validator`, `__init__`. Entry-point/module discovery, YAML loading, dependency/version validation, ordered registry, lifecycle, and shared `PipelineContext`/plugin contracts.

**`hunter.automation` (13):** `configuration`, `contracts`, `exceptions`, `execution`, `jobs`, `lifecycle`, `locking`, `models`, `persistence`, `runner`, `scheduler`, `status`, `__init__`. Typed schedules/jobs/plans/replay contexts, lifecycle state machine, in-process locks, pipeline adapter, persistence record conversion, restart recovery, polling, and observability. Timeout checks are cooperative; no distributed worker/queue exists.

**`hunter.dashboard` (7):** `configuration`, `contracts`, `data`, `exceptions`, `models`, `rendering`, `__init__`. Repository-backed dashboard view models/panels and static HTML rendering. Separate `dashboard_api.py` supplies the newer operational JSON surface.

**`hunter.execution` (7):** `canonicalization`, `clock`, `exceptions`, `hashing`, `identity`, `run`, `__init__`. Canonical value normalization, stable hashes/fingerprints/IDs, system/fixed clocks, intelligence identity factory, and immutable pipeline-run identity.

## 5. Persistence inventory

### 5.1 Canonical persistence abstraction

**`hunter.persistence` (9 core modules):** `contracts`, `exceptions`, `identity`, `models`, `records`, `repositories`, `serialization`, `versioning`, `__init__`.

- `records.py` contains immutable record types for pipeline/attempt/automation, committee, market validation, Evidence/Signal/Observation/Insight/Intelligence, fusion, opportunity timing, snapshots, configuration, and engine manifests.
- `repositories.py` defines repository contracts for each record family.
- `serialization.py` provides canonical dict/JSON/bytes conversion.
- `identity.py` enforces identity preservation.
- `versioning.py` models schema versions and migration plans but does not execute general migrations.

### 5.2 SQL implementation

**`hunter.persistence.sql` (11):** `base`, `engine`, `exceptions`, `factory`, `metadata`, `session`, `unit_of_work`, package initializer; repositories `base`, `records`, initializer.

- SQLAlchemy 2.x, currently SQLite.
- One internal `PersistenceRecordModel` stores typed deterministic serialized records plus metadata.
- `SessionManager` commits/rolls back/closes; `UnitOfWork` owns transaction and repository lifetimes.
- `RepositoryFactory` exposes 20 concrete SQL repositories: pipeline run, operational attempt, automation job/run, committee vote/assessment/champion, market-validation run/project result, evidence, signal, observation, insight, intelligence, fused intelligence, opportunity assessment/snapshot, configuration, engine manifest, and generic snapshot.
- Save is idempotent for identical identity/payload; differing payload raises identity conflict; deletion is logical.

### 5.3 Pipeline persistence integration

**`hunter.persistence.integration` (8):** `adapter`, `artifacts`, `exceptions`, `history`, `lifecycle`, `policies`, `snapshots`, `__init__`. Sits above repositories and wraps experimental pipeline execution with run/attempt lifecycle, manifest validation, artifact conversion/persistence, atomic/best-effort policies, snapshots, events/issues, and queryable history.

### 5.4 Domain repositories and physical stores

| Subsystem | Repository style | Default physical area |
| --- | --- | --- |
| Acquisition | In-memory or append/read JSONL | `data/acquisition/` |
| Macro / Whale | JSONL | `data/macro/`, `data/whale/` |
| Narrative discovery | JSONL | `data/narrative_discovery/` |
| On-chain | JSONL | `data/onchain/` |
| Technology / economic graphs | JSONL | `data/technology_graph/`, `data/economic_graph/` |
| Scenario / timing / backtest | JSONL | `data/scenarios/`, `data/timing/`, `data/backtesting/` |
| Historical acquisition/validation | JSONL plus CSV/JSON snapshots | `data/historical_acquisition/`, `data/historical_validation/` |
| Discovery registry | SQLite | configured by `configs/discovery.yaml`; `data/discovery/` area |
| Data operations | SQLite plus JSONL run detail | `data/data_ops.sqlite`, `data/data_ops/` |
| Competitive intelligence | Purpose-built SQLite repository | `data/competitive/runtime/competitive.sqlite` |
| Evidence intelligence | Purpose-built SQLite repository | default `data/evidence_intelligence/runtime/evidence_intelligence.sqlite` (directory absent in this snapshot) |
| Data sufficiency | Purpose-built SQLite repository and migrations | `data/sufficiency/runtime/sufficiency.sqlite` |
| Tokenomics | Purpose-built immutable SQLite repository and migrations | configured runtime DB; no checked-in DB in this snapshot |
| Operational corpus | JSON/JSONL state/event stores | `data/operational_corpus/` |

The domain-specific SQLite repositories (`competitive`, `evidence_intelligence`, `sufficiency`, `tokenomics`, discovery) are separate from the generic SQLAlchemy persistence repository layer and manage their own schemas/upserts/versioned keys.

## 6. CLI inventory

The CLI has 49 top-level command names (including seven compatibility placeholders) and a large nested surface.

### 6.1 Compatibility/placeholders

`analyze`, `discover`, `validate`, `whales`, `reports`, `backtesting`, and `alerts` accept an optional project slug and currently print a queued-operation response rather than execute the canonical pipeline.

### 6.2 Operational and system commands

- `status [--json]`; `dashboard-api [--pretty]`; `dashboard build`.
- `automation start|status|list-jobs|show-job|run-once|cancel`.
- `data-ops install|status|run-now|history|failures`.
- `auth status|validate|providers|quota|doctor`.
- `engines status|coverage|validate`.
- `rank` routes to committee, probability, pattern, or necessity ranking according to sort mode.

### 6.3 Discovery and trust commands

- `discovery run|sync|status|stats|coverage|validate|registry|candidates|report|candidate|screen|queue|conflicts|identity|automation`.
- `evidence-intelligence coverage|source-authority|document-lifecycle|claim-lifecycle|conflict-lifecycle|explain|providers|security-audit|automation`.
- `competitive coverage|report|peers|competitors|explain|conflicts|automation`.
- `sufficiency coverage|requirements|assess|missing|disagreements|report|automation`.

### 6.4 Acquisition/source commands

- `acquisition status|providers|validate|sync|history|health`.
- `coingecko sync|resume|universe|unresolved|resolve|health|statistics|pending|status|validate`.
- `defillama sync|status|validate|unresolved|resolve`.
- `github sync|status|validate|resolve|unresolved|statistics`.
- `protocol sync|coverage|validate|report|explain`.
- `developer sync|coverage|report|explain`.
- `macro status|providers|sync|validate|coverage|missing|failures|report|explain|history`.
- `whale status|providers|sync|validate|coverage|report|explain|history|failures`.
- `onchain registry|sync|coverage|report|explain|snapshots|providers|automation`.
- `capital-flow coverage|report|explain`.
- `narrative sync|resume|status|validate|statistics|coverage|missing|freshness|report|explain|sources|providers`.
- `sources discover|validate|status|report|coverage|unresolved|history`.
- `historical-acquisition sync|coverage|report|validate|explain`.

### 6.5 Analysis, validation, and reporting commands

- `market-validation run|report|compare|history`; `evidence status|coverage|validate|sources|missing`.
- `graph build|validate|status|report|coverage|explain|path|centrality|critical`.
- `technology coverage|report|build|explain`; `necessity coverage`.
- `economic build|validate|status|report|coverage|explain|path|centrality|moat`.
- `scenario run|status|report|explain|compare|history|coverage`.
- `backtest run|report|history|compare`; `calibration report|coverage|engines`.
- `replay report|compare|explain`; `benchmark`.
- `weights status|validate|report|recommend|activate`.
- `timing status|validate|report|explain|coverage|freshness|rebuild-status|dependencies|sync|history|compare`.
- `historical cases|build|replay|outcomes|evaluate|report|compare|leakage-check|survivorship-check|coverage|sync|expand|complete|progress|gaps|unresolved|summary|status|validate|providers|statistics|calibration|engines|challenges`.
- `explain`; `committee evaluate|report|ranking|champion|history`.

## 7. Automation and scheduled jobs

`configs/automation.yaml` enables UTC polling every 60 seconds. Checked-in jobs include:

- Experimental current-state project pipeline (daily, enabled).
- Historical replay example (one-time, disabled).
- CoinGecko market sync (every six hours).
- DefiLlama protocol sync (12-hour cron).
- GitHub developer sync (daily).
- Narrative source validation (daily).
- Narrative evidence sync (every six hours).
- Technology graph rebuild (daily, dependent on upstream acquisition jobs).
- Economic graph rebuild (daily).

Additional managers install/manage jobs for discovery, on-chain, competitive intelligence, evidence intelligence, and data sufficiency. `data_ops.py` defines the concrete data-operation executor and dependency/status tracking. On-chain also has `data/onchain/runtime/automation.yaml` and exposes a worker startup command, but execution remains local/synchronous. There is no Celery, Redis, Kubernetes, distributed lock, external queue, or automatic retry layer.

Lifecycle states are scheduled, claimed, running, succeeded, partial, failed, cancelled, skipped, and blocked. Replay/backtest jobs require a timezone-aware explicit `as_of`. Concurrency is guarded by an in-process job/target lock. Restart recovery marks abandoned claimed/running work failed and blocks ambiguous scheduled work for review.

## 8. Configuration inventory

| Configuration files | Architectural area |
| --- | --- |
| `acquisition.yaml`, `providers.yaml`, `project_identifiers.yaml` | acquisition, credentials, provider identities |
| `automation.yaml`, `dashboard.yaml`, `pipeline_persistence.yaml`, `plugins.yaml` | operations, UI, persistence policy, plugin loading |
| `discovery.yaml`, `project_domains.yaml` | global discovery and identity resolution |
| `macro.yaml`, `whale.yaml`, `onchain.yaml`, `narrative_sources.yaml`, `tokenomics_sources.yaml` | production/operational evidence sources |
| `developer_engine.yaml`, `macro_engine.yaml`, `narrative_engine.yaml`, `news_engine.yaml`, `onchain_engine.yaml`, `protocol_engine.yaml`, `social_engine.yaml`, `whale_engine.yaml` | experimental Intelligence Engine settings |
| `intelligence_fusion.yaml`, `opportunity.yaml`, `opportunity_timing.yaml` | experimental fusion/opportunity plus timing policy |
| `market_validation.yaml`, `weights.yaml`, `probability.yaml`, `patterns.yaml`, `investment_committee.yaml` | validation and derived decisions |
| `technology_graph.yaml`, `technology_necessity.yaml`, `capital_rotation.yaml` | dependency/necessity analysis |
| `governance_spaces.yaml` | governance source mapping |
| `historical_projects.yaml`, `historical_benchmarks.yaml`, `historical_validation.yaml` | replay cases, benchmarks, and validation |

`plugins.yaml` currently enables no plugins and contains empty configuration/load-order/priority/module-path maps; installed package entrypoints are discoverable independently.

## 9. Test-suite inventory

Pytest is configured with `src` on `pythonpath` and `tests/` as its only test path. The 79 suites are grouped below; every test module is listed.

### Acquisition, auth, and providers (10)

`test_acquisition`, `test_acquisition_engine_sources`, `test_auth`, `test_coingecko_provider`, `test_defillama_provider`, `test_github_provider`, `test_macro_acquisition`, `test_narrative_acquisition`, `test_whale_acquisition`, `test_onchain_capital_flow_acquisition`.

### Runtime, automation, persistence, dashboard, and end-to-end (13)

`test_automation`, `test_dashboard`, `test_dashboard_api`, `test_data_ops`, `test_execution_identity`, `test_operational_status`, `test_persistence_contracts`, `test_pipeline_persistence_integration`, `test_plugins`, `test_sql_persistence_repositories`, `test_v1_end_to_end_runtime_validation`, `test_intelligence`, `test_intelligence_engines`.

### Intelligence engines and fusion (14)

`test_developer_intelligence_engine`, `test_funding_foundation_intelligence_engine`, `test_governance_intelligence_engine`, `test_macro_intelligence_engine`, `test_narrative_intelligence_engine`, `test_news_intelligence_engine`, `test_onchain_foundation_intelligence_engine`, `test_onchain_intelligence_engine`, `test_protocol_intelligence_engine`, `test_security_intelligence_engine`, `test_social_intelligence_engine`, `test_tokenomics_intelligence_engine`, `test_whale_intelligence_engine`, `test_intelligence_fusion`.

### Discovery and source discovery (3)

`test_global_discovery_candidate_registry`, `test_narrative_source_discovery`, `test_narrative_acquisition` (also counted by acquisition coverage above; 79 total count treats the file once).

### Evidence intelligence (10)

`test_evidence_intelligence_claims`, `test_evidence_intelligence_conflicts`, `test_evidence_intelligence_intake`, `test_evidence_intelligence_models`, `test_evidence_intelligence_phase9`, `test_evidence_intelligence_provider`, `test_evidence_intelligence_relationships`, `test_evidence_intelligence_repository`, `test_evidence_intelligence_validation`, `test_market_validation`.

### Competitive intelligence (6)

`test_competitive_conflicts`, `test_competitive_inputs`, `test_competitive_peer_sets`, `test_competitive_phase1`, `test_competitive_phase8`, `test_competitive_repository`.

### Data sufficiency (6)

`test_sufficiency_phase1`, `test_sufficiency_phase2`, `test_sufficiency_phase3`, `test_sufficiency_phase4`, `test_sufficiency_phase5`, `test_sufficiency_phase6`.

### Tokenomics (2)

`test_tokenomics_phase_a`, `test_tokenomics_phase_b`.

### Graphs, scenarios, derived engines, committee, and explainability (13)

`test_backtesting_calibration`, `test_decision_explainability`, `test_economic_dependency_graph`, `test_investment_committee`, `test_opportunity_entry`, `test_opportunity_timing`, `test_pattern_matching`, `test_probability_engine`, `test_scenario_simulation`, `test_technology_dependency_graph`, `test_technology_necessity`, `test_timing_engine`, `test_weight_framework`.

### Historical validation (4)

`test_historical_acquisition`, `test_historical_acquisition_new_providers`, `test_historical_point_in_time_validation`, `test_market_validation` (cross-listed above).

### Notable coverage boundaries

- Tests are predominantly unit/component tests using in-memory, temporary JSONL, or temporary SQLite stores.
- Provider tests exercise transport behavior with controlled/fake transports; they do not constitute live-provider integration monitoring.
- The end-to-end suite validates the v1/canonical evidence-backed runtime, not a production migration to `PipelineOrchestrator`.
- The native Objective-C desktop executable/build script has no dedicated test suite.
- `operational_corpus.py` has no same-named dedicated suite; it is exercised indirectly by dashboard/status behavior.
- General schema version planning and build-backend purification have no dedicated test modules visible in `tests/`.

## 10. Desktop and UI surfaces

- `desktop/OperationalConsole/Sources/HunterOperationalConsole.m`: native Objective-C macOS status console.
- `desktop/OperationalConsole/build.sh`: application bundle compiler/packager.
- `desktop/OperationalConsole/dist/Hunter.app/...`: locally built app, including `Info.plist`, executable, and bundled `hunter_status.py`.
- `dist/Project Hunter Operational Console.app/...`: second built/distribution app bundle with plist and executable.
- `hunter.dashboard`: static HTML dashboard architecture.
- `hunter.dashboard_api`: structured operational JSON API consumed by UI/status surfaces.

The checked-in/present binary bundles are build artifacts, not Python runtime entrypoints. Their executable internals were not decompiled; their bundle manifests and adjacent source/build scripts were inventoried.

## 11. Documentation architecture

- `docs/ADR/0001`–`0015`: discovery-first, evidence-first, candidate registry, trust/entity/graph boundaries, canonical runtime, plugin SDK, repository purification, intelligence foundation, and developer/tokenomics/governance/security/on-chain engines.
- Core architecture: `HUNTER_ARCHITECTURE_MANIFEST`, `HUNTER_ARCHITECTURE_SPEC`, `HUNTER_IMPLEMENTATION_CONTRACT`, `CANONICAL_RUNTIME_ARCHITECTURE`, `PIPELINE_ORCHESTRATOR`, `PLUGIN_ARCHITECTURE`, `PLUGIN_SDK_ARCHITECTURE`.
- Persistence/execution: `PERSISTENCE_CONTRACTS`, `PERSISTENCE_REPOSITORY_LAYER`, `PIPELINE_PERSISTENCE_INTEGRATION`, `DETERMINISTIC_EXECUTION_IDENTITY`, `OPERATIONAL_ATTEMPTS_AND_RUN_LIFECYCLE`.
- Engines/layers: intelligence, fusion, evidence, discovery, competitive, sufficiency, macro/news/narrative/social/protocol/developer/whale/on-chain, timing, committee, dashboard, automation.
- Governance/delivery: constitution, principles, vision, roadmaps, implementation guides, AI review protocol, CI, sprint and release records.
- Top-level Markdown files describe the original Hunter analytical model (valuation, probability, risk, scoring, revenue, tokenomics, narrative, macro, portfolio, exit, watchlist, learning, databases, and research protocols). They are conceptual specifications rather than Python modules or executable entrypoints.

## 12. Architectural observations and reconciliation points

These are inventory findings, not implementation proposals:

1. **Two runtime architectures coexist.** Canonical production uses Market Validation; automation defaults include an enabled experimental plugin/fusion pipeline job. Operators must distinguish “production analytical runtime” from “operationally schedulable experimental runtime.”
2. **Several duplicate domain names represent different layers.** `macro`, `whale`, `narrative`, `onchain`, `timing`, and `tokenomics` have production/operational packages alongside experimental Intelligence Engine or Opportunity packages.
3. **Persistence is plural, not singular.** The generic SQLAlchemy record store, domain-specific SQLite schemas, and JSONL repositories all coexist. No single migration system governs all stores.
4. **The CLI is the dominant composition root.** It imports nearly every subsystem and contains command-specific assembly/report logic, making it the broadest coupling point.
5. **New trust subsystems outpace the canonical-runtime map.** Discovery, evidence intelligence, competitive intelligence, sufficiency, tokenomics, operational corpus, and desktop/status layers are real and tested but need an explicit production/experimental classification in a future architecture decision.
6. **Checked-in runtime state is present.** The repository includes mutable SQLite/JSONL operational data and built desktop bundles, so repository status can change from normal execution independently of source changes.
7. **No automatic execution of investments exists.** The system discovers, acquires, validates, scores, ranks, explains, reports, and schedules analytical/data operations only.

## 13. Working-tree state at inspection time

The repository was already non-clean before this report was created. Existing modified files included `data/data_ops.sqlite`, `data/historical_validation/runs.jsonl`, and `src/hunter/cli.py`. Existing untracked areas/files included competitive/operational-corpus/sufficiency data, desktop and distribution bundles, `dashboard_api.py`, `operational_status.py`, and their tests. This inventory includes those present files but does not imply they are committed architecture. The only file added by this task is this report.

## 14. Inventory completeness boundary

Included: every visible Python source module, test module, YAML configuration, runtime data family, documentation family, CI definition, build backend, desktop source/build file, app bundle manifest, and executable entrypoint. Excluded from semantic inspection: `.git`, Python bytecode/cache files, database row-by-row content, bulk JSONL record contents, and disassembly of compiled native executables. Those exclusions do not hide additional source modules or declared runtime entrypoints.
