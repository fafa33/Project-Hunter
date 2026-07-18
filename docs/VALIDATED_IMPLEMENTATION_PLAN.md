# Project Hunter — Validated Implementation Plan

**Validated:** 2026-07-18
**Repository examined:** `/Users/farhadafshari/Documents/GitHub/Project-Hunter`
**Status:** planning only; no implementation authorized or performed

## Validation basis

This plan is constrained by the current repository rather than the audit's stated `/Users/farhadafshari/Projects/Project-Hunter` path. Validation covered source code, CLI and automation composition, repository implementations, live local runtime stores, persistence schemas, all tests, the Constitution, and **every accepted ADR from 0001 through 0015**. Each recommendation was re-checked against the applicable ADR requirements; ADRs that do not govern a finding are identified below rather than treated as implicit authority for new work.

The complete suite result was **888 passed, 1 failed**. The sole failure is `tests/test_dashboard_api.py::test_dashboard_api_schema_is_stable`: `dashboard_api.py` now returns `operational_corpus` and `validation_corpus`, while the schema-contract test still expects the older key set.

### Governing constraints

- The Constitution requires one permanent analytical entrypoint, immutable evidence, non-overwritten historical snapshots, persisted inputs for scoring, traceable scores, and deterministic outputs.
- ADRs 0001–0005 require discovery-first sequencing, a SQL-backed canonical Candidate Registry, evidence provenance, explicit ambiguity/missingness, trust before advanced intelligence, and economic-entity/representation separation.
- ADR 0006 defers a knowledge graph until identity and trust foundations are production-stable; this plan does not authorize a graph database or a competing graph runtime.
- ADR 0007 keeps `EvidenceBackedProjectExecutor`/Market Validation canonical and classifies `PipelineOrchestrator`, Fusion, plugin engines, and fusion-backed opportunity timing as experimental until a future ADR explicitly changes the boundary.
- ADR 0008 makes the versioned Plugin SDK the target extension boundary but does not assert that current plugins are sandboxed or authorize them to bypass registry, evidence, trust, persistence, or production-scoring boundaries.
- ADR 0009 requires Provider → Service → Repository → Persistence; repositories cannot own authority, timestamps, replay decisions, or domain logic.
- ADR 0010 requires service-owned intelligence execution and persistence authorization; engines cannot access repositories or persist directly.
- ADRs 0011–0015 accept Developer, Tokenomics, Governance, Security, and On-chain Intelligence Engines as production **descriptive finding engines** on the ADR 0010 service boundary while preserving the canonical Market Validation runtime. They explicitly prohibit those engines from scoring, ranking, recommending, timing, composing engines, or inferring conclusions from absent evidence. Their production status must not be conflated with ADR 0007's experimental legacy/plugin orchestration path.

## ADR Applicability Matrix

| ADR | Applies? | Effect on this plan; explicit non-relevance boundary |
| --- | --- | --- |
| 0001 — Discovery-First | **Yes, sequencing** | Phase 1 and Phase 3 gates must preserve discovery as the market entrypoint and cannot prioritize speculative Opportunity scoring ahead of market visibility. It does not determine the storage design for probability, ranking, Dashboard, or prediction evaluation. |
| 0002 — Evidence-First Outputs | **Yes, pervasive** | Requires provenance, observation time, confidence, freshness, conflicts, cutoff-safe replay, missing/unavailable states, and no fabricated factors across persistence, Opportunity, ranking, prediction evaluation, and Dashboard. |
| 0003 — Dynamic Candidate Registry | **Yes, identity/input authority** | Candidate Registry remains the canonical SQL market map and project seed source for downstream services. It does not own analytical scores, rankings, Dashboard read models, or prediction correctness. |
| 0004 — Trust Before Intelligence | **Yes, gating** | Opportunity and any new cross-domain reasoning must consume trusted identity/evidence state and expose unresolved conflicts/missingness. It does not prescribe a specific Opportunity formula or repository backend. |
| 0005 — Entity Model Separation | **Yes, input semantics** | Opportunity/ranking inputs must state whether they concern project, protocol, token, network, contract, or representation; ticker/contract/listing equivalence is forbidden. It is not relevant to Dashboard schema repair or automation mechanics. |
| 0006 — Future Knowledge Graph | **Limited** | Requires stable relationship-ready IDs and keeps Candidate Registry/evidence repositories authoritative. No roadmap finding requires a knowledge graph; graph-database or replacement-runtime work remains out of scope until identity/trust are production-stable and a future ADR approves it. |
| 0007 — Canonical Runtime Option A | **Yes, decisive** | Market Validation remains canonical; PipelineOrchestrator, Fusion, legacy/plugin engine orchestration, and fusion-backed opportunity timing remain experimental unless a future ADR changes the boundary. All Opportunity/ranking persistence remains conditional. |
| 0008 — Plugin SDK Architecture | **Limited** | Applies only if roadmap work is exposed through plugins: plugins cannot bypass registry, evidence/trust layers, repositories, or production scoring and current module-path plugins cannot be described as sandboxed. The plan proposes no new plugin capability, so it creates no standalone work item. |
| 0009 — Repository Purification | **Yes, pervasive** | All authoritative writes must be Provider → Service → Repository → Persistence; repositories store authorized state only and cannot create timestamps, checkpoints, replay policy, conflicts, or domain decisions. |
| 0010 — Intelligence Engine Foundation | **Yes, engine boundary** | IntelligenceEngineService owns evidence loading, cutoff enforcement, validation, identity, and persistence authorization. Engines/builders cannot load, persist, rank, score, time, or compose. Opportunity/prediction services must remain downstream services, not additions to descriptive engines. |
| 0011 — Developer Intelligence Engine | **Selective** | Developer findings may be considered only as persisted descriptive evidence under a separately approved downstream source map; the engine cannot own `developer_momentum`, ranking, or Opportunity scoring. It is not relevant to graph/backtest storage, Dashboard schema repair, or prediction lifecycle. |
| 0012 — Tokenomics Intelligence Engine | **Selective** | Tokenomics findings remain descriptive, context/evidence-sufficient, conflict-preserving, and non-valuative. It strengthens the prohibition on directly turning balances, unlocks, fees, revenue, or TVL into Opportunity/risk scores. It does not require the separate operational tokenomics SQLite DB to exist and is not relevant to Dashboard schema repair. |
| 0013 — Governance Intelligence Engine | **Selective** | Governance findings cannot become governance-quality/decentralization scores, rankings, or recommendations without a future decision. Current roadmap factors do not include governance, so it creates no work item and is otherwise not relevant. |
| 0014 — Security Intelligence Engine | **Selective** | Security findings cannot become safety/trust/risk scores or recommendations, and contexts cannot be merged by candidate identity alone. Current Opportunity factors do not include security, so it creates no work item and is otherwise not relevant. |
| 0015 — On-chain Intelligence Engine | **Selective** | On-chain findings are descriptive and context-isolated; balances/transfers cannot imply ownership, accumulation/distribution intent, manipulation, or strategy. Any future Opportunity mapping requires a new downstream authority/scoring decision. It is not relevant to Dashboard schema repair or prediction lifecycle. |

## Validated finding register

| # | Audit finding | Classification | Repository evidence and disposition |
| --- | --- | --- | --- |
| 1 | `OpportunityMetricSnapshot` has no concrete persistence | **Verified** | `opportunity/repositories.py` defines only `OpportunityMetricRepository` Protocol; no record/repository/adapter exists. Retained, conditional on Phase 1 authority decision. |
| 2 | `OpportunityAssessment` has no concrete authoritative persistence | **Verified** | The model and deterministic engine exist, but persistence supports only `OpportunityTimingAssessmentRecord`; `opportunity/persistence.py` exports timing conversion only. Retained conditionally. |
| 3 | Opportunity metric source-authority map is missing | **Verified** | `OpportunityConfig.factor_weights` names inputs, but no service assembles them from persisted owners. Tests construct snapshots manually. Retained conditionally; ADRs 0002–0005 and 0010–0015 prohibit treating descriptive findings, identifiers, contexts, balances, or missing evidence as self-authorizing factor values. |
| 4 | Opportunity methodology/version persistence is missing | **Partially Verified** | Assessments carry configuration fingerprint and `identity_schema_version` in metadata, but no durable assessment record/configuration linkage exists. Retained as part of the conditional record contract, not a separate subsystem. |
| 5 | Opportunity source references/source versions are not durably persisted | **Partially Verified** | `OpportunityMetricSnapshot` has `evidence_ids` and assessment has `supporting_evidence`; no explicit per-factor source/version lineage and no durable general assessment record. Retained conditionally. |
| 6 | Opportunity ranking runtime owner is missing | **Verified** | `rank_opportunities()` is a pure sorter and CLI calls it with an empty tuple; no service owns current ranking production. Retained conditionally. |
| 7 | Opportunity ranking snapshot persistence is missing | **Verified** | No ranking record or repository exists. Operational corpus can store caller-supplied rankings but does not produce or authorize them. Retained conditionally. |
| 8 | Dashboard API opportunities source lacks an authoritative snapshot | **Not Verified** | The current API exposes no `opportunities` provider at all. The audit described a different/unavailable API surface. Discarded in its stated form; a future provider is allowed only after an approved ranking authority exists. |
| 9 | Prediction accuracy authority is missing | **Verified** | Operational corpus persists predictions, outcomes, validation samples, closures, and benchmark returns, but computes no correctness decision or aggregate accuracy. Retained. |
| 10 | Pending prediction/evaluation lifecycle authority is missing | **Partially Verified** | Open/closed/due state and horizon-based closure exist and are tested; there is no explicit pending-evaluation record/state machine or durable analytical owner beyond JSON/JSONL. Retained as lifecycle hardening, not greenfield lifecycle creation. |
| 11 | Probability durable repository is missing | **Verified** | Only `InMemoryProbabilityAssessmentRepository` exists; generic persistence has no `ProbabilityAssessmentRecord`. Retained conditionally for the experimental reasoning path. |
| 12 | Pattern durable repository is missing | **Verified** | Only `InMemoryPatternAssessmentRepository` exists; no generic persistence record/adapter. Retained conditionally. |
| 13 | Technology Necessity durable repository is missing | **Verified** | Only `InMemoryTechnologyNecessityAssessmentRepository` exists; no generic persistence record/adapter. Retained conditionally. |
| 14 | Committee runtime persistence is not consistently concrete | **Partially Verified** | In-memory runtime repository and record converters coexist with SQL record types/repositories. The pipeline adapter does not persist committee output, and `data_ops.sqlite` has no committee rows. Retained, but canonical Market Validation committee fields must remain distinct. |
| 15 | Market Validation outputs are absent from the canonical runtime DB | **Partially Verified** | SQL records/repositories exist, but the current `data/data_ops.sqlite` contains only 13 automation-job and 20 automation-run records. The DB is operational data-ops storage, not formally established as the one canonical analytical DB. Retained as runtime wiring/authority clarification, not “write everything to data_ops.sqlite.” |
| 16 | Tokenomics runtime DB is absent despite implemented schema | **Verified** | `data/tokenomics/runtime/tokenomics.sqlite` is absent; the separate operational tokenomics schema, migrations, ingestion, providers, repository, and tests exist. ADR 0012 does not require this DB—it governs the descriptive Tokenomics Intelligence Engine—so the gap is retained only as operational-store activation/readiness work, not as an ADR requirement or Opportunity prerequisite. |
| 17 | Discovery runtime DB is absent | **Obsolete** | `data/discovery/runtime/candidates.sqlite` exists with 650 candidates, 34 runs, 721 sources, and related identity/queue rows. Discarded. |
| 18 | Technology/economic graph repositories overwrite graph bodies | **Verified** | Both repositories write nodes/edges/metrics with mode `w` while appending only run summaries. This conflicts with the Constitution's historical-snapshot rule. Retained. |
| 19 | Macro/whale JSONL lacks explicit `known_at`, `recorded_at`, and schema version | **Verified** | Evidence/snapshot timestamps exist, but the stores lack a uniform recorded/known timestamp and schema-version contract. Retained as replay/schema hardening. |
| 20 | Operational corpus needs stronger authority labeling | **Verified** | It records caller-supplied rankings/recommendations and analytical-looking opportunity state downstream of execution. It is not an analytical authority. Retained as boundary/documentation/schema work. |
| 21 | Acquisition JSONL lacks an explicit schema version | **Verified** | Raw/normalized/validation/run/checkpoint payloads are unversioned. Retained. |
| 22 | Backtest metrics overwrite prior run-specific metric bodies | **Verified** | Runs/calibrations append, but `engine_metrics.jsonl` and `project_metrics.jsonl` are rewritten on each save; reconstructed historical runs reuse the current metric files. Retained with graph history repair. |
| 23 | Evidence Intelligence is an authoritative populated runtime store | **Partially Verified** | Service/repository/schema and extensive tests exist, but the default runtime DB is absent. Treat as implemented authority architecture, unavailable runtime data. No new architecture recommendation retained. |
| 24 | Sufficiency is an active authoritative input to Opportunity | **Partially Verified** | The SQLite schema and service boundary exist; current DB exists but all tables are empty, and no Opportunity input assembly wiring exists. Activation is retained; direct Opportunity wiring remains conditional. |
| 25 | Tokenomics, unlock risk, catalysts, and evidence sufficiency are required fields of current `OpportunityAssessment` | **Not Verified** | These fields do not exist on the current model or factor configuration. Discarded; adding them would be a new scoring decision requiring architecture approval. |
| 26 | Dashboard API currently exposes `kpis`, `opportunities`, `charts`, and `intelligence_feed` | **Not Verified** | None exists in current `dashboard_api.py`. Discarded. |
| 27 | Dashboard API schema is stable | **Not Verified** | The only failing test demonstrates current schema/test drift. Retained only as an immediate contract-repair item. |
| 28 | Timing can substitute for opportunity score/ranking | **Not Verified** | Canonical `hunter.timing` and experimental `OpportunityEngine` have different models and scores; ADR 0007 separates them. Explicitly prohibited. |
| 29 | Generic SQL persistence is available for immutable/idempotent records | **Verified** | SQLAlchemy record store, UnitOfWork, canonical hashes, logical deletion, repository tests, and pipeline integration exist. Reuse is required; no parallel persistence framework is recommended. |
| 30 | Overall architecture health is exactly 62/100 | **Not Verified** | No repository-owned scoring rubric or reproducible calculation supports the number. Discarded. |

## Recommendations discarded

- Creating or repairing a discovery DB: it already exists and is populated.
- Implementing the audit's described Dashboard `opportunities`, `kpis`, `charts`, or `intelligence_feed` providers: those current providers do not exist, and opportunity authority is unresolved.
- Adding tokenomics, unlock risk, catalysts, or evidence-sufficiency fields directly to `OpportunityAssessment`: the current contract does not require them; ADR 0007 forbids an unapproved production scoring expansion, and ADR 0012 expressly keeps tokenomics findings descriptive and non-valuative.
- Treating `data/data_ops.sqlite` as the predetermined universal analytical database: current evidence establishes it as an operational store, not that governance decision.
- Treating production `TimingAssessment`, Market Validation `hunter_score`, or operational-corpus rankings as a substitute for `OpportunityAssessment.opportunity_score`.
- Acting on the audit's numeric health score.

# Phase 1 — Critical architecture

## Objective

Resolve the single-runtime and analytical-authority boundary before adding any new scoring persistence, and restore the currently broken Dashboard API contract.

## Work packages

### 1.1 Runtime authority ADR

Choose one explicit direction:

- keep Market Validation as the sole production runtime and scope Opportunity/Probability/Patterns/Necessity/standalone Committee as experimental research infrastructure; or
- approve a staged migration into a service-owned analytical architecture with compatibility and cutover rules, explicitly distinguishing the ADR 0010 `IntelligenceEngineService` descriptive-finding boundary from the experimental `PipelineOrchestrator`/Fusion path.

The ADR must define distinct semantic owners for Market Validation `hunter_score`, production timing scores, experimental opportunity score, probability, committee decisions, and rankings. It must also classify discovery, evidence intelligence, competitive intelligence, sufficiency, operational tokenomics, ADR 0011–0015 descriptive Intelligence Engines, operational corpus, Dashboard API, and desktop console. It cannot reinterpret ADR 0011–0015 as authorization for cross-engine composition or scoring.

### 1.2 Authority registry and dependency contract

Create a documentation-first authority registry for every analytical output: semantic name, entity/representation scope, owner service/engine, approved persisted inputs, persistence owner, effective/recorded/known time policy, replay mode, consumers, and production status. It must preserve Candidate Registry identity authority and trust/conflict state. Encode machine-readable stage/output declarations only after the ADR is accepted.

### 1.3 Dashboard API contract repair

Reconcile `dashboard_api.py` and `test_dashboard_api.py`: either version the added top-level corpus fields as a compatible schema change or restore the declared v1 shape. Document field authority and unavailable/null semantics. Do not add opportunity or accuracy fields in this phase.

## Files/packages affected

- `docs/ADR/` (new runtime-authority ADR), `docs/CANONICAL_RUNTIME_ARCHITECTURE.md`, `docs/HUNTER_ARCHITECTURE_MANIFEST.md`
- `docs/DASHBOARD.md`, `docs/DASHBOARD_ARCHITECTURE.md`, `docs/PIPELINE_ORCHESTRATOR.md`
- `src/hunter/dashboard_api.py`, `tests/test_dashboard_api.py`
- Potential later contract location under `src/hunter/execution/` or `src/hunter/intelligence/engines/`; no implementation until the ADR selects it

## Architectural rationale

ADR 0007 currently makes most requested opportunity work experimental. ADRs 0010–0015 add production descriptive Intelligence Engines without replacing Market Validation or authorizing scoring/composition. The Constitution simultaneously requires one permanent pipeline entrypoint, while ADRs 0001–0005 require discovery, canonical identity, evidence, and trust to precede advanced intelligence. Persisting a second score/ranking authority before resolving those boundaries would institutionalize architectural drift. The existing failing API contract is a concrete current regression and can be repaired without inventing analytical authority.

## Expected impact

- One unambiguous production boundary and migration policy.
- Prevention of duplicate scoring/ranking authorities.
- A stable, tested Dashboard API v1 (or explicitly versioned successor).
- A trustworthy basis for all later persistence work.

## Risks

- A migration decision could unintentionally change production scoring semantics.
- Over-broad ADR language could classify unfinished packages as production.
- Dashboard consumers may depend on the current unversioned extra keys.

## Acceptance criteria

- Accepted ADR explicitly amends or reaffirms ADR 0007; it does not silently bypass it.
- Every analytical score and ranking has exactly one semantic owner and production classification.
- The decision distinguishes ADR 0011–0015 production descriptive engines from experimental plugin/Fusion orchestration and does not grant those engines scoring, ranking, timing, recommendation, or composition authority.
- Discovery/Candidate Registry, trust/conflict state, and economic-entity/representation scope remain upstream gates for approved advanced reasoning.
- Production and experimental timing/opportunity concepts are named distinctly.
- Dashboard schema documentation, implementation, and deterministic schema test agree.
- Full test suite passes with no production scoring changes.

# Phase 2 — Authority and persistence

## Objective

Make approved analytical outputs durable, immutable, lineage-complete, and replay-addressable through service-owned persistence boundaries.

## Work packages

### 2.1 Bitemporal analytical record contract

Extend or standardize analytical persistence around `effective_at`, `recorded_at`/`created_at`, and an explicit known-by cutoff policy. Define schema version, model/configuration fingerprints, source record IDs and versions, missing evidence, confidence, and correction/supersession rules. Reuse canonical serialization, deterministic identity, SQL repositories, UnitOfWork, and ADR 0009 service authorization.

### 2.2 Historical store preservation

Replace overwrite behavior for technology graph nodes/edges/metrics, economic graph nodes/edges/metrics, and backtest project/engine metrics with immutable run-addressed snapshots. Supply migrations/read compatibility for existing current-state files; never rewrite existing history in place.

### 2.3 JSONL schema and replay hardening

Version acquisition, macro, whale, and timing payloads. Add explicit recorded/known-time semantics where unavailable, preserving existing source/effective timestamps and backward-compatible readers. Define strict-known replay filtering and characterize old records as legacy schema rather than inventing timestamps.

### 2.4 Activate implemented authority stores

Provide explicit bootstrap/status/health workflows for the operational tokenomics and Evidence Intelligence runtime databases, plus sufficiency registry initialization. Keep the operational tokenomics store distinct from the ADR 0012 Tokenomics Intelligence Engine; any evidence flow between them must pass through an approved service/evidence contract. Empty or absent stores must remain `unavailable`/`insufficient_evidence`; bootstrap must never fabricate analytical data.

### 2.5 Approved derived records

If Phase 1 keeps these outputs in scope, add canonical records, repository contracts, SQL repositories, conversion services, and persistence-adapter routing for Probability, Pattern Matching, Technology Necessity, and standalone Committee outputs. If Phase 1 leaves them experimental, implement in an isolated experimental database/config and do not present them as production.

### 2.6 Market Validation persistence wiring

Define the approved analytical store and wire canonical Market Validation run/project records through a service-owned transaction. Do not assume `data_ops.sqlite`; keep operational and analytical database roles explicit. Preserve current report/scoring outputs byte-for-byte.

## Files/packages affected

- `src/hunter/persistence/{records,repositories,serialization}.py`
- `src/hunter/persistence/sql/`, `src/hunter/persistence/integration/`
- `src/hunter/graph/repository.py`, `src/hunter/economic/repository.py`, `src/hunter/backtest/repository.py`
- `src/hunter/acquisition/repositories.py`, `src/hunter/macro/`, `src/hunter/whale/`, `src/hunter/timing/`
- `src/hunter/tokenomics/`, `src/hunter/evidence_intelligence/`, `src/hunter/sufficiency/`
- Conditional: `src/hunter/probability/`, `patterns/`, `necessity/`, `committee/`, `market_validation/`
- Associated configs, persistence docs, migrations, and repository/replay tests

## Architectural rationale

The current repository mixes canonical SQL records, domain SQLite schemas, append-only JSONL, overwrite-style JSONL, and memory-only repositories. The priority is not forced storage uniformity; it is uniform authority, immutability, lineage, and replay semantics. Services must authorize records before repositories store them.

## Expected impact

- Historical graph/backtest state becomes reproducible.
- Replay can distinguish event/effective time from Hunter's known/recorded time.
- Approved derived outputs survive process boundaries.
- Runtime-store readiness becomes observable without conflating empty schema with available evidence.

## Risks

- Backward compatibility with existing JSONL data.
- Incorrectly synthesizing `recorded_at` for legacy records could create false replay guarantees.
- A generic record expansion could bypass richer domain schemas.
- Dual-write rollout could diverge unless transactional ownership is explicit.

## Acceptance criteria

- No new authoritative repository creates timestamps or makes domain decisions.
- Same authorized record identity/payload is idempotent; conflicting payload is rejected or represented as an explicit correction/supersession.
- Service authorization preserves canonical candidate identity, entity/representation context, source provenance, conflicts, and unavailable states; repositories do not resolve them.
- Graph and backtest histories retain multiple complete runs and reconstruct each run independently.
- Strict-known replay excludes records unavailable at the cutoff; legacy records expose limitations explicitly.
- Tokenomics/Evidence Intelligence/sufficiency health distinguishes absent, empty, populated, and failed states.
- Conditional derived records include deterministic identity, schema/model/config versions, lineage, confidence, missing evidence, effective time, and recorded time.
- Canonical Market Validation behavior and reports remain unchanged; tests prove parity.

# Phase 3 — Reasoning pipeline

## Objective

Build a service-owned, persisted-input reasoning chain only for outputs approved by Phase 1, with Opportunity assessment and ranking gated behind complete source authority.

## Work packages

### 3.1 Opportunity input authority service

If Opportunity is approved, define an `OpportunityAssessmentService` that loads approved persisted records at an explicit cutoff and constructs `OpportunityMetricSnapshot`. The service must resolve targets through the Candidate Registry/trust boundary and retain the economic-entity/representation scope of every input. For every current factor—valuation discount, relative/historical valuation, whale accumulation, smart-money positioning, developer momentum, macro tailwinds, future demand, sector strength, capital formation, validation health, freshness, confidence, backtesting quality, historical similarity, risk, and missing evidence—specify:

- one semantic owner;
- exact source record/field and version;
- normalization and missingness policy;
- effective/known-time selection;
- confidence and evidence lineage;
- replay behavior.

Unowned factors remain missing and reduce confidence; they are never defaulted into supportive evidence. ADR 0011–0015 findings may be loaded only as immutable persisted descriptive inputs through a downstream service; those engines cannot compute factor values, compose findings, resolve conflicts, or authorize scoring. In particular, developer findings do not automatically equal `developer_momentum`, and on-chain observations do not establish whale accumulation, smart-money positioning, ownership, intent, or strategy. Tokenomics, unlock risk, catalysts, and sufficiency may be proposed only through a separate scoring ADR because they are not current Opportunity factors; ADR 0012 does not authorize a valuation or risk mapping.

### 3.2 Opportunity persistence

Add immutable `OpportunityMetricSnapshotRecord` and `OpportunityAssessmentRecord`, repository contracts and concrete SQL repositories, service-owned persistence plans, configuration/model records, and history/as-of queries. Preserve the current deterministic engine as a pure scoring component; it must not load or save data.

### 3.3 Durable reasoning dependencies

Wire approved Probability, Pattern, Necessity, backtest, committee, and timing inputs by persisted IDs rather than in-memory object handoff. Any ADR 0011–0015 finding inputs remain independent descriptive records and are composed only by the approved downstream service, never by an Intelligence Engine. Enforce stage dependency declarations and fail closed with named missing-evidence, unresolved-identity, ambiguous-entity, and conflict states.

### 3.4 Ranking authority

Introduce an `OpportunityRankingService` only after durable assessments exist. It selects assessments under one cutoff/methodology/configuration and persists immutable `OpportunityRankingSnapshotRecord` rows with rank, score, tie-break explanation, included/excluded assessment IDs, and missing/insufficient reasons. `rank_opportunities()` remains a pure deterministic sorter.

## Files/packages affected

- `src/hunter/opportunity/{metrics,models,engine,repositories,ranking,persistence}.py`
- New service/composition modules under `src/hunter/opportunity/` consistent with ADR 0009
- `src/hunter/persistence/records.py`, repository contracts/factory/SQL implementations/integration adapter
- Conditional input packages: `market_validation`, `macro`, `whale`, `backtest`, `probability`, `patterns`, `necessity`, `committee`, `timing`
- `configs/opportunity.yaml` and new authority/methodology configuration only if approved
- Opportunity, persistence, replay, ranking, and end-to-end tests; corresponding ADR/architecture docs

## Architectural rationale

The score function exists and is deterministic, but tests manufacture its entire input snapshot. The missing component is not another formula; it is authoritative, replay-safe input assembly and durable output ownership. Ranking cannot be authoritative until assessments are authoritative.

## Expected impact

- Reproducible Opportunity assessments from persisted evidence.
- Auditable per-factor lineage and named missingness.
- Immutable, cutoff-specific rankings suitable for downstream read-only consumers.
- Elimination of ambiguity among Hunter score, timing score, and opportunity score.

## Risks

- Formalizing speculative factor mappings as facts.
- Collapsing project, protocol, token, network, contract, wallet, or representation contexts into one candidate-level score.
- Violating ADR 0011–0015 by converting descriptive findings into engine-owned scores or inferring intent/ownership from contextual identifiers or balances.
- Future leakage through “latest” records without known-time filtering.
- Double-counting correlated inputs across Market Validation, Timing, and derived engines.
- Changing current score behavior while adding input assembly.
- Treating an experimental output as production before ADR approval.

## Acceptance criteria

- Every populated factor traces to an approved persisted record and version; every unowned/unavailable factor is explicitly missing.
- Every factor declares its canonical candidate and entity/representation/context scope; ambiguous identity or context fails closed.
- ADR 0011–0015 engines remain evidence-only, independent, descriptive, repository-free, and non-composing; all factor mapping occurs in the separately approved downstream service.
- Same cutoff, persisted inputs, model, and configuration produce byte-identical snapshots, assessments, and rankings.
- Replay tests introduce post-cutoff records and prove they cannot affect output.
- Engine and sorter perform no I/O and own no persistence.
- Assessment records preserve per-factor values, weights, contribution, confidence, evidence/source IDs, model/config fingerprints, and missingness.
- Ranking snapshots contain only persisted assessment IDs from one compatible methodology/cutoff.
- No Dashboard or operational-corpus input participates in analytical scoring.

# Phase 4 — Operational improvements

## Objective

Create an authoritative prediction-evaluation lifecycle and expose only validated, versioned read models through operational APIs.

## Work packages

### 4.1 Prediction evaluation authority

Promote the existing open/due/closed operational workflow into an explicit service-owned lifecycle. Define prediction contract/version, canonical target/entity scope, source prediction authority, evaluation horizon, benchmark/target outcome policy, evaluability state, correctness decision, reason, and correction/supersession rules. Persist evaluation records and aggregate calibration/accuracy snapshots; do not infer correctness from closure alone or from evidence unavailable at the evaluation cutoff.

### 4.2 Operational corpus boundary hardening

Version corpus payloads and label caller-supplied rankings/recommendations as downstream observations referencing their authoritative record IDs. Reject or mark unverified analytical-looking payloads that lack authority references. Keep corpus readiness operational, not evidence of analytical correctness.

### 4.3 Read-only Dashboard providers

After authoritative records exist, add versioned providers for prediction evaluation/accuracy and—only if Phase 3 is completed—Opportunity ranking. Providers read snapshots and unavailable reasons; they perform no scoring, ranking, lifecycle mutation, or fallback interpretation. Update native console/UI only after the API contract is stable.

### 4.4 Runtime health and store readiness

Extend status checks to report analytical stores independently: absent, schema-only/empty, populated, stale, migration-required, or unreachable. Do not reduce these states to zero-valued KPIs.

## Files/packages affected

- `src/hunter/operational_corpus.py`, persistence records/repositories, automation monitor wiring
- `src/hunter/dashboard_api.py`, `src/hunter/operational_status.py`
- `tests/test_pipeline_persistence_integration.py`, `test_dashboard_api.py`, `test_operational_status.py`
- `desktop/OperationalConsole/` only after API stabilization
- Dashboard, automation, corpus, and prediction documentation/configuration

## Architectural rationale

Current prediction closure is real and tested, but correctness and aggregate accuracy are absent. The operational corpus is a downstream audit/observation store and must not become an accidental scoring authority. The Dashboard must remain a read interface.

## Expected impact

- Auditable prediction correctness and accuracy/calibration rather than open/closed counts alone.
- Clear distinction between unavailable, unevaluable, pending, incorrect, and correct.
- Dashboard and console consume authoritative snapshots without interpreting raw runtime files.
- Better operational diagnosis of empty versus unavailable analytical stores.

## Risks

- Defining correctness after observing outcomes can bias evaluation.
- Existing corpus rows lack schema/authority references and need explicit legacy handling.
- API evolution can break the native console.
- Aggregate accuracy can mislead when sample sizes or market regimes differ.

## Acceptance criteria

- Prediction lifecycle transitions are explicit, deterministic, idempotent, and correction-aware.
- Evaluation policy is versioned before outcomes are scored.
- Prediction and outcome records preserve canonical candidate/entity scope, provenance, observation time, known time, confidence, conflicts, and unavailable states.
- Accuracy/calibration aggregates retain numerator, denominator, exclusions, cohort/window, policy version, and source evaluation IDs.
- Dashboard returns `unavailable`/`insufficient_sample` rather than fabricated zeroes.
- Dashboard tests prove providers are read-only and deterministic.
- Operational corpus records authoritative IDs and cannot silently become the owner of scores/rankings.
- Native console behavior is covered by contract fixtures or integration tests before consuming new fields.

# Phase 5 — Future enhancements

## Objective

Improve scale, delivery, and analytical breadth only after authority, persistence, replay, and operational contracts are proven.

## Work packages

### 5.1 PostgreSQL and migration tooling

Add a PostgreSQL backend behind existing repository contracts, formal migration/version tooling, indexes for as-of/history queries, and migration verification. Retain SQLite for local use where appropriate.

### 5.2 Distributed execution

Replace in-process locks/polling with a durable queue, distributed leases, heartbeats, retry/backoff, and idempotent workers. Preserve automation's prohibition on business logic and the service-owned authority boundary.

### 5.3 Performance and observability

Add volume/latency benchmarks, repository query profiling, structured metrics, trace correlation across run/attempt/artifact IDs, retention policies, backup/restore tests, and corruption/recovery drills.

### 5.4 New analytical dimensions

Consider tokenomics, unlock risk, sell pressure, catalysts, sufficiency gates, and competitive assessments as Opportunity inputs only through explicit evidence contracts, a scoring ADR, historical validation, calibration, context/entity isolation, and anti-double-counting analysis. ADR 0012 tokenomics and ADR 0015 on-chain findings remain descriptive: do not infer valuation, risk, ownership, accumulation/distribution intent, manipulation, or strategy from similarly named fields, balances, transfers, or identifiers. Governance and security findings likewise cannot become quality, decentralization, safety, trust, or risk scores without a future decision that explicitly changes those downstream semantics while leaving the engines descriptive.

### 5.5 Rich UI and external API

Build additional Dashboard/Terminal pages, charts, alerts, and external APIs from versioned read models. No UI component may calculate or mutate analytical authority.

## Files/packages affected

- `src/hunter/persistence/sql/`, migration tooling, deployment configuration
- `src/hunter/automation/`, operational infrastructure adapters
- Observability/configuration/CI and performance tests
- Conditional future work in `tokenomics`, `onchain`, `competitive`, `sufficiency`, `opportunity`
- `dashboard_api.py`, desktop console, and future API/UI packages

## Architectural rationale

The current local synchronous architecture is adequate for correctness work. Distribution or broader scoring before authority and replay are stable would amplify inconsistency. New factors require evidence-backed historical validation, not field-name similarity.

## Expected impact

- Safer scale and recovery.
- Faster as-of and historical queries.
- Better production observability.
- Evidence-backed expansion without duplicate or opaque scoring.

## Risks

- Distributed coordination can weaken determinism and idempotency.
- Backend migrations can alter canonical serialization or query ordering.
- New factors can introduce correlation, leakage, or unjustified confidence.
- UI/API scope can outrun authoritative data availability.

## Acceptance criteria

- Backend conformance tests pass unchanged repository contracts and deterministic serialization fixtures.
- Distributed workers prove idempotency, lease expiry/recovery, bounded retry, and no duplicate authoritative records.
- Backup/restore and migration rollback are tested on representative datasets.
- Every new analytical factor has an accepted authority/scoring decision, persisted lineage, replay test, calibration evidence, and explicit missingness behavior.
- UI/API outputs remain read-only projections with versioned contracts and authority references.

## Delivery gates

Phases are sequential authority gates, not merely scheduling labels:

1. Phase 2 cannot define new production analytical records until Phase 1 resolves runtime authority.
2. Phase 3 Opportunity persistence cannot begin unless Phase 1 explicitly approves Opportunity's status and Phase 2 establishes the record/replay contract.
3. Phase 3 reasoning cannot begin until canonical candidate identity, trust/conflict state, and entity/representation scope are available for its inputs; this is a dependency gate, not authorization for new discovery or knowledge-graph work.
4. Ranking cannot begin before durable assessments and complete factor authority exist.
5. Dashboard Opportunity/accuracy providers cannot begin before their authoritative snapshots exist.
6. Future scoring dimensions require a separate accepted scoring/authority decision and historical validation; ADR 0011–0015 descriptive-engine acceptance is not that decision.

At every gate, the full test suite must pass, scoring parity must be demonstrated where semantics are unchanged, documentation must match implementation, and unavailable data must remain explicitly unavailable.
