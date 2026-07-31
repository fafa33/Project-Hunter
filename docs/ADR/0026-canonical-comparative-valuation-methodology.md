# ADR 0026: Canonical Comparative Valuation Methodology

## Status

Accepted.

Governing preparation record: [ADPR-0003 — Canonical Comparative Valuation](../architecture-records/ADPR-0003-canonical-comparative-valuation.md).

This ADR was accepted after independent architecture review. Implementation of the Comparative Valuation foundation is now authorized under a separately governed implementation Issue and the prerequisites listed below.

## Context

ADR 0021 assigns immutable peer selection and `comparative_valuation` exclusively to `CanonicalComparativeValuationService`. It fixes the semantic boundary: compare a target's observed market valuation with economically compatible peers through one predeclared fundamental denominator, preserve a signed peer-relative residual, and require a historically calibrated monotonic transform before a `[0,1]` Market Validation input can become available.

ADR 0022 defines the first Canonical Valuation entity class and requires any future peer-selection policy for that class to use the same entity-class criteria. ADR 0023 amends only the supply-coherence rule within those criteria. ADR 0024 removes scalar favorability from `valuation` but explicitly preserves the directionality and calibration obligation for `comparative_valuation`. ADR 0025 gives Assembled Fundamental Evidence a distinct owner and grants Comparative Valuation no implicit right to consume or recreate it.

The merged ADPR-0003 evaluated the unresolved methodology choices and recommends a narrow, fail-closed first method:

- a versioned point-in-time candidate universe;
- hierarchical economic-entity and representation eligibility;
- exact hard comparability gates;
- one native attributable value-capture-flow denominator;
- at least three eligible peers;
- the median peer multiple as the reference;
- retention of every eligible peer observation without trimming;
- separate immutable methodology, universe, decision, observation, and assessment records; and
- strict-known replay, append-only correction, explicit missingness, decomposed confidence, and a calibration gate.

No qualifying multi-entity cohort or calibrated normalization is currently established. Comparative Valuation therefore remains unavailable in production.

## Purpose

This ADR defines the canonical first methodology for determining how the observed market valuation of a target in ADR 0022's first supported entity class compares with a point-in-time, evidence-backed, methodologically compatible peer cohort.

Its purpose is relative valuation only. It does not estimate absolute fair value, calculate a market-versus-fair-value gap, assess payoff asymmetry, compose Market Validation, assess an opportunity, or rank projects.

## Scope

This decision governs:

- the first Comparative Valuation methodology for the same supported entity class as ADR 0022, including ADR 0023's supply-coherence amendment;
- peer-candidate sourcing and service-owned peer eligibility;
- mandatory comparability coordinates;
- the compatible numerator and single fundamental denominator;
- peer-set minimum, reference statistic, residual direction, and outlier treatment;
- immutable logical record families;
- provenance, correction lineage, strict-known replay, confidence, missingness, and conflict behavior;
- downstream authority boundaries; and
- prerequisites for later implementation and activation.

This decision is additive. It does not amend or supersede ADRs 0002, 0004, 0005, 0009, 0010, 0016, or 0020–0025.

## Authority and Ownership

`CanonicalComparativeValuationService` is the sole analytical write authority for:

- selecting the applicable peer-universe policy;
- constructing or accepting a point-in-time candidate universe;
- deciding candidate eligibility;
- validating target and peer metric compatibility;
- calculating target and peer multiples;
- calculating the peer reference and signed residual;
- authorizing Comparative Valuation persistence;
- assigning Comparative Valuation confidence, conflict, and availability state; and
- authorizing append-only corrections to Comparative Valuation records.

This ownership is narrow:

- providers acquire and normalize observations but never select canonical peers or calculate Comparative Valuation;
- Discovery and Candidate Registry authorities may supply versioned point-in-time candidates but never decide analytical eligibility;
- Canonical Identity and Trust authorities retain entity, representation, ambiguity, source-reliability, and conflict ownership;
- Market Facts and Fundamental Valuation Evidence authorities retain ownership of their native evidence;
- `CanonicalValuationService` retains absolute fair-value and methodology-contract input-eligibility authority;
- the Canonical Evidence Assembly Authority retains exclusive ownership of Assembled Fundamental Evidence;
- repositories persist and retrieve service-authorized immutable records and perform no eligibility, calculation, correction, timestamp, or replay decision;
- Market Validation remains the sole canonical production composition runtime under ADR 0016; and
- presentation, automation, scheduling, reports, and tests acquire no analytical authority.

No other component may write a canonical Comparative Valuation conclusion or a competing peer-eligibility decision.

## Peer Universe Authority

The canonical candidate source is one immutable, versioned point-in-time candidate universe known at the requested cutoff. Candidate membership is input evidence, not peer eligibility. The `CanonicalComparativeValuationService` owns only the analytical eligibility decisions applied to that universe; it does not create a Registry-plus-Discovery union or otherwise combine candidate sources.

The candidate-universe snapshot must preserve the exact source record IDs/versions, source provenance, cutoff, deterministic query/pagination/order policy, and canonical entity and representation identities. Duplicate candidate observations are resolved only by canonical economic-entity identity followed by representation compatibility under the selected hierarchical eligibility policy. No source-union or source-precedence rule is authorized by this ADR.

Each `PeerUniversePolicyRecord` must be immutable, versioned, effective before target evaluation, and must predeclare:

- supported entity class;
- candidate-universe snapshot selection and provenance requirements;
- taxonomy and taxonomy version;
- mandatory eligibility and exclusion rules;
- metric numerator and denominator policy;
- observation window and freshness limits;
- minimum cohort;
- equal peer weighting and the unweighted reference statistic;
- minimum decision and observation coverage required for availability;
- deterministic ordering and tie-breaking;
- outlier treatment;
- confidence policy;
- residual sign convention;
- normalization and calibration policy identity;
- correlation-group identity; and
- a bounded maximum candidate-universe size plus deterministic pagination/order behavior.

The service must persist every candidate considered and every included, excluded, or indeterminate decision. A current provider category, current market-cap list, hand-maintained peer list, analyst selection, or target-specific ad hoc cohort cannot become the canonical peer universe.

## Peer Eligibility

Eligibility is hierarchical.

First, the candidate must be a distinct canonical economic entity. A target cannot be its own peer, and one economic entity may appear at most once in a peer cohort.

Second, one compatible representation must be selected deterministically. Target and peer must satisfy ADR 0022's first-entity-class criteria:

1. single-chain, non-wrapped, non-bridged native representation;
2. exactly one eligible, explicit value-capture mechanism;
3. independently observable and coherent circulating, total, and fully diluted supply under ADR 0023's fixed tolerance rule; and
4. qualifying native attributable fundamental evidence for the required accounting window.

Third, every mandatory comparability gate below must pass from strict-known evidence:

- same supported entity class;
- same lifecycle state;
- same sector/capability classification under the exact policy taxonomy version;
- same value-capture mechanism type, economic entitlement, and attributable claim scope;
- same revenue/accounting meaning;
- same native fundamental-evidence type;
- same quote currency;
- same 365-day accounting horizon and compatible accounting-window boundary;
- same fully diluted supply basis;
- compatible observed-market-valuation boundary and freshness policy; and
- no unresolved identity, representation, source, denominator, supply, unit, currency, time, or evidence conflict.

Missing evidence for a mandatory gate is not a pass. It produces an `indeterminate` or `excluded` decision under the policy and cannot be repaired by a generic sector label, similarity score, operator judgment, lower confidence, or current-state fallback.

Soft compatibility tiers and continuous similarity scores are not authorized by this first methodology.

## Comparability Requirements and Compatible Metrics

The first methodology uses exactly one dimensionless multiple:

```text
comparative_multiple
    = fully_diluted_observed_market_value
      / native_attributable_value_capture_flow
```

The numerator and denominator must satisfy all of the following:

- `fully_diluted_observed_market_value` is bound to the exact economic entity, asset claim, native representation, quote currency, fully diluted supply basis, observation boundary, and strict-known `ObservedMarketFactRecord` lineage declared by the policy;
- `native_attributable_value_capture_flow` comes from one compatible, numeric, non-conflicted native `FundamentalEvidenceRecord`, bound by exact ID/version to the same economic entity and valued claim and to the applicable `ValueCaptureRuleSnapshot`;
- the denominator covers the exact 365-day accounting window required by the policy;
- target and every peer use the same numerator policy, denominator evidence type, accounting meaning, currency, horizon, supply basis, and value-capture attribution semantics;
- both numerator and denominator are strictly positive; zero, negative, absent, stale, conflicted, unit-incompatible, or unknown-known-time values are ineligible; and
- any permitted currency or unit conversion is exact, versioned, provenance-complete, and already authorized by the authority that owns that evidence. Comparative Valuation may not invent value attribution or a conversion policy.

Assembled Fundamental Evidence is not compatible input for this methodology. Comparative Valuation may neither consume an `AssembledFundamentalEvidenceRecord` nor recreate its constituents. A later accepted amendment with an explicit ownership and input-eligibility analysis is required to change this boundary.

## Reference and Residual Method

The target is never included in its own peer distribution.

At least three eligible economic-entity peers are required. With fewer than three, no canonical reference or residual is available.

Eligible peer observations are ordered deterministically by canonical economic-entity ID, representation ID, and source-record identity. All eligible observations are retained. No observation is trimmed, winsorized, silently excluded, or down-weighted after eligibility.

Every eligible economic entity contributes exactly one compatible peer observation with equal weight. The peer reference is the unweighted median of all eligible peer `comparative_multiple` values. For an even cohort, the median is the arithmetic mean of the two central ordered values. No alternative weighting policy is authorized for this methodology.

The minimum coverage gate is 100 percent coverage of the bounded point-in-time candidate universe and eligible cohort:

- every candidate in the universe snapshot has one persisted, deterministic eligibility decision;
- every candidate decision is resolved as included or excluded; an indeterminate decision fails the coverage gate;
- the target and every included eligible peer have one compatible, non-conflicted metric observation; and
- every included eligible peer observation participates in the unweighted median.

Any failure of this complete decision or observation coverage gate makes the assessment unavailable. Confidence cannot convert incomplete coverage into availability.

The raw signed residual is:

```text
raw_log_residual
    = ln(peer_median_multiple / target_multiple)
```

Therefore:

- a positive residual means the target is cheaper than the compatible peer reference;
- zero means equality with the peer reference; and
- a negative residual means the target is more expensive than the peer reference.

The assessment preserves the complete ordered peer-multiple distribution. Quantiles, interpolation, trimmed distributions, Bayesian estimates, composite denominators, and multiple-denominator assessments are not authorized by this methodology.

A `[0,1]` value may be produced only by a predeclared, versioned, monotonic transform of the raw residual that has been calibrated and leakage-tested on strict-known historical evidence. Until that calibration exists and passes independent review, the normalized value remains unavailable and `comparative_valuation` remains unavailable to Market Validation even when a raw assessment can be reproduced.

## Prohibited Comparisons

Canonical Comparative Valuation must not:

- compare different economic-entity classes;
- compare an economic entity with one of its own representations;
- count multiple representations of one economic entity as separate peers;
- compare wrapped, bridged, multi-chain aggregate, contract, listing, protocol, network, token, or asset-claim scopes as if interchangeable;
- compare different value-capture mechanisms, entitlement scopes, revenue meanings, native evidence types, accounting periods, currencies, units, supply bases, or observation windows;
- compare a native fundamental record with Assembled Fundamental Evidence;
- use provider categories, rankings, ratings, completeness, current market-cap lists, broad market averages, analyst peer sets, generic sector labels alone, or opaque third-party scores as canonical comparison authority;
- substitute `valuation`, provider fair-value targets, price return, spot price alone, market capitalization alone, TVL, fees, revenue without attributable value capture, descriptive intelligence, or any similarly named output for the declared multiple;
- use current/latest records or post-cutoff membership during replay;
- average conflicting evidence;
- use one or two peers as a canonical distribution;
- calculate Mispricing, Asymmetry, Opportunity Assessment, Market Validation composition, scoring, weighting, recommendation, or ranking; or
- compare the result with fair value or spot price to infer a market-versus-fair-value gap.

## Persistence Model

Comparative Valuation uses separate immutable logical record families persisted through the existing canonical persistence boundary. This ADR selects no database product, table, schema, file path, migration mechanism, or service deployment.

All families use the canonical immutable bitemporal envelope required by ADRs 0020 and 0021: record and logical identity, schema and semantic version, effective time, recorded time, explicit known time, exact source IDs paired with source versions, evidence references, methodology/configuration fingerprints, canonical hash, confidence, missing/conflicting evidence, and correction/supersession lineage.

### Immutable Record Families

1. `PeerUniversePolicyRecord`
   - owns the complete predeclared policy listed under Peer Universe Authority;
   - is reference policy, not a candidate or assessment result.

2. `PeerUniverseSnapshot`
   - binds the target, candidate source records, complete ordered candidate identities, cutoff, policy ID/version, and deterministic construction fingerprint;
   - preserves candidates regardless of later inclusion or exclusion.

3. `PeerEligibilityDecisionRecord`
   - binds one target/candidate pair to `included`, `excluded`, or `indeterminate`;
   - preserves every dimension-level decision, reason, exact evidence reference, confidence component, and conflict/missingness state;
   - is required because peer-decision corrections must remain independently auditable without rewriting the universe snapshot or assessment.

4. `ComparativeMetricObservationRecord`
   - binds one target or peer to the exact numerator and denominator records, multiple, currency, horizon, supply basis, value-capture pathway, availability/conflict state, and deterministic calculation fingerprint;
   - is required because evidence or coordinate corrections must remain independently traceable without duplicating native evidence or rewriting prior assessments.

5. `ComparativeValuationAssessmentRecord`
   - binds the target, policy, universe snapshot, included decision IDs, target and peer observation IDs, complete ordered peer distribution, median, raw log residual, normalization policy/status/value, cohort coverage, confidence decomposition, availability state, correlation group, exact provenance, and correction lineage.

These records do not duplicate the authority of native Market Facts, Fundamental Valuation Evidence, identity, or value-capture records. They reference those records by exact ID/version.

## Provenance

Every candidate decision, metric observation, and assessment must preserve:

- canonical economic-entity, asset-claim, and representation identity;
- exact policy, taxonomy, methodology, configuration, normalization, and calibration identities and versions;
- exact source/evidence record IDs and versions;
- candidate-source provenance and deterministic candidate ordering;
- exact included, excluded, and indeterminate peer decisions and reasons;
- numerator and denominator coordinates, units, currency, horizon, supply basis, and value-capture pathway;
- effective, recorded, and known times for the record and every transitive input;
- complete ordered peer observations and calculation fingerprints;
- missingness, conflict, confidence, freshness, and exclusions; and
- a deterministic canonical hash.

Provider payloads, source prose, analyst judgments, or mutable current projections are never sufficient provenance by themselves.

## Correction Lineage

All Comparative Valuation records are append-only.

A correction:

- creates one immutable successor;
- references exactly one predecessor;
- carries a mandatory non-blank reason, authorizing service, and corrected chronology/provenance;
- has strictly later `recorded_at` and `known_at` than its predecessor; and
- never changes what was knowable at an earlier cutoff.

Branching successors are prohibited. A corrected candidate, eligibility decision, observation, policy, taxonomy, calibration, or native input may support new successor downstream records, but it never rewrites or silently rebinds an existing universe or assessment. Methodology and policy changes create new versions, not corrections that reinterpret prior history.

## Replay Semantics

At replay cutoff `T`:

1. the service declares a timezone-aware cutoff and exact target identity/representation;
2. policy, taxonomy, candidates, candidate-source evidence, eligibility evidence, metric inputs, corrections, calibration, and every transitive dependency must each have been known at or before `T`;
3. records with unknown known-time are ineligible;
4. selection uses immutable envelopes and deterministic policy ordering, never current projections or mutable latest files;
5. the cutoff is selection context only and is never copied into an input or output timestamp;
6. historical replay performs no live provider call;
7. later candidate membership, reclassification, evidence, corrections, policy, taxonomy, or calibration cannot enter the replay; and
8. exact replay must reproduce record IDs/versions, ordered candidate and peer sets, inclusion/exclusion decisions, metric observations, median, raw residual, normalization state/value, confidence, missingness, and canonical hash.

If exact reconstruction is impossible, the assessment is unavailable. No fallback cohort, latest correction, prior median, neutral value, or current normalization is allowed.

## Confidence

Confidence is a decomposed, explainable reliability assessment, not a substitute for eligibility, cohort sufficiency, calibration, or comparative value.

Every assessment preserves at least:

- peer-universe coverage confidence;
- eligibility-evidence confidence;
- metric-evidence confidence;
- coordinate-compatibility confidence;
- peer-set cardinality and distribution confidence; and
- methodology-calibration confidence.

Each component must be derived deterministically from declared policy and exact input evidence. Overall confidence cannot exceed the weakest mandatory component. Confidence may only decrease because of evidence limitations; it cannot convert a failed hard gate, missing denominator, conflicted record, stale input, insufficient cohort, or absent calibration into availability.

Liquidity or market-quality evidence may reduce metric-evidence confidence or make the observed valuation unavailable when reliability fails the predeclared policy. It may not become peer favorability, Mispricing, or an eligibility similarity score.

## Missingness

The service must preserve explicit states including:

- `AVAILABLE`;
- `LIMITED_PEER_SET`;
- `UNAVAILABLE_NO_CANDIDATES`;
- `UNAVAILABLE_INSUFFICIENT_ELIGIBLE_PEERS`;
- `UNAVAILABLE_INCOMPATIBLE_COORDINATES`;
- `UNAVAILABLE_STALE_INPUTS`;
- `UNAVAILABLE_CONFLICTED_INPUTS`;
- `UNAVAILABLE_UNSUPPORTED_ENTITY_CLASS`; and
- `UNAVAILABLE_UNCALIBRATED_NORMALIZATION`.

`LIMITED_PEER_SET` is descriptive metadata only. Below three eligible peers the canonical assessment and residual are unavailable.

`AVAILABLE` requires all mandatory gates, at least three eligible peers, complete candidate-decision and eligible-cohort observation coverage under the peer policy, compatible target and peer observations, complete provenance, no unresolved required conflict, strict-known replayability, and a historically calibrated normalization accepted through independent review. Raw reproducible observations and a raw residual may be persisted before calibration, but they do not make the Market Validation input available.

Missing, incompatible, stale, conflicted, insufficient, or uncalibrated data never becomes zero, neutral, average, prior-filled, lower-confidence availability, or a supportive value.

## Conflict Handling

An unresolved conflict in a mandatory identity, eligibility, numerator, denominator, coordinate, chronology, policy, taxonomy, or calibration input blocks the affected candidate or observation.

The service:

- persists the conflict and the affected decision state;
- never averages, majority-votes, silently excludes, or chooses the latest conflicting record;
- recalculates cohort sufficiency after every blocked peer;
- returns the applicable unavailable state when fewer than three eligible, non-conflicted peers remain; and
- preserves the original conflicted record and any later resolution through append-only lineage.

Removing a conflicted peer never lowers or changes the predeclared methodology requirements.

## Downstream Boundaries

`ComparativeValuationAssessmentRecord` is the only canonical output of this authority.

Permitted downstream use is limited to:

- read-only audit, replay, and explainability;
- a future Mispricing authority only if a later accepted ADR explicitly authorizes the exact dependency; and
- canonical Market Validation acceptance only after the calibrated normalization and a separate accepted composition decision authorize that use.

This ADR does not define the Mispricing formula or owner beyond reaffirming ADR 0021. It grants no authority to Asymmetry, Opportunity Assessment, ranking, recommendation, Dashboard calculation, general scoring, or portfolio logic.

The correlation-group identity required by ADR 0021 must be preserved on the assessment. Any downstream contribution cap, downstream contribution weighting, residual-independence claim, or Market Validation composition remains unavailable until a separate accepted ADR defines it.

## Implementation Prerequisites

Implementation is authorized after acceptance of this ADR. Implementation still requires:

1. a separately governed implementation Issue and reviewable plan;
2. one real strict-known target and at least three eligible peers in ADR 0022's first supported entity class;
3. compatible numeric native attributable-flow evidence for the target and every peer covering the exact 365-day window;
4. compatible strict-known observed fully diluted market-valuation evidence for the target and every peer;
5. versioned policy, taxonomy, deterministic candidate-source, ordering, tie, freshness, and bounded-query rules;
6. a real historical calibration corpus for the raw log residual, with strict-known leakage tests and an accepted monotonic transform;
7. immutable service-owned record and persistence designs consistent with ADRs 0009, 0020, and 0021;
8. append-only correction, branching-rejection, conflict, missingness, permutation, cohort-boundary, multi-representation, future-leakage, and byte-identical replay tests;
9. additive migration, transactional write, compatibility, preflight, observability, rollback, and disabled-entry-point plans; and
10. independent implementation review and post-merge audit before any production activation.

Partial implementation or migration must fail closed. Rollback disables the new entry point and preserves immutable history; it never deletes accepted records. Existing Canonical Valuation, Market Validation, persistence, or runtime contracts must not change implicitly.

## Explicit Non-Goals

This ADR does not:

- implement code;
- modify runtime behavior;
- create a service, package, repository, database, table, schema, migration, CLI, scheduler, automation job, Dashboard/API field, or production entry point;
- activate `comparative_valuation`;
- change Canonical Valuation or admit Assembled Fundamental Evidence;
- define or calculate Mispricing;
- define or calculate Asymmetry;
- define Opportunity Assessment or factor mappings;
- define Market Validation composition, weighting, correlation caps, or scoring;
- introduce ranking, peer ranking, project ranking, recommendation, portfolio advice, forecasting, or scenario analysis;
- authorize soft similarity tiers, a continuous similarity score, composite or multiple denominators, quantiles, trimming, winsorization, Bayesian estimation, or opaque analyst adjustments;
- claim that Sky or any other named entity has a qualifying cohort;
- select providers or acquire evidence; or
- represent Comparative Valuation as production-ready.

## Compatibility

- ADR 0002 remains binding: provenance, conflict visibility, explicit missingness, and cutoff-safe replay are mandatory.
- ADR 0004 remains binding: trust, identity confidence, source reliability, freshness, and unresolved conflicts precede comparison.
- ADR 0005 remains binding: economic entity, claim, representation, contract, and listing identities are not interchangeable.
- ADR 0009 remains binding: Provider → Service → Repository → Persistence.
- ADR 0010 remains binding: service-owned execution and validation do not grant engines persistence or composition authority.
- ADR 0016 remains binding: Market Validation is the sole canonical production composition runtime and is not changed here.
- ADR 0020 remains binding: strict-known replay, anti-aliasing, immutable provenance, and unavailable-state behavior are unchanged.
- ADR 0021 is reaffirmed and specialized: its sole owner, semantic metric, record baseline, residual direction, normalization, correlation, replay, correction, and missingness contracts are preserved.
- ADR 0022 and ADR 0023 define the first supported entity-class and supply-coherence gates used here; no Canonical Valuation methodology is changed.
- ADR 0024 remains binding: only `valuation` is structured and non-directional; `comparative_valuation` retains peer-relative favorability and calibrated normalization.
- ADR 0025 remains binding: evidence assembly remains a distinct authority and Assembled Fundamental Evidence is ineligible here.

No accepted ADR is superseded, weakened, or contradicted.

## Consequences

Positive:

- Comparative Valuation gains one explicit owner and one auditable first methodology.
- Peer membership, eligibility, observations, calculations, and corrections remain point-in-time reproducible.
- Exact hard gates and a minimum-three cohort prevent false precision from superficial or sparse comparisons.
- The median reduces individual outlier influence while all observations remain visible.
- Separate immutable families preserve provenance and correction granularity without duplicating native evidence authority.
- Downstream Mispricing, Market Validation, Opportunity, and ranking boundaries remain closed.

Costs and risks:

- Exact gates may leave Comparative Valuation unavailable for most or all candidates.
- A real compatible cohort and calibration corpus may be difficult or impossible to acquire.
- Separate immutable families add future persistence and replay complexity.
- Small cohorts remain sensitive to genuine structural extremes even with a median.
- Taxonomy, policy, calibration, and correction versions add governance and operational overhead.

These costs are accepted because explicit unavailability is preferable to an economically incompatible or historically unreplayable comparison.

## Alternatives Considered

### Extend `CanonicalValuationService` or embed comparison in Market Validation

Rejected because both choices duplicate or collapse authority already assigned exclusively to `CanonicalComparativeValuationService`.

### Use a static peer registry, current discovery result, provider category, or market-cap list

Rejected as final peer authority because these sources are operator-biased, mutable, representation-ambiguous, or historically unreplayable. A versioned registry record may contribute candidate evidence only; eligibility remains service-owned.

### Use entity-only or representation-only eligibility

Rejected because entity-only comparison ignores market coordinates, while representation-only comparison can double-count one economic entity. Hierarchical entity-then-representation eligibility preserves both boundaries.

### Use soft tiers, continuous similarity, or human judgment

Rejected for the first methodology because weights and judgments are not calibrated and can become opaque ranking. A successor methodology requires a new preparation and accepted ADR or amendment.

### Use protocol cash flow, adjusted revenue/fees, or multiple/composite denominators

Not selected. Native attributable value-capture flow best preserves the first entity class's existing evidence and claim-attribution boundary. Other single denominators require a separately supported entity-class methodology; multiple/composite denominators require an explicit amendment to ADR 0021.

### Use mean, quantiles, trimming, winsorization, or Bayesian estimation

Rejected or deferred. Mean is outlier-sensitive; quantiles require a larger evidence-backed minimum and interpolation policy; trimming and winsorization can hide genuine observations; Bayesian estimates introduce unsupported priors and calibration burden. Median with full observation retention is the narrowest supported first reference.

### Permit one or two peers with reduced confidence

Rejected because confidence cannot manufacture a distribution. Fewer than three eligible peers remains unavailable.

### Persist only the final output or one monolithic snapshot

Rejected because neither preserves independently correctable peer decisions, metric lineage, and exact replay with sufficient authority separation.
