# ADR 0027: Canonical Market Validation Composition Authority

## Status

Proposed.

Governing preparation record: [ADPR-0004 — Canonical Market Validation Composition Authority (Phase 1)](../architecture-records/ADPR-0004-canonical-market-validation-composition-authority.md), independently reviewed (`READY_FOR_ADR_WITH_MINOR_FINDINGS`) on [PR #177](https://github.com/fafa33/Project-Hunter/pull/177), drafted under [Issue #178](https://github.com/fafa33/Project-Hunter/issues/178).

This ADR is drafted for independent architecture review. It does not authorize implementation, runtime activation, or any Market Validation execution. Implementation of the composition authority is authorized only after this ADR is independently reviewed, accepted, and merged, and remains additionally gated by the activation preconditions this ADR itself fixes.

## Context

ADR 0021 assigns `CanonicalValuationService`, `CanonicalComparativeValuationService`, `CanonicalMispricingService`, and `CanonicalAsymmetryService` sole ownership of the `valuation`, `comparative_valuation`, `mispricing`, and `asymmetry` assessments. ADR 0024 removes directional scalar semantics from `valuation`. ADR 0025 gives Assembled Fundamental Evidence a distinct owner with no implicit downstream composition right. ADR 0026 defines the first Comparative Valuation methodology and explicitly defers downstream composition, caps, residual independence, and weighting to a separate accepted ADR. ADR 0020 requires every Market Validation input to satisfy an exact semantic contract under strict-known replay and prohibits similarly named substitutes. ADR 0016 makes Canonical Market Validation the sole production analytical composition runtime and prohibits a parallel or competing composition authority.

No accepted ADR currently authorizes Canonical Market Validation to accept, normalize, correlate, cap, persist, replay, activate, canary, or roll back a contribution from any of the four valuation-family services. `docs/architecture-index.md` and the four services' own governing ADRs confirm all four valuation-family inputs — the structured, zero-weight `valuation` reference and the three weighted scalar inputs `comparative_valuation`, `mispricing`, and `asymmetry` — remain explicitly unavailable for this reason, not because of an implementation defect.

Independent inspection of the current runtime confirms this gap is not merely theoretical. `src/hunter/market_validation/runner.py` already carries `EngineValidationSource` records named `valuation`, `comparative_valuation`, `mispricing`, and `asymmetry`; its `_known_invalid_deferred_alias` function currently and unconditionally rejects every one of them, which is the correct fail-closed behavior ADR 0020, ADR 0021, ADR 0024, and ADR 0026 require today. `configs/weights.yaml` already carries four active legacy weight entries for the same four names (`valuation: 0.065`, `comparative_valuation: 0.055`, `mispricing: 0.055`, `asymmetry: 0.055`), and `WeightEngine.apply()`/`WeightEngine.score()` are already invoked, inside `EvidenceBackedProjectExecutor` (`src/hunter/market_validation/runner.py`), over the full source set that includes whatever is registered under those four engine names. If a future composition authority ever inserted a genuine, non-placeholder value under one of these names without an explicit boundary decision, `WeightEngine` would silently re-weight or re-scale it — double-weighting an already-weighted-and-capped contribution for `comparative_valuation`, `mispricing`, or `asymmetry`, or erroneously weighting `valuation`'s structured reference, which this architecture requires to remain unweighted at every step. This ADR must close both hazards before any of the four fields may become eligible for the composition service to include.

ADPR-0004 evaluated four coherent architectures for this decision and recommends Option 1: a service-owned composition authority with Market Validation-owned exact-version adapters, three historically calibrated monotonic scalar transforms (`comparative_valuation`, `mispricing`, `asymmetry`), a non-weighted structured `valuation` reference, evidence-lineage contribution groups, conservative combined caps, and fail-closed eligibility. Independent review confirmed ADPR-0004's technical claims against the actual codebase and found no decision-blocking defect. This ADR adopts that recommendation as binding architecture.

No real, leakage-safe calibration corpus, numeric cap-sensitivity study, or canary evidence currently exists for any of the three scalar families. This ADR authorizes the architecture, not production availability: every normalized contribution, and the composition as a whole, remains explicitly unavailable until the activation gates this ADR fixes are independently satisfied.

## Decision

Hunter adopts ADPR-0004's Option 1: a service-owned Canonical Market Validation composition authority. `CanonicalMarketValidationCompositionService` becomes the sole owner of correlation-group assignment, per-input and group contribution caps, residual-independence evaluation, the valuation-family weighting decision, and the immutable `MarketValidationCompositionSnapshot`. Market Validation-owned exact-version adapters, one per family, become the sole owners of eligibility verification for all four families (`valuation`, `comparative_valuation`, `mispricing`, `asymmetry`); the `comparative_valuation`, `mispricing`, and `asymmetry` adapters additionally own canonical normalization and normalized-scalar production, while the `valuation` adapter owns only structured-reference preservation and an explicit availability state, never a scalar. The four upstream valuation-family services keep their existing, unchanged raw-assessment authority under ADR 0021.

`comparative_valuation`, `mispricing`, and `asymmetry` each require a predeclared, versioned, monotonic (N3) calibration before contributing a normalized scalar; `valuation` contributes a structured, non-scalar, zero-weight reference only, per ADR 0024. `comparative_valuation` and `mispricing` share the `valuation-mispricing` correlation group by conservative default; `asymmetry` occupies its own group only when its complete evidence-reference set is disjoint from the other three, and becomes entirely ineligible for composition — never partially capped — when it is not. The existing `WeightEngine` must not re-weight, re-scale, or re-derive the composition service's already-capped valuation-family contribution, and the four legacy per-family weight entries in `configs/weights.yaml` must not remain active on this path; unproven compliance with this boundary makes the composition unavailable.

Composition is strict-known replayable, immutably persisted and atomically bound to its Market Validation run, append-only correctable, and fails closed to an explicit unavailable/`INSUFFICIENT_EVIDENCE` state whenever any required record, policy, or proof is absent. No implementation, calibration value, cap value, or runtime activation is authorized by this ADR itself; production activation requires the separate gates fixed under "Activation, Canary, and Rollback," below. This decision grants no authority to Opportunity Intelligence, ranking, timing, portfolio, recommendation, or committee decisions.

The remaining sections of this ADR state this decision's exact, binding detail; they do not introduce additional decisions beyond what is summarized here.

## Purpose

This ADR defines the sole binding authority, ownership boundaries, normalization contracts, correlation and cap policy, immutable persistence and replay contract, correction lineage, fail-closed and missingness behavior, and activation/canary/rollback gates for Canonical Market Validation's composition of the four canonical valuation-family assessments: the structured, non-scalar `valuation` reference and the weighted scalar assessments `comparative_valuation`, `mispricing`, and `asymmetry`. The composition service combines only the latter three into one immutable, weighted valuation-family contribution; `valuation`'s structured reference is preserved in the same immutable snapshot without ever being weighted or scored.

Its purpose is composition authority only. It does not define or calculate Opportunity, ranking, timing, portfolio, recommendation, or committee decisions, and it does not itself activate any runtime behavior.

## Scope

This decision governs:

- Canonical Market Validation composition ownership for the four valuation-family assessments: the structured `valuation` reference and the three weighted scalar assessments `comparative_valuation`, `mispricing`, and `asymmetry`;
- exact-version input-adapter ownership and eligibility for each family, and canonical normalization for the three weighted scalar families (`comparative_valuation`, `mispricing`, `asymmetry`);
- the structured, non-scalar composition treatment for `valuation` and the family normalization contract for `comparative_valuation`, `mispricing`, and `asymmetry`, including Asymmetry's zero-boundary transform;
- correlation-group assignment, evidence-lineage disjointness, anti-double-counting, and per-input and combined-group contribution caps;
- residual-independence policy and the narrow path by which a residualized upstream assessment may become separately eligible;
- weighting ownership and the exact, provable handoff boundary with the existing `WeightEngine`;
- the immutable `MarketValidationCompositionSnapshot` record family, provenance, correction, and supersession;
- exact-version eligibility and strict-known replay for the composed contribution;
- explicit unavailable states, fail-closed behavior, and `INSUFFICIENT_EVIDENCE` interaction;
- activation, shadow, canary, rollback, migration, and preflight gates; and
- downstream authority boundaries.

This decision is additive. It does not amend or supersede ADRs 0002, 0004, 0005, 0009, 0010, 0016, or 0020–0026. It does not authorize implementation, runtime activation, migrations, scheduler changes, UI, Dashboard, reports, APIs, Opportunity Intelligence, ranking, portfolio logic, timing logic, recommendations, or committee decisions.

## Authority and Ownership

Three distinct authorities exist on the composition path, and none may perform another's decision:

1. **The four canonical valuation-family services** (`CanonicalValuationService`, `CanonicalComparativeValuationService`, `CanonicalMispricingService`, `CanonicalAsymmetryService`) remain, unchanged, the sole owners of their respective raw assessments under ADR 0021, ADR 0024, and ADR 0026. This ADR grants them no new authority and removes none of their existing authority.
2. **Market Validation-owned exact-version adapters**, one per family, are the sole owners of eligibility for their family: verifying that a specific upstream assessment record is an exact-version-compatible, strict-known, non-conflicted input for composition, and returning an explicit unavailable state when it is not. Beyond eligibility, the four adapters' authority is not uniform:
   - the `comparative_valuation`, `mispricing`, and `asymmetry` adapters additionally own selecting and applying the declared normalization/calibration policy for their family and producing either a normalized scalar value or an explicit unavailable state;
   - the `valuation` adapter owns only preserving the structured, non-scalar reference (identity, `p10`/`p50`/`p90`, confidence decomposition, methodology identity) and an explicit availability state; it never performs canonical normalization and never produces a scalar or weighted contribution, consistent with ADR 0024.

   Adapters never recalculate, reinterpret, or override any raw upstream value. An adapter that cannot prove exact-version eligibility must return unavailable; it may not approximate compatibility by name, shape, or partial match.
3. **`CanonicalMarketValidationCompositionService`** is the sole owner of:
   - evidence-lineage intersection and correlation-group assignment;
   - residual-independence policy evaluation;
   - per-input and combined-group contribution caps;
   - the sole valuation-family weighting decision (see "Weighting Ownership and the WeightEngine Boundary" below);
   - production of the immutable `MarketValidationCompositionSnapshot`; and
   - authorizing append-only corrections to that snapshot.

No other component may write a canonical valuation-family composition conclusion for any of these four families, or a competing weighting decision for `comparative_valuation`, `mispricing`, or `asymmetry` — including the decision, which belongs exclusively to `CanonicalMarketValidationCompositionService`, that `valuation` itself is never weighted. Repositories that persist adapter or composition records remain mechanical persistence adapters under ADR 0009: they perform no eligibility, normalization, correlation, cap, weighting, or correction decision.

Canonical Market Validation (`EvidenceBackedProjectExecutor` and the Market Validation runtime, under ADR 0007 and ADR 0016) remains the sole production analytical composition runtime and the sole consumer authorized to accept the finished valuation-family contribution into its wider result. This ADR grants Canonical Market Validation no new scoring, ranking, timing, portfolio, or recommendation authority.

## Weighting Ownership and the WeightEngine Boundary

`CanonicalMarketValidationCompositionService` is the sole weighting-owner for the three weighted scalar valuation-family inputs (`comparative_valuation`, `mispricing`, `asymmetry`). It alone applies per-input caps, group caps, and the residual-independence policy, and it alone produces one final, immutable, post-cap valuation-family contribution. `valuation` is never weighted and is not one of these three inputs; the composition service's only responsibility toward it is including its structured reference, and the explicit availability state the `valuation` adapter produced, in the same immutable snapshot.

The existing `WeightEngine` (`src/hunter/weights/engine.py`) retains its existing authority to weight every other Market Validation input and to combine that final valuation-family contribution into the wider `hunter_score`/`final_score` result as one already-weighted, already-capped, immutable pass-through input. Specifically, and without exception:

1. `WeightEngine.apply()` and `WeightEngine.score()` must not be called over an `EngineValidationSource` record for `valuation`, `comparative_valuation`, `mispricing`, or `asymmetry` once this ADR's composition path is active for a given run. The final valuation-family contribution enters `WeightEngine`'s combination step, if at all, only as a single pre-weighted, pre-capped numeric value that `WeightEngine` adds without normalizing, weighting, scaling, capping, or decomposing.
2. The four legacy weight entries currently present in `configs/weights.yaml` (`valuation`, `comparative_valuation`, `mispricing`, `asymmetry`) must not be active or applied on the composition path. A future implementation must either remove these four entries or gate them so they cannot double-apply against a value `CanonicalMarketValidationCompositionService` has already weighted and capped. This ADR does not select which mechanism; it forbids both entries and the composition service's own cap from applying to the same value simultaneously.
3. If a future implementation cannot prove this exact handoff — that the four legacy weight entries are inactive on this path, that `WeightEngine.apply()`/`WeightEngine.score()` are not invoked over any of the four family records, and that no contribution is reconstructed from the normalized scalars of `comparative_valuation`, `mispricing`, or `asymmetry` after `CanonicalMarketValidationCompositionService` has already produced its post-cap value — the valuation-family composition remains unavailable. Ambiguity about this boundary is never resolved in `WeightEngine`'s favor by default; the composition fails closed.
4. `WeightEngine`'s existing authority over `risk`, `developer`, `protocol`, `future_demand`, `probability`, `pattern_matching`, `technology_necessity`, `capital_rotation`, `necessity_gap`, `validation_health`, `committee`, `whale_intelligence`, `macro_intelligence`, `opportunity_timing`, `narrative`, `news`, and `social` is unchanged by this ADR.

This boundary allocates authority only; it does not itself specify or authorize a `WeightEngine` or `EvidenceBackedProjectExecutor` code change. A future implementation Issue must define the exact mechanism (e.g., a distinct pass-through field, a runtime-conditional weight override, or removal of the four legacy entries) and prove it under the activation gates below.

## Family Normalization Contracts

Every scalar normalization is versioned, monotonic, and requires a real, historically calibrated, leakage-tested transform accepted through independent review before it may be used. No normalization in this ADR is self-authorizing; each requires the calibration record and evidence described under "Activation, Canary, and Rollback."

| Family | Composition treatment | Neutral/reference rule | Unsupported behavior |
|---|---|---|---|
| `valuation` | Structured, confidence-bearing reference only. It contributes zero weight to any weighted composition and is never converted into a scalar by an adapter, the composition service, or `WeightEngine`, consistent with ADR 0024. | Not applicable. | Missing or incompatible `valuation` is recorded explicitly; it is never a zero or neutral substitute, and it never blocks composition of the other three families by itself. |
| `comparative_valuation` | A predeclared, versioned, monotonic (N3) calibration of ADR 0026's raw log residual (`ln(peer_median_multiple / target_multiple)`). The positive-cheaper direction is preserved exactly: a larger raw residual must map to a larger or equal normalized value. | A raw residual of exactly `0` (peer-median equality) maps to the calibration policy's declared neutral anchor. | Outside the calibration's declared supported entity class, corpus range, or accounting horizon, or when no accepted calibration exists for the applicable policy version: unavailable. `LIMITED_PEER_SET`, `UNAVAILABLE_INSUFFICIENT_ELIGIBLE_PEERS`, or any other ADR 0026 unavailable state on the raw assessment makes the normalized input unavailable; the adapter never repairs or substitutes for an unavailable raw assessment. |
| `mispricing` | A predeclared, versioned, monotonic (N3) calibration of the raw signed ratio `(fair_value_p50 - observed_market_price) / observed_market_price`. Positive-undervaluation direction is preserved exactly. | A raw ratio of exactly `0` maps to the calibration policy's declared neutral anchor. | Outside the calibration's declared supported range, when either prerequisite (`valuation`, observed market value) is unavailable, incompatible, stale, or conflicted, or when no accepted calibration exists: unavailable. |
| `asymmetry` | A predeclared, versioned, monotonic (N3) calibration of `log1p(raw_asymmetry_ratio)` over the raw ratio's full mathematical domain `[0, +∞)`. Accepted raw values must be finite; `NaN` and positive or negative infinity are invalid and make the input unavailable. Because `log1p` is strictly increasing and finite everywhere on `[0, +∞)`, the transform preserves the raw ratio's ordering exactly and introduces no additional domain restriction beyond what ADR 0021 already requires of the raw ratio itself. | A raw ratio of exactly `1` (equal expected positive and negative payoff) maps to `log1p(1) = ln(2)`, the calibration policy's declared neutral anchor. A raw ratio of exactly `0` (zero expected positive payoff, i.e. the worst-case outcome under the declared scenario set) is mathematically well-defined under `log1p` (`log1p(0) = 0`) and is **valid worst-case evidence, never missingness**: it must be represented exactly as transformed value `0` and must never be reclassified as unavailable solely because it sits at the domain boundary. | Unavailable only for: an undefined denominator (including zero downside payoff, which ADR 0021 already treats as requiring a declared finite cap or an insufficient-result state before this transform ever applies), a non-finite or otherwise unsupported raw ratio, incomplete scenario coverage, or absence of an accepted calibration for the applicable policy version. A valid, finite raw ratio of `0` is never one of these unavailable conditions. |

Every calibration policy must declare, at minimum: supported entity class; the exact upstream methodology/service version it calibrates against; the raw domain and any winsorization prohibition; the neutral anchor; the outcome definition and horizon used for calibration, selected under strict-known cutoffs so no calibration record can be trained on information not yet knowable at its own effective time; temporal folds; minimum sample size and coverage; the monotonic direction; the fit algorithm and its version; validation metrics; a drift threshold; effective, recorded, and known times; and the exact corpus record IDs and versions used to fit and validate it. A calibration policy may never extrapolate silently: a raw value outside the declared supported domain is unavailable, never clamped, rounded, or forced into range.

No normalization in this table is authorized for production use by this ADR alone. Each of `comparative_valuation`, `mispricing`, and `asymmetry` requires its own accepted calibration record, produced and independently reviewed under the activation gates below, before its adapter may emit anything other than an unavailable state. `valuation` has no calibration record and none is required: its adapter always emits either the structured reference and an explicit availability state, or an explicit unavailable state, regardless of calibration activation.

## Correlation Groups, Contribution Caps, and Anti-Double-Counting

1. `valuation` and `mispricing` remain one correlation group, `valuation-mispricing`, per ADR 0021 and ADR 0024. Only `mispricing` contributes a weighted scalar in this phase; `valuation` contributes zero weight per its own normalization contract above.
2. `comparative_valuation` joins `valuation-mispricing` whenever its complete direct and transitive evidence-reference set intersects the evidence-reference set of `valuation` or `mispricing`'s underlying market or fundamental lineage, **or whenever distinctness cannot be proven from persisted, exact-version evidence references**. Absence of proof is resolved conservatively, toward the shared group, never toward an assumed-independent contribution.
3. `comparative_valuation` may receive a separate, independently weighted contribution group only when a versioned residual-independence policy exists that: identifies the exact shared explanatory inputs with `valuation`/`mispricing`; performs predeclared, strict-known residualization against those inputs; and passes temporal, out-of-sample independence and stability thresholds accepted through independent review. Absent such a policy, `comparative_valuation` remains in the shared group under rule 2.
4. `asymmetry` remains in its own correlation group, `asymmetry-scenario`, **only when** its complete direct and transitive evidence-reference set is disjoint from the complete evidence-reference sets of `valuation`, `comparative_valuation`, and `mispricing`. If any exact evidence record is shared, the entire `asymmetry` scalar becomes ineligible for this composition run; the composition service may not decompose, partially credit, or approximately cap the overlapping portion, because the upstream payoff ratio is a single aggregate value and no downstream adapter or the composition service itself may recalculate or partially reconstruct it (ADR 0009, ADR 0021). An ineligible `asymmetry` scalar under this rule makes the entire composition for that run explicitly unavailable; it is never silently dropped, zeroed, or replaced with a neutral value. Eligibility is restored only when a separately accepted ADR authorizes an upstream `CanonicalAsymmetryService` assessment that is itself already residualized, under an accepted, exact-version residual-independence policy, against the shared inputs — the residualization happens upstream, inside the owning service, never inside a Market Validation adapter or the composition service.
5. Every exact evidence record has exactly one primary contribution assignment under the composition policy in force. A record may be referenced by another family for confidence, provenance, or explainability without ever being counted a second time toward weight.
6. Each of `comparative_valuation`, `mispricing`, and `asymmetry` (when eligible) has a predeclared per-input cap, and each correlation group has a predeclared combined cap. The sum of a group's member contributions must never exceed that group's combined cap. Numeric cap and weight values are not fixed by this ADR; they must be recorded as versioned configuration inputs, accepted through the activation gates below, before any weighted composition may run. Absence of an accepted, exact-version cap policy makes the affected group's composition unavailable.
7. Runtime-estimated correlations, implicit residual-independence claims, and configuration-only independence declarations (i.e., a policy that merely asserts independence without a predeclared, strict-known, temporally out-of-sample-tested residualization) are prohibited. Any such attempt makes the affected contribution unavailable, not independently weighted.

## Residual Independence and Eligibility of Upstream Residualized Assessments

A family or record may claim a separate, independently weighted contribution group, rather than joining a shared correlation group by default, only through one of:

- **R2 — predeclared residualization:** a versioned policy that identifies the exact strict-known shared inputs, performs deterministic residualization against them, and passes temporal, out-of-sample independence and stability thresholds accepted through independent review; or
- **R4 — no separate contribution:** when independence cannot be proven under R2, the record does not receive a separate group; it either joins the conservative shared group (for `comparative_valuation`, per rule 2 above) or becomes entirely ineligible for this composition (for `asymmetry`, per rule 4 above, because its aggregate payoff ratio cannot be partially residualized downstream).

No other independence-proof mechanism (runtime regression against current data, a declared-only independence claim, or a partial/approximate residualization performed by an adapter or the composition service) is authorized. Any residual-independence policy is itself a versioned, immutable configuration record, strict-known at the composition cutoff exactly like every other input to this authority.

## Persistence Model

Composition uses one new immutable logical record family, persisted through the existing canonical persistence boundary. This ADR selects no database product, table, schema, file path, migration mechanism, or service deployment.

### `MarketValidationCompositionSnapshot`

This record is the only canonical output of `CanonicalMarketValidationCompositionService` and, together with the Market Validation run it is atomically bound to, the only authoritative record of a valuation-family contribution. It must contain, at minimum:

- canonical target entity and representation identity;
- composition policy ID/version and a deterministic canonical fingerprint;
- runtime/configuration version;
- effective, recorded, known, and replay-cutoff times;
- for every consumed upstream assessment: its exact logical ID, record ID, semantic version, content hash, and quality/conflict/availability state;
- exact adapter contract IDs and versions used for each family;
- for each of the three weighted scalar families (`comparative_valuation`, `mispricing`, `asymmetry`): exact normalization and calibration policy IDs/versions, corpus fingerprint, supported range, and both the raw and normalized value;
- the complete ordered set of direct and transitive evidence references considered for intersection/disjointness analysis;
- for each of the three weighted scalar families: primary contribution assignment, evidence-overlap intersections, correlation-group assignment, residual-independence policy reference (if any), per-input cap, group cap, and both the pre-cap and post-cap contribution;
- the sole weighting-owner identifier (`CanonicalMarketValidationCompositionService`), the final immutable post-cap valuation-family contribution, and explicit proof references (e.g., the specific test or preflight check IDs) that no legacy `WeightEngine` weight or second scaling stage was applied to that contribution;
- for `valuation`: its exact record and version identity, the structured, non-scalar reference (`p10`/`p50`/`p90`, confidence decomposition, methodology identity), and an explicit availability/missingness state — never a normalization or calibration policy reference, never a cap, and never a contribution field of any kind, because `valuation` is never scored;
- explicit scalar non-contribution reasons for each of the three weighted scalar families that did not contribute, including which specific rule above (coverage gate, calibration absence, correlation-group ineligibility, cap-policy absence, WeightEngine-boundary proof failure, etc.) produced that state — distinct from `valuation`'s own availability state, which is never a non-contribution reason because `valuation` never contributes a scalar to begin with;
- deterministic ordering of every list-valued field and a composition content hash; and
- correction predecessor ID, correction reason, and authorizing-service identity where applicable.

The `MarketValidationCompositionSnapshot` and the canonical Market Validation run it supports must be committed atomically: either both become authoritative, or neither does. A partially written snapshot or run is never authoritative and is never read back as available.

## Provenance

Every composition snapshot must preserve: canonical target entity/representation identity; exact adapter identities and versions for all four families, and exact calibration, correlation-group, cap, and residual-independence-policy identities and versions for `comparative_valuation`, `mispricing`, and `asymmetry`; exact source/evidence record IDs and versions for every consumed upstream assessment, including `valuation`'s, and every transitive dependency of those assessments; effective, recorded, and known times for the snapshot and every transitive input; complete ordered evidence-reference sets and the intersection/disjointness determination actually reached; missingness, conflict, confidence propagation, and exclusions; and a deterministic canonical hash.

Provider payloads, upstream services' own internal working state, or any value not already an exact-version persisted record are never sufficient provenance by themselves.

## Correction Lineage

All `MarketValidationCompositionSnapshot` records are append-only.

A correction:

- creates one immutable successor;
- references exactly one predecessor;
- carries a mandatory non-blank reason and the authorizing service identity;
- has strictly later `recorded_at` and `known_at` than its predecessor; and
- never changes what was knowable at an earlier cutoff.

Branching successors are prohibited: at most one direct successor may exist for a given predecessor snapshot. A correction to any upstream assessment, calibration policy, cap policy, correlation-group assignment, or residual-independence policy may support a new successor composition snapshot; it never rewrites or silently rebinds an existing snapshot or the Market Validation run it was atomically bound to. A calibration recalibration or cap-policy change creates a new version and a new composition, never a correction that reinterprets prior history.

## Exact-Version Eligibility and Strict-Known Replay

At composition cutoff `T`:

1. The service declares a timezone-aware cutoff and the exact target identity/representation.
2. Every upstream assessment record, every adapter contract, the calibration policy, the correlation-group policy, the cap policy, and the residual-independence policy (where applicable) must each independently satisfy their own accepted eligibility contract and have effective, recorded, and known times at or before `T`.
3. Schema, semantic, methodology, adapter, calibration, composition, cap, and residual-policy versions must be explicitly supported by declared version compatibility; compatibility by name, shape, or field similarity is prohibited.
4. A correction known only after `T` cannot enter replay; the version known at `T` remains selected, exactly as ADR 0020 and ADR 0025 already require of every other input.
5. Unknown known-time, an unresolved conflict, a stale mandatory input, an unsupported entity class or calibration range, incomplete evidence-reference lineage, a missing calibration or cap policy, or an unresolved evidence-overlap determination makes the affected family's contribution unavailable for `comparative_valuation`, `mispricing`, or `asymmetry`, or makes `valuation`'s own reference explicitly unavailable (`valuation` has no contribution to make unavailable).
6. Replay reads only immutable persisted histories and reproduces every input decision, missingness/availability state, and canonical hash; for `comparative_valuation`, `mispricing`, and `asymmetry` this additionally includes the normalized value, correlation-group assignment, evidence intersection, cap application, and contribution. It never calls a live provider and never substitutes a current/latest projection for a strict-known record.
7. If exact reconstruction is impossible for any of the three weighted scalar families (`comparative_valuation`, `mispricing`, `asymmetry`), the entire weighted valuation-family contribution is unavailable for that run. Partial scoring across only some of those three families is prohibited unless a future accepted ADR explicitly defines optional-family semantics and a minimum-coverage rule; this ADR authorizes no such partial-coverage behavior. `valuation` is not one of these three scored families — it contributes zero weight under its own normalization contract (see "Family Normalization Contracts" and "Correlation Groups, Contribution Caps, and Anti-Double-Counting" rule 1) — so its own reconstruction failure is recorded explicitly in the snapshot and never blocks the weighted contribution of the other three, consistent with ADR 0024.

## Missingness and Fail-Closed Behavior

The service must preserve explicit unavailable reasons covering at least:

- an upstream family assessment is itself unavailable or below its own methodology's coverage/eligibility gate;
- no accepted calibration policy exists, or the raw value falls outside its declared supported domain;
- no accepted cap or correlation-group policy exists;
- evidence-overlap intersection cannot be resolved to a definite correlation-group assignment;
- the residual-independence policy required for a separate contribution group does not exist or fails its own thresholds;
- the `WeightEngine`-boundary proof required by this ADR cannot be established for the current implementation; and
- exact-version or strict-known replay eligibility fails for any required input.

Missing, incompatible, uncalibrated, conflicted, or otherwise ineligible data never becomes zero, neutral, average, prior-filled, or a lower-confidence-but-present value. `Asymmetry`'s valid raw ratio of `0` is the sole explicit exception this ADR defines to a superficial reading of "boundary equals unavailable": it is genuine worst-case evidence and must be represented as such, never converted to unavailable and never confused with an actual missing-evidence state.

Where the applicable Market Validation evidence gate requires a valuation-family contribution and that contribution is unavailable under any rule in this ADR, Canonical Market Validation must report `INSUFFICIENT_EVIDENCE` for the affected run exactly as it already does for any other required-but-unavailable input under ADR 0020. No Dashboard, scheduler, automation, repository, or record existence may promote an unavailable valuation-family contribution to available.

## Conflict Handling

An unresolved conflict in any mandatory upstream assessment, evidence reference, calibration policy, correlation-group policy, cap policy, or residual-independence policy blocks the affected family from contributing (for `comparative_valuation`, `mispricing`, or `asymmetry`) or makes `valuation`'s own reference explicitly unavailable (`valuation` has no contribution to block). The composition service:

- persists the conflict and the affected unavailable state;
- never averages, majority-votes, silently excludes, or selects the most-recently-recorded conflicting record;
- recalculates group-cap applicability after any of the three weighted scalar families becomes unavailable due to conflict; and
- preserves the original conflicted record and any later resolution through append-only lineage, exactly as the four upstream services already do for their own conflicts.

## Downstream Boundaries

`MarketValidationCompositionSnapshot` and the resulting valuation-family contribution are consumed exclusively by:

- the existing Canonical Market Validation runtime, as one already-weighted, already-capped pass-through input into its wider result (see "Weighting Ownership and the WeightEngine Boundary" above); and
- read-only audit, replay, and explainability.

This ADR grants no authority to Opportunity Intelligence, any ranking or general-ranking service, the Timing Engine, portfolio logic, recommendation logic, the standalone Committee engine, Dashboard calculation, scheduler decision-making, or any other downstream consumer. No downstream contribution cap, downstream weighting, or further composition of the valuation-family contribution is authorized beyond what this ADR itself fixes. A future accepted ADR is required before any such downstream use may be added.

## Activation, Canary, and Rollback

### Activation gates

All of the following are mandatory before any weighted, available valuation-family contribution may be produced in production:

1. this ADR is independently reviewed and accepted;
2. a real, strict-known, leakage-safe historical calibration corpus exists for every scalar family to be enabled, with temporal folds, monotonicity, neutral-anchor, and supported-range validation, all independently reviewed;
3. accepted numeric per-input and group cap values exist, each accompanied by cap-sensitivity evidence, and are recorded as versioned configuration, never as code defaults;
4. complete evidence-lineage intersection and anti-double-counting tests pass, including the Asymmetry evidence-disjointness rule and the Comparative-Valuation shared-group default;
5. deterministic permutation-replay and correction-replay tests pass;
6. immutable atomic persistence and repository-purity tests pass, proving the composition snapshot and Market Validation run commit atomically or not at all;
7. fail-closed tests pass for every unsupported version, missing policy, and conflict scenario enumerated above;
8. `WeightEngine`-boundary tests pass, proving the final post-cap valuation-family contribution is passed through exactly once, that the four legacy `configs/weights.yaml` entries are inactive on this path, and that no cap or scaling is discarded or reapplied;
9. isolated shadow execution runs and proves no production write and no consumer-visible change results from shadow-only execution;
10. a rollback rehearsal succeeds; and
11. independent pre-activation review of the complete implementation, distinct from this ADR's own independent review, approves activation.

### Canary

Canary execution may follow only after every activation gate above has independently passed. Canary cohort, duration, success/failure thresholds, and rollback triggers must be fixed, as versioned configuration, before canary execution begins — never adjusted during or after observing canary results. Canary records are explicitly labeled non-authoritative and isolated from production consumers until an explicit promotion action. Canary evaluation compares deterministic replay reproducibility and availability/coverage rates for all four families, drift against the calibration's declared thresholds and cap-binding frequency for `comparative_valuation`, `mispricing`, and `asymmetry`, evidence-overlap frequency, and non-regression of every existing (non-valuation-family) Market Validation output; it never optimizes weights or calibration parameters against canary outcomes. Promotion out of canary requires a separate, explicit governance action; silence or elapsed time never promotes a canary result to production.

### Rollback

Rollback disables the composition entry point and restores the prior authoritative runtime configuration (i.e., the current, already-accepted behavior in which all four fields are unconditionally unavailable). Rollback preserves every immutable shadow, canary, composition-snapshot, and run record for audit; it never deletes, rewrites, or relabels accepted history. Rollback never falls back to a legacy alias, a previous calibration treated as still current, a partial composition across fewer than all three required weighted scalar families (`comparative_valuation`, `mispricing`, `asymmetry`), or an Opportunity-layer substitute. `valuation` remains a structured, zero-weight reference and is never one of these three required families; its presence or absence never affects, and is never part of, partial-scoring completeness. If no prior valid composition snapshot exists for a target, the valuation-family contribution simply returns to its current unavailable state.

### Migration and preflight

Any implementation of this ADR must be additive and must fail closed by default: the new composition entry point must ship disabled, with the current unconditional-unavailable behavior as its default, until every activation gate above is independently satisfied for the specific deployment. Preflight checks must verify, before any run is permitted to attempt a weighted composition, that: the required calibration, cap, correlation-group, and residual-independence policies exist and are strict-known at the run's cutoff; the `WeightEngine` boundary proof holds for the running code version; and no partial or degraded composition mode is silently substituted when a required policy or record is absent.

## Implementation Prerequisites

Implementation is authorized after this ADR is independently reviewed and accepted. Implementation still requires, in addition to the activation gates above:

1. a separately governed implementation Issue and reviewable plan, distinct from the Issue that authorized drafting this ADR;
2. exact-version Market Validation input adapters for `valuation`, `comparative_valuation`, `mispricing`, and `asymmetry`, implementing eligibility for all four families and canonical normalization for only `comparative_valuation`, `mispricing`, and `asymmetry`, exactly as this ADR fixes, and nothing beyond it;
3. `CanonicalMarketValidationCompositionService`, implementing evidence intersection, correlation-group assignment, per-input and group caps, residual-independence evaluation, and `MarketValidationCompositionSnapshot` production;
4. the exact, provable `WeightEngine` boundary change (or configuration change) required by "Weighting Ownership and the WeightEngine Boundary" above, and the tests proving it holds;
5. append-only correction, branching-rejection, conflict, missingness, permutation-replay, evidence-overlap, and byte-identical-replay tests for the new record family;
6. additive migration, transactional write, compatibility, preflight, observability, and disabled-by-default entry-point plans; and
7. independent implementation review and a post-merge audit before any production activation, consistent with `docs/AI_REVIEW_PROTOCOL.md`.

Partial implementation or migration must fail closed. Existing Canonical Valuation, Comparative Valuation, Mispricing, Asymmetry, Market Validation, persistence, or runtime contracts must not change implicitly as a side effect of this implementation.

## Explicit Non-Goals

This ADR does not:

- implement code, modify runtime behavior, or activate `comparative_valuation`, `mispricing`, `asymmetry`, or any weighted valuation-family contribution;
- create a service, package, repository, database, table, schema, migration, CLI, scheduler, automation job, Dashboard/API field, or production entry point;
- select or fix any numeric weight, cap, or calibration parameter value;
- change any accepted ADR or ADPR, or change the upstream valuation-family formulas, evidence authority, or record families ADR 0021/0022/0024/0025/0026 already define;
- define or authorize Opportunity Intelligence, ranking, timing, portfolio logic, recommendations, or committee decisions;
- authorize soft similarity tiers, unconstrained learned models, current/latest-record fallback, or any other mechanism ADR 0020, ADR 0021, ADR 0025, or ADR 0026 already prohibits;
- claim that any real historical calibration corpus, cap-sensitivity study, or canary evidence currently exists; or
- represent the composition authority as production-ready.

## Compatibility

- ADR 0002 remains binding: provenance, conflict visibility, explicit missingness, and cutoff-safe replay are mandatory for every composition record.
- ADR 0004 remains binding: trust, source reliability, freshness, and unresolved conflicts precede composition exactly as they precede every other analytical conclusion.
- ADR 0005 remains binding: economic entity, claim, representation, and listing identities are preserved through every adapter and the composition snapshot.
- ADR 0007 remains binding and is reaffirmed: Canonical Market Validation remains the sole production analytical runtime.
- ADR 0009 remains binding: Provider → Service → Repository → Persistence; repositories that persist composition records remain mechanical.
- ADR 0010 remains binding: this ADR grants intelligence engines no scoring or composition authority.
- ADR 0016 is reaffirmed, not superseded: Canonical Market Validation is the sole production composition runtime; this ADR authorizes no parallel runtime and grants Opportunity, ranking, timing, portfolio, and recommendation surfaces no new authority.
- ADR 0020 is reaffirmed and specialized: strict-known replay, anti-aliasing, immutable provenance, and explicit unavailable-state behavior are unchanged and are extended to the composition record family this ADR defines.
- ADR 0021 is reaffirmed and specialized: its four-service authority matrix, anti-double-counting policy, and correlation-group definitions are the direct basis for this ADR's correlation, cap, and residual-independence rules; none of ADR 0021's own text is amended by this ADR.
- ADR 0024 remains binding: `valuation` retains its structured, non-scalar contract; this ADR assigns it zero weight in composition, consistent with, not in tension with, ADR 0024.
- ADR 0025 remains binding: Assembled Fundamental Evidence remains a distinct upstream authority; this ADR grants the composition service no evidence-assembly right and no right to consume Assembled Fundamental Evidence directly.
- ADR 0026 is reaffirmed and specialized: its raw-residual semantics, prohibited-comparisons list, and deferral of downstream composition to a separate ADR are exactly what this ADR now resolves; ADR 0026's own text is not amended.

No accepted ADR is superseded, weakened, or contradicted.

## Consequences

Positive:

- Canonical Market Validation gains one explicit, auditable composition authority for the valuation family, closing a gap ADR 0021 and ADR 0026 both explicitly deferred.
- The concrete double-weighting risk already present in `configs/weights.yaml` and `market_validation/runner.py` is named and closed by an explicit, provable `WeightEngine` boundary requirement before any composition may activate.
- Evidence-overlap, correlation-group, and cap rules prevent one economic signal (especially a shared fair-value or market-value input) from being counted more than once.
- The Asymmetry zero-boundary rule prevents a mathematically invalid or overly conservative treatment of legitimate worst-case evidence.
- Immutable composition snapshots preserve exact replayability and auditable correction lineage without duplicating upstream services' own record authority.
- Downstream Opportunity, ranking, timing, portfolio, and recommendation boundaries remain explicitly closed.

Costs and risks:

- Every scalar family, and the composition as a whole, remains unavailable until real calibration, cap-sensitivity, and canary evidence exist and pass independent review; this ADR resolves architecture, not availability.
- The Asymmetry evidence-disjointness rule may leave `asymmetry` ineligible for composition in most realistic cases where scenario evidence shares any exact record with fair-value or market evidence, until a future ADR authorizes an upstream residualized Asymmetry assessment.
- The `WeightEngine` boundary requirement adds implementation and testing overhead beyond a naive field-insertion approach.
- Calibration, cap, and residual-independence policies add governance and operational versioning overhead.

These costs are accepted because explicit unavailability and a provable authority boundary are preferable to a composition that could silently double-count evidence or double-weight a contribution already weighted upstream.

## Alternatives Considered

### Option 2 — Upstream-owned normalized assessments

Rejected because distributing normalization and cap authority across the `comparative_valuation`, `mispricing`, and `asymmetry` upstream services (`valuation`'s own service gains no normalization authority under any option, per ADR 0024) would couple their raw-formula ownership to downstream Market Validation composition concerns, complicate rollback, and require cross-service orchestration to keep calibration epochs and cap versions coherent within one composition — exactly the authority-diffusion ADR 0009 and ADR 0021 already reject.

### Option 3 — Generic central scoring/normalization engine

Rejected because a reusable, generic scoring engine would overlap both the four upstream services' authority and Canonical Market Validation's sole-runtime authority, risks treating similarly named values as interchangeable (the exact failure ADR 0020 already corrected once), and would require its own separate promotion ADR and evidentiary basis before it could be trusted with semantic composition.

### Option 4 — Preserve complete unavailability

Rejected as the standing default rather than the target architecture: it is safe, but it leaves Canonical Market Validation permanently unable to consume the now-stabilized valuation family, and it does not resolve the concrete double-weighting risk (for `comparative_valuation`, `mispricing`, and `asymmetry`) or erroneous-weighting risk (for `valuation`, which must never be weighted at all) already latent in the current runtime code (`configs/weights.yaml`'s four legacy entries and `WeightEngine`'s unconditional iteration over all sources) should a future, less careful change ever attempt to wire these fields without an authority boundary. This ADR closes that gap explicitly rather than leaving it as an ambient risk. Full unavailability remains the correct behavior until the activation gates above are independently satisfied; this ADR's own fail-closed defaults preserve that behavior precisely until then.

### Immediate cutover or parallel authoritative runtimes (A0/A3)

Rejected because one semantic output may have only one production owner at one effective boundary (ADR 0016); any activation must proceed through isolated shadow execution and an explicit, separately governed canary and promotion decision, never an immediate or parallel authoritative cutover.

### Uncalibrated fixed transform, unconstrained learned model, or current-percentile normalization

Rejected for the same reasons ADR 0021 and ADR 0026 already reject them for `comparative_valuation`: an uncalibrated transform is not historically validated; an unconstrained model risks direction reversal and leakage; a current-percentile mapping is not replay-stable. Only a predeclared, versioned, monotonic, historically calibrated (N3/C3) transform is authorized for any of the three weighted scalar families in this ADR; `valuation` requires no transform because it is never converted into a scalar.

### Permit partial capping of the Asymmetry overlap instead of full exclusion

Considered as a literal reading of ADR 0021's "the group policy must remove or cap the duplicated component." Rejected for Asymmetry specifically because its raw ratio is one aggregate probability-weighted value; isolating and capping only the overlapping portion would require the composition service or an adapter to recalculate or partially reconstruct the upstream scenario math, which ADR 0009 and ADR 0021 both forbid outside the owning service. "Removing" the entire contribution when its duplicated component cannot be isolated without recalculation satisfies ADR 0021's instruction for this specific aggregate-ratio shape; it is not adopted as a general reinterpretation of "remove or cap" for `comparative_valuation` or `mispricing`, which retain conventional per-input capping because their contributions are not single inseparable aggregates in the same way.
