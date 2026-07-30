# ADPR-0003 — Canonical Comparative Valuation

## Metadata

- ADPR ID: `ADPR-0003`
- Status: `IN_RESEARCH`
- Version: 1.0
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

Canonical Comparative Valuation is the next separately owned analytical authority after Canonical Valuation. Its purpose is to determine how a target economic entity compares with a point-in-time, evidence-backed, methodologically compatible peer set without collapsing into canonical valuation, mispricing, asymmetry, Market Validation scoring, Opportunity Assessment, or generic current-project ranking.

This preparation record defines the problem, validates the need for a new authority, identifies governing constraints, inventories evidence, enumerates materially distinct architectural and methodological options, compares and falsifies them, and records the conditions required before a formal ADR can be drafted.

This record does not select or implement a comparative-valuation methodology. It does not create code, activate scoring, produce favorable directionality, or authorize downstream composition.

Self-assessment: `NEEDS_REVISION`. The preparation is structurally complete enough for independent review, but several evidence-dependent decisions remain open, particularly the first supported entity class, admissible peer metrics, and minimum peer-set rules.

## Problem Statement

### Current condition

Hunter now has canonical structured valuation authority and separately owned evidence foundations, but Comparative Valuation remains explicitly unavailable. No accepted authority currently owns:

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

A future ADR must determine:

1. the sole owner and write authority;
2. the peer-universe source and point-in-time eligibility contract;
3. the minimum comparability dimensions;
4. the permitted comparative metrics and coordinate rules;
5. the primary aggregation/distribution methodology;
6. outlier, sparse-peer, stale, conflict, confidence, and correction behavior;
7. the immutable record families and persistence contract;
8. the downstream boundary with Mispricing and all scoring/ranking systems.

### In scope

- architecture preparation for Canonical Comparative Valuation;
- exhaustive decision-space preparation;
- peer authority and eligibility;
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

### Accepted architecture constraints

- ADR 0005: economic entity and representation identity must remain explicit.
- ADR 0009: Provider → Service → Repository → Persistence.
- ADR 0010: validation and persistence authorization are service-owned.
- ADR 0016: Market Validation remains the sole canonical production composition runtime and must not be silently extended.
- ADR 0020: strict-known replay, truthful missingness, no aliases or current fallback.
- ADR 0021: Comparative Valuation is a distinct owner and may not be collapsed into valuation, mispricing, or asymmetry.
- ADR 0024: Canonical Valuation remains structured and non-scalar; Comparative Valuation must not invent favorable directionality merely to satisfy downstream scalar interfaces.

### Fixed prohibitions

- no current-list peer selection;
- no hindsight-selected peer eligibility;
- no mixing economic entities and token representations without explicit coordinate identity;
- no silent currency, horizon, supply-basis, or denominator conversion;
- no provider-supplied analytical score accepted as canonical Comparative Valuation;
- no averaging of incompatible or conflicted peers;
- no fallback to generic sector labels when material comparability dimensions are missing;
- no calculation of market-versus-fair-value gap;
- no scoring, ranking, weighting, or Opportunity Assessment activation.

## Evidence Inventory and Quality Assessment

| Evidence | Authority | Relevance | Limitations |
|---|---|---|---|
| Project Constitution and Principles | Constitutional | Highest | Defines constraints, not methodology |
| Development Governance | Process authority | Highest | Governs lifecycle, not analytical selection |
| Architecture Decision Preparation Guide | Preparation authority | Highest | Defines required preparation outputs |
| Canonical Architecture Map | Canonical architecture | Highest | Defines ownership/dependency direction, not detailed peer policy |
| Canonical Runtime Architecture | Canonical documentation | High | May require coherence updates; does not itself authorize unavailable runtime |
| ADR 0005 | Accepted ADR | Highest | Identity boundary only |
| ADR 0009 / 0010 | Accepted ADRs | Highest | Authority/persistence boundaries only |
| ADR 0016 | Accepted ADR | Highest | Downstream composition boundary only |
| ADR 0020 | Accepted ADR | Highest | Replay/missingness contract only |
| ADR 0021 | Accepted ADR | Highest | Separates Comparative Valuation authority; methodology not defined |
| ADR 0024 | Accepted ADR | Highest | Protects non-scalar valuation semantics and downstream boundary |
| Issue #135 | Repository audit/control | High | Identifies the gap and required scope; not an ADR |
| Issue #156 | Work authorization | High | Defines this ADPR scope; not architectural authority |
| Existing valuation/value-capture/market-facts records | Implementation evidence | High | Inputs may be reusable; do not define comparative methodology |

## Assumptions

The following are assumptions, not evidence:

1. The first supported Comparative Valuation entity class may be the same as Canonical Valuation's first supported class.
2. More than one valid peer will be available for the first production use case.
3. At least one economically meaningful comparative metric can be derived without violating existing evidence authority.
4. Comparative outputs are likely to be structured distributions rather than a single favorable scalar.
5. A future Mispricing authority will consume Comparative Valuation outputs, but the exact interface is not yet authorized.

Each assumption must be validated or removed before ADR readiness.

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

### Axis 1 — Analytical ownership

#### Option 1A — Dedicated `CanonicalComparativeValuationService`

A new sole write authority consumes canonical facts, valuation records, peer methodology, and peer-universe records.

- Advantages: strongest owner separation; mirrors existing canonical service architecture; easy to audit.
- Disadvantages: new package and persistence surface.
- Failure mode: duplicate authority if peer selection is split across unrelated services.
- Reconsideration condition: only if an existing accepted owner is explicitly amended to own this responsibility.

#### Option 1B — Extend `CanonicalValuationService`

- Advantages: reuses current valuation infrastructure.
- Disadvantages: collapses two owners prohibited by ADR 0021; couples absolute and relative methodologies.
- Falsification: rejected by current ownership constraints.
- Reconsideration condition: requires explicit ADR 0021 amendment.

#### Option 1C — Embed in Market Validation

- Advantages: direct access to downstream composition.
- Disadvantages: confuses evidence analysis with scoring/composition; invites scalar shortcuts.
- Falsification: rejected by ADR 0016/0021/0024 boundaries.

**Current viability:** only 1A remains viable under accepted authority.

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

### Axis 5 — Metric coordinate

#### Option 5A — Single permitted metric family for first entity class

- Advantages: narrow, auditable first implementation.
- Disadvantages: limited coverage.

#### Option 5B — Multiple independent metric families with no aggregation

- Advantages: preserves multidimensional evidence.
- Disadvantages: downstream consumer complexity.

#### Option 5C — Versioned composite metric

- Advantages: concise output.
- Disadvantages: weighting/calibration risk; may become hidden scoring.

#### Option 5D — Distributional comparison across compatible raw metrics

- Advantages: avoids premature scalar collapse; preserves uncertainty and metric-specific provenance.
- Disadvantages: more complex records and interpretation.

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

| Option family | Authority | Replay | Explainability | Sparse-data safety | Complexity | Current assessment |
|---|---:|---:|---:|---:|---:|---|
| Dedicated service | High | High | High | High | Medium | Viable |
| Extend valuation | Low | Medium | Low | Medium | Low | Rejected by ownership |
| Market Validation embedding | Low | Low | Low | Low | Medium | Rejected |
| Versioned point-in-time universe | High | High | High | High | Medium | Viable |
| Hierarchical entity/representation eligibility | High | High | High | High | Medium | Viable |
| Exact-match comparability | High | High | High | High | Low | Viable but may be too sparse |
| Tiered compatibility | High | High | High | Medium | High | Viable pending evidence |
| Continuous similarity | Medium | Medium | Low | Medium | High | High risk |
| Single first metric family | High | High | High | High | Low | Viable first scope |
| Multiple unaggregated metrics | High | High | High | High | Medium | Viable |
| Composite metric | Medium | High | Medium | Medium | High | High risk |
| Median | High | High | High | Medium | Low | Viable |
| Quantile distribution | High | High | High | Low/Medium | Medium | Viable with minimum count |
| Bayesian sparse estimate | Medium | High | Medium | High | Very high | Not ready |
| Separate immutable record families | High | High | High | High | High | Required for canonical use |

## Falsification Results

### Leading architecture hypothesis

A dedicated service using a versioned point-in-time candidate universe, hierarchical entity/representation eligibility, hard comparability gates plus possibly versioned tiers, one narrow first metric family, and separate immutable record families appears most compatible with current governance.

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

Names are provisional and do not authorize implementation:

1. `ComparativeValuationMethodologySnapshot`
   - methodology identity/version;
   - supported entity class;
   - mandatory/soft comparability dimensions;
   - permitted metric families;
   - peer minimums;
   - aggregation/distribution policy;
   - outlier policy;
   - confidence policy;
   - effective and recorded timestamps.

2. `PeerUniverseSnapshot`
   - target identity;
   - candidate peer identities;
   - candidate source/provenance;
   - point-in-time cutoff;
   - methodology reference.

3. `PeerEligibilityDecisionRecord`
   - target and candidate identity;
   - included/excluded/indeterminate;
   - dimension-level decisions;
   - evidence references;
   - confidence and conflict state;
   - correction lineage.

4. `ComparativeMetricObservationRecord`
   - peer identity;
   - metric family;
   - numerator/denominator identity;
   - currency, horizon, supply basis;
   - exact source records;
   - availability/conflict state.

5. `ComparativeValuationAssessmentRecord`
   - target identity;
   - methodology and peer-universe references;
   - included peer decision IDs;
   - metric-specific distributions/quantiles;
   - dispersion;
   - confidence decomposition;
   - availability state;
   - exact provenance;
   - correction lineage.

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

1. **False peers:** broad labels may create plausible but economically invalid comparisons.
2. **Sparse sets:** strict comparability may leave most assets unavailable.
3. **Hidden scoring:** similarity or composite metrics may become unauthorized ranking.
4. **Double counting:** multiple token representations may inflate peer count.
5. **Metric leakage:** market-derived metrics may blur into Mispricing.
6. **Operator bias:** curated peer registries may encode hindsight.
7. **Outlier concealment:** trimming may suppress real structural differences.
8. **Schema proliferation:** too many record families may raise operational cost.
9. **Uncalibrated confidence:** confidence values may appear precise without empirical support.

### Open questions blocking ADR readiness

1. What is the first supported Comparative Valuation entity class?
2. Which real entities form a plausible point-in-time candidate universe for that class?
3. Which metric family has canonical evidence authority today?
4. What minimum peer count is defensible for median and quantile outputs?
5. Which dimensions are hard gates versus soft tiers?
6. Are liquidity and market-quality dimensions eligibility inputs here or reserved for Mispricing?
7. Can any denominator transformation reuse existing canonical records without creating new analytical authority?
8. What independent historical dataset can validate peer selection without hindsight?
9. Should limited peer sets emit structured descriptive output or remain fully unavailable?
10. Which confidence components can be empirically calibrated now versus remain explicit unknowns?

## Constitution Check

- Evidence remains explicit and provenance-bearing: compliant.
- Missing and conflicted states remain explicit: compliant.
- Determinism and strict-known replay are required: compliant.
- No fabricated fallback is allowed: compliant.
- No unsupported deterministic price target is introduced: compliant.

No constitutional conflict is currently identified.

## Governance Check

- Preparation precedes ADR and implementation: compliant.
- Existing ownership boundaries are preserved: compliant.
- Comparative Valuation remains separate from valuation, Mispricing, Asymmetry, and Market Validation: compliant.
- Options and rejected paths remain visible: compliant.
- Independent audit remains required: compliant.

No known accepted-ADR conflict is introduced by this preparation record.

## Architecture Impact

Potential future implementation would introduce:

- a new canonical analytical owner;
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

The current repository evidence is sufficient to prove the architectural gap, but not sufficient to select all methodology details.

## Recommended Implementation Sequence

No implementation is authorized by this ADPR. A future accepted ADR should sequence work approximately as follows:

1. first supported entity-class and metric evidence study;
2. methodology and peer-policy ADR;
3. immutable methodology and peer-universe records;
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
- converting Comparative Valuation directly into favorable directionality;
- comparing comparative output to market price inside this authority;
- wiring output into Market Validation or Opportunity Assessment;
- using latest corrected data for historical replay.

## Readiness Determination

### Architecture readiness

`READY_FOR_REVIEW`

The problem, owner separation, major dimensions, option families, prohibited shortcuts, record inventory, replay contract, and downstream boundary are sufficiently specified for independent architecture review.

### ADR readiness

`NEEDS_REVISION`

ADR drafting remains blocked until the open evidence questions are resolved, particularly:

- first supported entity class;
- real peer-universe evidence;
- first permitted metric family;
- minimum peer-set rules;
- hard versus soft comparability dimensions;
- empirically defensible confidence and outlier policy.

## Traceability

```text
Issue #135
  -> Issue #156
  -> ADPR-0003 (this record)
  -> Independent Architecture Audit (not yet performed)
  -> Comparative Valuation ADR (not yet created)
  -> Implementation Issue/PRs (not authorized)
```

## Completion Criterion for This ADPR

This record is complete only when an independent architecture review determines that the Comparative Valuation decision space is sufficiently complete and evidence-grounded to proceed to a separate ADR without inventing authority, peer-selection, methodology, replay, correction, confidence, or downstream-composition rules during implementation.
