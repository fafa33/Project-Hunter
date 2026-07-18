# ADR 0016: Runtime Analytical Authority

## Status

Accepted.

## Context

Project Hunter contains one validated production analytical runtime and several additional execution, reasoning, persistence, automation, and presentation surfaces.

The production path established by ADR 0007 is:

```text
CLI
-> Acquisition
-> Validation
-> Repositories
-> EngineValidationSource
-> EvidenceBackedProjectExecutor
-> Weight Engine
-> Production Timing
-> Committee Fields
-> Explainability
-> Reports
```

The repository also contains `PipelineOrchestrator`, plugin lifecycle and engine execution, Intelligence Fusion, fusion-backed Opportunity Timing, a separate Opportunity score, Probability, Pattern Matching, Technology Necessity, a standalone Investment Committee engine, ranking helpers, generic analytical persistence contracts, automation, Dashboard projections, a desktop console, and an Operational Corpus.

The presence of a model, deterministic function, test, record type, repository, automation job, or user interface does not by itself establish production analytical authority. Without one explicit authority decision, these surfaces could create competing meanings for score, timing, committee decision, ranking, recommendation, or prediction correctness.

This ADR resolves that ambiguity before any new analytical persistence, Opportunity scoring runtime, ranking authority, prediction evaluation authority, or Dashboard analytical provider is implemented.

## Decision

Project Hunter reaffirms ADR 0007 and makes its conservative boundary explicit:

- `EvidenceBackedProjectExecutor` and the evidence-backed Market Validation path remain the **sole canonical production analytical runtime**.
- Canonical production Timing remains `hunter.timing.OpportunityTimingEvidenceEngine` as consumed by the Market Validation path. It is not the fusion-backed `hunter.opportunity.OpportunityTimingEngine` and is not an Opportunity score.
- `PipelineOrchestrator`, Intelligence Fusion, plugin intelligence-engine orchestration, the Opportunity score, Probability, Pattern Matching, Technology Necessity, the standalone Committee engine, ranking helpers, and fusion-backed Opportunity Timing remain **experimental/research infrastructure** unless a future accepted ADR explicitly promotes a defined output.
- The current compatibility call to an empty/default `PipelineOrchestrator` from `EvidenceBackedProjectExecutor` does not promote `PipelineOrchestrator`, its plugin lifecycle, Fusion, or its optional stages. Production authority remains with the surrounding Market Validation runtime and its defined result semantics.
- ADR 0010–0015 service-owned Developer, Tokenomics, Governance, Security, and On-chain Intelligence Engines remain accepted production **descriptive finding engines**. They do not replace Market Validation and do not acquire scoring, ranking, recommendation, timing, cross-engine composition, or investment-decision authority. Legacy/plugin orchestration of intelligence engines remains experimental under ADR 0007 and ADR 0008.
- Automation and Scheduler are **operational-only**. They may invoke an approved runtime and record operational lifecycle state, but they never own evidence interpretation, reasoning, scoring, ranking, recommendations, prediction correctness, or analytical authority.
- Dashboard API and the desktop console are **read-only operational projections**. They may display authoritative records and explicit unavailable states. They never establish authority, calculate scores or rankings, infer conclusions, repair missing data, or promote an experimental output.
- Operational Corpus is a **downstream operational/audit store**. It may record what an authorized runtime emitted and track operational prediction/outcome lifecycle observations. It does not own analytical rankings, recommendations, scores, Opportunity assessments, or correctness decisions. Caller-supplied values stored there do not become authoritative by persistence.
- No production or experimental output may substitute for a differently defined output merely because fields or labels appear similar.
- No parallel canonical analytical runtime or parallel production owner for the same semantic output may be introduced.

Repositories remain persistence adapters under ADR 0009. A repository, schema, table, JSONL file, or canonical record type stores only service-authorized state and never becomes the semantic owner of the stored conclusion.

## Semantic Owner Map

The table names the current single semantic owner and authority boundary for each required output. “No authorized persistence” means that repository work cannot begin as production work until the promotion rules in this ADR are satisfied.

| Output | Classification | Single semantic owner | Permitted persisted-input boundary | Persistence authorization owner | Allowed consumers | Prohibited substitutes and misuse |
| --- | --- | --- | --- | --- | --- | --- |
| Market Validation `hunter_score` | Canonical production analytical output | `EvidenceBackedProjectExecutor` within the Market Validation runtime | Validated `EngineValidationSource` inputs assembled from approved persisted acquisition/evidence repositories under Market Validation configuration and weights | Canonical Market Validation run/composition boundary; repositories only store an already-authorized result | Production reports, canonical comparisons/ranking views that explicitly rank `hunter_score`, explainability, historical validation | Timing scores, Opportunity score, probability, committee confidence, Operational Corpus values, Dashboard calculations, or plugin findings cannot be labeled or used as `hunter_score` |
| Market Validation project ranking | Canonical production analytical output | `MarketValidationRunner` deterministic project ordering within the canonical runtime | Project results produced in the same Market Validation run under one configuration/effective boundary | Canonical Market Validation run/composition boundary | Production Market Validation reports, comparisons, committee fields, and explainability | Opportunity/general ranking helpers, standalone Committee champion selection, Dashboard sorting, or Operational Corpus rankings cannot replace or be presented as the Market Validation ranking |
| Canonical Timing assessment and scores | Canonical production derived output | `hunter.timing.OpportunityTimingEvidenceEngine` in the production Market Validation ecosystem | Approved persisted acquisition, technology-graph, macro, whale, and declared timing dependency snapshots at the applicable cutoff | Current canonical Timing sync/application boundary (implemented by `OpportunityTimingEvidenceEngine.sync`); `TimingRepository` has no semantic authority, and any future persistence refactor remains governed by ADR 0009 | Market Validation coverage and committee fields, timing reports, explainability, declared production consumers | Fusion-backed Opportunity Timing, Opportunity score, `hunter_score`, probability, or caller-supplied corpus timing cannot substitute for canonical Timing |
| Experimental Opportunity score (`OpportunityAssessment.opportunity_score`) | Experimental/research | Pure `hunter.opportunity.OpportunityEngine` defines the experimental calculation; no production runtime service owns it | Manually supplied `OpportunityMetricSnapshot` in current tests/research only; there is no approved persisted factor-authority assembly boundary | None for authoritative production persistence | Tests and explicitly labeled research reports only | Must not be presented as `hunter_score`, canonical Timing, a production recommendation, or an authoritative ranking input; no descriptive ADR 0011–0015 finding automatically authorizes a factor value |
| Probability assessment | Experimental/research | `hunter.probability.ProbabilityEngine` | Explicit in-memory `ProbabilityInputSet` used by the experimental package; no approved production persisted-input assembly | None for authoritative production persistence | Tests and explicitly labeled research analysis only | Must not be presented as prediction accuracy, Market Validation confidence, committee decision, Opportunity score, or production probability; the named probability-derived view inside a Market Validation result does not promote the standalone assessment package |
| Pattern assessment | Experimental/research | `hunter.patterns.PatternMatchingEngine` | Explicit in-memory `PatternInputSet` and configured historical pattern library; no approved production persisted-input assembly | None for authoritative production persistence | Tests and explicitly labeled research analysis only | Must not be presented as historical validation, probability, Opportunity score, or proof of future performance; the named pattern-derived view inside a Market Validation result does not promote the standalone assessment package |
| Technology-necessity assessment | Experimental/research | `hunter.necessity.TechnologyNecessityEngine` | Explicit in-memory `TechnologyNecessityInputSet` derived for research from declared graph inputs; no approved production persisted-input assembly | None for authoritative production persistence | Tests and explicitly labeled research analysis only | Must not substitute for canonical technology-graph metrics, future demand, Opportunity score, or Market Validation score; the named necessity-derived view inside a Market Validation result does not promote the standalone assessment package |
| Canonical committee decision fields | Canonical production analytical output | `EvidenceBackedProjectExecutor` as fields on `ProjectValidationResult` | The same approved evidence-backed Market Validation inputs and canonical production Timing used by the project result | Canonical Market Validation run/composition boundary | Production Market Validation reports and explainability | Standalone Committee assessments/votes/champions, probability, ranking, Dashboard logic, or Operational Corpus recommendations cannot replace these fields |
| Standalone Committee assessment, votes, and champion | Experimental/research | `hunter.committee.InvestmentCommitteeEngine` | Explicit `CommitteeInputSet` in the experimental package; no approved production input-assembly path | No production authorization; record converters and SQL repository types do not confer production status | Tests, experimental reports, and research history only | Must not be presented as the canonical committee decision, an investment recommendation, a ranking authority, or a Market Validation result |
| Opportunity/general rankings | Experimental/research; no production Opportunity/general ranking authority | No production owner. Domain-specific pure sorters, including `rank_opportunities`, own only deterministic ordering of caller-supplied objects | Caller-supplied assessments in tests/research; Operational Corpus may record externally supplied rankings but is not an input authority | None for a general or Opportunity ranking snapshot | Tests and explicitly labeled research output only | No Timing score, Opportunity score, standalone committee champion, Dashboard sort, corpus entry, CLI placeholder, or Market Validation rank can be relabeled as a production Opportunity/general ranking |
| Prediction outcome/closure observations | Operational/non-analytical, not a production analytical conclusion | `OperationalCorpusRecorder` owns only downstream capture of supplied prediction, outcome, benchmark, and closure observations | Authorized runtime artifacts plus explicitly supplied outcome/benchmark observations | `OperationalCorpusRecorder` authorizes only its operational files | Operational status and audit/history | Closure, benchmark return, corpus presence, or Dashboard counts cannot establish correctness, accuracy, a recommendation, or a score |
| Prediction correctness and accuracy evaluation | Experimental/unimplemented; no production authority | No current owner service/runtime | No approved input boundary; future promotion must define prediction contract, outcome policy, benchmark policy, cutoff, and evaluability | None | No current analytical consumer; operational projections must report unavailable | Outcome capture, closure, historical-validation accuracy, Dashboard calculation, or Operational Corpus data cannot substitute for an authorized correctness decision or aggregate accuracy |
| ADR 0010–0015 intelligence findings/observations | Production descriptive findings, not canonical scoring outputs | `IntelligenceEngineService` authorizes execution and validated findings from the Developer, Tokenomics, Governance, Security, and On-chain engines; each engine owns only its declared descriptive analysis | Immutable service-loaded `EvidenceBundle` and `EngineContext` from approved persisted evidence, with canonical identity/context, provenance, conflicts, missing evidence, and replay cutoff | `IntelligenceEngineService`; engines and repositories never authorize persistence themselves | Descriptive intelligence reports and future explicitly approved downstream services consuming persisted findings | Findings cannot score, rank, recommend, time, compose other engines, resolve conflicts, infer absent facts, or substitute for Market Validation, Timing, Opportunity, risk, valuation, ownership, intent, or strategy |
| Legacy/plugin Intelligence outputs, including Signal/Observation/Insight/Intelligence objects | Experimental/research when executed through `PipelineOrchestrator`/plugin lifecycle | The emitting experimental engine defines content; `PipelineOrchestrator` coordinates but does not become analytical owner | Explicit plugin/pipeline context and approved evidence contracts in experimental execution | Experimental persistence adapter/service boundary only; no production analytical authorization | Tests, plugin conformance, experimental Fusion/research | Plugin registration, package entrypoint, persisted record, or Dashboard display cannot promote the output or bypass Candidate Registry, Evidence Intelligence, Trust, or Market Validation |

This map distinguishes semantic ownership from persistence ownership. An engine may define a deterministic calculation while still lacking authority to execute it as production. A repository may persist a record while having no authority over its meaning.

## Promotion And Migration Rules

An experimental output does not become production because it has any combination of models, deterministic identities, configuration, tests, CLI commands, automation jobs, record classes, repositories, persisted rows, reports, Dashboard fields, or desktop presentation.

Promotion requires a future accepted ADR that names one defined output and specifies all of the following:

1. Semantic purpose, scope, units, and prohibited substitutes.
2. Single owner service/runtime and its relationship to the canonical Market Validation path.
3. Canonical candidate and economic-entity/representation/context scope under ADRs 0003–0005.
4. Approved persisted authority inputs, provenance, lineage, conflict handling, missing-evidence behavior, and evidence sufficiency under ADR 0002.
5. Effective-time, recorded-time, and known-time policy.
6. Replay cutoff rules, deterministic identity, model/configuration versions, and correction/supersession lifecycle.
7. Historical-validation and calibration requirements appropriate to the claim.
8. Persistence authorization boundary consistent with Provider → Service → Repository → Persistence under ADR 0009.
9. Compatibility, migration, cutover, rollback, and retirement rules that prevent parallel analytical authority.
10. Allowed consumers and versioned Dashboard/API exposure, including explicit unavailable states.
11. Tests and conformance evidence proving production semantics without changing unrelated scoring, timing, committee, or reporting behavior.

If promotion would replace or alter a current canonical output, the future ADR must explicitly supersede or amend the affected part of ADR 0007 and this ADR. Additive descriptive Intelligence Engine work under ADRs 0010–0015 does not by itself amend either decision.

During migration, at most one runtime may be authoritative for one semantic output at one effective boundary. Shadow or comparison execution must be labeled non-authoritative, isolated from production consumers, and incapable of writing a competing production record.

## Interaction With Existing ADRs

This ADR is compatible with and governed by all accepted ADRs 0001–0015:

- **ADR 0001:** discovery remains the market entrypoint; this decision does not prioritize speculative deep scoring over market visibility.
- **ADR 0002:** every approved analytical output remains evidence-first, provenance-preserving, conflict-visible, missingness-explicit, and cutoff-safe.
- **ADR 0003:** the SQL-backed Candidate Registry remains the canonical discovered-market identity and lifecycle map; it does not own analytical scores.
- **ADR 0004:** trusted identity, source reliability, conflicts, freshness, and unavailable states precede advanced intelligence.
- **ADR 0005:** project, protocol, token, network, contract, representation, wallet, and provider listing contexts cannot be collapsed into interchangeable analytical targets.
- **ADR 0006:** Candidate Registry and evidence repositories remain authoritative; this ADR introduces no knowledge graph or competing graph runtime.
- **ADR 0007:** **reaffirmed and clarified, not superseded**. Option A and the Market Validation production boundary remain in force.
- **ADR 0008:** plugin capabilities remain behind versioned contracts and cannot bypass registry, evidence, trust, repository, or production-scoring boundaries; current module-path plugins are not treated as sandboxed.
- **ADR 0009:** services authorize domain decisions and persistence; repositories store supplied authorized state only.
- **ADR 0010:** `IntelligenceEngineService` owns evidence loading, cutoff enforcement, validation, identity validation, and persistence authorization; engines/builders do not load, persist, score, rank, time, or compose.
- **ADR 0011:** Developer findings remain independent, persisted-evidence-backed repository-activity observations; the engine cannot score, rank, recommend, value, time, compose, or infer findings from missing evidence.
- **ADR 0012:** Tokenomics findings remain descriptive and conflict-preserving; supply, unlock, vesting, balance, fee, revenue, and TVL observations do not authorize valuation, risk, ownership attribution, ranking, or recommendation.
- **ADR 0013:** Governance findings remain context-isolated and descriptive; they cannot become governance-quality/decentralization scores, rankings, recommendations, timing, or investment conclusions.
- **ADR 0014:** Security findings remain context-isolated and descriptive; they cannot become safety, trust, security-level, or risk scores, rankings, recommendations, or cross-engine conclusions.
- **ADR 0015:** On-chain findings remain context-isolated and descriptive; balances, transfers, and identifiers cannot imply ownership, accumulation/distribution intent, manipulation, profitability, strategy, scoring, ranking, or prediction.

## Operational And Presentation Boundaries

- Scheduler decides when approved work is due; it does not decide what conclusions mean.
- Automation records job/run lifecycle and invokes an approved execution boundary; configuration cannot promote an experimental job.
- Persistence records preserve authorized artifacts; record existence cannot promote an artifact.
- Operational Corpus records downstream execution observations. Its rankings, recommendations, predictions, and outcomes remain records of what a caller supplied or a runtime emitted, not independent analytical conclusions.
- Dashboard API and desktop console project stored/runtime state. They may show production, experimental, operational, unavailable, or insufficient states only with the classification supplied by the authoritative source. They cannot reinterpret null as zero, derive correctness from closure, or calculate a replacement score/ranking.

## Non-Goals

This decision does not authorize or define:

- new Opportunity factors or changes to existing Opportunity weights/formulas;
- tokenomics, unlock, catalyst, sufficiency, governance, security, or on-chain scoring integration;
- Opportunity assessment persistence or ranking implementation;
- any general ranking service or ranking snapshot;
- a database/backend selection or universal analytical database;
- Phase 2 persistence, schema, migration, lifecycle, or replay implementation;
- prediction correctness or accuracy implementation;
- Dashboard API, desktop console, CLI, automation, or Operational Corpus expansion;
- repair of the known Dashboard schema-contract test failure;
- a knowledge graph, distributed runtime, trading, portfolio execution, or investment recommendation;
- promotion, migration, or cutover of any experimental output.

## Consequences

- Market Validation remains the only canonical production analytical runtime.
- Production Timing and canonical committee fields retain their existing meanings and owners.
- Experimental models and repositories remain useful for research without acquiring production authority accidentally.
- ADR 0010–0015 descriptive engines retain their accepted production finding status without becoming scoring or orchestration authorities.
- Automation, persistence, Operational Corpus, Dashboard, and desktop presentation remain downstream or operational boundaries.
- Future persistence and Dashboard work is blocked from presenting Opportunity, ranking, or prediction correctness as authoritative until a promotion ADR satisfies this decision.
- Existing runtime behavior, storage, schemas, configuration, tests, and public interfaces are unchanged by this documentation-only decision.

## Alternatives Considered

- **Promote `PipelineOrchestrator` and Fusion now.** Rejected because no approved compatibility, cutover, persisted-input authority, replay, or historical-validation plan replaces the Market Validation runtime.
- **Treat Market Validation and the experimental pipeline as parallel production runtimes.** Rejected because this would violate the Constitution's single analytical entrypoint and create duplicate semantic owners.
- **Promote outputs individually when a model, test, or repository exists.** Rejected because implementation completeness does not establish provenance, replay, lifecycle, compatibility, or production meaning.
- **Use canonical Timing or `hunter_score` as the Opportunity score.** Rejected because these outputs have different semantics and owners.
- **Let Automation, Operational Corpus, Dashboard, or the desktop console define missing rankings or prediction correctness.** Rejected because operational and presentation layers cannot own analytical reasoning.
- **Treat ADR 0010–0015 descriptive engines as a cross-engine scoring pipeline.** Rejected because those ADRs explicitly preserve independent descriptive findings and prohibit scoring, ranking, timing, recommendation, and composition.
- **Select a database and persist all analytical candidates before resolving authority.** Rejected because storage cannot decide semantic ownership and would make later migration riskier.

## Reasoning

The conservative decision preserves the validated production path while making experimental work safe to continue. It prevents storage, automation, or presentation from silently becoming analytical authority and gives future promotion work a complete, auditable gate. This resolves Phase 1.1 without implementing Phase 2 or changing any runtime behavior.
