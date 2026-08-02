# ADPR-0004 — Canonical Market Validation Composition Authority (Phase 1)

## Metadata

- ADPR ID: `ADPR-0004`
- Status: `READY_FOR_REVIEW`
- Version: 1.1
- Author: Codex, on behalf of Issue #173
- Reviewers: independent architecture review required
- Created: 2026-08-02
- Approved: not yet approved
- Related Epic: not yet created
- Related Issue: [Issue #173](https://github.com/fafa33/Project-Hunter/issues/173)
- Planned or produced ADR: not yet created
- Supersedes: not applicable
- Superseded by: not applicable

## Executive Summary

The Canonical Valuation, Comparative Valuation, Mispricing, and Asymmetry foundations now have separate service owners, immutable evidence lineage, append-only correction, strict-known replay, explicit missingness, and fail-closed behavior. ADR 0016 makes Canonical Market Validation the sole production analytical composition runtime. ADRs 0020 and 0021 require a separately accepted composition decision before the valuation family may enter that runtime. ADR 0024 further requires `valuation` to remain a structured, non-directional assessment rather than a favorable scalar.

The unresolved decision is how Canonical Market Validation may accept, normalize, correlate, cap, persist, replay, activate, canary, and roll back valuation-family contributions without duplicating upstream authority or creating downstream Opportunity, ranking, timing, portfolio, or recommendation authority.

This preparation evaluates four coherent architectures and recommends a service-owned composition authority with Market Validation-owned exact-version adapters, immutable composition snapshots, three historically calibrated monotonic scalar adapters (`comparative_valuation`, `mispricing`, and `asymmetry`), a non-weighted structured `valuation` reference, evidence-lineage contribution groups, conservative group caps, and fail-closed eligibility. No production activation is authorized. Real leakage-safe calibration corpora, cap sensitivity evidence, and shadow/canary evidence remain activation gates.

Self-assessment: `READY_FOR_ADR`. The decision space is complete enough for independent review and a later ADR without inventing ownership, normalization, replay, persistence, correction, activation, or rollback rules during implementation.

## Problem Statement

### Current condition

The four valuation-family services produce immutable raw assessments, but their normalized scalar values remain unavailable where calibration is required. Existing Market Validation runtime code predates these canonical authorities and cannot acquire authority by accepting similarly named fields. No accepted decision currently defines composition adapters, contribution identity, calibration eligibility, correlation caps, a composition snapshot, or an activation procedure.

### Desired condition

Canonical Market Validation has one explicit composition authority that can deterministically:

- accept only exact compatible versions of canonical valuation-family assessments;
- preserve raw upstream meaning and provenance without recalculation;
- apply only predeclared, leakage-tested calibration policies;
- prevent correlated or duplicated evidence from multiplying contribution;
- persist one immutable, replayable composition decision;
- return an explicit unavailable result whenever any required authority, policy, lineage proof, or activation gate is absent; and
- remain incapable of producing Opportunity, ranking, timing, portfolio, or recommendation conclusions.

### Decision required

A future ADR must fix:

1. the normalization contract for each family;
2. historical monotonic calibration and out-of-sample validation;
3. correlation/contribution-group assignment and combined caps;
4. residual-independence proof and failure behavior;
5. adapter and composition ownership;
6. exact-version eligibility and strict-known replay;
7. immutable composition identity, persistence, and correction;
8. activation, canary, rollback, runtime, and downstream boundaries.

### In scope

- architecture and methodology preparation only;
- the four canonical valuation-family assessment types;
- Market Validation-owned acceptance and composition;
- normalization, calibration, caps, lineage, persistence, replay, availability, and operational activation gates.

### Out of scope

- implementation, runtime activation, migrations, scheduler changes, UI, Dashboard, reports, or APIs;
- changing any accepted ADR or ADPR;
- changing upstream valuation-family formulas or evidence authority;
- Opportunity Intelligence, ranking, portfolio logic, timing logic, recommendations, committee decisions, or trading;
- assigning production weights or claiming empirical calibration before qualifying evidence exists.

## Problem Validation

ADR 0021 acceptance criterion 8 requires every scalar normalization to be versioned, monotonic, historically calibrated, leakage-tested, and explicitly unavailable outside its supported scope. Its anti-double-counting policy requires contribution groups, caps, evidence assignments, and residualization rules to be immutable methodology inputs. ADR 0024 removes scalar normalization from `valuation` but leaves the other three obligations intact. ADR 0026 explicitly leaves downstream composition, caps, residual independence, and weighting to a separate accepted ADR. Issue #166 verifies the upstream implementation boundary but does not authorize composition. The problem is therefore real, unresolved, and architectural.

## Motivation

Without this decision, enabling the four fields would risk:

- treating raw values with different units and ranges as comparable scores;
- counting one fair-value or market-value signal multiple times;
- selecting current calibrations during historical replay;
- letting adapters or repositories silently become analytical owners;
- persisting partial or non-reconstructable runs; and
- promoting composition into ranking or recommendations by implication.

Remaining unavailable is safer than an invalid composition, but indefinite unavailability also prevents the stabilized foundations from serving their intended canonical consumer. A narrow composition authority resolves that boundary without activating it.

## Existing Architecture

| Boundary | Existing authority | Binding consequence |
|---|---|---|
| Canonical Valuation | `CanonicalValuationService` | Owns structured fair-value assessment; no favorable scalar under ADR 0024. |
| Comparative Valuation | `CanonicalComparativeValuationService` | Owns peer multiple and positive-cheaper raw log residual. |
| Mispricing | `CanonicalMispricingService` | Owns signed fair-value-versus-market ratio. |
| Asymmetry | `CanonicalAsymmetryService` | Owns probability-weighted payoff ratio and scenario lineage. |
| Market Validation input acceptance | service-owned Market Validation input assembly under ADR 0020 | Owns exact eligibility or missingness, never upstream semantics. |
| Production analytical composition | Canonical Market Validation under ADR 0016 | Sole production composition runtime. |
| Repositories | mechanical adapters under ADR 0009 | Persist/retrieve raw history; no cutoff, quality, conflict, or winner decisions. |
| Opportunity | experimental and downstream | Receives no authority or factor mapping from this preparation. |

## Constraints

### Constitutional

- Evidence authority precedes conclusions; missing evidence is not zero.
- One semantic conclusion has one owner.
- Execution and replay are deterministic and explainable.
- Historical evidence and correction lineage remain immutable.
- Architecture changes follow governance before implementation.

### Governance and accepted ADRs

- ADR 0005 preserves economic entity and representation identity.
- ADR 0009 places decisions in services and keeps repositories mechanical.
- ADR 0010 prevents descriptive engines from scoring or composing.
- ADR 0016 keeps Market Validation the sole production composition runtime.
- ADR 0020 requires exact strict-known input selection and explicit missingness.
- ADR 0021 fixes valuation-family meanings, calibration duties, correlation rules, and atomic run persistence.
- ADR 0024 makes `valuation` structured and non-scalar.
- ADR 0025 prevents composition from acquiring evidence-assembly authority.
- ADR 0026 preserves comparative residual semantics and defers downstream composition.

### Technical and operational

- Input adapters must not recalculate upstream assessments.
- A composition must use one timezone-aware cutoff and one exact configuration version.
- No current/latest fallback is permitted.
- Shadow or canary execution is non-authoritative and cannot write production results.
- Rollback disables the new composition entry point without deleting history.

### Persistence, replay, and provenance

- All records are append-only, content-addressed, and bitemporal.
- Corrections are single-predecessor successors; branching is rejected.
- Every transitive input, calibration, cap policy, adapter contract, and evidence assignment is exact-version and strict-known.
- The composition record persists complete evidence-reference sets and deterministic ordering.

## Evidence Inventory

| ID | Evidence | Authority/source | Finding | Quality and limitations | Supports or challenges |
|---|---|---|---|---|---|
| E-001 | Project Constitution | Canonical constitutional authority | Requires evidence authority, determinism, single source of truth, explainability, evolution, and governance. | Highest authority; non-numeric. | Supports service-owned immutable composition. |
| E-002 | ADR 0009 | Accepted ADR | Services decide; repositories persist mechanically. | Binding. | Rejects repository eligibility/composition. |
| E-003 | ADR 0016 | Accepted ADR | Market Validation is the sole production analytical runtime. | Binding; existing runtime predates this composition. | Supports one composition owner. |
| E-004 | ADR 0020 | Accepted ADR | Exact semantic authority, strict-known replay, unavailable-on-failure. | Binding. | Supports exact-version adapters and no fallback. |
| E-005 | ADR 0021 | Accepted ADR | Fixes normalization, correlation, cap, lineage, replay, and atomic-run obligations. | Binding; does not select exact transform/caps. | Defines the principal decision axes. |
| E-006 | ADR 0024 | Accepted ADR | `valuation` is structured, non-directional, and non-weighted until a future explicit decision. | Binding. | Rejects scalar valuation normalization in Phase 1. |
| E-007 | ADR 0025 | Accepted ADR | Evidence Assembly remains a separate upstream authority. | Binding. | Rejects composition-time evidence reconstruction. |
| E-008 | ADR 0026 | Accepted ADR | Comparative raw log residual is positive-cheaper; calibrated normalization and composition remain unavailable. | Binding; real calibration corpus absent. | Supports monotonic adapter and activation gate. |
| E-009 | Issue #166 / PR #169 stabilization | Repository-wide implementation evidence | Upstream services, lineage, replay, and repository purity are stabilized. | Strong implementation evidence; does not validate composition. | Establishes prerequisite readiness. |
| E-010 | Repository tests and records | Reproducible local evidence | Exact IDs, corrections, strict-known replay, correlation groups, and explicit unavailable normalization exist. | Strong for mechanics; synthetic fixtures do not prove empirical calibration. | Supports adapter feasibility; challenges activation. |
| E-011 | Historical calibration corpus | Not yet available | No qualifying multi-family strict-known outcome corpus or accepted calibration record exists. | Material missing evidence. | Blocks scalar availability and canary activation. |
| E-012 | Cap sensitivity/correlation study | Not yet available | No empirical evidence supports numeric production weights or caps. | Material missing evidence. | Blocks numeric cap selection and activation, not ADR architecture. |

## Assumptions

| ID | Assumption | Rationale | Confidence | Falsification condition | Consequence if false |
|---|---|---|---|---|---|
| A-001 | Upstream raw semantics remain stable during Phase 1. | They are accepted and stabilized prerequisites. | High | A successor ADR changes a raw formula or identity. | Composition policy requires a new version and re-audit. |
| A-002 | A strict-known historical corpus can eventually be assembled. | Immutable records and replay exist. | Medium | Coverage is too sparse or outcomes cannot be defined without leakage. | Affected scalar remains unavailable indefinitely. |
| A-003 | Monotonic calibration is preferable to an unconstrained predictive transform. | Direction is fixed by ADR 0021/0026. | High | Out-of-sample evidence shows monotonicity is false or unstable. | Reject the scalar mapping; reconsider the upstream meaning through a new ADPR. |
| A-004 | Evidence-reference intersection can conservatively identify duplicate contribution. | Exact lineage is persisted. | Medium | Transitive lineage is incomplete or semantically overlapping without shared IDs. | Inputs join one conservative group or remain unavailable. |
| A-005 | Shadow execution can be isolated from authoritative persistence. | ADR 0016 defines non-authoritative shadow boundaries. | High | Runtime cannot prevent shadow records from reaching consumers. | Canary is blocked. |

## Architectural Dimensions

1. **Semantic preservation:** composition must not redefine raw family meaning.
2. **Normalization:** scalar direction, range, neutral anchor, extrapolation, and unavailable behavior.
3. **Calibration:** corpus identity, outcome policy, monotonic fitting, leakage tests, temporal folds, recalibration, and drift.
4. **Eligibility:** exact record/version, schema, identity, cutoff, quality, conflict, calibration, and adapter compatibility.
5. **Correlation:** static family groups plus evidence-derived overlap.
6. **Contribution caps:** per-input and combined-group bounds fixed before execution.
7. **Residual independence:** proof, residualization owner, and conservative failure behavior.
8. **Anti-double-counting:** complete transitive evidence sets and one primary contribution assignment.
9. **Ownership:** analytical services, adapters, composer, repositories, runtime, and downstream consumers.
10. **Persistence/correction:** immutable snapshots, deterministic identity, atomicity, successor lineage.
11. **Replay:** strict-known selection of every input and policy; no latest calibration.
12. **Activation/operations:** preflight, shadow, canary, rollback, observability, and authority isolation.
13. **Compatibility/security:** fail closed on unknown versions and prohibit caller-forged normalized values.

## Exhaustive Option Inventory

### Normalization

- N0: no scalar normalization; every scalar family remains unavailable.
- N1: fixed analytic transform (logistic/min-max) without empirical calibration.
- N2: empirical rank/quantile mapping from a strict-known corpus.
- N3: versioned monotonic isotonic calibration against a predeclared outcome, with temporal out-of-sample validation.
- N4: unconstrained learned model.

`valuation` has a separate fixed constraint: V0, structured reference only with no favorable scalar or weight. Any V1 scalar valuation option conflicts with ADR 0024 and is not viable in Phase 1.

### Calibration lifecycle

- C0: one current calibration selected at runtime.
- C1: immutable calibration snapshots selected strict-known by cutoff.
- C2: recalibrate in place.
- C3: append-only calibration successors with corpus, code/policy fingerprint, temporal folds, supported range, neutral anchor, and drift evidence.

### Correlation and caps

- G0: independent per-field weights with no groups.
- G1: one valuation-family group for all scalar contributions.
- G2: fixed `valuation-mispricing` and `asymmetry-scenario` groups, with Comparative joining the first unless residual independence is proven.
- G3: dynamic data-estimated correlations at execution time.

Cap options are: no cap; per-input caps only; group caps only; or both per-input and combined-group caps. Numeric values require later empirical evidence and must be configuration records, never code defaults.

### Residual independence

- R0: declaration by configuration.
- R1: evidence-lineage disjointness only.
- R2: predeclared residualization using exact strict-known inputs plus historical independence tests.
- R3: runtime regression against current data.
- R4: no separate contribution when independence cannot be proven.

### Adapter ownership

- O0: upstream services emit Market Validation-ready normalized values.
- O1: repositories adapt records.
- O2: Market Validation owns thin exact-version adapters and composition.
- O3: a new shared generic normalization service owns all semantics.

### Persistence

- P0: ephemeral composition.
- P1: persist only final score/result.
- P2: persist one immutable composition snapshot containing every input decision, policy, calibration, group, cap, reference set, contribution, and unavailable reason; atomically bind it to the run.
- P3: mutable current composition row.

### Activation

- A0: immediate cutover.
- A1: shadow only.
- A2: preflight, isolated shadow, deterministic replay comparison, bounded canary, then explicit promotion.
- A3: parallel authoritative runtimes.

## Candidate Options

### Option 1 — Market Validation-owned exact-version composition (recommended)

- **Description:** Thin Market Validation-owned adapters validate exact upstream records. Scalar adapters use immutable N3/C3 calibrations. `valuation` remains a non-weighted structured reference. The composer applies G2, both per-input and group caps, R2-or-R4 independence, and persists P2. Activation follows A2.
- **Authority and ownership:** Upstream services own raw assessments; adapters own eligibility and normalization under the accepted composition policy; one Canonical Market Validation composition service owns grouping, caps, and the composition snapshot.
- **Boundaries:** No upstream recalculation, repository decision, or downstream Opportunity output.
- **Persistence and replay:** Complete immutable snapshot and atomic run binding; strict-known input/calibration/config selection.
- **Evidence and provenance:** Complete ordered transitive reference sets; overlap is explicit.
- **Advantages:** Maximum authority clarity, replayability, rollback safety, and compatibility.
- **Disadvantages:** More policy records and activation gates; likely unavailable until real calibration evidence exists.
- **Failure modes:** Sparse corpus, unsupported raw range, overlapping lineage, unknown version, drift, or partial snapshot all fail closed.
- **Migration implications:** Additive future implementation behind a disabled entry point.
- **Reversibility:** High; disable composition version while preserving records.
- **Open dependencies:** Historical corpora, outcome policy, numeric caps, canary thresholds.

### Option 2 — Upstream-owned normalized assessments

- **Description:** Each analytical authority calibrates and persists its own Market Validation scalar; Market Validation only weights results.
- **Authority and ownership:** Normalization is distributed among upstream owners.
- **Boundaries:** Raw and downstream-consumer semantics become coupled.
- **Persistence and replay:** Possible, but coordinated cap and calibration versions span services.
- **Evidence and provenance:** Each service must understand downstream contribution overlap.
- **Advantages:** Locality of raw formulas and tests.
- **Disadvantages:** Duplicates downstream policy, complicates rollback, and pressures upstream services to anticipate Market Validation.
- **Failure modes:** Mixed calibration epochs and inconsistent unavailable rules.
- **Migration implications:** Changes every upstream authority.
- **Reversibility:** Medium to low.
- **Open dependencies:** Cross-service orchestration and policy synchronization.

### Option 3 — Generic central scoring/normalization engine

- **Description:** A reusable engine reads raw records, learns transforms/correlations, and emits combined scores.
- **Authority and ownership:** New generic owner overlaps both analytical services and Market Validation.
- **Boundaries:** Risks recalculating or relabeling domain meaning.
- **Persistence and replay:** Requires a new authority and model lifecycle.
- **Evidence and provenance:** Opaque feature and correlation choices may conceal duplication.
- **Advantages:** Superficial flexibility and reuse.
- **Disadvantages:** Conflicts with single-owner and explicit-methodology constraints.
- **Failure modes:** Hidden ranking, unstable learned transforms, and current-data leakage.
- **Migration implications:** High and cross-cutting.
- **Reversibility:** Low.
- **Open dependencies:** A separate promotion ADR and extensive evidence.

### Option 4 — Preserve complete unavailability

- **Description:** Do not create a composition authority; continue exposing raw assessments only to audit/research.
- **Authority and ownership:** Existing boundaries remain unchanged.
- **Persistence and replay:** No composition record.
- **Evidence and provenance:** No new risk.
- **Advantages:** Safest under missing calibration evidence.
- **Disadvantages:** Canonical Market Validation cannot consume the completed family.
- **Failure modes:** Pressure for ad hoc aliases or manual composition persists.
- **Migration implications:** None.
- **Reversibility:** High.
- **Open dependencies:** Reconsider when evidence exists.

## Recommended Methodology Contract

### Normalization

| Family | Phase 1 composition treatment | Neutral/reference rule | Unsupported behavior |
|---|---|---|---|
| `valuation` | Structured, confidence-bearing reference only; no scalar and zero weighted contribution. | Not applicable. | Missing/incompatible valuation is recorded explicitly; no zero or neutral substitution. |
| `comparative_valuation` | N3 monotonic calibration of ADR 0026 raw log residual; positive-cheaper direction preserved. | Raw residual `0` maps to the calibration policy's declared neutral anchor. | Outside supported entity/corpus/range or absent calibration: unavailable. |
| `mispricing` | N3 monotonic calibration of raw signed ratio; positive-undervaluation direction preserved. | Raw ratio `0` maps to declared neutral anchor. | Unsupported range, weak prerequisite, or absent calibration: unavailable. |
| `asymmetry` | N3 monotonic calibration of `log1p(raw_asymmetry_ratio)` over the non-negative mathematical raw domain `[0, +inf)`; accepted raw values must be finite, so `NaN` and positive or negative infinity are invalid. Ordering remains equivalent to the raw ratio, and a legitimate raw ratio of `0` is represented exactly as transformed value `0`. | Raw ratio `1` / transformed value `log(2)` maps to the declared neutral anchor. Raw ratio `0` is valid worst-case evidence, not missingness. | Undefined denominator (including zero downside), non-finite/unsupported ratio, incomplete scenarios, or absent calibration: unavailable. A valid raw ratio of `0` may never be classified unavailable solely because of transform-domain limits. |

Calibration may never extrapolate silently. A policy must declare supported entity class, methodology versions, raw domain, winsorization prohibition or exact treatment, neutral anchor, outcome definition, horizon, temporal folds, minimum sample/coverage, monotonic direction, fit algorithm/version, validation metrics, drift threshold, effective/recorded/known times, and corpus record IDs/versions. Training and evaluation records must be selected as they were knowable at each historical cutoff. Future outcomes may label past predictions only after their predeclared horizon; they may not alter the historical feature snapshot.

### Correlation, caps, residual independence, and anti-double-counting

1. `valuation` and `mispricing` remain in `valuation-mispricing`; only `mispricing` can contribute a scalar in Phase 1.
2. Comparative joins `valuation-mispricing` whenever its complete evidence-reference set intersects valuation or mispricing market/fundamental lineage, or whenever distinctness cannot be proven.
3. Comparative may receive a separate contribution group only through a versioned residual-independence policy that identifies the shared explanatory inputs, performs predeclared strict-known residualization, and passes temporal out-of-sample independence and stability thresholds.
4. Asymmetry remains in `asymmetry-scenario` only when its complete direct and transitive evidence-reference set is disjoint from the complete evidence-reference sets of Valuation, Comparative Valuation, and Mispricing. If any exact evidence record intersects, the whole Asymmetry scalar is ineligible for composition; recording an overlap declaration or assigning the record to a primary group is not sufficient because the upstream payoff ratio is aggregate and adapters may not recalculate it. Eligibility can be restored only by a separately authorized upstream Canonical Asymmetry assessment that is already residualized under an accepted, exact-version residual-independence policy. Market Validation adapters and the composer may neither residualize nor subtract the overlap. Under the Phase 1 no-partial-composition rule, an ineligible Asymmetry scalar makes the complete composition explicitly unavailable; it is never replaced with zero or a neutral value.
5. Every exact evidence record has one primary contribution assignment per composition policy. References may support confidence or explainability but cannot add weight twice.
6. Each scalar has a predeclared per-input cap and each group a combined cap. The sum of member contributions cannot exceed the group cap. Numeric weights and caps remain undecided until E-012 exists; absence of an accepted exact-version cap policy makes composition unavailable.
7. Runtime-estimated correlations, implicit residual claims, and configuration-only independence declarations are prohibited.

### Ownership and runtime boundary

```text
Canonical analytical services
  -> immutable raw assessments only
  -> Market Validation-owned exact-version adapters
       - eligibility
       - normalization-policy selection
       - normalized scalar or explicit unavailable state
  -> Canonical Market Validation Composition Service
       - evidence intersections
       - contribution groups
       - residual-independence policy
       - sole valuation-family weighting authority
       - per-input and group caps
       - immutable composition snapshot
  -> one immutable post-cap valuation-family contribution
  -> existing Canonical Market Validation runtime (pass-through only for that contribution)

No path to Opportunity, ranking, Timing, portfolio, or recommendation authority.
```

Repositories retrieve raw deterministic history and persist service-authorized records only. The Market Validation-owned exact-version adapters remain the sole owners of valuation-family eligibility and canonical normalization. The composition service owns only valuation-family weighting, contribution caps, final composition, and immutable composition snapshot generation. It emits one final post-cap valuation-family contribution. After activation, the existing `WeightEngine` may continue to own weighting for non-valuation inputs, but it must treat that valuation-family contribution as immutable pass-through input: it may add it to the wider Market Validation result but may not normalize, weight, scale, cap, or decompose it. The four legacy `valuation`, `comparative_valuation`, `mispricing`, and `asymmetry` weight entries must not be active or applied on that path. The runtime must not call `WeightEngine.apply()` or `WeightEngine.score()` over those family records, and it must not reconstruct a contribution from their normalized scalars. If the runtime cannot prove this exact handoff, composition remains unavailable. This contract allocates authority only; it does not authorize or specify a runtime redesign.

The runtime invokes one accepted composition version but cannot choose policies. Scheduler/automation may eventually invoke an already approved runtime but cannot activate it or select cutoffs, calibrations, caps, or canary cohorts.

## Immutable Composition Record Requirements

A future `MarketValidationCompositionSnapshot` (provisional name) must contain at least:

- canonical target entity and representation;
- composition policy ID/version and canonical fingerprint;
- runtime/configuration version;
- effective, recorded, known, and replay-cutoff times;
- exact upstream logical IDs, record IDs, semantic versions, content hashes, quality/conflict/availability states;
- exact adapter contract IDs/versions;
- exact normalization and calibration IDs/versions, corpus fingerprint, supported range, raw and normalized values;
- complete ordered direct and transitive evidence-reference sets;
- primary contribution assignments, overlap intersections, correlation groups, independence-policy references, per-input caps, group caps, pre-cap and post-cap contributions;
- the sole weighting-owner identifier, the final immutable post-cap valuation-family contribution, and proof that no legacy family weight or second scaling stage was applied;
- structured non-scalar valuation reference;
- explicit missing/unavailable reasons;
- deterministic ordering and composition hash;
- correction predecessor, reason, and authority.

The snapshot and canonical Market Validation run must be committed atomically or neither becomes authoritative. A correction produces one successor snapshot and a successor run binding. Recalibration or policy change creates a new policy/version and new composition; it never reinterprets old records.

## Exact-Version Eligibility and Strict-Known Replay

At cutoff `T`:

1. The target identity and representation must match exactly across all selected records.
2. Every upstream record and transitive dependency must satisfy its own accepted eligibility contract and have effective, recorded, and known times at or before its applicable cutoff.
3. Schema, semantic, methodology, adapter, calibration, composition, cap, and residual-policy versions must be explicitly supported; compatibility by name or shape is prohibited.
4. Corrections known after `T` cannot enter replay; the version known at `T` remains selected.
5. Unknown time, unresolved conflict, stale mandatory input, unsupported entity class/range, incomplete lineage, missing calibration, missing cap policy, or ambiguous overlap makes the affected contribution unavailable.
6. Replay reads immutable histories and reproduces input decisions, normalized values, groups, intersections, caps, contributions, missingness, and hash. It never calls live providers or current/latest projections.
7. If exact reconstruction is impossible, the complete composition is unavailable; partial scoring is prohibited unless a future ADR explicitly defines optional-family semantics and minimum coverage.

## Activation, Canary, and Rollback

### Activation gates

All are mandatory:

1. accepted composition ADR;
2. independently approved ADPR and ADR exact revisions;
3. real strict-known calibration corpora for every scalar enabled;
4. temporal leakage, monotonicity, neutral-anchor, supported-range, drift, and out-of-sample tests;
5. accepted numeric weights and input/group caps with sensitivity evidence;
6. complete evidence-lineage intersection and anti-double-counting tests;
7. deterministic permutation and correction replay tests;
8. immutable atomic persistence and repository-purity tests;
9. fail-closed unsupported-version and missingness tests;
10. WeightEngine-boundary tests proving the final post-cap family contribution is passed through exactly once, all four legacy family weights are inactive on that path, and no cap or scaling is discarded or reapplied;
11. isolated shadow execution showing no production writes or consumer changes;
12. rollback rehearsal; and
13. independent pre-activation review.

### Canary

- Canary follows successful shadow operation; it cannot precede calibration acceptance.
- Cohort, duration, policy version, success/failure thresholds, and rollback triggers are fixed before execution.
- Canary records are explicitly labeled and isolated until promotion.
- Canary compares deterministic replay, availability, coverage, drift, cap binding, evidence overlap, and legacy-run non-regression; it does not optimize weights on canary outcomes.
- Promotion requires a separate explicit governance action. Silence or elapsed time never promotes it.

### Rollback

- Disable the composition policy/entry point and restore the prior authoritative runtime configuration.
- Preserve all immutable shadow, canary, composition, and run records for audit.
- Never delete, rewrite, or relabel accepted history.
- Do not fall back to aliases, previous current calibration, partial composition, or Opportunity output.
- If no prior valid composition exists, the valuation-family composition returns unavailable.

## Comparative Analysis

| Criterion | Option 1 | Option 2 | Option 3 | Option 4 |
|---|---|---|---|---|
| Correctness | High | Medium | Low | High but unavailable |
| Constitutional compliance | High | Medium | Low | High |
| Governance compliance | High | Medium | Low | High |
| Authority clarity | High | Medium-low | Low | High |
| Replayability | High | Medium | Low-medium | Not applicable |
| Evidence integrity | High | Medium | Low | High |
| Maintainability | Medium-high | Medium-low | Low | High |
| Scalability | Medium-high | Medium | Potentially high but unsafe | Low functional value |
| Operational complexity | Medium | High | High | Low |
| Migration risk | Low-medium | High | High | None |
| Implementation effort | Medium | High | High | None |
| Reversibility | High | Medium | Low | High |
| Long-term extensibility | High through versioned policies | Medium | High but authority-unsafe | Low |

## Falsification Results

| Option | Invalidation test or counterexample | Result |
|---|---|---|
| 1 | Replay at a cutoff before recalibration must reproduce the old normalized value. | Survives through exact calibration-version selection. |
| 1 | Comparative and mispricing share market/fundamental evidence. | Survives by conservative group assignment and cap; separate contribution requires proof. |
| 1 | Asymmetry reuses fair-value delta as upside. | Primary assignment or exclusion is insufficient: composition remains unavailable unless an explicitly authorized upstream residualized Asymmetry assessment restores eligibility. |
| 1 | Calibration corpus is absent or non-monotonic out of sample. | Correctly fails closed; activation is blocked. |
| 2 | A downstream cap policy changes without an upstream raw-methodology change. | Fails separation: multiple upstream versions would be required for a downstream-only policy. |
| 2 | Two upstream services select different calibration epochs. | Fails coherent atomic replay without extra orchestration authority. |
| 3 | Learned transform reverses the accepted favorable direction in a sparse region. | Fails monotonic and explainability constraints. |
| 3 | Generic engine treats similarly named values as interchangeable. | Fails single-owner and semantic-boundary constraints. |
| 4 | A valid accepted calibration corpus and cap policy become available. | Remains safe but no longer meets the objective; reconsider Option 1. |

## Rejected Options

- **Uncalibrated fixed transform (N1):** rejected because a monotonic formula without historical calibration violates ADR 0021. Reconsider only if a successor ADR changes that binding requirement.
- **Current empirical percentile (N2 without immutable calibration):** rejected because cohort changes rewrite meaning and break replay. Reconsider only as an immutable C3 calibration variant.
- **Unconstrained model (N4):** rejected for direction reversals, opacity, leakage risk, and unsupported complexity.
- **Repository adapters (O1):** rejected because eligibility and normalization are domain decisions.
- **Generic normalization owner (O3):** rejected because it creates overlapping semantic authority.
- **Runtime correlation estimation (G3/R3):** rejected because execution-time data changes methodology and permits leakage.
- **Parallel authoritative canary (A3):** rejected because one semantic output may have only one production owner at one effective boundary.
- **Final-result-only or mutable persistence (P1/P3):** rejected because exact composition decisions and historical policy cannot be reconstructed.

## Risks

| Risk | Category | Likelihood | Impact | Mitigation | Residual uncertainty |
|---|---|---:|---:|---|---|
| Calibration corpus is sparse or biased | Evidence | High | High | Minimum coverage, temporal folds, unsupported-scope unavailable state | Real coverage unknown |
| Outcome choice turns composition into implicit ranking optimization | Semantic | Medium | High | Predeclare outcome only for calibration quality; prohibit ranking/recommendation outputs | Outcome definition needs ADR scrutiny |
| Evidence overlap is semantically real but lacks shared IDs | Integrity | Medium | High | Conservative group assignment; require complete transitive lineage | Legacy lineage quality |
| Caps conceal rather than resolve duplication | Methodology | Medium | High | Primary assignment plus exclusion precedes caps | Exact numeric caps absent |
| Recalibration creates drift | Operations | Medium | High | Immutable versions, drift gates, shadow comparison, rollback | Thresholds need evidence |
| Adapter becomes a second analytical owner | Architecture | Medium | High | Thin validation/transform contract; no raw recalculation | Review discipline |
| Partial records appear authoritative | Persistence | Low | High | Atomic snapshot/run write and fail-closed reads | Backend implementation future |
| Canary leaks into consumers | Operations | Low | High | Physical/logical isolation and explicit promotion gate | Deployment design future |
| Downstream Opportunity consumes composition prematurely | Governance | Medium | High | No adapter, API, field mapping, or authority granted | Future pressure remains |

## Open Questions

| Question | Blocking? | Owner | Required evidence or action | Status |
|---|---|---|---|---|
| Exact historical outcome definition and horizon for each scalar calibration | Blocks ADR numeric methodology/activation | Future ADR reviewers | Leakage-safe outcome-policy evidence | Open |
| Minimum corpus size and temporal-fold thresholds | Blocks activation | Calibration evidence owner | Empirical coverage and stability study | Open |
| Numeric input weights and group caps | Blocks activation | Future composition ADR | Sensitivity/correlation study E-012 | Open |
| Residual-independence statistical thresholds | Blocks separate Comparative group, not conservative grouping | Future ADR | Predeclared temporal independence study | Open |
| Canary cohort, duration, and rollback thresholds | Blocks canary | Operations plan after ADR | Shadow evidence and deployment plan | Open |

These questions do not require implementation to complete the architecture decision space. The future ADR may select conservative unavailability and one-group treatment while leaving numeric activation parameters blocked by evidence.

## Architecture Impact

A future implementation would add one Market Validation composition service, thin family adapters, immutable calibration/cap/composition policy records, and an immutable composition snapshot atomically bound to the existing run. It would not alter upstream service ownership, repositories, raw formulas, Market Validation's sole-runtime status, or downstream Opportunity boundaries.

## Evidence Impact

Future work requires new evidence rather than inferred values:

- leakage-safe historical assessment/outcome pairs;
- immutable calibration corpora and temporal validation results;
- transitive evidence-reference closure;
- cap and correlation sensitivity studies;
- shadow/canary operational evidence.

Until those records exist and pass independent review, normalized contributions and the composition remain explicitly unavailable. Synthetic tests can prove determinism and boundaries but cannot prove calibration or production fitness.

## Constitution Review

| Rule | Application | Determination |
|---|---|---|
| Evidence Authority | No scalar or cap is authorized without real calibration evidence. | Compliant |
| Deterministic Intelligence | Exact versions, canonical ordering, immutable policies, and strict-known replay are mandatory. | Compliant |
| Architectural Integrity | Upstream, composition, repository, and downstream owners remain distinct. | Compliant |
| Single Source of Truth | One Market Validation composition service owns the combined decision. | Compliant |
| Explainability | Raw values, transforms, groups, caps, intersections, and missingness are persisted. | Compliant |
| Long-Term Evolution | Append-only versions and rollback preserve history. | Compliant |
| Governance | ADPR, independent review, later ADR, and activation review remain separate gates. | Compliant |

No constitutional conflict is identified.

## Governance Review

| Authority | Determination |
|---|---|
| Development Governance and ADPR Guide | Documentation-only preparation precedes ADR and implementation. |
| ADR 0005 | Exact entity/representation identity is mandatory. |
| ADR 0009 | Service owns all decisions; repositories remain mechanical. |
| ADR 0010 | Descriptive engines gain no scoring/composition authority. |
| ADR 0016 | Market Validation remains the sole production composition runtime. |
| ADR 0020 | Exact-version strict-known eligibility and explicit unavailable behavior are preserved. |
| ADR 0021 | Monotonic calibration, correlation groups, caps, lineage, and atomic run persistence are specialized, not weakened. |
| ADR 0024 | Valuation remains structured and non-scalar. |
| ADR 0025 | Composition references assembled/native evidence lineage but never performs evidence assembly. |
| ADR 0026 | Comparative residual semantics remain upstream-owned; composition/caps remain downstream-owned. |

No Accepted ADR or ADPR requires modification by this preparation.

## Quality Assessment

| Dimension | Rating | Rationale | Blocking limitation |
|---|---|---|---|
| Problem correctness | GOOD | The unresolved composition boundary is distinguished from completed upstream work. | None |
| Scope completeness | GOOD | All 16 required axes and exclusions are covered. | None |
| Canonical consistency | GOOD | Binding ADRs are treated as constraints. | None |
| Evidence integrity | ACCEPTABLE | Canonical evidence is strong; empirical calibration/cap evidence is explicitly absent. | Blocks activation |
| Assumption discipline | GOOD | Assumptions are falsifiable with consequences. | None |
| Option completeness | GOOD | Normalization, calibration, grouping, residual, ownership, persistence, and activation options are enumerated. | None |
| Comparative fairness | GOOD | Coherent options use common criteria. | None |
| Falsifiability | GOOD | Leading and alternative options have counterexamples. | None |
| Authority clarity | GOOD | Analytical, adapter, composition, repository, runtime, and downstream boundaries are explicit. | None |
| Replay/persistence | GOOD | Complete immutable snapshot, corrections, atomicity, and no-current fallback are defined. | None |
| Operational quality | ACCEPTABLE | Canary and rollback architecture is complete; numeric thresholds await evidence. | Blocks activation |
| Testability | GOOD | Eligibility, overlap, permutation, correction, leakage, drift, atomicity, and isolation tests are derivable. | None |
| Traceability | GOOD | Issue, ADPR, future ADR, PR, and absent artifacts are explicit. | Independent review pending |

## Architecture Readiness

- Outcome: `READY`
- Rationale: authority, normalization families, calibration lifecycle, correlation, caps, residual independence, anti-double-counting, persistence, replay, runtime, activation, canary, rollback, and downstream exclusions are fully bounded.
- Missing evidence: real calibration corpora, numeric cap evidence, residual-independence thresholds, and canary thresholds.
- Unresolved conflicts: none. Missing evidence is carried as an activation blocker, not replaced by assumptions.

## ADR Readiness

- Outcome: `READY_FOR_ADR`
- Proposed ADR title: Canonical Market Validation Composition Authority.
- Proposed ADR scope: service ownership, exact-version adapters, family normalization contracts, calibration records, evidence contribution groups, caps, residual-independence rules, immutable composition snapshots, replay, fail-closed behavior, activation, canary, rollback, and downstream boundaries.
- Decisions the ADR must fix: Option 1 or another coherent architecture; record families; calibration contract and outcome-policy constraints; conservative correlation/group rules; cap-policy requirements; eligibility; correction; activation gates.
- Matters the ADR must leave open: production numeric weights/caps until evidence exists; Opportunity factors; ranking; portfolio; timing; recommendations; UI/Dashboard; scheduler; implementation technology.

## Final Recommendation

Advance Option 1 to an independently reviewed ADR: Market Validation-owned exact-version adapters and one composition service, N3/C3 calibrated scalar transforms for Comparative Valuation, Mispricing, and Asymmetry, structured non-scalar Valuation, conservative contribution grouping, both input and group caps, R2-or-R4 residual independence, P2 immutable atomic snapshots, and A2 staged activation.

The ADR may authorize this architecture but must not claim production availability. Every scalar and the combined composition remain unavailable until real calibration, numeric caps, anti-double-counting evidence, shadow validation, canary criteria, and independent activation review exist.

## Decision History

| Date | State | Change | Author or reviewer |
|---|---|---|---|
| 2026-08-02 | READY_FOR_REVIEW | Initial complete preparation for Issue #173. | Codex |
| 2026-08-03 | READY_FOR_REVIEW | Incorporated approved review corrections for zero-boundary normalization, enforceable overlapping-evidence exclusion, and the single weighting-owner boundary; preserved all existing constitutional and activation constraints. | Codex; corrections merged through PR #175 |

## Traceability

- Epic: not yet created
- Issue: [#173](https://github.com/fafa33/Project-Hunter/issues/173)
- Preparation working document: this record
- Checklist review: completed against `docs/checklists/ARCHITECTURE_DECISION_PREPARATION_CHECKLIST.md`
- ADPR: ADPR-0004
- ADR: not yet created; prohibited until independent approval
- Implementation plan: not authorized
- Initial preparation PR: [PR #174](https://github.com/fafa33/Project-Hunter/pull/174), merged as `3209f1f`
- Approved review-correction PR: [PR #175](https://github.com/fafa33/Project-Hunter/pull/175), merged as `35e68e8`
- Independent architecture review: pending
- Merge commits: `3209f1f`, `35e68e8`
- Release: not yet assigned

## Immutability and Supersession

After `APPROVED`, this record is historical evidence. Substantive corrections require a new ADPR that explicitly supersedes it. Link completion and typographical corrections must remain auditable. Until approval, independent review may require revisions on the preparation branch.
