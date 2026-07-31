# ADPR-0003 — Canonical Comparative Valuation

## Metadata

- ADPR ID: `ADPR-0003`
- Status: `READY_FOR_REVIEW`
- Version: 1.1
- Author: ChatGPT, on behalf of Issue #156
- Reviewers: not yet assigned
- Created: 2026-07-31
- Approved: not yet approved
- Related Epic: Issue #135
- Related Issue: Issue #156
- Planned ADR: not yet created
- Supersedes: not applicable
- Superseded by: not applicable

## Executive Summary

ADR 0021 already establishes Canonical Comparative Valuation as a separately owned analytical authority after Canonical Valuation. Its purpose is to determine how a target economic entity's observed market valuation compares with a point-in-time, evidence-backed, methodologically compatible peer set without collapsing into canonical valuation, mispricing, asymmetry, Market Validation composition, Opportunity Assessment, or generic current-project ranking.

This preparation record does not reopen that accepted owner or semantic contract. It validates the unresolved methodology problem, identifies governing constraints, inventories evidence, enumerates the materially distinct methodology options that remain available under ADR 0021, compares and falsifies them, and records the conditions a formal methodology ADR must fix before implementation.

This record recommends a bounded methodology direction but does not implement it. It does not create code, activate a Market Validation input, define Market Validation composition, or authorize Opportunity Assessment. ADR 0021's peer-relative favorability and historically calibrated normalization contract remains binding; ADR 0024 removed scalar favorability only from `valuation`, not from `comparative_valuation`.

Self-assessment: `READY_FOR_ADR`. The accepted owner and semantic boundary are treated as fixed constraints, the remaining option axes are normalized and falsified, and the recommendation is fail-closed where real peer evidence or calibration is unavailable. This is an author self-assessment only; independent audit controls the readiness verdict.

## Problem Statement

### Current condition

Hunter now has canonical structured valuation authority and separately owned evidence foundations. ADR 0021 already assigns immutable peer selection and `comparative_valuation` exclusively to `CanonicalComparativeValuationService`, but the input remains explicitly unavailable because no accepted methodology, qualifying point-in-time cohort, calibrated normalization, or service-owned persistence implementation exists. The unresolved problem is therefore not ownership. It is the methodology and evidence contract for:

- construction of a point-in-time peer universe;
- peer eligibility and rejection decisions;
- comparability across economic entity, token representation, stage, sector, revenue model, supply basis, and value-capture mechanism;
- peer-relative metric distributions;
- sparse, invalid, stale, or conflicted peer-set behavior;
- immutable comparative methodology snapshots;
- strict-known replay of peer selection and comparative outputs.

Any attempt to add peer-relative analysis directly to Canonical Valuation, Mispricing, Market Validation, or ranking would blur separately governed ownership boundaries.

### Desired condition

Hunter has a separately governed Comparative Valuation authority that can:

- select or reject peers using point-in-time evidence only;
- preserve exact entity and representation identity;
- compare only compatible metrics and coordinates;
- fail closed when a valid peer set does not exist;
- produce immutable, versioned, confidence-bearing comparative outputs;
- persist exact provenance, peer decisions, correction lineage, and replay state;
- remain structurally incapable of calculating Mispricing or activating scoring/ranking.

### Decision required

A future ADR must preserve the owner, input/output semantics, and minimum record contracts already fixed by ADR 0021 and determine only the unresolved methodology:

1. the peer-candidate source and point-in-time eligibility contract;
2. the minimum comparability dimensions;
3. the single predeclared fundamental denominator and coordinate rules required by ADR 0021;
4. the peer-reference statistic and residual calculation;
5. outlier, sparse-peer, stale, conflict, confidence, calibration, and correction behavior;
6. any additional immutable supporting records needed beyond ADR 0021's fixed `PeerUniversePolicyRecord`, `PeerUniverseSnapshot`, and `ComparativeValuationAssessmentRecord`;
7. implementation and activation gates that preserve the downstream boundary with Mispricing and Market Validation composition.

### In scope

- architecture preparation for Canonical Comparative Valuation;
- exhaustive decision-space preparation;
- peer-candidate sourcing and service-owned eligibility under ADR 0021's fixed authority;
- metric and coordinate compatibility;
- methodology options;
- persistence, identity, replay, provenance, confidence, conflict, missingness, and correction;
- proposed implementation sequence without implementation.

### Out of scope

- implementation code;
- ADR creation or acceptance;
- canonical valuation changes;
- Mispricing or Asymmetry calculation;
- Market Validation input wiring, scoring, weighting, or ranking;
- Opportunity Assessment factor mapping;
- forecasting, scenarios, portfolio advice, or dashboard work;
- hindsight-selected or current-list peer sets.

## Problem Validation

The problem is real and unresolved:

- ADR 0021 separates Comparative Valuation from Canonical Valuation, Mispricing, and Asymmetry.
- Issue #135 identifies Comparative Valuation as the next unavailable authority after Canonical Valuation.
- Current canonical valuation records do not define peer eligibility, peer-set persistence, relative metrics, or comparative distributions.
- Current Market Validation interfaces cannot be treated as authority for comparative valuation because they own a different composition and scoring concern.
- Generic discovery or ranking outputs cannot substitute for point-in-time, economically compatible peer authority.

Therefore a new architectural decision is required before implementation.

## Motivation and Existing Architecture

Without a methodology ADR, Hunter must keep `comparative_valuation` unavailable. Implementing directly would force code to invent a denominator, peer cohort, residual, outlier rule, calibration target, or record boundary that ADR 0021 intentionally leaves to a later methodology decision. The resulting number could appear precise while comparing incompatible claims or leaking current peer membership into historical replay.

The existing architecture already provides:

- canonical economic-entity and representation identity under ADR 0005;
- provider-observed market facts under service-owned Market Facts authority;
- native fundamental evidence, value-capture rules, and supply snapshots under `hunter.value_capture`;
- structured Canonical Valuation records under `hunter.valuation_authority`;
- immutable analytical persistence envelopes and strict-known repository reads.

It does not provide:

- a comparative methodology snapshot;
- a persisted point-in-time peer policy or peer-universe snapshot;
- service-owned peer eligibility decisions;
- coherent target/peer multiple observations;
- a calibrated peer-relative residual transform;
- a Comparative Valuation production service, repository, CLI, scheduler, or Market Validation adapter.

The current Sky pilot is evidence that the first entity class is registered, not evidence that a comparative cohort exists. Its persisted fundamental disclosure lacks the numeric amount and 365-day accounting coverage needed by the first Canonical Valuation methodology, and no additional compatible peers are currently persisted. The only truthful current availability state is therefore unavailable.

## Governing Authority and Constraints

### Constitutional constraints

- Unknown remains unknown.
- Missing remains missing.
- Conflicting evidence remains conflicted until resolved.
- Deterministic outputs must be reproducible from the same point-in-time evidence.
- No convenience fallback may masquerade as canonical evidence.

### Governance constraints

- Architecture preparation precedes ADR drafting and implementation.
- Options must be enumerated before recommendation.
- Rejected options and reconsideration conditions remain permanent.
- Independent architecture review is required before ADR work begins.

### Technical, operational, and compatibility constraints

- candidate construction and eligibility evaluation must have deterministic ordering and bounded query cost;
- every included and excluded peer decision must be observable by record ID, reason, methodology version, effective time, recorded time, and known time;
- no live provider call may occur during historical replay;
- partial service or migration deployment must fail closed and must not expose an assessment as available;
- the first implementation must be additive: existing valuation, Market Validation, and persistence contracts remain compatible;
- rollback disables the new entry point and preserves immutable records; it never deletes accepted history;
- secrets, personal data, and opaque analyst judgments are not eligible methodology inputs;
- Assembled Fundamental Evidence remains outside this scope under ADR 0025;
- performance must be bounded by a versioned maximum candidate-universe size and deterministic pagination/order policy fixed by the future ADR.

### Accepted architecture constraints

- ADR 0005: economic entity and representation identity must remain explicit.
- ADR 0009: Provider → Service → Repository → Persistence.
- ADR 0010: validation and persistence authorization are service-owned.
- ADR 0016: Market Validation remains the sole canonical production composition runtime and must not be silently extended.
- ADR 0020: strict-known replay, truthful missingness, no aliases or current fallback.
- ADR 0021: `CanonicalComparativeValuationService` is the sole owner of immutable peer selection and `comparative_valuation`; its fixed semantic output compares observed market valuation through one predeclared fundamental denominator, produces a peer-relative residual, and requires a historically calibrated monotonic transform before becoming a `[0,1]` Market Validation input.
- ADR 0024: only `valuation` lost scalar favorability. It explicitly leaves `comparative_valuation`'s peer-relative directionality, normalization, and calibration contract unchanged.
- ADR 0025: Assembled Fundamental Evidence is a distinct Layer 2 subtype owned by Canonical Evidence Assembly Authority and is authorized immediately upstream of Canonical Valuation only when a valuation methodology opts in. Comparative Valuation receives no implicit right to consume or recreate assembled evidence; a later amendment would be required before such evidence became eligible.

### Fixed prohibitions

- no current-list peer selection;
- no hindsight-selected peer eligibility;
- no mixing economic entities and token representations without explicit coordinate identity;
- no silent currency, horizon, supply-basis, or denominator conversion;
- no provider-supplied analytical score accepted as canonical Comparative Valuation;
- no averaging of incompatible or conflicted peers;
- no fallback to generic sector labels when material comparability dimensions are missing;
- no calculation of market-versus-fair-value gap;
- no Market Validation composition, generic ranking, weighting, or Opportunity Assessment activation before separately accepted downstream architecture;
- no removal of the peer-relative sign convention or normalization obligation already fixed by ADR 0021.

## Evidence Inventory and Quality Assessment

| ID | Evidence | Authority/source | Finding | Quality and limitations | Supports or challenges |
|---|---|---|---|---|---|
| E-001 | Constitutional Rules 2, 3, 4, and 5 | `docs/PROJECT_CONSTITUTION.md` | Evidence, replay, explicit ownership, and one canonical owner are mandatory. | Highest authority; does not select a numeric method. | Supports fail-closed evidence, deterministic replay, and fixed ownership. |
| E-002 | Canonical authority hierarchy and Valuation → Opportunity direction | `docs/CANONICAL_ARCHITECTURE_MAP.md` | Accepted ADRs are binding; Comparative Valuation is downstream of Canonical Valuation and upstream of Mispricing/Opportunity. | Highest architecture navigation; intentionally high-level. | Rejects authority duplication and downstream shortcuts. |
| E-003 | Comparative Valuation authority matrix | ADR 0021, Decision and authority-matrix `comparative_valuation` row | Fixes sole owner, exact market-value-to-fundamental meaning, single predeclared denominator, residual sign, required record families, peer-policy fields, calibration, correlation, strict-known replay, and prohibited substitutes. | Binding and directly applicable; leaves the numeric methodology and first cohort unselected. | Fixes the baseline and removes owner/semantic alternatives from the viable set. |
| E-004 | Valuation scalar amendment | ADR 0024, Decision and exact amendments | Removes scalar favorability only from `valuation`; explicitly preserves `comparative_valuation` normalization and favorability language. | Binding and precise. | Challenges any non-directional/no-normalization interpretation of Comparative Valuation. |
| E-005 | Fundamental-evidence assembly boundary | ADR 0025, Decision and ownership boundary | Assembled Fundamental Evidence is distinct Layer 2 evidence owned by Canonical Evidence Assembly Authority and conditionally available to Canonical Valuation. | Binding; does not grant Comparative Valuation consumption authority. | Requires native fundamental evidence for this scope unless a later accepted amendment says otherwise. |
| E-006 | Strict-known input authority | ADR 0020 | Prohibits aliases, future/current fallback, and unknown-known-time substitution. | Binding; methodology-independent. | Supports versioned point-in-time candidate and observation selection. |
| E-007 | Identity boundary | ADR 0005 | Economic entity, asset claim, and representation are distinct identities. | Binding; does not define peer compatibility. | Supports hierarchical entity then representation eligibility. |
| E-008 | Provider/service/repository/persistence authority | ADR 0009 and ADR 0010 | Providers acquire; services validate and authorize; repositories persist. | Binding; does not select data providers. | Rejects provider-selected canonical peers and repository-owned eligibility. |
| E-009 | Existing first Canonical Valuation entity class and Sky pilot | ADR 0022; `docs/IMPLEMENTATION_REPORTS/v3.6.0-milestone-1-entity-registration.md`; `tests/test_valuation_real_evidence_v1.py` | ADR 0021 implementation order requires Comparative Valuation for the same supported entity class; Sky is the persisted pilot, but its real fundamental evidence has no numeric amount and covers 30 rather than 365 days. | Repository-observed implementation evidence; one entity is not a peer cohort and cannot validate a comparative denominator. | Fixes the initial entity-class boundary while proving production activation must remain unavailable. |
| E-010 | Current record contracts | `src/hunter/value_capture/models.py`, `src/hunter/market_facts/`, `src/hunter/valuation_authority/` | Immutable fundamental, supply, market-fact, and valuation records exist; no Comparative Valuation service or peer-policy persistence exists. | Direct implementation evidence at this revision. | Supports reuse by exact reference and confirms new service/persistence work remains future scope. |
| E-011 | Architecture audit and capability gap | Issue #135 | Comparative Valuation remains unavailable and separately governed. | Repository audit evidence, not architectural authority. | Supports the need for methodology preparation. |
| E-012 | Authorized preparation scope | Issue #156 | Requires exhaustive preparation without ADR or implementation. | Work authorization, not architectural authority. | Fixes deliverables and prohibited scope. |

No real, strict-known multi-entity cohort with compatible numeric fundamental denominators exists in the repository at this revision. That absence is evidence of an activation blocker; it is not replaced with a hypothetical cohort.

## Assumptions

| ID | Assumption | Rationale | Confidence | Falsification condition | Consequence if false |
|---|---|---|---|---|---|
| A-001 | The first Comparative Valuation methodology should use ADR 0021's required same supported entity class as Canonical Valuation. | ADR 0021 implementation order fixes this boundary. | High | A later accepted ADR explicitly changes the implementation-order or entity-class boundary. | A new ADPR or explicit amendment is required. |
| A-002 | At least three eligible economic-entity peers may eventually be acquired for the first class. | Three is the smallest cohort that permits a target-independent median with two-sided dispersion; it is an activation floor, not evidence that such peers exist. | Low | Evidence acquisition finds fewer than three eligible peers at a historical cutoff. | Output remains `UNAVAILABLE_INSUFFICIENT_ELIGIBLE_PEERS`; no fallback or confidence-only substitute is allowed. |
| A-003 | One numeric, attributable native `FundamentalEvidenceRecord` denominator can eventually be made compatible across target and peers. | ADR 0021 requires one predeclared denominator; current Sky evidence proves the record family exists but not numeric cross-peer coverage. | Low | No common numeric denominator with coherent periods and value-capture attribution can be acquired. | The methodology cannot activate; denominator scope must be revised through governance. |
| A-004 | Median plus explicit empirical distribution is a safer first reference than mean, trimming, or model-based shrinkage. | It preserves observations and reduces single-outlier influence without inventing exclusions or priors. | Medium | Historical validation shows systematic instability or bias relative to another predeclared method. | A successor methodology version or later ADR may select another normalized option. |
| A-005 | A future Mispricing authority may consume Comparative Valuation only by exact record reference. | Accepted dependency direction places Mispricing downstream. | Medium | A later accepted ADR selects a different dependency or prohibits consumption. | No current contract changes; the downstream link remains unavailable. |

Assumptions A-002 and A-003 are production-activation gates, not ADR-readiness blockers: the ADR can require fail-closed behavior when they are false without asserting that qualifying evidence already exists.

## Architectural Dimensions

1. authority and ownership;
2. target and peer identity;
3. peer candidate discovery;
4. point-in-time eligibility;
5. comparability dimensions;
6. metric and denominator authority;
7. quote currency and time horizon;
8. supply-basis and value-capture compatibility;
9. peer-set cardinality and sparsity;
10. aggregation/distribution method;
11. outlier treatment;
12. confidence decomposition;
13. conflict and missingness;
14. methodology versioning;
15. immutable record design;
16. provenance and exact input identity;
17. correction and supersession;
18. strict-known replay;
19. deterministic historical reconstruction;
20. performance and operability;
21. testability;
22. migration and backward compatibility;
23. downstream Mispricing boundary;
24. prohibition on scoring/ranking shortcuts.

## Decision-Driver Matrix

| Driver | Required outcome |
|---|---|
| Authority clarity | One sole analytical write authority |
| Point-in-time correctness | Peer universe reconstructed exactly at cutoff |
| Economic comparability | Incompatible peers rejected explicitly |
| Identity correctness | Economic entity and representation never conflated |
| Metric coherence | Same denominator, currency, horizon, and supply basis |
| Truthful missingness | Invalid or sparse peer sets remain unavailable |
| Determinism | Same inputs and methodology produce byte-identical output |
| Auditability | Every included/excluded peer decision is persisted |
| Correction safety | Append-only lineage with branching rejection |
| Downstream safety | No Mispricing, scoring, or ranking is calculated |
| Reversibility | Methodology can be superseded without rewriting history |
| Operational viability | Bounded peer-set construction and query cost |

## Candidate Option Inventory

### Fixed baseline — Analytical ownership

#### Required baseline 1A — Dedicated `CanonicalComparativeValuationService`

ADR 0021 already assigns sole write authority to this service. The future methodology ADR must preserve it.

- Advantages: strongest owner separation; mirrors existing canonical service architecture; easy to audit.
- Disadvantages: new package and persistence surface.
- Failure mode: duplicate authority if peer selection is split across unrelated services.
- Reconsideration condition: only through an explicit amendment to ADR 0021.

#### Option 1B — Extend `CanonicalValuationService`

- Advantages: reuses current valuation infrastructure.
- Disadvantages: collapses two owners prohibited by ADR 0021; couples absolute and relative methodologies.
- Falsification: rejected by current ownership constraints.
- Reconsideration condition: requires explicit ADR 0021 amendment.

#### Option 1C — Embed in Market Validation

- Advantages: direct access to downstream composition.
- Disadvantages: confuses evidence analysis with scoring/composition; invites scalar shortcuts.
- Falsification: rejected by ADR 0016/0021/0024 boundaries.

**Current viability:** 1A is a fixed constraint, not an option to be selected. Options 1B and 1C are retained only as rejected alternatives and amendment boundaries.

### Axis 2 — Peer-universe source

#### Option 2A — Static manually maintained canonical peer registry

- Advantages: explicit and easy to inspect.
- Disadvantages: stale, operator-biased, poor historical fidelity.
- Falsification: unacceptable as sole production authority.
- Reconsideration: may be allowed only as reviewed candidate input, never final eligibility authority.

#### Option 2B — Dynamic current discovery results

- Advantages: broad and automated.
- Disadvantages: current-state leakage and hindsight risk; unstable replay.
- Falsification: rejected as canonical final peer universe.

#### Option 2C — Versioned point-in-time candidate universe plus service-owned eligibility decisions

- Advantages: strict-known replay; preserves candidate and decision history; separates discovery from analytical eligibility.
- Disadvantages: additional record families and storage.
- Failure mode: candidate source gaps can produce sparse universes.

#### Option 2D — Hybrid registry + point-in-time discovery candidate union

- Advantages: broad recall and explicit operator knowledge.
- Disadvantages: complex provenance and deduplication; registry bias remains.
- Condition: every candidate source and timestamp must be persisted and eligibility remains service-owned.

### Axis 3 — Eligibility granularity

#### Option 3A — Economic-entity-level eligibility only

- Advantages: simple.
- Disadvantages: ignores representation-specific supply, liquidity, and quote coordinates.

#### Option 3B — Representation-level eligibility only

- Advantages: precise market coordinate.
- Disadvantages: may split one economic entity into misleading pseudo-peers.

#### Option 3C — Hierarchical economic-entity eligibility followed by representation compatibility

- Advantages: preserves both economic identity and market representation.
- Disadvantages: more complex decision graph.

### Axis 4 — Comparability policy

#### Option 4A — Hard-gate exact-match dimensions

Require exact match on all selected dimensions.

- Advantages: highest purity.
- Disadvantages: frequent unavailable outcomes and sparse sets.

#### Option 4B — Tiered compatibility classes

Define mandatory hard gates plus versioned soft-comparability tiers.

- Advantages: balances validity and coverage; explicit confidence effects.
- Disadvantages: requires careful methodology and calibration.

#### Option 4C — Continuous similarity score

- Advantages: flexible ranking of peers.
- Disadvantages: opaque weighting, hard to calibrate, risks becoming generic project scoring.
- Falsification: unacceptable unless every feature, weight, and calibration is separately governed and no ranking shortcut emerges.

#### Option 4D — Human-curated compatibility judgment

- Advantages: domain nuance.
- Disadvantages: non-deterministic and not replay-safe unless converted into immutable evidence-backed decisions.

### Axis 5 — Fundamental denominator and metric coordinate

All viable options preserve ADR 0021's fixed observed-market-value-to-one-predeclared-fundamental-denominator meaning and peer-relative log or percentage residual.

#### Option 5A — Attributable value-capture-flow denominator

- Advantages: closest to economic value transfer and existing value-capture authority.
- Disadvantages: numeric cross-peer coverage is currently unproven; accounting periods and claim attribution may differ.
- Failure mode: fees or revenue are mislabeled as attributable value capture.
- Reconsideration: unavailable unless every peer has strict-known, compatible numeric evidence.

#### Option 5B — Attributable protocol cash-flow denominator

- Advantages: economically interpretable where entitlement is direct.
- Disadvantages: likely sparse for tokenized networks and sensitive to accounting policy.
- Failure mode: treasury or protocol cash flow is not attributable to the valued claim.
- Reconsideration: only for an entity class with accepted claim-attribution evidence.

#### Option 5C — Revenue or fee denominator with an explicit value-capture adjustment policy

- Advantages: potentially broader evidence coverage.
- Disadvantages: the adjustment policy can become a second valuation model and must not manufacture attribution.
- Failure mode: unadjusted revenue or fees enter as a canonical denominator.
- Reconsideration: only when exact value-capture-rule references and adjustment semantics are fixed.

#### Rejected Option 5D — Multiple denominators or a composite metric in one assessment

- Advantages: broader descriptive coverage.
- Disadvantages: violates ADR 0021's one-predeclared-denominator contract or introduces hidden weighting.
- Reconsideration: requires an explicit ADR 0021 amendment and a new preparation scope.

### Axis 6 — Peer-set summary methodology

#### Option 6A — Arithmetic mean

- Advantages: simple.
- Disadvantages: highly outlier-sensitive; silent averaging risk.
- Falsification: rejected as default canonical method.

#### Option 6B — Median

- Advantages: robust and explainable.
- Disadvantages: discards distribution shape.

#### Option 6C — Quantile distribution

- Advantages: preserves dispersion and supports uncertainty-aware downstream use.
- Disadvantages: needs adequate peer count and explicit interpolation rules.

#### Option 6D — Winsorized or trimmed distribution

- Advantages: controls outliers.
- Disadvantages: threshold selection can conceal evidence.

#### Option 6E — Hierarchical/Bayesian estimate

- Advantages: handles sparse sets and uncertainty.
- Disadvantages: large calibration burden; assumptions may dominate evidence.

### Axis 7 — Sparse peer sets

#### Option 7A — Hard unavailable below fixed minimum count

- Advantages: clear and fail-closed.
- Disadvantages: low coverage.

#### Option 7B — Variable output with degraded confidence

- Advantages: more coverage.
- Disadvantages: risks treating one or two peers as meaningful comparison.

#### Option 7C — Tiered availability states

Examples: `AVAILABLE`, `LIMITED_PEER_SET`, `UNAVAILABLE_INSUFFICIENT_PEERS`, `UNAVAILABLE_CONFLICTED`.

- Advantages: truthful and expressive.
- Disadvantages: downstream interfaces must respect states.

### Axis 8 — Outlier policy

#### Option 8A — No removal; expose full distribution

- Advantages: no hidden exclusion.
- Disadvantages: extreme observations may dominate interpretation.

#### Option 8B — Rule-based exclusion with persisted decisions

- Advantages: auditable.
- Disadvantages: rule thresholds require evidence and versioning.

#### Option 8C — Robust statistics without peer exclusion

- Advantages: preserves all peers while reducing influence.
- Disadvantages: method complexity and calibration.

### Axis 9 — Persistence model

#### Option 9A — Persist only final comparative output

- Advantages: minimal storage.
- Disadvantages: insufficient audit and replay.
- Falsification: rejected.

#### Option 9B — Persist methodology, universe, eligibility decisions, observations, and output as separate immutable families

- Advantages: complete replay and audit.
- Disadvantages: more schema and migration work.

#### Option 9C — Persist one monolithic snapshot

- Advantages: simple retrieval.
- Disadvantages: poor correction granularity, duplication, and difficult lineage.

## Comparative Evaluation

The same criteria are applied to every option. `Pass` means compatible with the fixed authority and strict-known constraints; `Conditional` means viable only behind a declared evidence or calibration gate; `Fail` means non-viable without an explicit accepted-ADR amendment.

| Option | Authority | Replay | Evidence integrity | Sparse/failure safety | Operational/migration impact | Reversibility | Assessment |
|---|---|---|---|---|---|---|---|
| 1A dedicated service | Pass; fixed by ADR 0021 | Pass | Pass | Pass | Additive service/repository migration | High; disable entry point | Required baseline |
| 1B extend valuation | Fail; duplicate owner | Conditional | Conditional | Conditional | Lower initial cost, high coupling | Low | Rejected |
| 1C embed in Market Validation | Fail; composition owns a different concern | Fail | Fail | Fail | Hidden runtime coupling | Low | Rejected |
| 2A static registry only | Conditional candidate evidence only | Fail as final universe | Operator-biased | Conditional | Low cost, permanent curation burden | Medium | Rejected as sole source |
| 2B current discovery | Fail | Fail; future leakage | Fail | Fail | Low cost, non-replayable | Low | Rejected |
| 2C versioned point-in-time universe | Pass | Pass | Pass | Pass | New immutable snapshots and bounded queries | High | Preferred |
| 2D registry plus point-in-time union | Pass if every source is versioned | Pass | Conditional; dedup/source bias | Pass | Higher provenance and dedup cost | High | Viable successor, not first scope |
| 3A entity-only eligibility | Conditional | Pass | Misses representation coordinates | Conditional | Low | High | Rejected for first scope |
| 3B representation-only eligibility | Conditional | Pass | Risks double counting | Conditional | Low | High | Rejected for first scope |
| 3C hierarchical entity then representation | Pass | Pass | Pass | Pass | Medium decision-graph cost | High | Preferred |
| 4A exact hard gates | Pass | Pass | Pass | Pass but often unavailable | Low | High | Preferred first scope |
| 4B hard gates plus soft tiers | Pass if tiers are versioned/calibrated | Pass | Conditional | Conditional | High calibration/maintenance cost | Medium | Viable successor |
| 4C continuous similarity | Conditional authority risk | Conditional | Low without calibrated features | Conditional | High model governance cost | Medium | Not first scope |
| 4D human judgment | Fail unless converted to immutable evidence | Conditional | Low | Conditional | High operator burden | Low | Rejected as final authority |
| 5A attributable value-capture flow | Pass | Pass | Conditional on numeric coverage | Pass/fail closed | Medium acquisition cost | High | Preferred denominator class |
| 5B attributable protocol cash flow | Pass | Pass | Conditional on claim attribution | Pass/fail closed | Medium/high accounting cost | High | Viable for an eligible entity class |
| 5C adjusted revenue/fees | Conditional; adjustment must not create attribution | Pass | Higher evidence risk | Pass/fail closed | High methodology burden | Medium | Successor only |
| 5D multiple/composite denominator | Fail under one-denominator contract | Conditional | Hidden weighting risk | Conditional | High | Low | Rejected absent ADR 0021 amendment |
| 6A mean | Pass | Pass | Outlier-sensitive | Low | Low | High | Rejected as default |
| 6B median | Pass | Pass | Pass with minimum cohort | Conditional | Low | High | Preferred first reference |
| 6C quantiles | Pass | Pass | Pass with larger cohort/interpolation rule | Conditional | Medium | High | Emit only when calibrated minimum is met |
| 6D trimmed/winsorized | Conditional | Pass | Exclusion-threshold risk | Conditional | Medium | High | Successor only |
| 6E Bayesian estimate | Conditional | Pass | Prior/calibration risk | High | Very high model/validation cost | Medium | Not first scope |
| 7A hard unavailable below minimum | Pass | Pass | Pass | Highest | Low | High | Required activation behavior |
| 7B degraded confidence below minimum | Conditional | Pass | Risks false availability | Low | Low | High | Rejected for first scope |
| 7C tiered availability | Pass if `LIMITED` is non-authoritative | Pass | Pass | High | Medium downstream contract cost | High | Descriptive metadata only |
| 8A retain all; expose distribution | Pass | Pass | Highest transparency | Conditional interpretation risk | Low | High | Preferred first scope |
| 8B persisted rule exclusion | Conditional | Pass | Pass if rule/evidence are exact | Conditional | Medium | High | Viable successor |
| 8C robust statistic without exclusion | Pass | Pass | Pass | Conditional | Medium | High | Median is selected instance |
| 9A final output only | Fail | Fail | Fail | Fail | Low storage, no audit | Low | Rejected |
| 9B separate immutable families | Pass | Pass | Pass | Pass | Highest additive schema cost | High | Required |
| 9C monolithic snapshot | Conditional | Pass | Duplication/correction risk | Conditional | Medium | Medium | Rejected for first scope |

### Preferred coherent option bundle

The first methodology ADR should combine 1A, 2C, 3C, 4A, 5A, 6B with conditional 6C, 7A with descriptive 7C, 8A/8C, and 9B. This bundle:

- preserves every fixed ADR 0021 contract;
- uses one denominator and no hidden composite;
- fails closed below three eligible peers;
- emits a median reference and full ordered observations at three or more peers;
- emits quantiles only after the ADR fixes and historical evidence validates a higher minimum and interpolation policy;
- treats `LIMITED_PEER_SET` as descriptive metadata, never an available canonical scalar;
- keeps all observations and uses the median rather than silently deleting outliers;
- makes activation conditional on real strict-known cohort and calibration evidence.

## Falsification Results

### Leading architecture hypothesis

The preferred coherent bundle above is the leading hypothesis. The following option-specific falsification conditions prevent a favorable narrative from substituting for evidence:

| Viable option | Invalidation condition | Evidence/test required before implementation | Result at this revision |
|---|---|---|---|
| 2C point-in-time universe | candidate membership cannot be reconstructed without a current/latest lookup | boundary-cutoff and post-cutoff-exclusion tests over persisted candidates | Survives architecturally; implementation evidence pending |
| 2D hybrid union | provenance or dedup cannot distinguish operator registry from discovery source | duplicate-identity and source-bias replay tests | Viable successor only |
| 3C hierarchical eligibility | one economic entity can enter twice through representations | multi-representation adversarial test | Survives; required rejection rule identified |
| 4A exact hard gates | no real cohort can satisfy the gates | real historical cohort study for the first class | Survives as fail-closed first scope; activation pending |
| 4B soft tiers | tier weights cannot be historically calibrated without hindsight | strict-known calibration study | Not first scope |
| 5A value-capture denominator | numeric attributable flow is absent or accounting windows cannot be aligned | real target/peer evidence acquisition and period-coherence tests | Current Sky record fails the numeric-coverage gate; production remains unavailable |
| 5B protocol cash flow | cash flow cannot be bound to the valued claim | claim-attribution audit | Entity-class dependent; not selected first |
| 5C adjusted revenue/fees | adjustment invents value capture or duplicates valuation authority | transitive provenance and prohibited-input tests | Successor only |
| 6B median | fewer than three eligible peers exist or deterministic ordering changes the result | permutation and boundary-cardinality tests | Survives with hard minimum three |
| 6C quantiles | sample size/interpolation cannot be calibrated | historical stability study | Deferred until an evidence-backed higher minimum exists |
| 7A hard unavailable | downstream code treats unavailable as neutral/zero | adapter and missingness tests | Survives; downstream implementation remains separate |
| 7C tiered metadata | `LIMITED` is consumed as authoritative availability | contract tests | Survives only as descriptive metadata |
| 8A/8C retain all + median | genuine extremes make the reference unstable beyond declared tolerance | outlier/adversarial replay corpus | Survives first scope; distribution remains visible |
| 8B rule exclusion | exclusion threshold cannot distinguish error from genuine structure | evidence-backed threshold study | Deferred |
| 9B immutable families | correction lineage cannot reproduce exact prior cohort/output | append-only, branching-rejection, and byte-identical replay tests | Survives and remains required |

### Counterexamples and failure tests

1. **Only one eligible peer exists**
   - A valid architecture must not manufacture a distribution.
   - Required outcome: unavailable or explicitly limited state.

2. **Peers share sector labels but not value-capture economics**
   - Sector-only comparison is insufficient.
   - Required outcome: rejection or lower compatibility tier explicitly justified.

3. **Same economic entity has multiple representations**
   - Treating representations as independent peers double-counts one entity.
   - Required outcome: hierarchical identity handling.

4. **Current peer appears eligible but was not eligible at historical cutoff**
   - Current-list selection leaks future information.
   - Required outcome: exclusion under strict-known replay.

5. **Compatible metric values use different supply bases**
   - Silent conversion creates false comparability.
   - Required outcome: reject or use an explicitly authorized conversion record.

6. **Outlier is genuine, not erroneous**
   - Automatic removal may erase meaningful market structure.
   - Required outcome: robust statistics or persisted exclusion rationale; no silent trimming.

7. **Peer evidence is conflicted**
   - Averaging conflicting records is prohibited.
   - Required outcome: peer or metric unavailable until conflict resolution.

8. **Soft similarity score becomes a ranking score**
   - This would blur authority.
   - Required outcome: similarity, if ever used, remains an eligibility aid with no downstream ranking authority.

9. **Comparative output is directly compared with spot price**
   - That is Mispricing.
   - Required outcome: structurally impossible within this authority.

10. **Post-cutoff correction changes historical peer set**
    - Replay must select the version known at cutoff, not latest corrected state.

### Falsification conclusion

No option survives unless peer candidacy, eligibility, methodology, observations, and outputs are independently versioned and strict-known replayable. Static current lists, implicit similarity, monolithic outputs, silent averaging, and direct downstream scoring fail current constitutional and ADR constraints.

## Proposed Record-Family Inventory

ADR 0021 already requires `PeerUniversePolicyRecord`, `PeerUniverseSnapshot`, and `ComparativeValuationAssessmentRecord`. The methodology ADR must preserve those identities. The names below distinguish fixed records from provisional supporting records and do not authorize implementation:

1. `PeerUniversePolicyRecord` (**fixed by ADR 0021; carries methodology policy**)
   - methodology identity/version;
   - supported entity class;
   - mandatory/soft comparability dimensions;
   - permitted metric families;
   - peer minimums;
   - aggregation/distribution policy;
   - outlier policy;
   - confidence policy;
   - raw residual sign convention and normalization-policy/calibration identity;
   - correlation group and combined-contribution cap policy required by ADR 0021;
   - effective and recorded timestamps.

2. `PeerUniverseSnapshot` (**fixed by ADR 0021**)
   - target identity;
   - candidate peer identities;
   - candidate source/provenance;
   - point-in-time cutoff;
   - methodology reference.

3. `PeerEligibilityDecisionRecord` (**provisional supporting family**)
   - target and candidate identity;
   - included/excluded/indeterminate;
   - dimension-level decisions;
   - evidence references;
   - confidence and conflict state;
   - correction lineage.

4. `ComparativeMetricObservationRecord` (**provisional supporting family**)
   - peer identity;
   - metric family;
   - numerator/denominator identity;
   - currency, horizon, supply basis;
   - exact source records;
   - availability/conflict state.

5. `ComparativeValuationAssessmentRecord` (**fixed by ADR 0021**)
   - target identity;
   - methodology and peer-universe references;
   - included peer decision IDs;
   - the predeclared denominator, target/peer multiples, reference statistic, and raw signed residual required by ADR 0021;
   - full ordered peer observations and any permitted quantiles;
   - dispersion;
   - raw log or percentage residual, normalized value/status when calibrated, and correlation group;
   - confidence decomposition;
   - availability state;
   - exact provenance;
   - correction lineage.

The two provisional supporting families are viable only if the future ADR demonstrates that exact inclusion/exclusion and metric-observation lineage cannot be represented without them. They may not replace or rename ADR 0021's fixed records, duplicate native evidence, or become independent analytical owners.

## Authority and Dependency Map

```text
Discovery / registered candidate sources
        |
        v
Point-in-time Peer Candidate Evidence
        |
        v
CanonicalComparativeValuationService
  - validates target identity
  - constructs/loads peer universe
  - decides eligibility
  - validates metric coordinates
  - produces comparative distributions
        |
        v
Comparative Valuation Repository
        |
        v
Canonical Persistence

Downstream boundary:
Comparative Valuation Assessment
        |
        v
Future Mispricing Authority only after separate Accepted ADR

No direct path to:
- Market Validation scoring
- ranking
- Opportunity Assessment
- portfolio advice
```

## Replay, Correction, Conflict, Missingness, and Confidence Contracts

### Strict-known replay

At cutoff `T`, every selected methodology, candidate, eligibility decision, metric observation, correction, and output must have been known at or before `T`. Latest-state fallback is prohibited.

### Correction

- corrections are append-only;
- every correction references exactly one superseded logical predecessor;
- branching correction lineage is rejected;
- historical replay selects the version known at cutoff;
- corrected peer decisions may change later assessments but never rewrite prior history.

### Conflict

- unresolved evidence conflict blocks the affected peer/metric;
- no averaging across conflicting records;
- conflict state is persisted and replayable;
- removing one conflicted peer must not silently change methodology requirements.

### Missingness

Required explicit states include at least:

- `AVAILABLE`;
- `LIMITED_PEER_SET`;
- `UNAVAILABLE_NO_CANDIDATES`;
- `UNAVAILABLE_INSUFFICIENT_ELIGIBLE_PEERS`;
- `UNAVAILABLE_INCOMPATIBLE_COORDINATES`;
- `UNAVAILABLE_STALE_INPUTS`;
- `UNAVAILABLE_CONFLICTED_INPUTS`;
- `UNAVAILABLE_UNSUPPORTED_ENTITY_CLASS`.

### Confidence

Confidence must be decomposed, not represented as a single unexplained number. Candidate components:

- peer-universe coverage;
- eligibility evidence confidence;
- metric evidence confidence;
- coordinate compatibility confidence;
- peer-set cardinality/dispersion confidence;
- methodology calibration confidence.

Overall confidence must not exceed the weakest mandatory component unless a future ADR provides a justified alternative.

## Risks and Open Questions

### Material risks

| Risk | Category | Likelihood | Impact | Mitigation | Residual uncertainty |
|---|---|---:|---:|---|---|
| Broad labels admit false peers | Evidence/correctness | High | High | Exact hard gates, dimension-level decisions, fail closed | Real cohort coverage is unknown |
| Strict gates yield sparse sets | Availability | High | Medium | Minimum-three hard gate and explicit unavailable state | Production coverage may remain zero |
| Similarity/composites become hidden scoring | Authority/governance | Medium | High | Exclude from first scope; require explicit later ADR | Future pressure for coverage |
| Multiple representations double-count an entity | Identity | Medium | High | Entity-first eligibility and deterministic representation selection | Cross-chain cases remain future scope |
| Denominator is not attributable to the valued claim | Evidence/authority | High | High | Exact native evidence and value-capture references; no assembled evidence | Numeric coverage remains unproven |
| Current registry or discovery leaks future membership | Replay | Medium | High | Persist candidate source, effective/recorded/known time; no live replay calls | Provider historical completeness |
| Genuine outliers are concealed | Methodology | Medium | Medium | Retain all observations; median first; persist any later exclusion | Extreme small cohorts remain unstable |
| Extra supporting records duplicate authority | Architecture | Medium | High | Keep ADR 0021 families canonical; justify each supporting family | Schema shape remains an ADR decision |
| Confidence appears calibrated without outcomes | Evidence | High | High | Expose components; unknown calibration stays unknown; cap by weakest mandatory component | Suitable historical outcomes may not exist |
| Partial migration exposes false availability | Operations/migration | Low | High | Additive schema, transactional writes, entry point disabled until preflight | Deployment tooling is future implementation work |

### Resolved preparation questions and remaining ADR/activation decisions

| Question | Classification | Resolution or required action | Owner | Status |
|---|---|---|---|---|
| First entity class | Fixed constraint | Use ADR 0021's same supported entity class as Canonical Valuation; Sky is the pilot identity but not a demonstrated cohort. | Accepted ADR baseline | Resolved |
| Sole owner | Fixed constraint | `CanonicalComparativeValuationService`. | ADR 0021 | Resolved |
| Semantic metric | Fixed constraint | Observed market value divided by one predeclared attributable fundamental denominator, plus peer-relative log or percentage residual. | ADR 0021 | Resolved |
| First denominator class | ADR decision | Prefer attributable value-capture flow; ADR must specify exact eligible evidence types and period/claim coherence. | Future ADR | Prepared |
| Minimum cohort | ADR decision | Hard minimum three for median availability; quantiles require a separately evidenced higher minimum and interpolation policy. | Future ADR | Prepared |
| Hard versus soft comparability | ADR decision | Exact hard gates for first scope; soft tiers deferred to a successor methodology. | Future ADR | Prepared |
| Liquidity/market quality | Boundary decision | May reduce evidence confidence or availability only when it affects observation reliability; may not become peer favorability or Mispricing. | Future ADR | Prepared |
| Denominator transformation | Authority decision | Only exact, versioned unit/currency/period transformations over native authoritative evidence; no new value attribution and no ADR 0025 assembly reuse. | Future ADR | Prepared |
| Historical validation set | Activation gate | Acquire a real strict-known cohort and preserve unavailable state until it exists. | Implementation/evidence issue | Non-blocking for ADR drafting; blocks activation |
| Limited peer output | ADR decision | Descriptive `LIMITED_PEER_SET` metadata is permitted, but canonical assessment remains unavailable below minimum. | Future ADR | Prepared |
| Confidence calibration | Activation gate | Structural confidence components may be exposed; empirical calibration remains unknown until strict historical evidence exists. | Implementation/evidence issue | Non-blocking for ADR drafting; blocks calibrated availability |

## Constitution Check

| Constitutional rule | Application | Determination |
|---|---|---|
| Rule 2 — Evidence Authority | Real cohort and denominator evidence remain missing; unavailable is required until acquired. | Compliant |
| Rule 3 — Deterministic Intelligence | Every candidate, eligibility decision, observation, methodology, correction, and output is strict-known and replayable. | Compliant |
| Rule 4 — Architectural Integrity | ADR 0021's owner and semantic contract are fixed rather than reopened. | Compliant |
| Rule 5 — Single Source of Truth | Comparative Valuation owns peer-relative assessment; native evidence, Valuation, Mispricing, and Market Validation retain their owners. | Compliant |
| Rule 6 — Explainability | Inclusion/exclusion reasons, denominator, distribution, residual, confidence, and provenance are explicit. | Compliant |
| Rule 7 — Long-Term Evolution | Versioned methods and additive immutable records preserve correction and supersession. | Compliant |
| Rule 8 — Governance | ADR drafting and implementation remain gated by independent audit and a later accepted ADR. | Compliant |

No constitutional conflict is currently identified.

## Governance Check

| Authority | Requirement | Determination |
|---|---|---|
| Development Governance | Planning and independent architecture review precede ADR implementation. | Compliant; this PR is documentation-only. |
| Preparation Guide | Evidence, assumptions, normalized options, falsification, risks, readiness, and traceability are required. | Compliant after version 1.1 remediation. |
| ADR 0021 | Preserve sole owner, one-denominator comparative meaning, record baseline, residual, calibration, correlation, replay, and missingness. | Compliant; treated as fixed constraints. |
| ADR 0024 | Do not apply `valuation`'s non-scalar amendment to `comparative_valuation`. | Compliant; comparative favorability/calibration remains binding. |
| ADR 0025 | Do not silently consume or recreate assembled evidence. | Compliant; native evidence only in this scope. |
| ADR 0005/0009/0010/0016/0020 | Preserve identity, service authority, runtime composition, and strict-known boundaries. | Compliant. |

No known accepted-ADR conflict is introduced by this preparation record.

## Architecture Impact

Potential future implementation would introduce:

- an implementation of the already accepted `CanonicalComparativeValuationService` owner;
- new immutable record families;
- new persistence and replay surfaces;
- peer-universe and eligibility contracts;
- a new upstream dependency for future Mispricing.

It must not modify current Canonical Valuation semantics or Market Validation composition.

## Evidence Impact

Future implementation would require:

- explicit point-in-time peer candidate evidence;
- persisted dimension-level eligibility evidence;
- canonical metric inputs with coherent coordinates;
- historical candidate and correction evidence;
- empirical evidence for peer minimums, outlier policy, and confidence calibration.

The current repository evidence is sufficient to define the methodology ADR and its fail-closed activation gates. It is not sufficient to claim production availability, calibrate quantiles or normalization, or persist an available assessment. Those limitations are carried explicitly into the proposed ADR scope.

## Recommended Implementation Sequence

No implementation is authorized by this ADPR. A future accepted ADR should sequence work approximately as follows:

1. methodology and peer-policy ADR for ADR 0021's same supported entity class;
2. real point-in-time cohort and native-denominator evidence acquisition;
3. immutable peer policy and peer-universe records;
4. eligibility decision authority;
5. comparative metric observation records;
6. comparative assessment service and repository;
7. strict-known replay and correction tests;
8. real evidence-backed operational validation;
9. independent post-merge audit;
10. only afterward, separate Mispricing ADPR.

## Rejected Shortcuts

- reusing Canonical Valuation as the comparative owner;
- selecting peers from current rankings or market-cap lists;
- using provider-defined peer scores;
- using sector label alone as comparability;
- averaging incompatible metrics;
- treating one peer as a valid distribution;
- manufacturing favorable directionality or `[0,1]` normalization without ADR 0021's required strict historical calibration;
- comparing comparative output to market price inside this authority;
- wiring output into Market Validation or Opportunity Assessment;
- using latest corrected data for historical replay.

## Quality Assessment

| Dimension | Rating | Evidence and rationale | Blocking limitation |
|---|---|---|---|
| Problem correctness | GOOD | Distinguishes the accepted owner from the unresolved methodology/activation problem. | None |
| Scope completeness | GOOD | In/out scope, ADR scope, activation gates, and downstream exclusions are explicit. | None |
| Canonical consistency | GOOD | ADR 0021/0024/0025 amendments and fixed semantics are represented directly. | None |
| Evidence integrity | ACCEPTABLE | Repository and accepted-authority evidence is exact; real cohort evidence is truthfully absent. | Blocks activation, not ADR drafting |
| Assumption discipline | GOOD | Assumptions have confidence, falsification, and consequences. | None |
| Option completeness | GOOD | All material candidate-source, identity, compatibility, denominator, statistic, sparsity, outlier, and persistence options are retained. | None |
| Comparative fairness | GOOD | One normalized criterion set is applied to every option. | None |
| Falsifiability | GOOD | Every viable option has an invalidation condition and required evidence/test. | None |
| Authority and ownership clarity | GOOD | ADR 0021's service owner is fixed; prohibited overlaps are explicit. | None |
| Persistence and replay quality | GOOD | Immutable records, correction, ordering, cutoff, and no-current-fallback contracts are explicit. | None |
| Evidence and provenance quality | ACCEPTABLE | Exact lineage requirements exist; qualifying multi-entity evidence is not yet available. | Blocks activation, not ADR drafting |
| Operational quality | ACCEPTABLE | Bounded construction, no live replay, fail-closed migration, rollback, and observability requirements are defined. | Implementation detail remains for the ADR/plan |
| Implementation and migration impact | ACCEPTABLE | Additive records/service, disabled entry point, rollback, and compatibility are described. | None |
| Testability and validation | GOOD | Boundary, leakage, ordering, cardinality, lineage, and calibration gates are derivable. | None |
| Maintainability and extensibility | GOOD | Narrow first scope, versioned successor paths, and no speculative composite are explicit. | None |
| Risk quality | GOOD | Material risks include likelihood, impact, mitigation, and residual uncertainty. | None |
| Traceability | GOOD | Issues, ADPR, PR, future audit/ADR, and absent artifacts are explicit and indexed. | Independent verdict pending |

## Readiness Determination

### Architecture readiness

- Outcome: `READY`
- Rationale: the fixed authority baseline, unresolved methodology axes, preferred coherent bundle, activation gates, risks, and validation obligations are explicit.
- Missing evidence: a real qualifying cohort and calibration evidence; both are recorded as fail-closed activation gates.
- Unresolved conflicts: none.

### ADR readiness

- Outcome: `READY_FOR_ADR`
- Proposed ADR title: Canonical Comparative Valuation Methodology.
- Proposed ADR scope: first methodology for ADR 0021's same supported entity class, one native attributable denominator, point-in-time peer policy, median reference, raw signed residual, confidence, calibration, persistence, and fail-closed activation.
- Decisions the ADR must fix: exact eligible evidence types; hard comparability fields; minimum-three rule; deterministic ordering/ties; median/residual formula; quantile deferral; outlier retention; confidence components; methodology and record fields; strict-known tests; calibration and activation gates.
- Matters the ADR must leave open: soft similarity tiers, composite/multiple denominators, Bayesian estimates, cross-chain entity classes, Assembled Fundamental Evidence eligibility, Mispricing, Market Validation composition, and Opportunity Assessment.

## Final Recommendation

Draft a narrow ADR using the preferred coherent option bundle. The ADR must authorize architecture and fail-closed contracts, not production availability. Implementation may begin only after that ADR is accepted. The input must remain unavailable until a real strict-known cohort, numeric native denominator coverage, historical leakage validation, and the required monotonic normalization calibration all exist and pass independent review.

## Decision History

| Date | State | Change | Author or reviewer |
|---|---|---|---|
| 2026-07-31 | IN_RESEARCH | Initial decision-space preparation. | ChatGPT |
| 2026-07-31 | READY_FOR_REVIEW | Reconciled accepted ADR semantics; normalized options; completed evidence, falsification, risk, quality, readiness, and traceability records. | Codex remediation for Issue #156 |

## Traceability

- Epic: Issue #135
- Issue: Issue #156
- Preparation working document: this record
- Checklist review: completed against `docs/checklists/ARCHITECTURE_DECISION_PREPARATION_CHECKLIST.md`
- ADPR: ADPR-0003
- Pull Request: PR #157
- Reviewed commit: to be recorded by the independent reviewer
- Independent Architecture Audit: pending on the final PR head
- Comparative Valuation ADR: not yet created
- Implementation plan and PRs: not authorized
- Merge commit: not yet created
- Release: not yet assigned

## Completion Criterion for This ADPR

This record is complete only when an independent architecture audit on the exact final revision determines that the Comparative Valuation decision space is sufficiently complete and evidence-grounded to proceed to a separate ADR without inventing authority, peer-selection, methodology, replay, correction, confidence, calibration, or downstream-composition rules during implementation.
