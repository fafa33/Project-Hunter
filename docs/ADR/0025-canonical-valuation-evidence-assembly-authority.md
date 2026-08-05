# ADR 0025: Canonical Valuation Evidence Assembly Authority

## Status

Accepted. Amends ADR 0021 (Evidence and record-family boundaries, Layer 2 only) and ADR 0022 (Valuation inputs, Persistence requirements, Prohibited methodologies clarification only) exactly as stated in "Exact amendment to ADR 0021" and "Exact amendment to ADR 0022" below — see this ADR for the complete authoritative wording; ADR 0021 and ADR 0022 themselves carry only short pointer notes at the affected passages, consistent with the pattern ADR 0023 established for ADR 0022. ADR 0020 is reaffirmed and relied upon without any change to its text; this ADR is deliberately not accompanied by an ADR 0020 edit (see "Compatibility With Accepted ADRs" below for why none is needed). All other sections of ADR 0020, ADR 0021, ADR 0022, ADR 0023, and ADR 0024 are unchanged.

## Context

ADR 0021 and ADR 0022 built a narrow, auditable path from observed facts to a first canonical fair-value methodology, and ADR 0024 corrected that methodology's scalar semantics. That path assumes exactly one native `FundamentalEvidenceRecord` per accounting window: ADR 0022's Scope criterion 4 requires "an accounting period fully contained within the valuation horizon's lookback window," and `CanonicalValuationService.estimate_fair_value` (Milestone 3) hard-rejects any evidence record whose `accounting_period_days` does not exactly equal `methodology.horizon_days` (365 by default) — see the explicit comment in `src/hunter/valuation/service.py`: "an arbitrary-period amount cannot be treated as a fixed 365-day flow without an authorized annualization policy."

That assumption does not match how real protocols actually disclose attributable economic evidence. Value-capture disclosures commonly arrive continuously (streamed fee accrual), daily, weekly, monthly, quarterly, epoch-based (governance- or protocol-defined periods that do not align to calendar boundaries), or event-driven (individual buyback/burn/distribution transactions). A protocol that discloses monthly fee distributions, or that only exposes an on-chain transaction log of individual buyback events, can never satisfy Scope criterion 4 today even though twelve consecutive, non-overlapping, gap-free monthly disclosures — or a complete transaction log spanning the exact 365-day window — would jointly and losslessly represent exactly the same observed economic interval a single native 365-day disclosure would represent. ADR 0021 itself does not require a 365-day native disclosure; it requires only that "every transitive record must be strict-known at cutoff" and that the record's "accounting period" be established. The 365-day-single-record assumption is Milestone 3's implementation choice inside `CanonicalValuationService`, made because ADR 0022 named no other lawful way to satisfy the horizon requirement — not a limit ADR 0021 itself imposes.

No accepted authority currently owns closing this gap. `CanonicalValuationService` is, and must remain, a pure consumer of already-authoritative evidence (ADR 0022's "Valuation inputs" section: "No other record family... may inform a fair-value estimate. This list is exhaustive, not illustrative."); it has no authority to combine multiple `FundamentalEvidenceRecord`s itself without becoming a second, undocumented evidence-authority decision made inside valuation arithmetic — exactly the failure mode ADR 0020 and ADR 0021 exist to close off. Providers acquire and normalize observations but never validate authority, resolve identity, or make composition decisions (ADR 0009). `hunter.value_capture`'s existing services validate and persist individual `FundamentalEvidenceRecord`s but have no mandate to combine several of them into one derived interval — doing so inside that service would silently redefine what "one accepted, non-conflicted `FundamentalEvidenceRecord`" (ADR 0022 Scope criterion 4) means, without an ADR authorizing the redefinition.

This is an architecture authority gap, not a defect in `CanonicalValuationService` and not a defect in any specific protocol's registered evidence. `CanonicalValuationService`'s Milestone 3 implementation correctly refuses to guess at composition today, exactly as ADR 0022's Missingness section requires ("Absence of any one of the four required input records... makes the entire fair-value estimate explicitly unavailable — never a partial or degraded-confidence estimate"). The gap is that no accepted authority may lawfully produce a complete, gap-free, non-overlapping composed interval from multiple already-accepted granular disclosures before that refusal becomes necessary.

Closing this gap does not mean most evidence becomes assemblable. Most real-world disclosure patterns will remain incomplete, overlapping, ambiguous, or otherwise non-composable, and must remain explicitly unavailable exactly as they are today. This ADR authorizes a narrow, invariant-gated authority that converts an already-complete, already-gap-free, already-non-overlapping set of already-authoritative granular disclosures into one derived evidence record — nothing more. It does not authorize turning an incomplete disclosure history into a usable one.

## Decision

Hunter establishes a single service-owned **Canonical Evidence Assembly Authority**, exercised exclusively by `CanonicalEvidenceAssemblyService`, positioned strictly between native Layer 2 evidence (ADR 0021) and methodology consumption (ADR 0022 and future methodology ADRs):

```text
Native Fundamental Valuation Evidence
        ↓
Evidence Assembly Authority
        ↓
Assembled Fundamental Evidence
        ↓
CanonicalValuationService
   (methodology-contract input-eligibility evaluation, then valuation arithmetic)
        ↓
FairValueEstimate
```

Methodology-contract input-eligibility evaluation — whether a specific native or assembled record is permitted as an input under the methodology contract in force — is owned exclusively by `CanonicalValuationService`, immediately followed by that same service's valuation arithmetic; it is not a separate pipeline stage or a separate authority (see "Methodology contract," below). The Evidence Shape Registry (see below) is versioned reference data consulted by the Evidence Assembly Authority. It is not a node in this pipeline and makes no valuation decisions.

This ADR authorizes the semantic contract, authority boundaries, invariants, record family, and replay semantics for this authority. It does not authorize implementation, activate any Market Validation input, change `CanonicalValuationService`'s existing arithmetic, discount-rate policy, horizon, or confidence formulas, or make Assembled Fundamental Evidence available for the first canonical methodology (`discounted-value-capture-flow-v1`) — see "Exact amendment to ADR 0022" below for the precise, narrow conditional availability this ADR creates.

### Authority boundaries

| Evidence Assembly owns | Evidence Assembly does not own |
| --- | --- |
| Lossless composition eligibility determination | External acquisition |
| Constituent selection under strict-known cutoff | Parsing external disclosures |
| Contiguity and non-overlap validation | Native `FundamentalEvidenceRecord` validation (owned by `hunter.value_capture`, unchanged) |
| Accounting-window coverage proof | Source trust policy already owned elsewhere (ADR 0004, ADR 0009) |
| Entity continuity proof | Methodology definition (owned by ADR 0022 and future methodology ADRs) |
| Representation continuity proof consumption and validation only | Evidence Assembly is a consumer only. It consumes the exact proof reference supplied by `CanonicalEvidenceSemanticInputAuthority`; it never produces, owns, infers, reconstructs, substitutes, or authorizes Representation Continuity Proof. Representation Continuity Proof is produced only through `CanonicalEvidenceSemanticInputAuthority` as the `EvidenceSemanticInputPolicySnapshot` itself, as defined by ADR 0028. |
| Currency and unit compatibility validation | Discount rates |
| Competing-source conflict detection | Valuation horizons |
| Assembly lineage recording | Valuation formulas |
| Production of `AssembledFundamentalEvidenceRecord` | `p10`/`p50`/`p90` calculation |
| Correction and supersession of assembled records | Market validation |
| — | Opportunity scoring |
| — | Recommendations |
| — | Methodology-contract input-eligibility evaluation (owned exclusively by `CanonicalValuationService`, ADR 0022) |

Repositories that persist `AssembledFundamentalEvidenceRecord`s remain mechanical persistence/query authorities under ADR 0009: they store and retrieve service-authorized records deterministically and make no assembly, eligibility, conflict, or correction decisions.

**Assembly is methodology-contract-aware, not methodology-agnostic.** `CanonicalEvidenceAssemblyService` consumes the declared methodology input contract (see "Methodology contract" below) as read-only reference data — the exact accounting interval, continuity requirements, and accepted evidence shapes a target methodology requires — solely to determine what constituent set, if any, would satisfy that methodology's stated requirements. It never defines, amends, selects, or overrides a methodology; it only checks whether its own lossless-composition invariants can be satisfied for the interval a methodology contract declares it needs. A methodology contract that does not declare acceptance of assembled evidence, or that declares incompatible requirements, makes assembly for that methodology inapplicable regardless of whether a lossless composition would otherwise be possible.

`CanonicalValuationService` (and any future `CanonicalComparativeValuationService`, `CanonicalMispricingService`, `CanonicalAsymmetryService` per ADR 0021) remains the exclusive owner of valuation arithmetic and `FairValueEstimateRecord`/other assessment production. Evidence Assembly never estimates a fair value, never scores an opportunity, and never produces a Layer 3 record.

### The lossless-only rule

**Lossless composition** means: deterministic arithmetic over complete, authoritative constituent records that jointly represent the exact required observed interval, using only unit-preserving or already-governed exact unit conversion, where every output value is traceable to complete constituent evidence. Nothing else qualifies.

Permitted:

- deterministic summation (or other exact, evidence-defined combination) over complete, authoritative constituent records that jointly and exactly cover the required observed interval with no gap and no overlap;
- unit-preserving conversion, or unit conversion governed by an existing, already-accepted exact conversion authority;
- composition where every output value is traceable, field by field, to complete constituent evidence.

Prohibited, without exception:

- annualizing one incomplete period;
- multiplying a daily, weekly, monthly, or quarterly figure to represent unobserved periods;
- interpolation;
- extrapolation;
- smoothing;
- run-rate assumptions;
- seasonality assumptions;
- trend assumptions;
- imputation;
- averaging conflicting disclosures;
- filling gaps with zero;
- current/latest fallback;
- relabeling native evidence as assembled evidence, or assembled evidence as native evidence.

Unknown remains unknown. Any missing constituent, overlap, conflict, scope discontinuity, or provenance failure fails closed: the result is an explicit unavailable or conflict outcome, never a degraded or partial `AssembledFundamentalEvidenceRecord`. A complete, gap-free, non-overlapping assembly that jointly and exactly covers the required observed interval is lossless composition, not annualization, interpolation, or extrapolation — the distinction is completeness and exactness of interval coverage, not granularity. Twelve complete, contiguous, non-overlapping monthly disclosures spanning exactly the required 365-day window are eligible; eleven complete months plus one estimated twelfth is not, regardless of how small the estimated contribution is.

### Assembly preconditions

Every one of the following is a mandatory, non-optional invariant. Any one being unsatisfied makes assembly for that target unavailable; none may be treated as an illustrative example or weighed against the others:

1. Every candidate constituent is drawn from the same canonical economic entity (ADR 0005).
2. Every candidate constituent shares the same economically relevant representation, unless an explicitly governed continuity relation already accepted elsewhere in this repository's architecture proves equivalence (e.g., a documented token migration mapping); a continuity relation is never inferred by the Evidence Assembly Authority itself.
3. Every candidate constituent shares the same value-capture pathway (the same `ValueCaptureRuleSnapshot` mechanism/entitlement, referenced by exact ID and version, or a set of versions the governing methodology's continuity rule explicitly treats as the same pathway).
4. Every candidate constituent shares a compatible supply basis.
5. Every candidate constituent shares a compatible currency.
6. Every candidate constituent shares compatible units.
7. Every candidate constituent shares compatible accounting meaning (e.g., cumulative figures are never composed as if they were period-specific, and period-specific figures are never composed as if they were cumulative; the Evidence Shape Registry's structural classification governs this check).
8. The composed set provides exact target-interval coverage: the union of constituent accounting windows equals the methodology-declared required window exactly, with no partial coverage accepted.
9. No gaps exist between consecutive constituent accounting windows.
10. No overlaps exist between any two constituent accounting windows.
11. Constituent ordering is deterministic (chronological by accounting-window start, ties broken by record ID) and reproducible under replay.
12. No unresolved conflict exists among candidate constituents or between a candidate constituent and any other accepted record covering an overlapping window.
13. Every constituent independently satisfies strict-known eligibility (ADR 0020) at the requested replay cutoff — its own effective time, recorded time, and known time each satisfy the cutoff, and this remains true regardless of which other constituents are being composed with it.
14. No constituent may be selected merely because it was recorded later than a competing candidate; selection is governed by these invariants and by deterministic conflict handling (see "Conflicts and multiple disclosures" below), never by recency.
15. No constituent may cross a governance-boundary, token-migration, value-capture-rule, accounting-policy, or representation boundary unless the accepted architecture explicitly proves semantic continuity across that boundary (see invariant 2); crossing such a boundary without an explicit, already-accepted continuity proof makes assembly across that boundary unavailable, not merely lower-confidence.

Assembly is unavailable, in full, whenever any invariant above is unsatisfied. There is no partial-invariant, best-effort, or reduced-confidence assembly outcome.

### Current availability decision

Adoption of this ADR does not itself compose any evidence. It authorizes the authority, its invariants, and its record family; it neither implements `CanonicalEvidenceAssemblyService` nor activates assembled evidence for any methodology. Most evidence will remain unavailable for assembly, exactly as most evidence remains unavailable for native valuation under ADR 0021 and ADR 0022 today — because the required complete, gap-free, non-overlapping constituent set will frequently not exist, not because the authority declines to compose evidence that genuinely qualifies.

## Assembled Fundamental Evidence record family

`AssembledFundamentalEvidenceRecord` is a new, immutable, bitemporal record family, produced only by the Canonical Evidence Assembly Authority. It represents:

- a deterministic, provenance-complete assembly of multiple authoritative Layer 2 evidence records;
- an observed interval fully and exactly supported by its constituents;
- a derived evidence representation, not a native disclosure.

It never represents:

- a protocol-issued native disclosure;
- an estimate;
- a forecast;
- a normalized run rate;
- a valuation conclusion;
- inferred economic activity;
- an undisclosed period.

It must remain visibly and queryably distinguishable from native `FundamentalEvidenceRecord`s at all times — through an explicit, non-optional native-versus-assembled marker field, never through naming convention, table location, or caller inference alone. This ADR does not prescribe a database schema, a migration mechanism, or programming-language types; the fields below are semantic minimums, to be expressed in whatever concrete form a future implementation ADR or issue selects, consistent with this repository's existing generic analytical persistence envelope (ADR 0021's "logical record families, not a database product" framing applies identically here).

Every `AssembledFundamentalEvidenceRecord` carries, at minimum:

**Identity and versioning**

| Field | Meaning |
| --- | --- |
| record ID | Unique identifier of this exact record version |
| logical ID | Stable identifier across corrections for the same assembled lineage |
| schema version | Structural schema version |
| semantic version | Semantic content version |
| assembly-rule version | Exact version of the composition rule set applied (the lossless-only rule and preconditions above, as implemented) |
| evidence-shape-registry version | Exact Evidence Shape Registry version consulted during assembly |
| methodology-contract ID and version | Exact `ValuationMethodologySnapshot` (or future methodology contract) identity and version this assembly was evaluated for compatibility against |

**Scope**

| Field | Meaning |
| --- | --- |
| entity ID | Canonical economic entity (ADR 0005) |
| representation ID or governed continuity proof | Representation scope, or the explicit continuity-proof reference authorizing composition across a representation boundary |
| value-capture pathway ID | Exact `ValueCaptureRuleSnapshot` mechanism/entitlement identity shared by every constituent |
| supply-basis identity | Supply basis shared by every constituent |
| currency | Shared currency |
| unit | Shared unit |
| accounting-window start | Explicit start of the composed interval |
| accounting-window end | Explicit end of the composed interval |
| accounting-period days | Explicit length of the composed interval, in days |

**Lineage**

| Field | Meaning |
| --- | --- |
| constituent record IDs | Exact IDs of every constituent record, in deterministic order |
| constituent logical IDs | Exact logical IDs of every constituent |
| constituent versions | Exact semantic versions of every constituent |
| deterministic constituent order | The ordering rule and resulting order actually applied |
| source count | Number of constituent records |
| assembly content hash | Deterministic canonical hash of the complete constituent set, order, and assembly rule applied |
| aggregation lineage | Full description of the deterministic arithmetic/combination actually applied |
| native-versus-assembled marker | Explicit, non-optional marker identifying this record as assembled, never native |

**Temporal authority**

| Field | Meaning |
| --- | --- |
| effective_at | Equals the accounting-window end — the moment the composed economic interval becomes fully observed. It is never ingestion time and is never inferred from record insertion order. |
| recorded_at | The time the assembled record was durably recorded by the Canonical Evidence Assembly Authority. It is never the evidence interval end and is never the requested replay cutoff. |
| known_at | Equals the maximum authoritative `known_at` of all constituents, or the record's own `recorded_at`, whichever is later. It is never replaced by the requested replay cutoff and never precedes the moment the assembled record itself became durably known. |

**Quality and conflict**

| Field | Meaning |
| --- | --- |
| quality state | Assembled-record quality classification |
| confidence state | Confidence classification, bounded by the weakest contributing constituent (never higher than any single constituent's own confidence) |
| conflict state | Explicit conflict classification; an open conflict on any constituent propagates to an open conflict on the assembled record |
| completeness state | Explicit statement that interval coverage is exact and complete (assembly never produces a partial-completeness record; incompleteness makes assembly unavailable, not partially complete) |
| continuity proof state | Explicit statement of which continuity invariants (representation, governance, migration boundary) were checked and how they were satisfied |
| non-overlap proof state | Explicit statement that the non-gap/non-overlap invariants were checked and satisfied |

**Correction**

| Field | Meaning |
| --- | --- |
| predecessor/supersedes ID | ID of the assembled record this record corrects, if any |
| correction reason | Mandatory, non-blank reason for a correction |
| supersession state | Explicit state distinguishing an active record from a superseded one |

## Evidence Shape Registry

The Evidence Shape Registry is governed, versioned reference data consulted by the Canonical Evidence Assembly Authority. It is not a pipeline stage, does not acquire evidence, does not produce evidence, does not decide fair value, and does not override methodology contracts.

The Registry may describe structural properties such as:

- native disclosure versus derived assembly;
- continuous versus discrete disclosure cadence;
- accounting-period semantics (calendar-aligned, epoch-based, event-driven);
- whether amount, rate, and period are bound together in one disclosure or separately disclosed and must be validated for consistency;
- cumulative versus period-specific values;
- event-driven versus interval-based evidence;
- which composition operations (e.g., exact summation of period-specific values) are structurally compatible with a given shape, and which are structurally incompatible (e.g., summing cumulative values, which would double-count).

**Governance owner.** The Evidence Shape Registry is governed by the Canonical Evidence Assembly Authority under this ADR's own amendment mechanism: Registry entries are governance-authored, versioned reference data, additions, modifications, aliases, and deprecations to which require the same accepted-ADR-governed amendment discipline this repository already applies to fixed policy constants (the pattern ADR 0023 established for `SUPPLY_COHERENCE_RELATIVE_TOLERANCE`). The Registry is never amended by a code-only commit, and never scoped, conditioned, or waived for one named entity or provider.

The Registry must preserve historical interpretability: a taxonomy change never silently reclassifies previously assembled or previously rejected evidence. A record composed against Registry version N remains explained by version N's definitions under replay, even after the Registry advances to version N+1; a later Registry version may add new shapes or deprecate a shape for new assembly, but it does not retroactively reinterpret what was true about an already-assembled record's shape at version N.

## Methodology contract

Every valuation methodology (the accepted `discounted-value-capture-flow-v1` methodology under ADR 0022, and any future methodology) must explicitly declare an evidence-input contract stating, at minimum:

- accepted native evidence families;
- whether assembled evidence is accepted at all;
- if accepted, the accepted Evidence Shapes (per the Registry) and accepted assembly-rule families, or an explicit prohibition of assembly for that methodology;
- the required accounting interval;
- the exact interval-coverage rule (e.g., exact, gap-free, non-overlapping coverage of the full horizon — no partial-window acceptance);
- continuity requirements (entity, representation, value-capture pathway boundaries the methodology will and will not tolerate crossing);
- provenance minimums;
- conflict policy;
- confidence/quality minimums;
- entity and representation scope requirements;
- currency and unit requirements;
- missingness behavior;
- strict-known cutoff behavior.

The methodology contract is read-only input to both the Canonical Evidence Assembly Authority and `CanonicalValuationService`. The Canonical Evidence Assembly Authority must not invent undeclared compatibility: if a methodology contract does not explicitly declare acceptance of assembled evidence, or does not declare a requirement the assembly authority can verify against, assembly is inapplicable for that methodology regardless of whether a lossless composition would otherwise satisfy the assembly authority's own invariants. `CanonicalValuationService` is the sole and exclusive owner of methodology-contract input-eligibility evaluation: it alone decides, once, whether a specific native or assembled record is permitted as an input to a specific fair-value estimate under the methodology contract in force. This is not a second, corroborating, or "final defensive" check of a prior evaluation — no other step in this chain evaluates methodology-contract input eligibility at all (see "Exact amendment to ADR 0022" below).

This two-way separation avoids duplicated semantic ownership:

- the Canonical Evidence Assembly Authority owns whether a valid `AssembledFundamentalEvidenceRecord` can be constructed at all, from the constituent evidence that exists; it makes no determination about which, if any, valuation methodology may consume that record, and it holds no valuation-methodology authority of any kind;
- `CanonicalValuationService` owns both methodology-contract input-eligibility evaluation and valuation arithmetic. These two responsibilities belong to the same single, already-accepted service because `CanonicalValuationService` already owns the active `ValuationMethodologySnapshot` and final valuation-input acceptance (ADR 0022); assigning input-eligibility evaluation to a separate, new authority would create a second, unnecessary owner for one decision, and `CanonicalValuationService` holds no evidence-construction authority of any kind — it never assembles, composes, or corrects an `AssembledFundamentalEvidenceRecord`.

No step in this chain may re-derive, re-compose, weaken, or override a decision another step already owns.

## Temporal and replay semantics

This ADR reaffirms ADR 0020's strict-known replay policy without modification and extends it, without contradiction, to `AssembledFundamentalEvidenceRecord`:

1. Every constituent must independently satisfy the replay cutoff (assembly preconditions invariant 13, above).
2. Assembled evidence cannot be known before its latest-known constituent: `known_at` equals the maximum authoritative `known_at` of all constituents, or the assembled record's own `recorded_at`, whichever is later (see "Temporal authority" table above). It is never replaced by, backfilled from, or set equal to the requested replay cutoff.
3. `recorded_at` represents when the assembled record was durably recorded by the Canonical Evidence Assembly Authority — never the evidence interval end and never ingestion time of any individual constituent.
4. `effective_at` represents the accounting-window end — the economic interval semantics this ADR defines — never ingestion time and never a value inferred from record insertion order.
5. The accounting-window start and end are explicit, declared fields on the record; they are never inferred from insertion order, file order, or query order.
6. Strict-known replay of an `AssembledFundamentalEvidenceRecord` must reconstruct the exact constituent set (exact IDs and versions), the exact deterministic order, the exact assembly-rule version, the exact Evidence Shape Registry version, and the exact methodology-contract ID/version originally used to evaluate compatibility. Any divergence in any of these makes the reconstruction a different, non-equivalent replay, and the original record's known-at, recorded-at, and content hash remain the only valid basis for judging what was knowable at the original cutoff.
7. No current/latest fallback is permitted at any step: constituent selection, Registry version selection, and methodology-contract selection are each strict-known, never "whatever is current."
8. Later corrections to a constituent never rewrite an already-persisted `AssembledFundamentalEvidenceRecord`. A corrected constituent produces, where reassembly again becomes valid under every precondition above, a new successor `AssembledFundamentalEvidenceRecord` that references the corrected constituent version; the predecessor assembled record remains exactly as it was.
9. Correction propagation is append-only and inspectable: every successor record names its predecessor, states a mandatory non-blank correction reason, and carries a strictly later `recorded_at`/`known_at` than its predecessor, mirroring the pattern already implemented and independently audited in `hunter.value_capture` and `hunter.valuation` (ADR 0022's Correction/versioning rules).
10. Branching correction successors — two successors both claiming the same predecessor — are prohibited, mirroring the existing `_authorize_correction` pattern already enforced in `hunter.valuation.service` and `hunter.valuation_methodology.service`.
11. A historical `FairValueEstimateRecord` that consumed an `AssembledFundamentalEvidenceRecord` remains permanently linked to the exact assembled-record ID and version it originally consumed. A later correction to that assembled record never retroactively alters the historical `FairValueEstimateRecord`; it can only support a new, successor `FairValueEstimateRecord` referencing the corrected assembled evidence, exactly as ADR 0022's own Replay semantics section already requires for every other input.
12. Any unresolved source conflict among candidate constituents, or between a candidate constituent and another accepted record covering an overlapping window, makes assembly explicitly unavailable for that target and interval. It is never resolved by averaging, recency preference, or silent exclusion (see "Conflicts and multiple disclosures," below).

## Conflicts and multiple disclosures

The Canonical Evidence Assembly Authority must resolve the following situations deterministically or produce an explicit conflict/unavailable outcome. It never averages conflicting evidence, under any circumstance.

- **Duplicate-identical constituent records.** Records that are byte-identical in every economically relevant field (value, unit, accounting window, source) are deduplicated deterministically to one constituent reference; this is not a conflict.
- **Divergent duplicate records.** Two records covering the identical accounting window with differing values are an unresolved conflict. Assembly is unavailable for any interval requiring that window until a proper correction resolves which record is authoritative; the two candidates are never averaged, and neither is silently preferred by recency alone.
- **Overlapping official disclosures.** Two records whose accounting windows overlap (even partially) violate the non-overlap invariant outright. Assembly is unavailable across the overlapping span regardless of whether the overlapping values agree.
- **Competing sources for the same interval.** Where more than one otherwise-eligible source covers the same window, selection must be deterministic and declared in the methodology contract or Evidence Shape Registry before evaluation (e.g., a fixed source-priority policy); absent a declared deterministic rule, the interval is an unresolved conflict, not a coin-flip or latest-wins default.
- **Corrections to previously selected sources.** A correction to a constituent already used in an existing `AssembledFundamentalEvidenceRecord` does not alter that record; it makes a new successor assembly eligible once every precondition is again satisfied (see "Temporal and replay semantics," item 8).
- **Multiple valid granularities covering the same interval.** Where both a complete set of monthly disclosures and a complete set of weekly disclosures independently and losslessly cover the identical required interval for the identical value-capture pathway, both are structurally eligible; the methodology contract or Evidence Shape Registry must declare a deterministic granularity preference before evaluation (finer-granularity-preferred is the default absent an explicit declared override, because finer granularity requires no additional transformation beyond straightforward summation and provides no information the coarser set lacks). This default may be overridden only by an explicit, declared rule — never chosen ad hoc per assembly.
- **A native annual disclosure existing alongside a composable granular series.** A qualifying native `FundamentalEvidenceRecord` that already, by itself, exactly covers the required interval takes precedence over composing an equivalent `AssembledFundamentalEvidenceRecord` from granular constituents covering the same interval. This rule follows directly from evidence authority and minimal transformation: a native disclosure requires zero transformation and carries the protocol's own boundary-drawn attestation, while composition asserts, through this ADR's own authority, that several separate disclosures losslessly represent one interval — a stronger claim than simply reading the protocol's own single disclosure. Assembly is therefore used only to cover an interval for which no single qualifying native record exists; it is never used to replace or override an existing qualifying native record for the same interval.

## Compatibility With Accepted ADRs

| ADR | Compatibility effect |
| --- | --- |
| 0001 | Discovery remains upstream and unaffected; this ADR does not value, rank, or assemble evidence during discovery. |
| 0002 | Every `AssembledFundamentalEvidenceRecord` is provenance-preserving, conflict-visible, confidence-bearing, missingness-explicit, and replay-safe, exactly as ADR 0002 requires of every authoritative output. |
| 0003 | Candidate Registry remains canonical candidate identity/lifecycle authority; this ADR does not touch it. |
| 0004 | Trust, reliability, conflicts, and unavailable states precede assembly exactly as they precede valuation; a constituent that fails trust/reliability validation is never eligible for assembly regardless of interval coverage. |
| 0005 | Entity, representation, value-capture-pathway, and supply-basis scope boundaries from ADR 0005 are the literal basis of this ADR's assembly preconditions; no assembly is authorized across a boundary ADR 0005 treats as non-interchangeable absent an explicit governed continuity proof. |
| 0006 | No knowledge/technology/economic graph becomes a constituent, a shape classification, or a compatibility decision; graphs remain outside this ADR's scope entirely. |
| 0007 | Reaffirmed: Option A and Canonical Market Validation remain the production runtime; this ADR authorizes no parallel runtime and produces no Market Validation input by itself. |
| 0008 | Plugins cannot become constituent sources, assembly authorities, or bypass this ADR's invariants through registration or orchestration. |
| 0009 | The Canonical Evidence Assembly Authority owns validation, clocks, cutoff selection, corrections, and persistence authorization for its record family exactly as ADR 0009 requires of every service-owned authority; repositories that persist `AssembledFundamentalEvidenceRecord`s remain mechanical. |
| 0010 | Intelligence engines remain descriptive and cannot supply, classify, or substitute for assembled evidence. |
| 0011–0015 | Domain intelligence findings (developer, tokenomics, governance, security, on-chain) remain descriptive and are not eligible constituent evidence; this ADR does not change their status. |
| 0016 | Reaffirmed, not superseded: Market Validation remains the sole canonical production analytical runtime; assembled evidence, like native evidence and fair-value estimates, becomes a composition input only through a separately accepted ADR (per ADR 0024, unchanged by this ADR). |
| 0017 | Experimental Opportunity remains isolated and gains no authority from this ADR; assembled evidence is not an Opportunity factor. |
| 0018 | Experimental factor mappings confer no assembly or valuation authority; unaffected. |
| 0019 | Prediction Evaluation remains separate audit authority; unaffected by this ADR. |
| 0020 | **Reaffirmed and relied upon, not amended.** ADR 0020's strict-known replay policy already governs any immutable persisted record selected for canonical input assembly generically; this ADR's "Temporal and replay semantics" section specializes that generic policy to `AssembledFundamentalEvidenceRecord` without requiring any change to ADR 0020's own text — no sentence in ADR 0020 states or implies a limit this ADR must remove, unlike ADR 0022's Scope criterion 3 (amended by ADR 0023) or ADR 0021's `valuation` row (amended by ADR 0024). Consistent with `docs/DEVELOPMENT_GOVERNANCE.md`'s and `docs/ADR/README.md`'s proportionality principle and this ADR's own instruction not to rewrite ADR 0020 broadly absent necessity, ADR 0020 is therefore left unedited; this table entry is the complete cross-reference. |
| 0021 | Amended — see "Exact amendment to ADR 0021," below. All other sections reaffirmed. |
| 0022 | Amended — see "Exact amendment to ADR 0022," below. All other sections, including the discounted value-capture-flow model, 365-day horizon, entity-class criteria, prohibited-methodology list (beyond the one clarifying note below), strict-known replay, append-only correction, provenance, confidence, uncertainty, and audit gates, are unchanged. |
| 0023 | Unaffected. The supply-basis coherence tolerance governs `SupplyBasisSnapshot`, which is not a constituent record family under this ADR. |
| 0024 | Unaffected. `valuation`'s scalar-semantics boundary is orthogonal to whether `valuation`'s underlying evidence is native or assembled; this ADR changes neither `valuation`'s structured-assessment contract nor its exclusion from directionally favorable scalar normalization. |

No accepted ADR 0001–0024 is superseded, weakened, or contradicted.

### Exact amendment to ADR 0021

This ADR amends only ADR 0021's "Evidence and record-family boundaries" section, Layer 2 description, and only in the way stated here. ADR 0021's five-layer evidence model is preserved in full; no layer is added, removed, renumbered, or collapsed.

ADR 0021's Layer 2 description currently reads, in relevant part:

> 2. **Fundamental valuation evidence:** attributable protocol cash flow, fees/revenue only with an explicit value-capture path, economic entitlement, token/network utility with measurable value transfer, dilution/emission/claim seniority, treasury or liabilities where attributable, supply basis, accounting window, source methodology, and uncertainty. Descriptive observations remain non-valuation until the valuation service validates this contract.

It is amended to add the following, appended to that same paragraph, with the original sentences otherwise unchanged:

> *(As amended by ADR 0025: this layer includes a distinct derived subtype, Assembled Fundamental Evidence, produced exclusively by the Canonical Evidence Assembly Authority through lossless composition of multiple already-authoritative Layer 2 constituent records under ADR 0025's invariants. Assembled Fundamental Evidence is never a native disclosure and is never relabeled as one, and a native disclosure is never relabeled as assembled evidence; the two remain visibly and queryably distinct at all times through an explicit native-versus-assembled marker. Complete source and composition lineage — exact constituent IDs, versions, deterministic order, and assembly-rule and Evidence Shape Registry versions — is mandatory on every Assembled Fundamental Evidence record. Lossless composition of already-authoritative Layer 2 evidence does not convert that evidence into a Layer 3 fair-value estimate or any other Layer 3+ conclusion; it remains Layer 2 evidence, subject to the same missingness, conflict, and strict-known rules as native Layer 2 evidence. This amendment does not imply that assembly is generally possible: most real disclosure histories will remain incomplete, overlapping, or otherwise non-composable, and must remain explicitly unavailable exactly as before. See ADR 0025 for the complete record family, authority boundaries, invariants, and replay semantics.)*

The "Required new record families and minimum fields" table immediately following that section is unchanged; a new row is not added to it, because `AssembledFundamentalEvidenceRecord` is a distinct record family defined in full by ADR 0025, not a field extension of `FundamentalEvidenceRecord`. One sentence is appended immediately after that table:

> *A distinct `AssembledFundamentalEvidenceRecord` family, produced only by the Canonical Evidence Assembly Authority, is defined in full by ADR 0025 and remains visibly and queryably distinguishable from every record family in this table.*

No other part of ADR 0021 — including its four-service authority matrix, source-provider eligibility rules, anti-double-counting policy, implementation order, and acceptance criteria for a future canonical canary — is changed by this ADR.

### Exact amendment to ADR 0022

This ADR amends only ADR 0022's "Valuation inputs," "Prohibited methodologies," and "Persistence requirements" passages, exactly as stated here. The permitted model family (discounted value-capture flow), the fixed 365-day horizon, the entity-class Scope criteria, the discount-rate and sensitivity policies, and every other ADR 0022 decision are unchanged.

**Valuation inputs.** ADR 0022's exhaustive list currently reads, in relevant part:

> 1. `hunter.market_facts.ObservedMarketFactRecord`... 2. `hunter.value_capture.FundamentalEvidenceRecord`... 3. `hunter.value_capture.ValueCaptureRuleSnapshot`... 4. `hunter.value_capture.SupplyBasisSnapshot`... 5. `ValuationMethodologySnapshot`... No other record family, provider, repository, configuration file, Dashboard projection, report, or caller-supplied value may inform a fair-value estimate. This list is exhaustive, not illustrative.

It is amended to add a sixth, strictly conditional entry, and its closing sentence is amended as shown:

> 6. *(Added by ADR 0025.)* An `AssembledFundamentalEvidenceRecord` (ADR 0025) may **substitute for** item 2 above, for the same attributable-flow input scope, **only** when the `ValuationMethodologySnapshot` in force explicitly declares acceptance of assembled evidence and states the exact requirements under which it accepts it (ADR 0025 §"Methodology contract"). This substitution is exclusive, not additive: for a single attributable-flow input scope, a fair-value estimate must consume either the native `FundamentalEvidenceRecord` under item 2 or a compatible `AssembledFundamentalEvidenceRecord` under this item 6 — never both. No constituent native `FundamentalEvidenceRecord` may be separately consumed under item 2 once its assembled derivative (a record for which it is a constituent, per ADR 0025) is the record selected under this item 6. Where both a qualifying native record and a qualifying assembled derivative exist for the same interval, ADR 0025's native-precedes-assembled selection rule (§"Conflicts and multiple disclosures") governs and the assembled derivative is not selected. No summation, averaging, blending, stacking, or other combination of a native and an assembled record is authorized under any circumstance. The `discounted-value-capture-flow-v1` methodology this ADR authorizes does not declare such acceptance and therefore continues to consume only native evidence under item 2; a future `ValuationMethodologySnapshot` version may declare acceptance, without requiring a new methodology ADR, provided it satisfies ADR 0025's methodology-contract requirements in full.
>
> No other record family, provider, repository, configuration file, Dashboard projection, report, or caller-supplied value may inform a fair-value estimate. *(As amended by ADR 0025: this list is exhaustive subject to item 6 above; item 6 does not expand it beyond the exact conditional terms ADR 0025 defines, and does not by itself make assembled evidence available for any methodology snapshot that does not explicitly declare acceptance.)*

**Prohibited methodologies.** Immediately following that section's existing bullet list (unchanged), the following clarifying note is appended:

> *(As amended by ADR 0025: a complete, gap-free, non-overlapping `AssembledFundamentalEvidenceRecord` that jointly and losslessly covers the exact accounting period this methodology's horizon requires — per ADR 0025's lossless-only rule and assembly preconditions — is not annualization, interpolation, extrapolation, or any other prohibited transformation under this section. The prohibition on "an arbitrary-period amount... treated as a fixed 365-day flow without an authorized annualization policy" continues to apply, unchanged, to any single record whose own accounting period does not match the required horizon; it does not apply to a complete composed set whose union exactly matches the required horizon with no gap and no overlap.)*

**Assembled evidence acceptance and methodology-contract input eligibility.** The following new subsection is added to ADR 0022, immediately after "Missingness":

> ### Assembled evidence acceptance and methodology-contract input eligibility *(added by ADR 0025)*
>
> Where a `ValuationMethodologySnapshot` declares acceptance of Assembled Fundamental Evidence (ADR 0025), a compatible, strict-known `AssembledFundamentalEvidenceRecord` becomes eligible to substitute, exclusively, for the native `FundamentalEvidenceRecord` otherwise required by Valuation Inputs item 2, subject to item 6's exclusivity rule above. `CanonicalValuationService` is the sole and exclusive owner of the decision whether a specific record — native or assembled — is accepted as that input for a specific fair-value estimate:
>
> 1. `CanonicalValuationService` is the only authority anywhere in this chain that evaluates methodology-contract input eligibility. This is not a second, corroborating, or "final defensive" check of a prior evaluation performed elsewhere — no other service or authority evaluates whether any record is permitted as a methodology input. The Canonical Evidence Assembly Authority (ADR 0025) never performs, anticipates, or duplicates this evaluation; its own authority ends once it has produced (or declined to produce) a valid `AssembledFundamentalEvidenceRecord` under its own lossless-composition invariants, and it makes no determination about which, if any, valuation methodology may consume that record.
> 2. At estimate-construction time, `CanonicalValuationService` validates that the consumed record's declared entity, representation, value-capture pathway, currency, unit, and accounting window satisfy this ADR's Scope and Valuation Inputs requirements, exactly as it already independently validates every other input (identity match, strict-known cutoff, unit match, and so on).
> 3. A rejection at this boundary makes the fair-value estimate explicitly unavailable, under the same Missingness rule that already governs every other input; it never triggers ad hoc re-assembly, substitution, partial acceptance, or a fallback to a different evidence family.

**Persistence requirements.** The "Additional minimum fields beyond ADR 0021's baseline" cell for `FairValueEstimateRecord` in ADR 0022's Persistence requirements table is amended by appending the following clause:

> ; and — only when Assembled Fundamental Evidence is consumed under a methodology snapshot that accepts it — the exact `AssembledFundamentalEvidenceRecord` ID, version, assembly-rule version, Evidence Shape Registry version, and methodology-contract ID/version it was assembled and evaluated under (ADR 0025).

No other cell in that table, and no other section of ADR 0022 (Terminology, Observed facts vs. derived evidence, Permitted methodology, Replay semantics, Provenance, Correction/versioning rules, Confidence rules, Uncertainty handling, Comparability rules, Peer-selection principles, Historical validation requirements, Calibration requirements, Audit requirements, or Current availability decision), is changed by this ADR.

## Consequences

Positive:

- Hunter gains architectural compatibility with real market disclosure patterns — continuous, daily, weekly, monthly, quarterly, epoch-based, and event-driven — without weakening evidence integrity anywhere in the chain from observed fact to fair-value estimate.
- No existing evidence-integrity guarantee is loosened: every invariant that already applies to native evidence (strict-known cutoff, entity/representation/value-capture-pathway scope, missingness-on-failure, append-only correction) applies identically to assembled evidence, plus additional invariants (gap-free, non-overlap, deterministic ordering, lossless-only composition) that native evidence never needed because it was never composed.
- Replay remains fully deterministic: an `AssembledFundamentalEvidenceRecord`'s constituent set, order, assembly-rule version, Registry version, and methodology-contract version are all exact, versioned, and strict-known.
- Native and assembled evidence remain explicitly and permanently distinguishable, closing off a relabeling failure mode before it can occur.
- Future methodology contracts gain a declared, auditable mechanism for stating whether and how they accept assembled evidence, rather than each methodology inventing its own ad hoc composition logic.
- Correction lineage for assembled evidence is clear, append-only, and non-branching, matching the pattern already independently audited for `hunter.value_capture` and `hunter.valuation`.

Costs and risks:

- A new authoritative record family (`AssembledFundamentalEvidenceRecord`) must be implemented, persisted, and independently audited before it can be used, exactly as every prior foundation record family in this repository has required (Issue #88, Issue #95, and this repository's established governance pattern).
- Replay becomes more complex for any input that consumes assembled evidence: a full replay must reconstruct not only the assembled record but its entire constituent set, order, and the Registry/methodology-contract versions in force at assembly time.
- Correction propagation adds a second correction surface (constituent-level correction can trigger a new successor assembly) beyond the correction surface `FairValueEstimateRecord` already has.
- The Evidence Shape Registry introduces an additional governed artifact requiring its own versioning discipline and governance owner, adding process overhead proportional to the number of distinct disclosure shapes Hunter eventually needs to classify.
- Storage and computation grow with the number of constituent records composed per assembled record, though this growth is bounded by the exact, gap-free interval each assembly targets.
- Weakening any single invariant in this ADR — even one that appears minor, such as the non-overlap check or the deterministic-ordering rule — would silently reintroduce an annualization-, interpolation-, or double-counting-equivalent failure mode; every invariant in "Assembly preconditions" is load-bearing and none may be relaxed by implementation convenience.
- Methodology contracts now carry additional required declarations (whether assembled evidence is accepted, and under what exact terms), which every future methodology ADR must address explicitly rather than by omission.

## Alternatives Considered

### Annualize a single incomplete period

Rejected. Multiplying a partial disclosure to represent an unobserved remainder is estimation, not composition of already-known facts, and directly contradicts ADR 0021's and ADR 0022's shared prohibition on inventing values Hunter does not actually possess (Principle 9, "No Fabricated Evidence").

### Normalize partial periods to a run rate

Rejected for the same reason as annualization: a run rate encodes an assumption about unobserved future or past behavior, not a fact already disclosed.

### Interpret ambiguous disclosures to infer a composable value

Rejected. Interpretation substitutes Hunter's own judgment for an authoritative disclosure, exactly the "silently filling gaps" pattern Principle 9 prohibits.

### Extrapolate beyond the last observed period

Rejected for the same reason as annualization and run-rate normalization: it fabricates evidence for a period no disclosure actually covers.

### Interpolate between two known periods to cover a gap

Rejected. Interpolation invents a value for an interval no constituent record actually observes, which the lossless-only rule explicitly and permanently prohibits.

### Permit ad hoc human manifest approval of a composed value without persisted authority

Rejected. An unpersisted, unauditable human sign-off cannot satisfy strict-known replay, provenance, or correction-lineage requirements, and would create exactly the kind of opaque authority Constitutional Rule 6 (Explainability) prohibits.

### Implement composition inside `CanonicalValuationService` itself

Rejected. This is the failure mode this ADR's Context section identifies directly: it would make evidence-composition decisions inside valuation arithmetic, without an accepted authority governing them, duplicating and blurring the boundary ADR 0009 and ADR 0021 already establish between evidence validation and valuation calculation.

### Duplicate assembly logic inside each future methodology (comparative valuation, mispricing, asymmetry)

Rejected. Each methodology would then define its own composition rules, invariants, and Registry-equivalent classification independently, recreating exactly the "duplicated business behavior across multiple components" the Implementation Contract's Service Contract section prohibits, and making cross-methodology consistency unauditable.

### Treat Evidence Shape Classification as a mandatory runtime pipeline authority rather than versioned reference data

Rejected. Elevating the Registry to a pipeline stage would give it decision-making authority over assembly or valuation outcomes, which this ADR reserves exclusively for the Canonical Evidence Assembly Authority and, downstream, `CanonicalValuationService`. The Registry describes evidence structure; it does not decide anything.

### Treat assembled evidence as indistinguishable from native evidence once composed

Rejected outright. Indistinguishability would make it impossible to audit, later, whether a given fair-value estimate rested on a protocol's own disclosure or on Hunter's own lossless-but-derived composition — a material distinction for explainability (Constitutional Rule 6) even when the composition is provably lossless.

### Accept partial interval coverage when it represents "most" of the required window

Rejected. Any threshold short of exact, complete coverage reintroduces an estimation judgment about the uncovered remainder, which is exactly what the lossless-only rule exists to prohibit. "Most of the window" is not "the window."

### Average conflicting disclosures for the same interval

Rejected. Averaging fabricates a value neither disclosure actually states and conceals which disclosure, if either, is authoritative — the same failure mode Principle 9 and ADR 0020's anti-substitution rules already prohibit for every other input.

### Modify native evidence records in place to reflect a composed value

Rejected. Native records are append-only and immutable (ADR 0002, ADR 0009, ADR 0021); rewriting one to hold a composed value would destroy the historical record of what was actually, individually disclosed and break every existing strict-known replay guarantee for that record.
