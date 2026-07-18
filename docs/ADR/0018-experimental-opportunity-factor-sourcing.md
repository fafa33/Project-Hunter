# ADR 0018: Experimental Opportunity Factor Sourcing

## Status

Accepted.

## Context

ADR 0016 makes Market Validation the sole canonical production analytical runtime. ADR 0017 permits an isolated experimental Opportunity pipeline only when every factor has an approved persisted semantic owner and a leakage-safe contract. The current 17-factor contract is defined by `POSITIVE_FACTORS`, `GATING_FACTORS`, `NEGATIVE_FACTORS`, and `OpportunityConfig`; Phase 3.1 approves five exact fields from canonical `MarketValidationProjectResultRecord` and leaves twelve factors unowned.

The repository also contains persisted or persistable Macro, Whale, Backtest, Probability, Pattern Matching, Technology Necessity, standalone Committee, Timing, and descriptive Intelligence outputs. Their availability and similarly named fields do not establish semantic equivalence. Several are composites, have incompatible entity scope, lack strict-known provenance, or reuse evidence already represented by Market Validation or other candidate inputs.

This ADR decides the current source eligibility of all seventeen factors without changing the factor contract, weights, scoring formula, or runtime.

## Decision

No currently unowned factor receives a newly approved source. Of the twelve unowned factors, eight proposed mappings are explicitly rejected and four are deferred. All twelve remain missing in authorized Opportunity assembly.

The five existing canonical Market Validation mappings are reaffirmed unchanged. This decision is intentionally fail-closed: a durable model, repository, JSONL row, test, or similar field name is not enough to authorize a factor.

### Complete factor decision matrix

For the five reaffirmed rows, the exact record is canonical `MarketValidationProjectResultRecord` (`market-validation-project-result`). Eligibility requires project identity equality; `effective_at <= effective_as_of`; recorded and explicit `known_at <= known_by`; no known-time limitation; current non-superseded production lineage; compatible schema; and preserved record ID/version, source IDs/versions, evidence references, confidence, and times. Stale, unavailable, legacy/non-strict, incompatible, or missing data remains missing. Identity normalization is used except that `missing_evidence` is `clamp(len(missing_evidence) / 17, 0, 1)`.

| Factor | Current source state | Decision | Approved source or evaluated candidate | Exact field / normalization | Strict-known, missingness, and freshness | Correlation / double-counting analysis and rationale |
| --- | --- | --- | --- | --- | --- | --- |
| `valuation_discount` | Unowned | **Explicitly rejected** | No approved source; evaluated Market Validation `valuation` and `mispricing` | None | Remains missing | Those fields are broader conclusions, not a discount-to-reference measure. Reuse would feed Market Validation analysis back into Opportunity and overlap `hunter_score`, ranking, and committee evidence. |
| `relative_valuation` | Unowned | **Explicitly rejected** | No approved source; evaluated Market Validation `comparative_valuation` | None | Remains missing | Comparative valuation is not a declared normalized relative discount and can share the same valuation evidence as the rejected `valuation_discount` mapping and `hunter_score`. |
| `historical_discount` | Unowned | **Deferred / remains missing** | No current durable semantically equivalent record | None | Remains missing | Backtest history measures validation behavior, not price/value discount. A future source needs a benchmark, units, entity/representation scope, and point-in-time reference series. |
| `whale_accumulation` | Unowned | **Explicitly rejected** | No approved source; evaluated `WhaleSnapshot.accumulation_score`, whale metrics, and ADR 0015 findings | None | Remains missing | `WhaleSnapshot` has no target/project field, current snapshots have unknown known-time provenance, and descriptive on-chain observations cannot establish wallet ownership or accumulation intent. It would overlap canonical Timing's whale inputs. |
| `smart_money_positioning` | Unowned | **Explicitly rejected** | No approved source; evaluated `WhaleSnapshot.smart_money_score`, wallet flows, standalone Committee, and on-chain findings | None | Remains missing | “Smart money” requires actor identity, ownership, strategy, and positioning semantics that current records do not prove. It would duplicate whale evidence and improperly infer intent. |
| `developer_momentum` | Unowned | **Explicitly rejected** | No approved source; evaluated ADR 0011 Developer findings | None | Remains missing | Developer findings are descriptive, context-specific records without an authorized momentum aggregate or normalization. Recomposition would duplicate evidence already available to Market Validation and violate the descriptive non-scoring boundary. |
| `macro_tailwinds` | Unowned | **Deferred / remains missing** | Candidate for a future ADR: versioned `MacroSnapshot`, including `risk_on_score`; not approved now | None | Remains missing; current snapshot writes explicitly declare unknown known time and therefore fail strict-known eligibility | `risk_on_score` is a composite of liquidity, policy, crypto liquidity, and risk-off inputs also consumed by canonical Timing. A future mapping must define non-overlapping components and trustworthy known time before normalization can be approved. |
| `future_demand` | Unowned | **Explicitly rejected** | No approved source; evaluated `experimental.technology-necessity-assessment` fields and descriptive findings | None | Remains missing | Technology Necessity is technology-scoped, while Opportunity is project-scoped; its composite includes macro/probability/necessity inputs and is not observed future demand. Mapping it would double count proposed macro and derived-reasoning evidence. |
| `sector_strength` | Unowned | **Deferred / remains missing** | No current persisted sector taxonomy plus strength assessment | None | Remains missing | No durable sector membership/version, cohort boundary, or strict-known sector-strength record exists. Market Validation ranks projects, not sectors. |
| `capital_formation` | Unowned | **Explicitly rejected** | No approved source; evaluated `TechnologyNecessityAssessment.capital_rotation_score`, Macro liquidity, and Whale flows | None | Remains missing | Capital rotation, liquidity, and wallet flow are not capital formation. The candidate mappings overlap `macro_tailwinds`, whale factors, and canonical Timing. |
| `validation_health` | Approved canonical source | **Reaffirm existing source** | Market Validation production data | `validation_health`; identity `[0,1]` | Strict-known contract above; unavailable source fails closed to the existing validation gate | It is a gate, not a substitute score. It may not be derived from `hunter_score`, committee confidence, Timing, Probability, or Dashboard health. |
| `evidence_freshness` | Approved canonical source | **Reaffirm existing source** | Market Validation production data | `data_freshness`; identity `[0,1]` | Strict-known contract above; stale or absent remains missing | Must not be recomputed from Macro/Whale timestamps or Dashboard/store health, preventing duplicate freshness reward. |
| `confidence` | Approved canonical source | **Reaffirm existing source** | Market Validation production data | `confidence`; identity `[0,1]` | Strict-known contract above; absent remains missing | Standalone Probability, Pattern, Necessity, Committee, or source-specific confidence cannot substitute or create a second confidence reward. |
| `backtesting_quality` | Unowned | **Deferred / remains missing** | Candidate for a future ADR: immutable `BacktestRun` project metrics; not approved now | None | Remains missing; current backtest records lack a complete effective/recorded/known-time contract for strict-known project selection | Coverage, consistency, calibration, and reliability are distinct measures; choosing or combining them would create new semantics. Raw accuracy would also overlap future prediction evaluation and Market Validation historical validation. |
| `historical_opportunity_similarity` | Unowned | **Explicitly rejected** | No approved source; evaluated `experimental.pattern-assessment.native_assessment.historical_similarity` and `overall_similarity` | None | Remains missing | Current similarity is outcome-agnostic: similarity to an unsuccessful pattern can be high and would become supportive under the Opportunity formula. Deriving a positive-only value would be a new factor interpretation and could reuse Probability, Timing, Fusion, and snapshot evidence embedded in Pattern inputs. |
| `risk` | Approved canonical source | **Reaffirm existing source** | Market Validation production data | `risk`; identity `[0,1]` | Strict-known contract above; absent remains missing and never becomes a reassuring zero | Security, Tokenomics, Macro, Whale, Committee, Timing, or descriptive findings cannot be folded into a second risk value. This prevents reuse of evidence already reflected in Market Validation. |
| `missing_evidence` | Approved canonical source | **Reaffirm existing source** | Market Validation production data | `missing_evidence`; `clamp(count / 17, 0, 1)` | Strict-known contract above; assembly also preserves every factor's own missing state | It is an explicit penalty, not evidence. Dashboard emptiness, repository health, or missing fields in other experimental assessments cannot substitute or be counted twice. |

### Resulting source state

- Newly approved mappings: **0**.
- Explicitly rejected mappings: **8** — `valuation_discount`, `relative_valuation`, `whale_accumulation`, `smart_money_positioning`, `developer_momentum`, `future_demand`, `capital_formation`, and `historical_opportunity_similarity`.
- Deferred mappings: **4** — `historical_discount`, `macro_tailwinds`, `sector_strength`, and `backtesting_quality`.
- Reaffirmed existing mappings: **5** — `validation_health`, `evidence_freshness`, `confidence`, `risk`, and `missing_evidence`.
- Factors that remain missing after this ADR: **12**, namely every rejected or deferred factor above.

“Rejected” rejects the evaluated current mapping, not all conceivable future evidence. “Deferred” means the repository lacks enough semantic or temporal contract to evaluate a concrete mapping. Either status can change only through a future accepted ADR with repository-backed evidence and the complete contract required by ADR 0017.

### Mandatory anti-double-counting boundary

An Opportunity input service must not consume `hunter_score`, Market Validation rank, canonical committee fields, canonical Timing, Dashboard projections, Operational Corpus data, or report prose as factor inputs. It must not reuse the same evidence lineage in multiple approved factors unless a future ADR defines a deterministic non-overlapping partition and records that partition in the snapshot.

In particular, Whale data cannot supply both accumulation and smart-money factors; Macro composites cannot supply macro, capital-formation, and Timing-derived support; Technology Necessity cannot supply future demand and capital formation; Pattern or Probability cannot repackage inputs already used by Opportunity; and source-specific confidence/freshness cannot supplement the canonical Market Validation confidence/freshness factors. When lineage overlap cannot be proven absent, the candidate factor remains missing.

### Dependency contract for later Phase 3.3 work

This ADR authorizes no Phase 3.3 implementation because it approves no new source. Any later implementation following a future source-approval ADR must satisfy all of these rules:

1. A service receives persisted record IDs and compatible schema/version identities only. In-memory assessment or snapshot object handoff is prohibited.
2. The service resolves exact records in their authorized store and selects only records satisfying effective, recorded, and explicit known-time cutoffs, current immutable lineage, target/entity scope, methodology/configuration compatibility, freshness, and confidence requirements.
3. There is no `latest`, current-state, raw-file, manually entered, Dashboard, Operational Corpus, or report-text fallback.
4. Missing, stale, legacy/non-strict, incompatible, ambiguous-identity, cross-entity, conflicted, or overlapping evidence fails closed to an explicit missing factor and lower confidence; it never becomes zero-valued support.
5. Source IDs, versions, fields, transformations, evidence lineage, cutoffs, confidence, missingness, and rejection reasons are persisted in the immutable experimental Opportunity snapshot.
6. Pure engines and repositories perform no source selection or I/O orchestration. The service owns assembly and write authorization; repositories only retrieve exact persisted IDs and store authorized records.

### Authority and non-goals

This decision is limited to experimental Opportunity research. It does not promote Opportunity, Probability, Pattern Matching, Technology Necessity, standalone Committee, Backtest, Macro, Whale, Timing, or any descriptive Intelligence output to production. ADR 0016 and ADR 0017 are reaffirmed, not superseded. Market Validation remains the sole canonical production analytical runtime.

This ADR does not change factors, weights, scoring, validation gates, Market Validation, Timing, persistence schemas, source records, runtime wiring, ranking, Dashboard/API/UI, Operational Corpus, alerts, automation, scheduling, Tokenomics, unlock risk, catalysts, sell pressure, Sufficiency, or general cross-domain ranking authority.

## Compatibility With Accepted ADRs

| ADR | Compatibility effect |
| --- | --- |
| 0001 | Discovery remains upstream; factor sourcing cannot become a discovery or candidate-prioritization shortcut. |
| 0002 | Provenance, explicit missingness, confidence, freshness, conflicts, and cutoff-safe replay cause every unsupported mapping to fail closed. |
| 0003 | Candidate Registry identity remains authoritative; no factor source may invent or infer project membership. |
| 0004 | Trust, reliability, conflicts, staleness, and unavailable states must be preserved rather than normalized away. |
| 0005 | Project, technology, asset, token, contract, wallet, network, listing, and global macro scopes are not interchangeable; this rejects several tempting mappings. |
| 0006 | No graph authority or graph-based factor is introduced; existing registry and persisted evidence boundaries remain authoritative. |
| 0007 | Reaffirmed: Option A/Market Validation remains the production runtime and Opportunity remains isolated research. |
| 0008 | Plugins and orchestration cannot confer factor authority or bypass persisted-ID and source-contract requirements. |
| 0009 | Services own input meaning, clocks, replay, lifecycle, and write authorization; repositories only retrieve/store authorized records. |
| 0010 | Intelligence engines remain pure descriptive engines; downstream factor composition, if ever approved, belongs to an Opportunity service. |
| 0011 | Developer findings remain descriptive and do not establish `developer_momentum`; the current mapping is rejected. |
| 0012 | Tokenomics remains descriptive and no tokenomics, unlock, sell-pressure, catalyst, valuation, or risk mapping is authorized. |
| 0013 | Governance findings remain descriptive; the current factor contract contains no governance factor and this ADR creates none. |
| 0014 | Security findings remain descriptive and cannot become Opportunity `risk` or another factor. |
| 0015 | On-chain findings cannot infer ownership, intent, strategy, or accumulation; current whale/smart-money mappings are rejected. |
| 0016 | Reaffirmed, not superseded: one canonical production runtime and no parallel analytical authority. |
| 0017 | Reaffirmed, not superseded: experimental factors require exact persisted authority, strict-known replay, missingness, and anti-double-counting; this ADR grants no production authority. |

No accepted ADR 0001–0017 is superseded, weakened, or contradicted.

## Consequences

- Phase 3.2 can continue to persist honest partial or all-missing experimental snapshots without acquiring unsupported values.
- Phase 3.3 remains blocked for the twelve unowned factors until future ADRs approve concrete, repository-backed mappings.
- Current experimental and production packages remain unchanged.
- Research may improve source contracts, entity scope, known-time provenance, and anti-correlation evidence without claiming factor authority.

## Alternatives Considered

### Approve every similarly named persisted field

Rejected because field-name similarity does not prove semantic equivalence, entity alignment, point-in-time safety, or non-overlapping evidence.

### Approve Macro, Whale, Backtest, Necessity, and Pattern composites provisionally

Rejected because provisional supportive values would conceal unknown known time, entity ambiguity, outcome direction, and correlated composite inputs. Missingness is the truthful current state.

### Ban future factor sourcing permanently

Rejected because repository-backed evidence and a future accepted ADR could establish a valid mapping. This decision rejects current mappings, not future proof.
