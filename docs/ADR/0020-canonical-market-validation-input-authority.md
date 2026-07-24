# ADR 0020: Canonical Market Validation Input Authority and Strict-Known Replay

## Status

Accepted.

## Context

Canonical Market Validation is Hunter's sole production analytical runtime, but that authority does not make an input authoritative merely because a provider record, graph field, engine label, repository, or test exists. A readiness audit at `2026-07-11T00:00:00+00:00` confirmed five input-contract defects and one canonical Timing defect:

- one generic CoinGecko market-profile/completeness mean is relabeled as `valuation`, `comparative_valuation`, `mispricing`, and `asymmetry`;
- input assembly selects latest/current acquisition, Timing, and graph state instead of strict-known records;
- graph, economic, scenario, and other derived inputs can be stamped with the requested cutoff rather than retaining their actual effective, recorded, and known times;
- economic-graph and scenario timing-like values can satisfy `opportunity_timing` even though ADR 0016 assigns canonical Timing solely to `OpportunityTimingEvidenceEngine` and `TimingAssessment`;
- the same technology-graph centrality mean is emitted as both `technology_necessity` and an otherwise undefined `necessity_gap`; and
- tests currently demonstrate deterministic aliasing rather than authoritative input meaning.

At the audited cutoff, all CoinGecko evidence, canonical Timing assessments, and technology-graph runs were recorded after the cutoff. The apparent availability of timing and necessity-gap values therefore came from substitutes and cutoff timestamp backfill, not valid point-in-time authority.

ADRs 0002, 0004, 0005, 0009, and 0016 already require evidence-first execution, explicit unavailable states, entity separation, service-owned replay decisions, repository purity, and single semantic owners. This ADR applies those requirements to the six inputs that must be corrected before Canonical Market Validation can treat them as available.

## Decision

Canonical Market Validation may consume an input only when the exact semantic contract in this ADR is satisfied by immutable persisted records selected under strict-known replay. Source reachability, a similar label, a normalized metric, a repository method, current/latest state, a model, a test, or scheduler execution never establishes input authority.

`EvidenceBackedProjectExecutor` remains the sole canonical production analytical runtime under ADRs 0007 and 0016. Its service-owned Market Validation input-assembly boundary is the sole production authority that may accept or mark missing the inputs below. Providers acquire observations; calculation services defined by a future accepted contract may produce authorized input records; repositories only store them. `acquisition_engine_sources` and other builders are not independent semantic owners.

### Input-authority matrix

| Input | Semantic contract | Sole production owner | Authorized persisted contract | Current decision and formula | Normalization, confidence, and missingness | Time, replay, provenance, and correction | Explicitly prohibited substitutes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `valuation` | Estimated fundamental value of one explicitly scoped economic entity or representation, expressed in a declared currency or dimensionless ratio only when the methodology defines that ratio. Higher normalized values mean more favorable value under that exact methodology. Project, protocol, network, native asset, token, wrapped/bridged asset, contract, and provider listing are never interchangeable. | Service-owned Canonical Market Validation input assembly accepts the record; a future valuation-contract ADR must name the calculation service that owns the value. | No record/field/version is authorized today. A future immutable `valuation` contract must bind entity scope, valuation date/horizon, currency/units, methodology and configuration versions, source-record versions, evidence, conflicts, uncertainty, and all three times. | **Unavailable until a later accepted valuation contract. No production formula is authorized.** | No normalization is authorized. Confidence is unavailable, not zero. Missing or incompatible evidence produces explicit missingness and lowers run coverage/confidence under the declared Market Validation policy; it never produces a neutral or supportive value. | Future records must retain source effective time, service-recorded time, explicit known time, immutable input snapshot, provenance IDs/versions, methodology/configuration fingerprint, canonical hash, and correction/supersession lineage. Strict-known selection is mandatory. | CoinGecko completeness/profile means, price alone, market capitalization, TVL, fees, revenue, tokenomics findings, descriptive intelligence, Opportunity, Dashboard, reports, Operational Corpus, graph scores, and caller-supplied values. |
| `comparative_valuation` | Relative valuation of the same explicitly scoped entity against a predeclared compatible comparable cohort, in a declared ratio/percentile or currency unit. Higher normalized values mean more favorable relative value only as defined by the methodology. | Same Market Validation acceptance/missingness owner; a future comparative-valuation contract must name its calculation service. | None authorized. Future record must version the target, cohort membership and selection rule, entity/representation compatibility, measurement fields and units, comparison formula, observation boundary, exclusions, and provenance. | **Unavailable until a later accepted comparative-valuation contract. No production formula or cohort is authorized.** | No generic averaging or percentile is allowed. Confidence must reflect target evidence, cohort coverage, comparability, conflicts, and staleness. Missing cohort authority or incompatible entities makes the input missing. | Target and every cohort input must be immutable and strict-known at the same declared cutoff; later cohort membership or observations cannot enter replay. Corrections create successors and preserve the original cohort snapshot. | The `valuation` value, provider rankings/categories, current market-cap lists, arbitrary sector labels, CoinGecko completeness, Opportunity/general ranking, Dashboard sorting, and latest/current comparable sets. |
| `mispricing` | Declared divergence between an authorized fundamental/reference value and an authorized observed market value for the same entity, representation, currency, and effective boundary. Direction must be fixed by the future methodology; a higher normalized score may mean more favorable underpricing only if explicitly declared. | Same Market Validation acceptance/missingness owner; a future mispricing contract must name its pure calculation/service authority. | None authorized. Future record must reference exact versioned valuation and market-observation records, formula, units, direction, tolerance, uncertainty, entity matching, and methodology/configuration version. | **Unavailable until both prerequisite authorities and a later accepted mispricing contract exist. No production formula is authorized.** | No value is calculated if either prerequisite is absent, incompatible, stale, disputed, or unknown-time. Confidence cannot exceed the weakest prerequisite and must incorporate model uncertainty. Missingness never becomes zero mispricing. | Both prerequisites must be strict-known at the declared cutoff. The derived record preserves their IDs, versions, times, provenance, immutable snapshot, and calculation fingerprint. Corrections are successor records. | Price return, discount labels, CoinGecko profile/completeness, valuation alone, comparative valuation alone, report prose, Opportunity score, Timing, and post-cutoff prices. |
| `asymmetry` | Predeclared upside/downside payoff asymmetry for one scoped entity and horizon under versioned scenarios, expressed in a declared ratio or bounded score. Direction, loss convention, zero-denominator behavior, and horizon must be explicit; higher normalized values mean more favorable reward-to-risk only under that policy. | Same Market Validation acceptance/missingness owner; a future asymmetry contract must name its scenario/calculation service. | None authorized. Future record must bind scenario definitions/probabilities or confidence treatment, baseline, horizon, payoff units, downside/loss convention, source inputs, dependencies, correlation policy, and methodology/configuration version. | **Unavailable until a later accepted asymmetry contract. No production formula or scenario set is authorized.** | No scenario, probability, or payoff may be invented. Missing downside evidence, incompatible units, or undefined denominator makes the input missing. Confidence must propagate scenario coverage, evidence quality, uncertainty, and correlation limits. | The complete scenario and input snapshot must be persisted before evaluation and selected strict-known. Later prices, scenarios, probabilities, or outcomes cannot backfill an earlier replay. Corrections preserve predecessor lineage. | CoinGecko completeness/profile, volatility alone, confidence, generic risk, mispricing, Opportunity/Probability/Pattern outputs, scenario-simulation labels without an authorized asymmetry contract, and report text. |
| `opportunity_timing` | Canonical assessment of entry-window timing for the scoped Market Validation target; existing `TimingAssessment` units, direction, classification, confidence, and entity scope are governed by the canonical Timing contract. Higher `entry_score` means a more favorable entry window only under that contract. | Solely `hunter.timing.OpportunityTimingEvidenceEngine`; Market Validation input assembly may only accept its persisted `TimingAssessment`. | Versioned, immutable canonical `TimingAssessment` plus its declared dependency snapshot, evidence/repository IDs, normalized factors, source times, methodology/configuration fingerprint, and correction lineage. | **Available only when a compatible canonical Timing record exists strict-known at cutoff.** No fallback formula is allowed. | Preserve canonical normalization and confidence unchanged. `INSUFFICIENT_EVIDENCE`, absent lineage, incompatible entity scope/version, or unavailable strict-known record means explicitly missing. Market Validation must not recalculate Timing. | Select through strict-known persisted records, using actual effective, recorded, and known times. Never call `latest_by_project()` for canonical replay and never replace `generated_at` or another time with the requested cutoff. Corrections are immutable successors. | Technology/economic graph metrics, scenario outputs, fusion-backed Opportunity Timing, experimental Opportunity, caller-supplied timing, Dashboard, Operational Corpus, reports, automation fields, and latest/current files. |
| `necessity_gap` | Difference between a declared required level of technological necessity/capability and an observed authorized level for one explicit entity, representation, dependency context, and horizon, in a future-declared ratio or bounded score. Direction must state whether a higher value is a favorable unmet need or an adverse deficiency. | Same Market Validation acceptance/missingness owner; no production calculation owner currently exists. The experimental `TechnologyNecessityEngine` is not the owner. | None authorized. A future contract must name required-level and observed-level records/fields/versions, entity/dependency scope, gap formula, direction, normalization, provenance, and anti-double-counting relationship to technology necessity and other graph factors. | **Unavailable unless a later accepted ADR retains the factor and defines its contract. No production formula is authorized.** | Technology centrality is not a gap. No default required level, observed level, normalization, or confidence is authorized. Missing either side makes the input missing and lowers coverage/confidence. | Future prerequisite and gap records must be immutable, snapshot-linked, strict-known, and preserve actual effective/recorded/known times, evidence and repository versions, calculation fingerprint, and correction lineage. Legacy graph summaries without trustworthy snapshot linkage are ineligible. | Technology-necessity score, infrastructure/dependency centrality, uniqueness means, current graph state, scenario propagation, experimental Necessity/Opportunity, Dashboard, reports, and similarly named fields. |

For the five deferred inputs, the Market Validation boundary owns the authoritative decision **that the input is missing**. It does not own or imply a numeric value. A future accepted ADR may authorize a calculation only after defining the complete semantic and evidence contract; implementation alone cannot promote it.

### Strict-known replay policy

Every canonical input selection must satisfy all of the following:

1. The service declares a timezone-aware replay cutoff and the target entity/representation context.
2. It reads immutable persisted record envelopes, not current projections or mutable latest files.
3. The selected record's effective time, recorded time, and explicit known time are preserved and meet the contract's cutoff rule. Unknown known-time is ineligible where strict-known replay is required.
4. Every transitive input and dependency used to derive the record was itself known by the applicable cutoff and is referenced by exact ID and version.
5. Selection is deterministic among compatible records and never falls back to `latest`, current state, filesystem time, post-cutoff data, a legacy summary without trustworthy snapshot linkage, or another semantic domain.
6. The requested cutoff is selection context only. It may never be written into a source record as if it were that record's effective, generated, recorded, or known time.
7. If no compatible record qualifies, the result is explicitly unavailable. Zero, neutral, average, confidence, completeness, or a substitute source is not a missing-value policy.

### Provenance, snapshots, and corrections

An authorized input record must carry canonical target identity and entity/representation scope; schema, semantic, methodology, model, and configuration versions; exact source/evidence/repository record IDs and versions; effective, recorded, and known times; units and normalization; confidence and missingness; conflicts and exclusions; an immutable input snapshot; and a deterministic canonical hash.

Records are append-only. A correction creates an immutable successor with explicit predecessor/supersession linkage, reason, authority, and time context. It never mutates the historical record or changes what was knowable at an earlier replay cutoff. Repositories mechanically store and retrieve service-authorized records and do not choose cutoffs, timestamps, corrections, semantics, formulas, or substitutes.

## Invalid Current Behaviors

The following behaviors are explicitly invalid for canonical execution and replay:

1. Applying `_evidence_score` to `coingecko_market_profile` and emitting that one value as any or all of `valuation`, `comparative_valuation`, `mispricing`, and `asymmetry`.
2. Treating schema, mandatory, optional, or metadata completeness as investment value.
3. Selecting normalized acquisition evidence with `_latest_valid_evidence` without strict-known cutoff enforcement.
4. Using `TimingRepository.latest_by_project()` or a repository's current/latest graph for point-in-time Market Validation.
5. Assigning the requested `as_of` cutoff as the timestamp of graph, economic, scenario, macro, whale, or other data generated or recorded later.
6. Accepting economic-graph, scenario-simulation, Fusion, experimental Opportunity Timing, or any other timing-like output as canonical `opportunity_timing`.
7. Emitting identical technology-centrality calculations as both `technology_necessity` and `necessity_gap`.
8. Treating a model, test fixture, scheduler job, persistence row, report field, Dashboard field, Operational Corpus row, or similarly named value as proof of authority.
9. Silently converting missing, incompatible, legacy, unknown-time, or post-cutoff data to zero, neutral, average, available, or supportive values.

Existing records remain readable for audit, but invalidly aliased or cutoff-backfilled values are not canonical input authority and cannot be grandfathered into replay.

## Required Implementation Sequence After Acceptance

Acceptance of this ADR authorizes correction planning, not runtime execution or new analytical authority. Implementation must proceed in this order:

1. **Strict-known assembly fix.** Introduce service-owned selection of immutable records by actual effective, recorded, and known times, with deterministic missingness and leakage tests.
2. **Remove aliases and substitutes.** Stop producing the valuation quartet from CoinGecko profile/completeness, stop emitting technology centrality as necessity gap, stop cutoff timestamp backfill, and reject every noncanonical Timing source.
3. **Authoritative valuation-family contracts and records.** Through later accepted ADR(s), define and then implement the required service calculations, immutable record types, evidence mappings, confidence, and replay behavior. Until then all four fields remain missing.
4. **Canonical Timing consumption.** Read only compatible strict-known persisted `TimingAssessment` records produced by `OpportunityTimingEvidenceEngine`, preserving canonical values and lineage.
5. **Replayable necessity-gap handling only if retained.** A later accepted ADR must decide whether the factor remains required. If retained, implement its distinct prerequisites, formula, owner, immutable snapshots, and anti-double-counting policy; otherwise remove it through an explicit compatibility decision rather than fabricating a value.

No step may enable a store, schedule a job, acquire provider data, alter Dashboard/API behavior, introduce Opportunity or general ranking, or create parallel analytical authority merely because this ADR is accepted.

## Compatibility With Accepted ADRs

| ADR | Compatibility effect |
| --- | --- |
| 0001 | Discovery remains the market entrypoint; this ADR does not prioritize or score candidates during discovery. |
| 0002 | Provenance, explicit missingness, conflicts, confidence, time context, and replay safety directly govern every input. |
| 0003 | Candidate Registry remains the canonical candidate identity/lifecycle authority; Market Validation cannot create or merge candidates. |
| 0004 | Trust, source reliability, freshness, conflicts, and unavailable states precede valuation and cannot be normalized away. |
| 0005 | Every valuation, comparison, timing, and gap record must declare economic entity and representation scope. |
| 0006 | Technology or economic graphs remain evidence/dependency structures and do not become valuation, Timing, or necessity-gap semantic owners. |
| 0007 | Reaffirmed: Option A and Canonical Market Validation remain the production runtime, subject to valid evidence-backed inputs. |
| 0008 | Plugins and Fusion cannot supply or promote canonical inputs through registration or orchestration. |
| 0009 | Services own validation, clocks, cutoff selection, corrections, and persistence plans; repositories remain mechanical adapters. |
| 0010 | Intelligence execution remains service-owned and descriptive; intelligence engines cannot supply valuation, Timing, or gap values. |
| 0011 | Developer findings remain descriptive and cannot substitute for valuation-family inputs or timing. |
| 0012 | Tokenomics findings, fees, revenue, TVL, unlocks, and supply observations remain descriptive and require a later valuation contract before use. |
| 0013 | Governance findings remain descriptive and cannot become valuation, asymmetry, or gap scores. |
| 0014 | Security findings remain descriptive and cannot become downside, risk, asymmetry, valuation, or timing scores. |
| 0015 | On-chain findings remain descriptive; balances and transfers cannot imply value, ownership, intent, mispricing, or timing. |
| 0016 | Reaffirmed, not superseded: Market Validation remains the sole canonical production analytical runtime and `OpportunityTimingEvidenceEngine` remains the sole canonical Timing owner. Input validity is a prerequisite for that runtime's conclusions. |
| 0017 | Experimental Opportunity remains isolated; similarly named Opportunity factors cannot supply these production inputs. |
| 0018 | Experimental Opportunity factor mappings do not authorize Market Validation inputs, and their missingness/anti-aliasing gates remain intact. |
| 0019 | Prediction Evaluation remains a separate audit authority and cannot supply or validate Market Validation input values. |

No accepted ADR 0001–0019 is superseded, weakened, or contradicted.

## Acceptance Criteria

Implementation conforming to this ADR must prove that:

1. The valuation quartet is unavailable when only CoinGecko profile/completeness evidence exists, and no shared source value is relabeled across those fields.
2. Each deferred input remains explicitly missing until its later accepted contract and exact authorized record version exist.
3. Canonical Timing accepts only strict-known persisted `TimingAssessment` records owned by `OpportunityTimingEvidenceEngine`.
4. Economic graph, scenario, Fusion, experimental Opportunity Timing, Dashboard, Operational Corpus, report, and caller-supplied timing values are rejected as canonical Timing.
5. Necessity gap cannot be populated from technology centrality, technology necessity, graph reachability, or a similarly named field.
6. Every selected input and transitive dependency has actual effective, recorded, and known times at or before its applicable cutoff.
7. A post-cutoff record is rejected even if its effective value appears relevant, and the requested cutoff is never copied into the record's timestamps.
8. Latest/current repository reads, mutable files, filesystem timestamps, and legacy summaries without trustworthy snapshot linkage cannot satisfy canonical replay.
9. Missing, unknown-time, incompatible, stale, conflicted, or insufficient inputs remain explicit and cannot become zero, neutral, average, or supportive defaults.
10. Exact replay selects the same compatible record IDs, versions, snapshots, configuration/methodology fingerprints, values, confidence, and missingness.
11. Corrections create immutable successors and cannot rewrite predecessor records or earlier known-time replay.
12. Tests exercise future-data leakage, same-name substitution, alias rejection, entity/representation mismatch, unknown-time, missingness, and correction lineage.
13. Market Validation scoring, ranking, and committee output cannot run as complete/authoritative when the configured evidence gate requires one of these inputs and that input is unavailable.
14. No Dashboard, scheduler, automation, repository, store state, or source-file existence can promote an unavailable input.

## Consequences

- Historical and live Market Validation runs may report more missing inputs until genuine contracts and cutoff-valid records exist.
- Existing apparent coverage from aliases, substitutes, current graphs, or timestamp backfill is intentionally removed.
- Canonical replay becomes auditable and leakage-resistant at the cost of refusing conclusions that current evidence cannot support.
- Valuation-family and necessity-gap implementation requires later architecture decisions; this ADR does not invent formulas from insufficient evidence.
- Canonical Timing retains one owner and one persisted semantic contract.

## Alternatives Considered

### Keep the current aliases for compatibility

Rejected because deterministic relabeling of completeness does not create valuation semantics and produces false evidence coverage.

### Permit graph or scenario timing when canonical Timing is missing

Rejected because same-name fallback creates parallel timing authority and violates ADR 0016.

### Stamp derived records with the requested replay cutoff

Rejected because selection context is not evidence time and would conceal future-data leakage.

### Define provisional formulas in this ADR

Rejected because current evidence does not establish defensible valuation-family or necessity-gap semantics, entity scope, units, methodology, or validation. Explicit missingness is the only valid present decision.

### Allow latest/current reads for live runs but strict-known reads for replay

Rejected because it would create two semantic input-selection paths and make live conclusions non-reproducible.
