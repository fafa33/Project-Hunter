# ADR 0019: Prediction Evaluation Authority

## Status

Accepted.

## Context

Project Hunter currently records operational prediction-like observations in Operational Corpus JSON/JSONL. `OperationalCorpusRecorder` creates an `open` prediction from a pipeline execution, can append caller-supplied realized outcomes and benchmark values, and can close a due prediction when a later execution supplies comparable benchmark values. It also writes validation-sample and closure observations. The monitor chooses a later operational execution, computes simple start/end returns, and records `EVALUATION_COMPLETE`; it does not decide whether the original claim was correct.

Current prediction rows contain an identity, target ID/type, effective/published and optional horizon times, operational run/corpus references, evidence observations, rankings, recommendations, confidence values, benchmark values, artifacts, and `open` status. They do not require one immutable prediction claim, direction, threshold or condition, a versioned horizon rule, baseline measurement semantics, outcome/benchmark policy, model/methodology/configuration versions, or explicit recorded/known-time policy. Closure therefore means only that an operational outcome was attached. It is not correctness, accuracy, or calibration.

ADR 0016 classifies prediction correctness, evaluation records, and aggregate accuracy/calibration as unimplemented authority. The validated implementation plan requires this authority decision before Phase 4.1 creates a service or persistence path.

## Decision

Project Hunter establishes a distinct **canonical prediction-evaluation authority** for auditable evaluation only. This authority is not an investment-analysis runtime and does not alter Market Validation's status as the sole canonical production analytical runtime.

The future `PredictionEvaluationService` is the sole service/runtime owner of:

- immutable prediction-contract validation and evaluability state;
- outcome selection under a predeclared evaluation policy;
- prediction evaluation records and correctness decisions;
- aggregate accuracy snapshots; and
- calibration snapshots and insufficient-sample decisions.

The service owns semantic validation, target/entity matching, policy selection, clocks and cutoffs, strict-known selection, lifecycle transitions, correction authorization, aggregate cohort selection, and persistence write plans. A pure policy evaluator may calculate a declared comparison, but owns no I/O, lifecycle, or persistence. Repositories only store and retrieve service-authorized immutable records.

### Authority map

| Output | Sole semantic owner | Authorized inputs | Persistence authorization | Permitted consumers | Prohibited substitutes and claims |
| --- | --- | --- | --- | --- | --- |
| Prediction evaluation record | `PredictionEvaluationService` | One complete immutable prediction contract, one predeclared policy, and strict-known authorized outcome/benchmark records | `PredictionEvaluationService` | Audit/research, historical validation, future versioned read APIs | Operational closure, validation sample, corpus outcome, Backtest result, Opportunity, Timing, rank, recommendation, or Dashboard calculation cannot substitute. |
| Correctness decision | `PredictionEvaluationService`, applying the policy bound before the outcome | Exact evaluation record inputs only | Same service as part of the evaluation record | Same as evaluation record | No engine, repository, corpus, scheduler, automation job, CLI, Dashboard, or human-edited label may declare correctness. |
| Evaluability state | `PredictionEvaluationService` | Contract completeness, due rule, target/outcome availability, strict-known provenance, policy compatibility, and data deadline | Same service through immutable lifecycle records | Operations and audit may project the state read-only | `open`, `closed`, due timestamp, outcome presence, store readiness, or return availability cannot imply evaluability. |
| Aggregate accuracy | `PredictionEvaluationService` aggregate boundary | Compatible immutable evaluated-correct/incorrect records only | Same service through immutable aggregate snapshots | Audit, historical validation, future read-only API/Dashboard | Raw closure counts, Operational Corpus readiness, Backtest accuracy, Market Validation rank, or Dashboard arithmetic cannot substitute. |
| Calibration metrics | `PredictionEvaluationService` aggregate boundary | Compatible evaluations whose prediction contract contained a policy-authorized pre-outcome probability | Same service through immutable calibration snapshots | Audit, research, future read-only API/Dashboard | Generic confidence, committee confidence, Probability package output, post-outcome confidence, or missing probability cannot be treated as a forecast probability. |

Operational Corpus remains a downstream operational/audit store and a possible source of legacy observations; it owns neither prediction meaning nor correctness. Dashboard API and desktop console remain read-only projections. Automation and Scheduler may invoke due-work checks after Phase 4.1, but cannot select policy, outcomes, state, correctness, cohorts, exclusions, or metrics. Backtest remains a separate historical-testing subsystem and cannot write or replace canonical prediction evaluations. Market Validation `hunter_score`, Market Validation ranking and committee fields, canonical Timing, experimental Opportunity, experimental Probability/Pattern/Necessity/Committee, and all ranking helpers remain semantically separate.

### Immutable prediction contract

A prediction is eligible for the new authority only if every field below is fixed and durably recorded before the outcome can be known:

1. Stable prediction identity and immutable contract/schema version.
2. Canonical target identity plus explicit entity and representation scope under the Candidate Registry/entity model.
3. Prediction type and one unambiguous machine-evaluable claim.
4. Direction and, where applicable, comparison operator, threshold, tolerance, units, condition, and treatment of equality.
5. Effective time, publication time, horizon definition, due-time rule, timezone/calendar convention, observation window, and outcome-data deadline.
6. Baseline/reference measurement identity, value, units, observation time, source record ID/version, and adjustment policy.
7. Target-outcome and benchmark policy IDs/versions, including benchmark identity, measurement source, comparison formula, corporate/token/network-event adjustments where applicable, and missing/ambiguous-data behavior.
8. Model, methodology, and configuration identity/version. A claim emitted without a model must explicitly identify its rule/manual authority rather than invent a model version.
9. Evidence, provenance, source-record IDs/versions, conflicts, confidence semantics, and missing evidence.
10. Effective, recorded, and explicit known-time context for the prediction and every input. Unknown-known-time inputs cannot support strict evaluation.
11. Optional forecast probability only when its semantics, range, class/event, and calibration policy are fixed before publication.
12. The evaluation-policy ID/version and immutable canonical hash of the complete pre-outcome contract.

The contract cannot contain multiple recommendations or rankings and later choose one as “the prediction.” Ambiguous prose is not machine-evaluable. A policy change, threshold change, target correction, horizon correction, or baseline correction creates a successor prediction contract; it never mutates the original.

### Existing records and legacy classification

All prediction rows currently produced by `operational_corpus._prediction`, including existing open and closed rows, are **legacy operational predictions** under this ADR. They remain readable. They are incomplete for strict evaluation because the schema does not require the claim/direction/threshold, horizon rule, measurement semantics, policy binding, model/method/config versions, or trustworthy complete recorded/known-time contract.

Existing `outcomes.jsonl`, `validation_samples.jsonl`, `prediction_closures.jsonl`, benchmark returns, and `prediction_state.json` remain operational observations. They may be linked for audit, but cannot be relabeled as canonical evaluation records or used in authoritative aggregates. No service may fabricate missing baselines, timestamps, claim semantics, provenance, policies, probabilities, or outcomes. A legacy record is reported as `legacy-unevaluable` unless a future separately recorded prediction contract proves that every required field was fixed before the outcome was knowable; post-hoc enrichment is prohibited.

### Evaluability lifecycle

Lifecycle records are immutable and idempotent. Repeating the same authorized transition produces the same identity/content; conflicting content for that identity is rejected. Time passing may make a transition eligible, but only the service authorizes and records it.

| State | Meaning | Permitted next state(s) |
| --- | --- | --- |
| `pending` | Complete accepted contract exists and due time has not arrived | `due`, `invalidated`, or successor correction (`superseded`) |
| `due` | The predeclared due rule is satisfied; no correctness inference has occurred | `awaiting-data`, `evaluable`, `unevaluable`, `invalidated`, or `superseded` |
| `awaiting-data` | Required outcome/benchmark data is not yet available but the predeclared data deadline has not passed | `evaluable`, `unevaluable`, `invalidated`, or `superseded` |
| `evaluable` | Exact compatible strict-known outcome inputs exist and the policy can deterministically evaluate the claim | `evaluated-correct`, `evaluated-incorrect`, `invalidated`, or `superseded` |
| `evaluated-correct` | The predeclared policy evaluates the claim as correct | terminal, except an explicit correction successor; predecessor becomes `superseded` |
| `evaluated-incorrect` | The predeclared policy evaluates the claim as incorrect | terminal, except an explicit correction successor; predecessor becomes `superseded` |
| `unevaluable` | Required data or unambiguous semantics are unavailable at the declared deadline, with a named reason | terminal, except an explicit correction successor; never counted as incorrect |
| `invalidated` | A pre-outcome integrity, authority, identity, policy, or contract defect makes the prediction invalid; reason and authorization are required | terminal, except an explicit correction successor; never counted in accuracy |
| `superseded` | An immutable successor corrects or replaces this lifecycle/evaluation record with explicit predecessor linkage and reason | terminal; only the successor can advance |

`legacy-unevaluable` is a legacy classification, not a route that silently upgrades into `pending`. Closure is orthogonal operational state: an open or closed corpus row can still be unevaluable, and a canonical evaluation can exist only through this lifecycle.

A correction is a new record with a new identity, the same logical prediction/evaluation identity where appropriate, explicit `supersedes_id`, correction reason, authorizing policy version, and complete lineage. Corrections never erase predecessors or rewrite aggregates in place; affected aggregates receive successor snapshots referencing the corrected evaluation set.

### Evaluation-policy contract

Every policy is immutable, versioned, persisted, and bound into the prediction contract before publication. It must define:

- eligible prediction type, entity/representation scope, units, direction, operator, threshold, tolerance, equality and boundary behavior;
- baseline source and observation rule;
- horizon/due-time calculation, timezone/calendar, target observation window, late-data deadline, and acceptable observation timestamp distance;
- authorized outcome provider/record types, source/version/provenance requirements, adjustment and conflict policy;
- benchmark identity and exact target-versus-benchmark formula where comparison is claimed;
- missing, stale, disputed, revised, unavailable, ambiguous, outlier, zero-denominator, and provider-failure behavior;
- minimum evidence/trust requirements and named unevaluable/invalidated reason codes;
- deterministic numeric precision and rounding after, not before, comparison;
- policy identity, schema/methodology/configuration versions, effective period, compatibility rules, and canonical hash.

Correctness compares only the declared claim against authorized outcome data. The policy may not be chosen, amended, or reinterpreted after the outcome to improve results. Tolerance cannot be introduced retroactively. Ambiguous claims and incompatible units or entities are unevaluable, not guessed.

Strict-known evaluation selects only prediction, baseline, policy, outcome, and benchmark records effective, recorded, and explicitly known at or before their declared cutoffs. Revised outcome data may produce an explicit correction when the policy authorizes revisions, but a later record cannot rewrite what was knowable at an earlier replay cutoff. There is no `latest`, current-price, current-state, raw-file, Dashboard, Operational Corpus state, or post-cutoff fallback.

### Aggregate accuracy contract

An accuracy snapshot is immutable and contains:

- aggregate identity, schema and metric-policy version;
- cohort definition and target/entity scope;
- inclusive window start/end and the time basis used;
- prediction type, evaluation-policy version, model/methodology/configuration versions, and any compatibility partition;
- ordered source evaluation IDs and versions;
- numerator (`evaluated-correct` count), denominator (`evaluated-correct + evaluated-incorrect` count), and exact ratio;
- counts and reason breakdown for pending, due, awaiting-data, unevaluable, invalidated, superseded, legacy, and otherwise excluded records;
- effective, recorded, known-time and replay cutoff context;
- confidence-interval method/version and interval, or explicit `insufficient-sample` status with null metric/interval;
- correction/supersession lineage and canonical hash.

Different policies, prediction types, entity scopes, forecast horizons, incompatible model/configuration versions, or cutoff semantics cannot be pooled unless an accepted aggregate policy explicitly defines a compatible partition. Unevaluable and invalidated predictions are exclusions, not incorrect predictions and not denominator entries.

The aggregate policy fixes a minimum total sample and any subgroup minimum before aggregation. At or below an inadequate sample threshold, status is `insufficient-sample`; accuracy and significance claims are null. When a binary accuracy interval is justified, the policy must specify a deterministic method such as a versioned 95% Wilson interval. A missing denominator produces `insufficient-sample`, never `0%`.

### Calibration contract

Calibration exists only for prediction contracts containing a pre-outcome forecast probability for the exact evaluated binary event. Generic confidence, ranking score, Opportunity score, Probability assessment, committee confidence, or post-outcome value is not interchangeable with forecast probability.

A calibration snapshot contains the same cohort, compatibility, lineage, time, exclusion, sample-size, and source-evaluation metadata as accuracy plus the probability-field contract. A versioned calibration policy may define Brier score, logarithmic loss with a declared clipping rule, observed-versus-predicted reliability bins, calibration error, and deterministic uncertainty intervals. Bin boundaries, minimum total sample, minimum per-bin sample, weighting, clipping, and interval methods are fixed before data selection. Unsupported metrics remain unavailable. Sparse totals or bins are labeled `insufficient-sample`, with null aggregate or bin values; no empty bin becomes zero and no statistical significance is implied without the policy's required evidence.

### Persistence and store boundary

Phase 4.1 must use a **dedicated canonical prediction-evaluation persistence boundary**, separate from Operational Corpus, `data_ops.sqlite`, experimental Opportunity/derived-reasoning storage, canonical Market Validation storage, Backtest files, Dashboard state, and automation records. The boundary contains immutable prediction contracts, evaluation policies, lifecycle/evaluation records, aggregate policies, accuracy snapshots, and calibration snapshots.

This ADR chooses the logical and authority boundary, not a database product or migration design. Phase 4.1 may reuse the repository's generic immutable/bitemporal record infrastructure only through prediction-evaluation-specific service authorization, semantic allow-listing, configuration, and physical store isolation. Backend selection must preserve atomic authorized writes, deterministic identity/idempotency, correction lineage, exact-ID reads, strict-known replay, and independent backup/retention. Store reachability or population never implies correctness.

Permitted consumers are the evaluation service, audit/historical-validation research, and future explicitly versioned read-only API/Dashboard/desktop projections. Consumers receive authoritative records or explicit unavailable/legacy/insufficient states. They cannot recompute correctness, alter exclusions, combine cohorts, substitute metrics, or fabricate zeros.

### Non-goals and production boundary

This ADR does not implement evaluation; authorize automatic investment recommendations; create Opportunity ranking; change Market Validation scoring, ranking, committee, Timing, or report semantics; promote Opportunity or experimental engines; implement Dashboard/API/desktop views; schedule evaluation jobs; select a database product; migrate legacy data; or permit retroactive policy fitting.

ADR 0016 is reaffirmed, not superseded. Prediction evaluation is a canonical audit/evaluation authority over fully contracted predictions, not a second canonical investment-analysis runtime. Market Validation remains the sole canonical production analytical runtime.

## Compatibility With Accepted ADRs

| ADR | Compatibility effect |
| --- | --- |
| 0001 | Evaluation measures declared predictions and does not become discovery, screening, or candidate prioritization authority. |
| 0002 | Complete provenance, explicit missingness, conflict visibility, strict-known replay, and no fabricated legacy fields govern the entire contract. |
| 0003 | Candidate Registry supplies canonical target identity/lifecycle; evaluation cannot create or merge candidates. |
| 0004 | Trust, reliability, conflicts, staleness, and unavailable states control evaluability and cannot be normalized away. |
| 0005 | Entity and representation scope is mandatory for predictions, outcomes, benchmarks, cohorts, and comparisons. |
| 0006 | No knowledge graph or competing identity/evidence authority is introduced. |
| 0007 | Market Validation remains the canonical production investment-analysis runtime; pipeline/corpus prediction observations remain non-authoritative legacy inputs. |
| 0008 | Plugins cannot emit canonical evaluations, bypass the evaluation service, or gain authority through registration. |
| 0009 | The evaluation service owns decisions, clocks, lifecycle, replay, and writes; repositories remain mechanical persistence adapters. |
| 0010 | Intelligence engines remain descriptive and cannot evaluate predictions, select outcomes, or calculate accuracy/calibration. |
| 0011 | Developer findings remain descriptive evidence and cannot become correctness or calibration decisions. |
| 0012 | Tokenomics findings remain descriptive and context-bound; no outcome, return, or correctness inference is authorized. |
| 0013 | Governance findings remain descriptive and cannot establish prediction correctness. |
| 0014 | Security findings remain descriptive and cannot establish prediction correctness or investment success. |
| 0015 | On-chain findings remain descriptive; balances/transfers cannot infer ownership, profitability, strategy, or correctness. |
| 0016 | Reaffirmed: prediction evaluation is distinct audit authority and does not alter Market Validation's sole production analytical-runtime status. |
| 0017 | Experimental Opportunity outputs are not canonical predictions or evaluations and cannot be promoted through this lifecycle. |
| 0018 | Factor-source decisions and missingness remain unchanged; evaluation outcomes cannot retroactively authorize Opportunity factors. |

No accepted ADR 0001–0018 is superseded, weakened, or contradicted.

## Consequences

- Phase 4.1 may implement one auditable evaluation service and isolated canonical store without turning operational files into analytical authority.
- Existing corpus predictions and outcomes remain visible but do not contaminate correctness, accuracy, or calibration.
- Accuracy and calibration can be reproduced from exact compatible evaluations and cutoffs, with honest exclusions and insufficient-sample states.
- Operational automation and presentation can later project states without owning evaluation semantics.
- Implementing the complete prediction contract before new predictions are evaluated is mandatory; legacy closure volume cannot accelerate the process.

## Alternatives Considered

### Let Operational Corpus own correctness and accuracy

Rejected because it stores operational and caller-supplied observations, lacks the complete pre-outcome contract, and currently equates closure only with attached outcomes.

### Treat every closed prediction as correct or incorrect from return sign

Rejected because current rows do not bind a claim, direction, threshold, tolerance, benchmark, or outcome policy. Return sign alone cannot evaluate arbitrary recommendations or rankings.

### Reuse Backtest or Market Validation as the evaluation owner

Rejected because Backtest measures historical test runs and Market Validation owns current production analysis. Prediction evaluation has a distinct lifecycle, policy, and correction boundary.

### Compute aggregates in Dashboard or automation

Rejected because presentation and operational scheduling cannot own cohort selection, exclusions, correctness, or statistical policy.
