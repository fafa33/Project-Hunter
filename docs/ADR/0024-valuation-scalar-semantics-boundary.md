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

All other ADR 0021 authority boundaries, anti-aliasing rules, correlation controls, strict-known semantics, and unavailable-state requirements remain unchanged.

### Exact amendment to ADR 0022

This ADR amends only normalization-dependent portions of ADR 0022:

1. `ValuationMethodologySnapshot` no longer requires a `normalization_policy_id` for the first canonical valuation methodology.
2. `ValuationAssessmentRecord` no longer requires scalar-normalization status or value. It must expose the referenced fair-value estimate, confidence decomposition, methodology identity, provenance, uncertainty, and explicit availability or unavailable reason.
3. The `Calibration set` terminology and scalar-normalization calibration section are removed from the first canonical valuation methodology.
4. Historical validation remains mandatory for deterministic strict-known reconstruction and leakage safety of raw `p10`/`p50`/`p90` estimates.
5. Market Validation may consume `valuation` only as the structured assessment authorized here; no adapter may manufacture a scalar from its raw value fields.
6. The alternatives section's rejection of raw fair-value estimates as Market-Validation-consumable is replaced: raw estimates are permitted only as structured, non-directional valuation evidence with separate confidence, never as a synthetic `[0,1]` score.

All other ADR 0022 decisions remain unchanged, including the discounted value-capture-flow model, 365-day horizon, entity-class criteria, input allow-list, prohibited methodologies, strict-known replay, append-only correction, provenance, confidence, uncertainty, missingness, and audit gates.

## Market Validation contract

The canonical `valuation` input adapter must preserve semantic structure rather than flatten it into a favorable scalar. At minimum it exposes:

- availability status and explicit unavailable reason;
- exact `FairValueEstimateRecord` and `ValuationMethodologySnapshot` IDs/versions;
- per-unit and total `p10`/`p50`/`p90` values;
- quote currency, supply basis, and horizon;
- confidence and confidence decomposition;
- model dispersion and uncertainty;
- strict-known chronology and provenance fingerprints.

Market Validation may use these fields only through a separately accepted scoring/composition contract that preserves the distinction between value magnitude, confidence, and mispricing. This ADR does not authorize a new overall scoring formula or any Opportunity Assessment mapping.

## Implementation consequences for Issue #107

- Milestone 4 becomes historical determinism and leakage validation for raw fair-value estimates; scalar fitting, calibration-set sizing, observed-outcome pairing, normalization-model selection, and calibration-parameter persistence are removed from Issue #107.
- Milestone 5 wires read-only structured valuation consumption and must prove that no scalar is synthesized inside `market_validation/`.
- `valuation` may become available when the complete strict-known, non-conflicted methodology/evidence/estimate/confidence chain exists and passes independent audit.
- `mispricing`, `comparative_valuation`, and `asymmetry` remain unavailable and require their own later methodologies and calibrated transforms where applicable.

No runtime or persistence change is made by adopting this ADR. Implementation requires normal issue, branch, test, review, and audit workflow.

## Compatibility

- ADR 0005 entity/representation boundaries are strengthened because absolute values are not compared across incompatible representations.
- ADR 0016 and ADR 0020 remain unchanged: Market Validation is the sole production consumer and strict-known missingness remains mandatory.
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
