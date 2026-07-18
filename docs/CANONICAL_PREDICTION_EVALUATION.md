# Canonical Prediction Evaluation

## Authority boundary

ADR 0019 assigns `PredictionEvaluationService` sole authority for canonical prediction publication, evaluability, correctness, accuracy, and calibration. This is an audit/evaluation authority, not an investment-analysis runtime. Market Validation remains the sole canonical production analytical runtime.

Operational Corpus predictions, outcomes, validation samples, closures, benchmark returns, and state files remain operational observations. Existing rows are readable as `legacy-unevaluable`; they are never imported, republished, scored, or aggregated automatically. Dashboard, desktop, CLI, Automation, Scheduler, Backtest, Market Validation, Opportunity, Timing, ranking, experimental stores, and repositories do not calculate or authorize evaluation conclusions.

## Dedicated store

The canonical store is configured by `configs/prediction_evaluation_persistence.yaml` and defaults to `data/prediction_evaluation/canonical_prediction_evaluation.sqlite`. It is disabled by default, has its own physical path, and is separate from Operational Corpus, `data_ops.sqlite`, canonical Market Validation, experimental reasoning, Backtest, Dashboard, and automation storage.

Opening the store never creates it. `bootstrap_prediction_evaluation_store(path)` is the only structural bootstrap and must be called explicitly with a deliberate path. Bootstrap creates the existing generic persistence schema and no rows. Tests use temporary paths only. Readiness reports `absent`, `schema-only`, `populated`, or `unreachable` without bootstrapping or interpreting population as correctness.

The store accepts only the `prediction-evaluation` identity namespace, `canonical-evaluation` authority classification, and these semantic types:

- `canonical.prediction-evaluation-policy`
- `canonical.prediction-publication`
- `canonical.prediction-evaluation`
- `canonical.prediction-accuracy-snapshot`
- `canonical.prediction-calibration-snapshot`

Repositories mechanically persist service-authorized plans, exact-load records, return type-scoped history and lineage, and perform strict-known selection. One repository transaction covers publication plus initial lifecycle state, evaluable plus terminal evaluation, and paired accuracy plus calibration snapshots. Failure rolls back the entire transaction.

## Contracts

`EvaluationPolicy` fixes identity/version, claim and entity scope, comparison mode/operator, units, baseline and horizon rules, precision, outcome source/version, observation and data-deadline windows, benchmark rule, tolerance, missing/ambiguous behavior, strict-known rule, aggregate sample minimums, correction policy, and methodology version. Policy identity is immutable: an identical retry is idempotent and conflicting content is rejected.

`PredictionPublication` requires an unambiguous machine-evaluable claim, target/entity identity, operator/threshold/condition, baseline value and provenance, effective/publication/due/recorded/known times, exact pre-existing policy identity/version, model/methodology/configuration versions, source IDs/versions, evidence references, and optional event-specific forecast probability. The bound policy must have been strict-known before publication. Corrections require an explicit current predecessor and reason and are rejected at or after due time.

`OutcomeObservation` is the only outcome input. It carries target/entity scope, source type/version, measurement and units, effective/recorded/known times, evidence references, and optional benchmark measurement. There is no provider, network acquisition, raw corpus ingestion, or latest/current fallback.

Evaluation records bind the exact publication, policy, optional outcome, source versions, state, reason, correctness, clocks, and lineage. Correctness is calculated only after a compatible strict-known outcome is selected and uses the policy's comparison mode, precision, operator, threshold, and tolerance.

Aggregate requests declare a cohort, filter definition, target set, window, policy/model/methodology/configuration compatibility, and the exact current evaluation IDs. The service verifies that the supplied IDs equal the complete declared compatible cohort. Accuracy and calibration snapshots bind the ordered IDs and a canonical source fingerprint.

## Lifecycle

| Current state | Authorized successors |
| --- | --- |
| `pending` | `due`, `invalidated`, `superseded` |
| `due` | `awaiting-data`, `evaluable`, `unevaluable`, `invalidated`, `superseded` |
| `awaiting-data` | `evaluable`, `unevaluable`, `invalidated`, `superseded` |
| `evaluable` | `evaluated-correct`, `evaluated-incorrect`, `invalidated`, `superseded` |
| `evaluated-correct` | `superseded` |
| `evaluated-incorrect` | `superseded` |
| `unevaluable` | `superseded` |
| `invalidated` | `superseded` |
| `superseded` | none |

Retries of the current state are idempotent. Invalid reversals fail. Due or closure alone leaves correctness null. Outcome-dependent states can be created only by `evaluate()`. Missing data before the deadline becomes `awaiting-data`; missing data after it becomes `unevaluable`. Unknown-known-time, stale/window-incompatible, post-cutoff, wrong-source, wrong-entity, wrong-unit, and benchmark-incompatible observations cannot produce correctness.

Every correction is an immutable successor with explicit predecessor and reason. Predecessors remain loadable. Strict-known replay excludes future-effective, future-recorded, future-known, unknown-known-time, and superseded records at the selected cutoff. It never falls back to latest state.

## Accuracy and calibration

Accuracy includes only current compatible `evaluated-correct` and `evaluated-incorrect` records. It preserves numerator, denominator, exclusions by lifecycle state, cohort/filter/target/window, policy and model versions, source IDs, fingerprint, and a deterministic 95% Wilson interval when the policy's minimum sample is met.

An empty or inadequate denominator yields `insufficient-sample`, null accuracy, and null interval—never zero. Unevaluable, invalidated, awaiting, pending, due, legacy, and superseded records do not become incorrect observations.

Calibration additionally requires every included publication to contain a pre-outcome event-specific forecast probability. When sufficient, the service records Brier score and fixed decile reliability bins. Each bin below the policy minimum remains `insufficient-sample` with null observed and forecast rates. Missing probabilities or inadequate total samples produce a null Brier score rather than substituting generic confidence or experimental Probability output.

## Consumers and non-goals

Permitted consumers are direct service tests, audit/research tools, and explicitly versioned read-only projections. `dashboard-api.v1` exposes the additive `predictions.canonical_evaluation` projection version `canonical-prediction-evaluation-dashboard.v1`. It performs strict-known reads of current canonical lifecycle and compatible aggregate snapshots only; it does not evaluate, aggregate, write, bootstrap, or fall back to another store. No desktop analytical view, Operational Corpus, Automation, Scheduler, alert, acquisition, Market Validation, Opportunity, Timing, ranking, Backtest, or experimental-store wiring is authorized. This changes no analytical score or authority outside ADR 0019 and creates no live runtime store or data.
