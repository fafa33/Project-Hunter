# ADR 0024: Valuation Scalar Semantics Boundary

## Status

Proposed.

## Context

ADR 0021 and ADR 0022 require a versioned, monotonic, historically calibrated scalar normalization before `valuation` may become available as a directionally favorable `[0,1]` Market Validation input.

Architecture review of Issue #107 found that this requirement is not implementable without inventing a new semantic meaning. ADR 0022 defines fair value as a quote-currency estimate for one declared representation and explicitly states that fair values are not comparable across representations without a separately authorized comparability contract. Absolute per-unit or total fair-value magnitude therefore cannot truthfully mean "better" when mapped monotonically to `[0,1]` across assets whose denominations, supplies, and economic claims differ.

Using observed market price, market capitalization, or fully diluted valuation to turn fair value into a directionally favorable opportunity score would instead measure divergence between fair value and market value. ADR 0021 assigns that meaning exclusively to `mispricing`, and ADR 0022 prohibits price-derived fair-value methodology. Normalizing confidence, freshness, or model dispersion would measure evidence reliability rather than underlying fair value and would therefore not satisfy the existing wording either.

Without this amendment, Milestone 4 of Issue #107 would force an implementer to choose an economically false scalar, collapse `valuation` into `mispricing`, or silently redefine confidence as valuation.

## Decision

Hunter separates fundamental-value magnitude, estimate reliability, and market-relative attractiveness without exception:

- `valuation` owns the raw, immutable `FairValueEstimateRecord` distribution (`p10`/`p50`/`p90`) and its independently exposed confidence decomposition.
- `valuation` does **not** produce a directionally favorable `[0,1]` scalar from absolute fair-value magnitude.
- `mispricing` exclusively owns comparison of an authorized fair-value estimate with a compatible observed market price or value and exclusively owns any directionally favorable scalar derived from that comparison.
- `comparative_valuation` exclusively owns peer-relative valuation.
- `asymmetry` exclusively owns scenario/payoff balance.

A larger absolute fair-value estimate is not architecturally defined as a better investment signal. No monotonic transform of `fair_value_per_diluted_unit_*`, `total_diluted_value_*`, confidence, freshness, or dispersion may be labeled as the canonical `valuation` scalar.

### Exact amendment to ADR 0021

This ADR amends only the `valuation` row and directly related availability language in ADR 0021:

1. In the `valuation` row, the sentence requiring a directionally favorable `[0,1]` normalization of fair value is removed.
2. `valuation` becomes eligible for Market Validation as a structured, confidence-bearing assessment containing the raw fair-value distribution, units, horizon, methodology identity, provenance, uncertainty, and explicit availability state.
3. Missing attributable fundamentals, supply basis, value-capture rule, methodology, strict-known provenance, or required confidence decomposition keeps `valuation` unavailable. Absence of scalar normalization does not.
4. The required-record-family description for `FairValueEstimateRecord` / `ValuationAssessmentRecord` is amended so that scalar normalization status/value is not required for `valuation`.
5. ADR 0021 acceptance criterion 8 continues to govern every scalar normalization that remains authorized for `comparative_valuation`, `mispricing`, or `asymmetry`; it no longer creates a scalar-normalization requirement for raw fundamental `valuation`.
6. `ValuationAssessmentRecord` remains part of the `valuation`/`mispricing` correlation group defined by ADR 0021's anti-double-counting and correlation policy, but that membership is semantic only: it identifies which assessments describe the same underlying economic claim for conflict and lineage purposes. Correlation-group weight caps apply only to assessment records that actually contribute a weighted scalar input to Market Validation composition. Because `ValuationAssessmentRecord` no longer contributes a directionally favorable scalar under this ADR, it contributes no weighted scalar to any correlation-group weight cap until a future ADR explicitly authorizes one for `valuation`. `mispricing`'s own weight-cap behavior, and its dependency on `valuation`'s underlying `FairValueEstimateRecord`, are unchanged.

All other ADR 0021 authority boundaries, anti-aliasing rules, correlation controls, strict-known semantics, and unavailable-state requirements remain unchanged.

### Exact amendment to ADR 0022

This ADR amends only normalization-dependent portions of ADR 0022:

1. `ValuationMethodologySnapshot` no longer requires a `normalization_policy_id` for the first canonical valuation methodology.
2. `ValuationAssessmentRecord` no longer requires scalar-normalization status or value. It must expose the referenced fair-value estimate, confidence decomposition, methodology identity, provenance, uncertainty, and explicit availability or unavailable reason.
3. The `Calibration set` terminology and scalar-normalization calibration section are removed from the first canonical valuation methodology.
4. Historical validation remains mandatory for deterministic strict-known reconstruction and leakage safety of raw `p10`/`p50`/`p90` estimates.
5. Market Validation may consume `valuation` only as the structured assessment authorized here; no adapter may manufacture a scalar from its raw value fields.
6. The alternatives section's rejection of raw fair-value estimates as Market-Validation-consumable is replaced: raw estimates are permitted only as structured, non-directional valuation evidence with separate confidence, never as a synthetic `[0,1]` score.
7. The `ValuationAssessmentRecord` field ADR 0022's Persistence requirements table lists as `correlation-group weight-cap reference` is reconciled with the ADR 0021 amendment above: the field, and the `correlation_group` value it accompanies, remain required and continue to identify `valuation`'s membership in the `valuation-mispricing` correlation group for lineage and conflict purposes. It does not imply a weighted scalar contribution, since none exists for `valuation` under this ADR. The field is retained as a semantic-membership reference, not a weight-bearing one, until a future ADR authorizes a `valuation` scalar.

All other ADR 0022 decisions remain unchanged, including the discounted value-capture-flow model, 365-day horizon, entity-class criteria, input allow-list, prohibited methodologies, strict-known replay, append-only correction, provenance, confidence, uncertainty, missingness, and audit gates.

### Exact amendment to ADR 0020

This ADR explicitly amends ADR 0020's `valuation` row in the input-authority matrix, and only that row:

1. ADR 0020's `valuation` row states, in its Semantic contract column: "Higher normalized values mean more favorable value under that exact methodology." This sentence no longer applies to `valuation`, because `valuation` no longer produces a normalized, directionally favorable scalar under this ADR or any methodology it authorizes.
2. `valuation`'s semantic contract under ADR 0020 is amended to read: the input is an estimated fundamental value expressed in a declared currency or ratio, exposed as the structured, confidence-bearing assessment defined by this ADR's amendments to ADR 0021 and ADR 0022 above. It carries no normalized favorability ordering.
3. ADR 0020's remaining `valuation`-row language is unaffected: `valuation` remains unavailable absent a complete, strict-known, non-conflicted, authorized record chain; no production formula is authorized outside the accepted methodology; and the row's prohibited-substitutes list is unchanged.
4. This amendment is scoped exclusively to the `valuation` row. It does not amend ADR 0020's `comparative_valuation`, `mispricing`, `asymmetry`, or `opportunity_timing` rows, its strict-known replay policy, its provenance/correction requirements, or any other section. Those rows' own normalized-favorability language, where present, is unchanged and continues to govern their respective future contracts.

## Market Validation contract

The canonical `valuation` input adapter must preserve semantic structure rather than flatten it into a favorable scalar. At minimum it exposes:

- availability status and explicit unavailable reason;
- exact `FairValueEstimateRecord` and `ValuationMethodologySnapshot` IDs/versions;
- per-unit and total `p10`/`p50`/`p90` values;
- quote currency, supply basis, and horizon;
- confidence and confidence decomposition;
- model dispersion and uncertainty;
- strict-known chronology and provenance fingerprints.

Market Validation composition — how, whether, or when these structured fields are combined into any score, ranking input, or weighted contribution — is explicitly outside the scope of this ADR and outside the scope of Issue #107:

- Issue #107 must not define, implement, or infer a Market Validation composition model for `valuation`.
- A separate Accepted ADR is required before any Market Validation composition of `valuation`'s structured fields may be implemented.
- Milestone 5 of Issue #107 may consume and expose the structured `valuation` assessment record defined above, read-only, and nothing else.
- Milestone 5 may not define, infer, approximate, or hard-code a composition, weighting, or scoring model for `valuation`, whether inside `market_validation/` or elsewhere, pending that separate Accepted ADR.

This ADR does not authorize a new overall scoring formula or any Opportunity Assessment mapping.

## Implementation consequences for Issue #107

- Milestone 4 becomes historical determinism and leakage validation for raw fair-value estimates; scalar fitting, calibration-set sizing, observed-outcome pairing, normalization-model selection, and calibration-parameter persistence are removed from Issue #107.
- Milestone 5 wires read-only structured valuation consumption only, per "Market Validation contract" above; it must not define, infer, or implement any composition model, and must prove no scalar is synthesized inside `market_validation/`.
- `valuation` may become available when the complete strict-known, non-conflicted methodology/evidence/estimate/confidence chain exists and passes independent audit.
- `mispricing`, `comparative_valuation`, and `asymmetry` remain unavailable and require their own later methodologies and calibrated transforms where applicable.

No runtime or persistence change is made by adopting this ADR. Implementation requires normal issue, branch, test, review, and audit workflow.

## Compatibility

- ADR 0005 entity/representation boundaries are strengthened because absolute values are not compared across incompatible representations.
- ADR 0016 remains unchanged: Market Validation is the sole production consumer.
- ADR 0020 is amended only as stated in "Exact amendment to ADR 0020" above (the `valuation` row's normalized-favorability sentence); strict-known missingness, replay, provenance, and every other row remain mandatory and unchanged.
- ADR 0017 and ADR 0018 remain unchanged: no Opportunity mapping is authorized.
- ADR 0019 remains separate evaluation authority; raw fair-value estimates may later be evaluated but are not outcome-fitted under this methodology.
- ADR 0021 and ADR 0022 are amended only as explicitly stated above and are otherwise reaffirmed.
- ADR 0023 is unaffected.

## Consequences

- Hunter no longer creates an economically meaningless favorable score from absolute fair-value magnitude.
- The semantic boundary between `valuation` and `mispricing` becomes enforceable in code and audit.
- Confidence remains reliability metadata and cannot be relabeled as fundamental value.
- Issue #107 loses several unnecessary architecture decisions: calibration-set minimum size, normalization transform, normalized field, observed-outcome pairing, and fitted-parameter persistence.
- Market Validation must support structured valuation evidence rather than assuming every input is a scalar. Any required scoring change needs its own explicit architecture decision.

## Alternatives Considered

### Normalize absolute per-unit or total fair value

Rejected because denomination, supply, entity scope, and economic claim differ. Higher absolute value is not inherently more favorable and cannot be compared across representations under ADR 0022.

### Normalize valuation confidence instead

Rejected because confidence measures reliability, not economic value. Relabeling it as `valuation` would collapse two separate semantics and recreate the aliasing problem ADR 0020 prohibited.

### Use fair-value-to-market-price divergence as the valuation scalar

Rejected because this is `mispricing` by ADR 0021's exact authority definition. Implementing it inside `valuation` would duplicate authority and double-count evidence.

### Retain the requirement but defer algorithm selection

Rejected because the missing decision is semantic, not algorithmic. Isotonic regression, logistic calibration, rank mapping, and min-max scaling cannot repair an invalid target meaning.

### Remove valuation from Market Validation entirely

Rejected. Fundamental valuation remains useful structured evidence and a prerequisite for future `mispricing`; only the unsupported directionally favorable scalar is removed.
