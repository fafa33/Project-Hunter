# ADR 0022: Canonical Valuation Methodology

## Status

Proposed.

## Context

ADR 0021 established the evidence and service-authority contracts for the four valuation-family Market Validation inputs (`valuation`, `comparative_valuation`, `mispricing`, `asymmetry`) and explicitly deferred the numeric methodology itself: *"This ADR authorizes the semantic and record contracts, not a numeric production methodology... No formula is authorized for estimating fair value or selecting/adjusting comparable multiples merely to fill a field."* ADR 0021's own implementation order names this ADR by description, not by number: *"Adopt a separate methodology ADR or accepted methodology specification for the first supported entity class... Do not attempt one universal crypto formula."*

The two prerequisite foundations ADR 0021 requires before any methodology can be written are complete and independently audited APPROVED:

- Issue #88 (`hunter.market_facts`): immutable `ObservedMarketFactRecord` with strict-known replay, versioned conflict resolution, and provenance-validated raw payloads.
- Issue #95 (`hunter.value_capture`): immutable `FundamentalEvidenceRecord`, `ValueCaptureRuleSnapshot`, `SupplyBasisSnapshot`, all service-owned, strict-known-replayable, and (as of the Issue #88 hardening slice) cross-validated against real `ObservedMarketFactRecord`s rather than opaque references.

No code implementing fair-value calculation exists anywhere in the repository (grepped for `CanonicalValuationService`, `ValuationMethodologySnapshot`, `FairValueEstimateRecord`: zero matches). No open GitHub issue addresses valuation methodology; the only open issues (#1–#3) are unrelated V2 placeholders explicitly marked "do not implement." No entity is currently registered in production configuration with both a disclosed value-capture mechanism and a strict-known-eligible fundamental-evidence chain: `configs/market_fact_sources.yaml`'s one enabled identity binding is Bitcoin, which has no value-capture rule by its nature; `configs/value_capture_sources.yaml` has no identity-scoped bindings at all. This ADR must therefore define the first supported entity class by *criteria*, not by naming a specific already-registered project.

Without this ADR, any attempt to implement `CanonicalValuationService` would either (a) violate ADR 0021's explicit prohibition on inventing a formula without an accepted methodology contract, or (b) implicitly become a second, undocumented methodology decision made inside code review rather than architecture review — exactly the failure mode ADR 0020 and ADR 0021 were both written to close off.

## Decision

Hunter adopts the Canonical Valuation Methodology defined below for the first supported entity class only. This ADR authorizes the methodology's semantic contract, terminology, permitted and prohibited model families, replay/persistence/provenance/correction rules, and acceptance gate. **It does not authorize implementation, does not change any runtime behavior, and does not activate `valuation` or any other Market Validation input.** `CanonicalValuationService` remains unimplemented until Issue #107 (opened alongside this ADR) is completed and independently audited APPROVED, per this repository's established governance pattern for every prior foundation issue (#88, #95, and their respective hardening PRs).

### Scope: the first supported entity class

The first supported entity class is defined by the following criteria, all of which must hold for a candidate representation before it is eligible for canonical valuation:

1. Single-chain, non-wrapped, non-bridged native representation (excludes wrapped/bridged assets and multi-chain aggregate representations — ADR 0005's entity/representation boundaries apply without exception).
2. Exactly one explicit, officially disclosed or on-chain-observable value-capture mechanism drawn from the existing `ValueCaptureRuleType` allow-list already implemented in `hunter.value_capture.models` — specifically one of `fee_distribution`, `revenue_distribution`, `buyback_and_burn`, `staking_distribution`, `redemption_entitlement`, or `collateral_entitlement`. An entity whose only available rule is `no_direct_value_capture`, `unavailable`, or `unsupported` is out of scope for this methodology (it may still be valued under a future, separately authorized methodology for value-less-by-design assets, which this ADR does not define).
3. Circulating, total, and fully diluted supply are each independently observable and internally coherent under the existing `SupplyBasisSnapshot` contract (`circulating <= total <= fully_diluted`).
4. At least one accepted, non-conflicted `FundamentalEvidenceRecord` exists whose `attribution_rule_id` matches an accepted `ValueCaptureRuleSnapshot` for the same economic claim, with an accounting period fully contained within the valuation horizon's lookback window.

No specific project is named as satisfying these criteria today. Registering a concrete entity that satisfies all four criteria — including acquiring real evidence through `hunter.value_capture`'s existing service boundary — is implementation work, not an architectural decision, and is out of scope for this ADR.

### Terminology

- **Fair value**: a versioned point-in-time estimate distribution (`p10`/`p50`/`p90`) of the economic value attributable to one declared asset representation's value-capture entitlement, derived exclusively from evidence meeting the Valuation Inputs section below. Fair value is not a prediction of future price and is not comparable across representations without a separately authorized comparability contract.
- **Valuation methodology**: one immutable, versioned `ValuationMethodologySnapshot` naming the permitted model, horizon, currency, discount/risk assumptions, sensitivity policy, and normalization policy in force for a given estimate.
- **Fair-value estimate**: one immutable `FairValueEstimateRecord`, produced by exactly one methodology snapshot applied to exactly one immutable input snapshot.
- **Valuation assessment**: the record exposed to Market Validation input assembly (`ValuationAssessmentRecord`), which references a fair-value estimate plus its confidence decomposition and scalar-normalization status.
- **Calibration set**: an immutable, strict-known-selected historical dataset of resolved fair-value estimates and their subsequently observed market outcomes, used exclusively to fit and leakage-test the scalar normalization function — never to select or bias the fair-value model itself.

### Observed facts vs. derived evidence

This ADR reaffirms ADR 0021's five-layer boundary without modification and adds no new layer. Canonical valuation consumes layers 1–3 only (observed market facts, fundamental valuation evidence, fair-value estimates) and produces layer 3. It does not consume or produce layer 4 (comparative analysis) or layer 5 (scenario evidence) — those remain the exclusive scope of the future Comparative Valuation and Asymmetry methodology ADRs respectively.

### Valuation inputs

Exactly four record families may inform a fair-value estimate, each consumed by exact ID and version, strict-known at the declared cutoff:

1. `hunter.market_facts.ObservedMarketFactRecord` — for supply-basis cross-reference and (only where the methodology's declared currency conversion requires it) quote-currency context. Never for price-derived valuation (see Prohibited Methodologies).
2. `hunter.value_capture.FundamentalEvidenceRecord` — the sole source of attributable value-capture flow magnitude.
3. `hunter.value_capture.ValueCaptureRuleSnapshot` — the sole source of the entitlement pathway, dilution treatment, and claim seniority applied to that flow.
4. `hunter.value_capture.SupplyBasisSnapshot` — the sole source of the per-unit denominator.
5. `ValuationMethodologySnapshot` (new, authorized by this ADR, defined below) — the sole source of model family, horizon, discount/risk assumptions, and normalization policy.

No other record family, provider, repository, configuration file, Dashboard projection, report, or caller-supplied value may inform a fair-value estimate. This list is exhaustive, not illustrative.

### Permitted methodology

For the first supported entity class only, the sole permitted model family is a **discounted value-capture flow model**: the present value of the disclosed value-capture entitlement's attributable flow (per `ValueCaptureRuleSnapshot`) over a fixed 365-day horizon (per ADR 0021's default, unless a future amendment to this ADR authorizes another), discounted at a versioned, documented discount-rate policy declared inside the methodology snapshot, expressed per fully-diluted unit unless the methodology snapshot explicitly declares and justifies a different supply basis. Raw output is non-negative quote-currency per diluted unit and total diluted quote-currency value, with `p10`, `p50`, `p90` reflecting model and input dispersion — matching ADR 0021's `valuation` row exactly.

No other model family is authorized under this ADR. A future amendment or a separate methodology ADR is required before a second entity class or a second model family may be implemented — this ADR does not pre-authorize either.

### Prohibited methodologies

The following are explicitly prohibited for the first supported entity class, regardless of implementation quality, backtested performance, or industry familiarity:

- Any methodology whose output is derived, directly or indirectly, from `ObservedMarketFactRecord.spot_price`, `market_capitalization`, or `fully_diluted_valuation` — this is circular (using price to value price) and directly contradicts ADR 0020's prohibition on market-cap/completeness-derived valuation.
- Token-velocity or quantity-theory-of-money formulas (equation of exchange, MV=PQ variants) — not falsifiable against this repository's evidence contracts and explicitly rejected by ADR 0021's alternatives-considered precedent for the same reasoning class.
- Stock-to-flow, scarcity-only, or supply-schedule-only models — these have no value-capture basis and are categorically inapplicable to an entity class defined by the presence of a value-capture rule.
- TVL-multiple, fee-multiple, or revenue-multiple heuristics applied without exact attribution to the specific `ValueCaptureRuleSnapshot` pathway (undisclosed or unattributed fees/TVL remain non-valuation evidence per ADR 0021 verbatim).
- Peer- or comparable-multiple valuation of any kind — this is `comparative_valuation`'s exclusive scope under a separate future ADR (ADR 0021 implementation-order step 4) and may not be folded into this methodology.
- Provider-supplied price targets, analyst price estimates, or any opaque third-party fair-value conclusion — these are evidence observations at best (per ADR 0021 §Source-provider eligibility item 4) and never a substitutable methodology output.
- Any discount rate, terminal-value assumption, or growth assumption not fixed, versioned, and declared inside the `ValuationMethodologySnapshot` before evaluation — ad hoc or per-estimate assumption tuning is prohibited.
- Any model requiring an input not enumerated in the Valuation Inputs section above.
- Governance-token voting-power monetization without a documented, evidenced entitlement equivalent to one of the permitted `ValueCaptureRuleType` values.

### Replay semantics

This ADR reaffirms ADR 0020's strict-known replay policy without modification and extends it with one methodology-specific rule: a `FairValueEstimateRecord` is replay-eligible only when its own `effective_at`/`recorded_at`/`known_at` satisfy the requested cutoff **and** every one of its four input records (per Valuation Inputs) independently satisfies the same cutoff at the time of estimate construction, not at replay time. A later-published `ObservedMarketFactRecord`, `FundamentalEvidenceRecord`, `ValueCaptureRuleSnapshot`, or `SupplyBasisSnapshot` correction that would have changed the estimate does not retroactively alter it — it can only produce a new, successor `FairValueEstimateRecord` referencing the corrected inputs.

The requested replay cutoff is selection context only. It is never written into any produced record's `effective_at`, `recorded_at`, or `known_at`.

### Persistence requirements

All records persist through Hunter's existing canonical generic SQL authority in `data/data_ops.sqlite` — no standalone valuation database, exactly as ADR 0021 requires and as both prerequisite foundations already demonstrate. No new database, schema migration mechanism, or persistence technology is authorized.

Minimum fields, extending ADR 0021's own record-family table:

| Record family | Additional minimum fields beyond ADR 0021's baseline |
| --- | --- |
| `ValuationMethodologySnapshot` | entity-class criteria (the four conditions above, machine-checkable); permitted model identifier (fixed to `discounted-value-capture-flow-v1` for this ADR); horizon (365 days, fixed); currency; discount-rate policy ID/version; sensitivity policy; supply-basis selection rule; normalization-policy ID (unassigned until Milestone 4 in the accompanying implementation issue); correlation group (`valuation-mispricing`, per ADR 0021) |
| `FairValueEstimateRecord` | exact IDs/versions of all four input records; discount-rate value actually applied; horizon actually applied; model-dispersion decomposition; confidence decomposition (see Confidence Rules) |
| `ValuationAssessmentRecord` | normalization status (`unavailable` until Milestone 4 is independently audited); correlation-group weight-cap reference |

### Provenance

Every produced record carries: canonical entity/asset/representation identity (ADR 0005); exact source-record IDs and versions for all four inputs; the exact `ValuationMethodologySnapshot` ID/version applied; a deterministic canonical hash of the complete input+methodology snapshot; and the three-clock chronology (`effective_at`, `recorded_at`, `known_at`) already required uniformly across `market_facts` and `value_capture`. No field may be populated from a source outside the Valuation Inputs list, even for display or explanatory purposes.

### Correction/versioning rules

`FairValueEstimateRecord` and `ValuationAssessmentRecord` are append-only. A correction is a new record referencing its predecessor by ID, with a mandatory non-blank reason, and a strictly later `recorded_at`/`known_at` than its predecessor — the exact pattern already implemented and independently audited twice in `hunter.value_capture` and `hunter.market_facts`. Branching corrections (two successors claiming the same predecessor) are prohibited, mirroring the existing `_authorize_correction`/`insert_record` pattern. A `ValuationMethodologySnapshot` correction (e.g., a discount-rate-policy revision) does not retroactively alter any `FairValueEstimateRecord` already produced under the prior methodology version; it authorizes only new estimates.

### Confidence rules

Confidence is a bounded `[0,1]` decomposition, never a single opaque number. It is reduced (never increased) by: entity-link confidence and evidence confidence inherited from the four input records; value-capture-rule confidence; supply-basis confidence; model dispersion between `p10` and `p90`; and evidence freshness relative to the horizon. Confidence cannot exceed the minimum confidence of any single required input — mirroring the existing `mispricing` row's rule in ADR 0021's authority matrix ("Confidence cannot exceed either prerequisite") applied here to all four inputs of `valuation` itself, which ADR 0021 left open for this ADR to close.

### Uncertainty handling

The `p10`/`p50`/`p90` triple is mandatory on every estimate and must be internally consistent (`p10 <= p50 <= p90`, all non-negative). Uncertainty is propagated from declared evidence uncertainty (`FundamentalEvidenceRecord.uncertainty`, `ValueCaptureRuleSnapshot.uncertainty`, `SupplyBasisSnapshot.uncertainty`) through the discount-rate sensitivity policy — it is never fabricated as a fixed percentage band unrelated to input uncertainty.

### Missingness

Absence of any one of the four required input records, or a record that is stale, disputed, unregistered, incompatible, or has an unresolved `open`/`contested` conflict state, makes the entire fair-value estimate explicitly unavailable — never a partial or degraded-confidence estimate substituting for a missing input. This mirrors the existing `_require_evidence`/`_require_observed_market_facts` fail-closed pattern already implemented and independently audited in `hunter.value_capture.service`.

### Comparability rules

This ADR sets ground rules binding any future comparative-valuation work, without implementing comparative valuation itself:

- A fair-value estimate is comparable to another only when both share the identical `ValuationMethodologySnapshot` ID/version, entity class, currency, and horizon. Cross-methodology or cross-entity-class comparison is prohibited outright.
- Comparability is a property of the methodology snapshot, declared before evaluation, never inferred post hoc from similar output magnitude.
- This ADR does not define a comparable-cohort mechanism, denominator, or adjustment policy — that is ADR 0021 implementation-order step 4's exclusive scope, requiring its own separate methodology ADR.

### Peer-selection principles

For the same reason, this ADR states principles only, deferring the full contract to the future Comparative Valuation methodology ADR:

- Any future peer-selection policy must be fixed and versioned before target evaluation, never selected ad hoc or from a current/latest provider list (reaffirming ADR 0021 verbatim).
- Peer eligibility must require the same entity-class criteria defined in this ADR's Scope section — a peer failing those criteria cannot be a comparable, regardless of superficial similarity (sector label, market-cap proximity, or provider category).
- Minimum cohort size, denominator choice, and outlier treatment are Comparative Valuation methodology decisions, not decisions this ADR makes.

### Historical validation requirements

Before any fair-value estimate may be exposed to Market Validation (Milestone 5 in the accompanying implementation issue), the discounted value-capture flow model must be demonstrated, using only strict-known historical data, to:

1. Reproduce identical `p10`/`p50`/`p90` output under exact replay at a fixed historical cutoff (determinism).
2. Show no sensitivity to any record whose `known_at` is later than the replay cutoff (leakage-safety), using the existing `hunter.historical.bias_controls`/`cutoff`/`replay` infrastructure rather than a new bespoke harness.
3. Produce a calibration set of sufficient size (minimum size to be fixed by the implementation issue, not by this ADR, since it depends on real entity registration coverage not yet established) spanning at least one full horizon window (365 days) of strict-known-eligible input history.

### Calibration requirements

Scalar `[0,1]` normalization (ADR 0021: "No directionally favorable `[0,1]` Market Validation value is authorized until a versioned monotonic normalization is historically calibrated without using post-cutoff outcomes") must be: versioned; monotonic in the underlying fair-value estimate; fit exclusively on the calibration set defined above; leakage-tested by the same standard as item 2 above; and explicitly unavailable (not zero, not neutral) outside its calibrated input range. Until a calibration set of adequate size exists and passes leakage testing, `valuation` remains unavailable in Market Validation regardless of whether `CanonicalValuationService` itself is implemented and producing raw (non-normalized) `FairValueEstimateRecord`s.

### Audit requirements

This ADR and its accompanying implementation issue must each undergo independent architecture review before implementation begins, and the completed implementation must undergo a separate independent post-merge audit returning `APPROVED` before `valuation` may become available in Market Validation — mirroring, without exception, the two-stage review pattern already used for Issue #88, Issue #95, and both hardening PRs (#105, #106) in this repository's history. No self-reported completion is sufficient.

## Current availability decision

Adoption of this ADR does **not** make `valuation` available in Market Validation. `valuation`, `comparative_valuation`, `mispricing`, and `asymmetry` all remain explicitly unavailable until: (a) the accompanying implementation issue is completed on a dedicated branch, (b) all required quality gates pass on one exact final HEAD, (c) a Draft PR is opened and independently audited APPROVED, and (d) the calibration and leakage-testing requirements above are independently verified, not merely asserted. This ADR authorizes semantic and record contracts only, exactly as ADR 0021 did for the layer beneath it.

## Compatibility With Accepted ADRs

| ADR | Compatibility effect |
| --- | --- |
| 0001 | Discovery remains upstream and unaffected; this ADR does not value or rank during discovery. |
| 0002 | Every produced record remains provenance-preserving, conflict-visible, confidence-bearing, missingness-explicit, and replay-safe. |
| 0003 | Candidate Registry remains canonical candidate identity/lifecycle authority; this ADR does not touch it. |
| 0004 | Trust, reliability, conflicts, and unavailable states precede every valuation conclusion, unchanged. |
| 0005 | Entity/representation/contract/listing boundaries are the literal basis of this ADR's entity-class scope definition. |
| 0006 | No knowledge/technology/economic graph becomes a valuation input; not referenced by this methodology. |
| 0007 | Reaffirmed: canonical Market Validation remains the sole production runtime; this ADR produces an input to it, never a competing runtime. |
| 0009 | `CanonicalValuationService` (when implemented) is the sole service-owned authority; repositories remain mechanical, exactly as this ADR's Persistence and Correction sections require. |
| 0010 | This ADR's service-owned validation pattern mirrors ADR 0010's intelligence-engine execution boundary. |
| 0016 | Reaffirmed, not superseded: Market Validation remains the sole canonical production analytical runtime; this ADR's output is a consumed input, never a parallel authority. |
| 0017 | Experimental Opportunity Assessment gains no authority from this ADR. ADR 0018 already explicitly rejected the naive mapping of Market Validation `valuation` onto Opportunity's `valuation_discount` factor; this ADR does not reverse that rejection and creates no new mapping. |
| 0018 | Reaffirmed without change: the eight explicitly-rejected and four deferred Opportunity factor mappings are unaffected by this ADR. Any future mapping of this ADR's output into Opportunity requires a separate scoring ADR with its own anti-double-counting analysis, per ADR 0017 §Explicit exclusions. |
| 0019 | Prediction Evaluation remains separate audit authority; this ADR's calibration set is used only to fit normalization, never to retroactively judge or tune valuation at its original cutoff. |
| 0020 | Reaffirmed and specialized: strict-known replay, no aliases, no market-cap/completeness-derived valuation — this ADR's Prohibited Methodologies section makes ADR 0020's price-derived-valuation prohibition explicit and binding for this specific model family. |
| 0021 | This ADR is ADR 0021's own required "separate methodology ADR" for implementation-order step 3. It authorizes no capability ADR 0021 did not already anticipate, and changes nothing in ADR 0021's authority matrix, evidence boundaries, or anti-double-counting policy. |

No accepted ADR 0001–0021 is superseded, weakened, or contradicted.

## Consequences

- A concrete, narrow, auditable path exists from the two completed evidence foundations (#88, #95) to a first canonical fair-value methodology, without inventing a universal crypto valuation formula.
- Implementation cannot proceed until a real entity satisfying the Scope criteria is identified and evidenced — this is a genuine, currently-unmet precondition, not a formality, and the accompanying implementation issue must treat entity registration as an explicit milestone rather than an assumption.
- `comparative_valuation`, `mispricing`, and `asymmetry` remain unavailable and unaffected; `mispricing` gains a concrete dependency target (this ADR's `FairValueEstimateRecord`) but no new authorization.
- Opportunity Assessment gains no new authority or eligibility from this ADR; ADR 0018's rejections stand unchanged.
- Historical/backtest infrastructure (`hunter.historical.*`) gains a new consumer but requires no modification itself, since its leakage-safety machinery is reused, not duplicated.
- This ADR itself changes no runtime behavior, no schema, no persisted record, and no test outcome — it is a documentation-only decision, consistent with the precedent set by ADR 0020's and ADR 0021's own "documentation-only decision" consequence statements.

## Alternatives Considered

### Define one universal valuation formula for all entity classes now

Rejected for the same reason ADR 0021 rejected it: protocols, networks, native assets, tokens, stablecoins, wrapped representations, and non-token projects expose materially different economic claims, and a value-less-by-design asset (e.g., Bitcoin, the only currently production-registered entity) cannot be valued by a value-capture-flow model at all.

### Permit peer-multiple valuation as a fallback when cash-flow evidence is absent

Rejected because it would silently substitute `comparative_valuation`'s future scope for `valuation`'s own scope, violating ADR 0021's explicit separation of the two inputs and creating exactly the aliasing failure mode ADR 0020 was written to close.

### Allow provider-supplied price targets as a stopgap methodology

Rejected verbatim per ADR 0021 §Alternatives Considered ("Fill missing inputs with neutral values or provider targets... conceal missing authority and create false score completeness") and ADR 0021's explicit prohibited-substitutes list for `valuation`.

### Skip calibration and ship raw (non-normalized) fair-value estimates as directly Market-Validation-consumable

Rejected because ADR 0021 explicitly prohibits any directionally favorable `[0,1]` value without historically calibrated, leakage-tested, versioned monotonic normalization — raw `p10`/`p50`/`p90` output alone is not authorized to enter Market Validation scoring.

### Name a specific entity now to unblock implementation sooner

Rejected because no currently production-registered entity satisfies this ADR's Scope criteria (Bitcoin, the sole registered `market_facts` identity, has no value-capture rule by design), and naming an unregistered, unevidenced entity in an ADR would misrepresent implementation readiness that does not yet exist.
