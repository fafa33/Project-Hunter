# ADR 0021: Canonical Valuation Evidence Authority

## Status

Accepted.

## Context

ADR 0020 made `valuation`, `comparative_valuation`, `mispricing`, and `asymmetry` explicitly unavailable after confirming that one generic CoinGecko market-profile/completeness value had been relabeled as all four. Removing that alias restored truthful missingness, but Canonical Market Validation still needs durable contracts that can eventually distinguish genuinely undervalued opportunities from market-data availability.

The current acquisition layer can preserve observed market facts such as price, supply, market capitalization, volume, provider identity, retrieval time, and metadata completeness. These observations do not establish fundamental value, comparable selection, mispricing, payoff distributions, or investment attractiveness. The Candidate Registry and entity model also distinguish projects, protocols, networks, native assets, tokens, contracts, wrapped or bridged representations, and provider listings. A valuation attached at the wrong level can be numerically precise and economically false.

The generic analytical persistence envelope can preserve immutable, bitemporal, provenance-linked records, but persistence does not authorize their meaning. ADR 0016 makes Market Validation the sole canonical production analytical runtime. ADR 0020 requires strict-known input selection and prohibits aliases, future data, latest/current fallbacks, and timestamp backfill. This ADR defines the evidence and service-authority contracts that must exist before the four valuation-family inputs can become available.

## Decision

Hunter adopts four separate canonical valuation-family authorities. They are coordinated inputs to Canonical Market Validation, not independent investment runtimes. Each service owns only its declared assessment and service-authorized persistence plan:

- `CanonicalValuationService` owns fair-value estimation and the canonical `valuation` assessment;
- `CanonicalComparativeValuationService` owns immutable peer selection and `comparative_valuation`;
- `CanonicalMispricingService` owns comparison of an authorized fair-value estimate with a separate compatible observed market value; and
- `CanonicalAsymmetryService` owns versioned scenario/payoff assembly and `asymmetry`.

`EvidenceBackedProjectExecutor` remains the sole canonical production analytical runtime and sole consumer authorized to combine these assessments into Market Validation scoring. Providers acquire observations. Services validate meaning, identity, provenance, time, compatibility, methodology, corrections, and persistence authorization. Repositories store and retrieve service-authorized immutable records. No provider, repository, configuration file, scheduler job, Dashboard projection, report, test, or field name becomes a valuation owner.

This ADR authorizes the semantic and record contracts, not a numeric production methodology, store activation, provider acquisition, or runtime implementation. Where a required methodology, calibration set, entity linkage, or evidence family does not yet exist, the corresponding Market Validation input remains explicitly missing.

### Authority matrix

| Input | Exact meaning and decision purpose | Scope, unit, direction, range, and horizon | Sole production owner and required records | Normalization, confidence, missingness, and correlation | Strict-known, correction, and prohibited substitutes |
| --- | --- | --- | --- | --- | --- |
| `valuation` | A versioned estimate distribution of the economic value attributable to one declared asset representation, derived from auditable fundamental value creation and value-capture evidence. Its purpose is to establish a defensible fair-value basis, not whether the asset is currently cheap. | Target must bind economic entity, valued asset/claim, representation, chain/contract where applicable, quote currency, supply basis, and entitlement/value-capture scope. Raw unit is quote currency per diluted unit and total diluted quote-currency value, with `p10`, `p50`, and `p90`. Horizon is fixed by methodology; the first production methodology must use an explicit 365-day horizon unless a later ADR authorizes another. Raw range is non-negative. No directionally favorable `[0,1]` Market Validation value is authorized until a versioned monotonic normalization is historically calibrated without using post-cutoff outcomes. | `CanonicalValuationService`; consumes immutable `FundamentalEvidenceRecord`, `ValueCaptureRuleSnapshot`, `SupplyBasisSnapshot`, `ValuationMethodologySnapshot`, and produces `FairValueEstimateRecord` plus `ValuationAssessmentRecord`. | Methodology defines allowed models, aggregation, discount/risk assumptions, sensitivity ranges, and normalization. Confidence is bounded by entity-link confidence, fundamental coverage, value-capture certainty, supply certainty, source reliability, conflict penalties, model dispersion, and freshness. Missing attributable fundamentals, supply basis, value-capture rule, methodology, or calibrated normalization keeps the scalar input missing. Valuation evidence may be shared by downstream inputs only by reference; its contribution is counted once. | Every transitive record must be strict-known at cutoff. Corrections append successors. Price, market cap, completeness, TVL, fees or revenue without attributable value capture, tokenomics/descriptive findings, provider estimates, analyst prose, Opportunity, rankings, Dashboard, Operational Corpus, and current/latest files cannot substitute. |
| `comparative_valuation` | A deterministic assessment of how the target's observed market valuation compares with economically compatible peers after applying one predeclared fundamental denominator and adjustment policy. Its purpose is relative valuation, not absolute fair value. | Target and peers must share compatible entity, asset-claim, value-capture, currency, accounting period, and representation scope. Raw units are the declared market-value-to-fundamental multiple and a peer-relative log residual or percentage residual. Positive direction means cheaper than the compatible peer reference after the methodology's sign convention. Residual may be unbounded; the Market Validation value is `[0,1]` only through a predeclared historically calibrated monotonic transform. Horizon/measurement window is fixed in the methodology and all observations use one compatible cutoff window. | `CanonicalComparativeValuationService`; consumes `ObservedMarketFactRecord`, compatible `FundamentalEvidenceRecord`, `PeerUniversePolicyRecord`, and an immutable `PeerUniverseSnapshot`; produces `ComparativeValuationAssessmentRecord`. | Peer policy fixes eligibility, exclusions, minimum cohort size, sector/capability taxonomy version, lifecycle state, representation compatibility, denominator, outlier treatment, weighting, minimum coverage, and tie-breaking before target evaluation. Confidence incorporates cohort size, peer compatibility, denominator coverage, source conflicts, dispersion, and freshness. Insufficient peers or denominator evidence means missing. Comparative and mispricing signals are correlated: configuration must declare their correlation group and cap their combined contribution. | The target, every peer, cohort membership, and every observation must be strict-known. Later reclassification creates a successor peer snapshot and cannot rewrite replay. Ad-hoc sectors, current provider categories/lists, current market-cap rankings, hand-picked peers, broad market averages, valuation output alone, Dashboard sorting, and Opportunity/general ranking are prohibited. |
| `mispricing` | The signed divergence between one authorized canonical fair-value estimate and a separately observed market price/value for the identical asset claim, representation, quote currency, supply basis, and compatible effective window. Its purpose is to quantify under- or overpricing relative to that declared estimate. | Raw unit is a decimal return-like ratio: `(fair_value_p50 - observed_market_price) / observed_market_price`. Positive means estimated undervaluation; zero means equality; negative means estimated overvaluation. Raw range is `[-1, +∞)` when both inputs are positive. The fair-value distribution and uncertainty interval remain attached. Horizon equals the referenced valuation horizon; market observation tolerance is fixed by methodology. A `[0,1]` score requires a versioned monotonic transform calibrated on strict historical data and must preserve sign around a declared neutral point. | `CanonicalMispricingService`; consumes exact-version `FairValueEstimateRecord`, `ObservedMarketFactRecord`, entity/representation linkage, and `MispricingMethodologySnapshot`; produces `MispricingAssessmentRecord`. | Confidence cannot exceed either prerequisite and is further reduced by valuation dispersion, price-source conflicts, liquidity/market-quality limitations, timestamp distance, representation uncertainty, and stale evidence. Zero/negative market price, unit mismatch, missing quote conversion, incompatible supply basis, absent fair value, or absent normalization makes the input missing. The service references rather than re-counts valuation evidence; Market Validation must place valuation and mispricing in one declared correlation group with a combined-weight cap. | Fair value and market observation must both be strict-known and temporally compatible. A later price or corrected valuation creates a successor assessment, never a rewrite. Valuation alone, price alone, market-cap completeness, price return, discount labels, comparative valuation, provider targets, report prose, Opportunity score, or current quote cannot substitute. |
| `asymmetry` | The probability-weighted balance of favorable and adverse payoff for the same asset representation across an immutable, predeclared scenario set. Its purpose is to express reward-versus-loss asymmetry under uncertainty, not to repeat mispricing. | Scenario horizon, baseline price, quote currency, entity/representation, terminal valuation rule, probability policy, and payoff units are fixed before calculation. Raw upside is the probability-weighted positive terminal return; raw downside is the absolute probability-weighted negative terminal return. Raw asymmetry ratio is `expected_positive_payoff / expected_negative_payoff`, range `[0, +∞)`. Zero downside does not imply infinity: the methodology must declare a finite cap or mark the result insufficient. Higher means more favorable. A `[0,1]` value requires a versioned monotonic transform and declared neutral ratio. | `CanonicalAsymmetryService`; consumes `ObservedMarketFactRecord`, immutable `ScenarioSetSnapshot`, `ScenarioEvidenceRecord`, `ScenarioProbabilityRecord`, and `ScenarioPayoffEstimateRecord`; may reference a compatible `FairValueEstimateRecord` only under the declared anti-double-counting policy; produces `AsymmetryAssessmentRecord`. | Probabilities must be declared before payoff evaluation, sum under a versioned policy, and expose uncertainty. Confidence reflects scenario coverage, probability quality, payoff uncertainty, dependency/correlation structure, tail coverage, price quality, and model dispersion. Missing material downside, unsupported probabilities, undefined denominator, incomplete scenario coverage, or absent normalization makes the input missing. Evidence already used by valuation/mispricing is referenced and assigned to one contribution group; duplicated evidence cannot independently increase multiple scores. | All scenarios, probabilities, baselines, dependencies, and payoff estimates must be immutable and strict-known. Corrections append successors to affected scenario and assessment records. Generic profile data, historical return or volatility alone, one optimistic/base case, risk score, mispricing copied as upside, scenario-simulation labels without this contract, experimental Probability/Pattern/Opportunity, reports, and current/latest data are prohibited. |

### Current availability decision

All four Market Validation scalar inputs remain **unavailable** after acceptance of this ADR. The repository does not yet have the complete authorized record families, value-capture/entity linkage, production methodologies, strict-known calibration sets, normalization policies, or service-owned persistence paths required above. This is deliberate fail-closed behavior, not an implementation defect to bypass.

The raw formula specified for mispricing and the raw payoff formula specified for asymmetry do not authorize a scalar Market Validation input until their prerequisites, confidence policy, correlation controls, versioned normalization, and historical leakage tests are implemented and accepted. No formula is authorized for estimating fair value or selecting/adjusting comparable multiples merely to fill a field.

## Evidence and record-family boundaries

Hunter separates five layers. A record may be consumed by the next layer only through exact IDs and versions; layers cannot be collapsed by relabeling.

1. **Observed market facts:** provider-observed price, quote currency, circulating/total/max supply, market capitalization, volume, venue or aggregation scope, provider listing ID, observation/effective time, retrieval/recorded time, explicit known time, raw payload hash, units, quality flags, conflicts, and canonical entity/representation linkage.
2. **Fundamental valuation evidence:** attributable protocol cash flow, fees/revenue only with an explicit value-capture path, economic entitlement, token/network utility with measurable value transfer, dilution/emission/claim seniority, treasury or liabilities where attributable, supply basis, accounting window, source methodology, and uncertainty. Descriptive observations remain non-valuation until the valuation service validates this contract.
3. **Fair-value estimates:** immutable input snapshot, allowed valuation method, assumptions, sensitivity analysis, discount/risk policy, value-capture rule, supply basis, `p10/p50/p90`, horizon, units, model dispersion, confidence, methodology/configuration fingerprints, and complete provenance.
4. **Comparative analysis:** immutable peer-policy version, peer-universe snapshot, inclusion/exclusion reasons, comparable denominator, target and peer measurements, adjustment/outlier policy, minimum cohort rule, residual calculation, correlation group, confidence, and lineage.
5. **Scenario evidence:** immutable scenario definitions, dependency graph/version where used, baseline, horizon, probability source and uncertainty, terminal payoff model, positive/negative payoff, correlation/dependency matrix, tail and missing-scenario policy, and complete provenance.

### Required new record families and minimum fields

Every family uses an immutable bitemporal envelope with record ID, logical ID, schema version, semantic version, effective time, recorded time, explicit known time, source IDs paired with source versions, evidence references, canonical hash, confidence, missing/conflicting evidence, methodology/configuration fingerprints where applicable, and correction/supersession lineage.

| Record family | Additional minimum fields |
| --- | --- |
| `ObservedMarketFactRecord` | canonical entity ID; asset/claim and representation ID; chain/contract and provider listing IDs where applicable; fact type; value; unit/quote currency; venue/aggregation scope; observation time; provider; raw record/hash; quality/conflict flags |
| `FundamentalEvidenceRecord` | economic entity; attributable asset claim; evidence type; value and unit; accounting period; attribution/value-capture rule; source methodology; entity-link confidence; uncertainty; conflict state |
| `ValueCaptureRuleSnapshot` | valued claim; cash-flow/value pathway; entitlement or burn/buyback/distribution/utility mechanism; dilution and seniority treatment; applicability period; evidence IDs; limitations |
| `SupplyBasisSnapshot` | representation; circulating, total, diluted, locked and excluded quantities as applicable; unit; supply policy; observation time; source versions; conflicts |
| `ValuationMethodologySnapshot` | permitted model family; horizon; currency; assumptions; discount/risk and terminal-value rules; sensitivity policy; model aggregation; required evidence; normalization policy ID; correlation group |
| `FairValueEstimateRecord` / `ValuationAssessmentRecord` | target scope; total and per-unit `p10/p50/p90`; currency; horizon; model estimates and dispersion; complete input snapshot; value-capture and supply references; confidence decomposition; scalar normalization status/value or explicit unavailable reason |
| `PeerUniversePolicyRecord` / `PeerUniverseSnapshot` | taxonomy/version; eligibility and exclusion rules; minimum cohort; denominator; lifecycle/representation compatibility; ordered peer IDs/versions; inclusion/exclusion reasons; cutoff; outlier/weighting/tie policy |
| `ComparativeValuationAssessmentRecord` | target and peer snapshot; target/peer multiples; denominator and units; reference statistic; raw residual; normalized value/status; cohort coverage/dispersion; confidence; correlation group |
| `MispricingMethodologySnapshot` / `MispricingAssessmentRecord` | fair-value and market-fact IDs/versions; exact entity/representation match; formula/version; timestamp tolerance; raw signed ratio; uncertainty interval; neutral point; normalized value/status; confidence; correlation group |
| `ScenarioSetSnapshot` / `ScenarioEvidenceRecord` | scenario IDs/types; baseline; horizon; causal assumptions; dependencies/correlations; evidence and source versions; material omitted-scenario policy; uncertainty |
| `ScenarioProbabilityRecord` / `ScenarioPayoffEstimateRecord` | scenario-set version; probability and uncertainty; probability authority/method; terminal value/payoff and unit; positive/negative classification; model/config version; evidence lineage |
| `AsymmetryAssessmentRecord` | exact scenario/payoff versions; expected positive and negative payoff; raw ratio; denominator/cap treatment; normalized value/status; tail coverage; confidence decomposition; correlation group |

This ADR selects logical record families, not a database product, schema migration, file path, or store activation. A later implementation plan may use the generic analytical envelope only through domain-specific semantic allow-listing and the four service-owned authorization boundaries.

## Source-provider eligibility

1. CoinGecko and similar market-data providers may provide only facts they actually observe or publish: price, quote currency, supply measures, market capitalization, volume, venue/aggregation metadata, provider identifiers, timestamps, and raw metadata. Provider categories and completeness are metadata, not valuation.
2. Provider facts are eligible only after canonical entity/representation resolution, unit validation, provenance preservation, conflict detection, and immutable persistence. A provider listing is not the economic entity or valued asset claim.
3. Protocol revenue, fees, TVL, token supply, treasury, emissions, unlocks, developer activity, governance, security, and on-chain observations may enter `FundamentalEvidenceRecord` only when their exact semantic contract and attribution to the valued claim are established. Their existence does not automatically imply value capture.
4. Third-party fair-value targets, ratings, peer sets, scenario probabilities, or analyst opinions are evidence observations, never canonical conclusions. They require source licensing, timestamp, methodology visibility, conflict handling, and independent service validation; opaque targets cannot become fair value.
5. Missing, unavailable, rate-limited, stale, disputed, unit-incompatible, representation-ambiguous, or unknown-known-time provider data remains missing. No provider fallback may fabricate a zero, median, prior value, or current/latest replacement.
6. Providers never select peers, estimate fair value, declare mispricing, assign scenario probabilities, calculate asymmetry, normalize inputs, resolve corrections, or authorize persistence.

## Time, replay, provenance, and lifecycle

All records and transitive dependencies follow ADR 0020 strict-known semantics. The Market Validation cutoff is selection context, never a replacement record timestamp. An input is eligible only when its effective time, recorded time, explicit known time, and all transitive source known times satisfy the declared cutoff and its methodology's observation-window/tolerance policy.

Service assembly persists an immutable input snapshot before or atomically with its assessment. Exact replay must reproduce target scope, source record IDs/versions, peer/scenario membership, assumptions, methodology/configuration versions, raw values, normalization, confidence, missingness, and canonical hash. Unknown-known-time and legacy latest/current records are never strict-known eligible.

Corrections are append-only successors with predecessor ID, reason, authorizing service, and corrected time/provenance. A correction available after a replay cutoff cannot change the earlier selection. Entity relinking, provider revisions, peer-policy changes, scenario changes, methodology changes, and normalization recalibration create new versions or successors; they never rewrite history.

## Anti-double-counting and correlation policy

- Every evidence record has one primary contribution group within a Market Validation methodology. Other assessments may reference it for dependency or confidence without independently counting the same economic signal.
- `valuation` and `mispricing` are one valuation/fair-value correlation group because mispricing depends on valuation. Their combined weight is capped by configuration and cannot exceed the predeclared group weight.
- `comparative_valuation` joins that group when it shares the same market-value or fundamental denominator evidence; otherwise the methodology must demonstrate distinct evidence and residual independence before assigning a separate contribution.
- `asymmetry` cannot count the same fair-value delta as both mispricing upside and scenario upside. When fair value is a scenario input, the scenario contribution must reference it and the group policy must remove or cap the duplicated component.
- Correlation groups, caps, evidence-to-factor assignments, and residualization rules are immutable methodology/configuration inputs fixed before execution. Missing correlation analysis means the affected scalar inputs remain unavailable.

## Implementation order after acceptance

1. Implement canonical entity/asset-claim/representation linkage and immutable `ObservedMarketFactRecord` persistence with strict-known reads; preserve CoinGecko and other providers as fact sources only.
2. Implement `FundamentalEvidenceRecord`, value-capture rules, and supply-basis snapshots, including conflict, attribution, unit, and missingness validation.
3. Adopt a separate methodology ADR or accepted methodology specification for the first supported entity class. Implement `CanonicalValuationService`, fair-value snapshots, historical leakage tests, calibration, and normalization. Do not attempt one universal crypto formula.
4. Implement deterministic peer-policy records/snapshots and `CanonicalComparativeValuationService` for the same supported entity class.
5. Implement `CanonicalMispricingService` only after compatible fair-value and observed-market records exist.
6. Implement scenario/probability/payoff records and `CanonicalAsymmetryService`, including tail, dependency, correlation, and duplicate-evidence controls.
7. Add the four service-authorized input adapters to Canonical Market Validation, then run read-only strict-known eligibility and historical replay gates before any canary.
8. Run one controlled canary only when all configured required inputs are genuinely available; otherwise retain `INSUFFICIENT_EVIDENCE`.

Implementation proceeds entity class by entity class. Supporting one explicit asset-claim model does not authorize inference for unrelated protocols, networks, tokens, stablecoins, wrapped assets, or non-token projects.

## Acceptance criteria for a future canonical canary

A candidate is eligible for a valuation-backed canary only when tests and preflight prove:

1. One canonical economic entity, asset claim, and representation are resolved without ambiguity at the cutoff.
2. Observed market facts retain provider-observed values, units, observation/effective time, recorded time, explicit known time, raw provenance, and conflicts; completeness is never an analytical value.
3. The applicable value-capture, supply, and fundamental evidence contracts are complete for the supported entity class.
4. Fair-value `p10/p50/p90`, horizon, units, assumptions, model dispersion, confidence, and immutable inputs reproduce under strict-known replay.
5. Peer policy and peer snapshot were fixed at cutoff, meet minimum cohort/compatibility rules, and reproduce without current-list or ad-hoc selection.
6. Mispricing references the exact fair-value and separately observed market-fact versions, matches entity/representation/currency/supply scope, and uses the declared signed formula and timestamp tolerance.
7. Asymmetry uses a predeclared complete scenario set, probability/uncertainty policy, downside and tail evidence, dependency/correlation controls, and no duplicated mispricing payoff.
8. Every scalar normalization is versioned, monotonic, historically calibrated using strict-known data, leakage-tested, and has an explicit unavailable state outside its supported range or scope.
9. Confidence decompositions, missingness, conflicts, stale evidence, correlation groups, combined-weight caps, and provenance are present and deterministic.
10. Future, latest/current, legacy, unknown-time, superseded, stale, invalid, incompatible, opaque, and similarly named records are rejected.
11. Corrections and methodology changes create immutable successors and replay selects only corrections known by the cutoff.
12. The complete Market Validation run persists atomically with record IDs, source versions, methodology/configuration fingerprints, report hash, and no partial project result.
13. Failure of any required contract produces explicit `INSUFFICIENT_EVIDENCE`, no qualified candidate, and no synthesized score.
14. No Dashboard, Operational Corpus, automation, scheduler, report, provider, repository, or experimental engine establishes or recalculates valuation authority.

## Compatibility With Accepted ADRs

| ADR | Compatibility effect |
| --- | --- |
| 0001 | Discovery remains market-wide and precedes deep valuation; this ADR does not value or rank during discovery. |
| 0002 | Every valuation-family result is provenance-preserving, conflict-visible, confidence-bearing, missingness-explicit, and replay-safe. |
| 0003 | Candidate Registry remains canonical candidate identity/lifecycle authority and does not own valuation. |
| 0004 | Trust, reliability, identity confidence, conflicts, freshness, and unavailable states precede every valuation conclusion. |
| 0005 | Economic entity, asset claim, token/representation, contract, network, protocol, and provider listing scopes are explicit and non-interchangeable. |
| 0006 | Knowledge/technology/economic graphs may supply declared relationship evidence but cannot own or substitute valuation conclusions. |
| 0007 | Option A remains canonical; no parallel production runtime is introduced. |
| 0008 | Plugins cannot become valuation providers or bypass evidence, identity, service, replay, or persistence authority. |
| 0009 | The four services own validation, clocks, methodology application, corrections, and writes; repositories remain mechanical. |
| 0010 | Intelligence engines remain descriptive and cannot calculate or persist valuation-family assessments. |
| 0011 | Developer findings may be evidence only under a future attributable fundamental contract; activity is not value. |
| 0012 | Tokenomics observations require asset-claim, supply, dilution, and value-capture contracts; fees, revenue, TVL, unlocks, and balances are not valuation by themselves. |
| 0013 | Governance findings remain descriptive and cannot become discount rates, probabilities, or valuation scores without a later explicit methodology contract. |
| 0014 | Security findings remain descriptive and cannot become downside, discount, or asymmetry values by relabeling. |
| 0015 | On-chain observations do not imply ownership, value capture, intent, profitability, or mispricing. |
| 0016 | Reaffirmed, not superseded: Market Validation remains the sole canonical production analytical runtime; the four services own inputs only. |
| 0017 | Experimental Opportunity remains isolated and cannot consume or present incomplete valuation inputs as production. |
| 0018 | Experimental factor mappings confer no production valuation authority and cannot bypass missingness or anti-double-counting gates. |
| 0019 | Prediction Evaluation remains separate audit authority; outcomes cannot retroactively tune or relabel a valuation at its original cutoff. |
| 0020 | Reaffirmed and specialized: aliases remain removed, strict-known selection is mandatory, and every unsupported valuation-family input remains missing. |

No accepted ADR 0001–0020 is superseded, weakened, or contradicted.

## Consequences

- Hunter gains an auditable path from observed facts to fair value, comparison, mispricing, and payoff asymmetry without treating data completeness as opportunity.
- All four scalar inputs remain unavailable until their complete evidence, methodology, normalization, calibration, service, and persistence contracts are implemented.
- The first implementation must support a narrow explicit entity/asset-claim class rather than pretend one formula values every crypto project.
- More missing results are expected and correct until real attributable evidence exists.
- Shared evidence and correlated outputs receive explicit contribution groups and weight caps.

## Alternatives Considered

### Restore CoinGecko-derived proxy scores

Rejected because price, supply, market cap, volume, categories, and completeness are observed facts, not fair value, peer analysis, mispricing, or payoff asymmetry.

### Adopt one universal valuation formula now

Rejected because protocols, networks, native assets, tokens, stablecoins, wrapped representations, and non-token projects expose materially different economic claims and evidence.

### Use current sector averages and market-cap rankings as comparables

Rejected because the cohort would be mutable, provider-defined, representation-ambiguous, and unreplayable.

### Treat valuation-to-price delta as both mispricing and asymmetry

Rejected because it would duplicate one signal and omit scenario probability, downside, tail, and dependency evidence.

### Fill missing inputs with neutral values or provider targets

Rejected because neutral defaults and opaque external conclusions conceal missing authority and create false score completeness.
