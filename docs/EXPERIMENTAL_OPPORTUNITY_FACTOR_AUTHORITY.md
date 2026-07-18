# Experimental Opportunity Factor Authority

## Boundary

This map implements ADR 0017 for Phase 3.1. It authorizes only construction of an in-memory experimental `OpportunityMetricSnapshot` at explicit effective-as-of and known-by cutoffs. It does not authorize Opportunity scoring as production, persistence, ranking, recommendations, Dashboard/API/UI exposure, Operational Corpus authority, automation, or scheduling.

The current contract contains 17 factors: the 15 names in `OpportunityConfig.factor_weights` plus the existing `risk` and `missing_evidence` penalty inputs. No Tokenomics, unlock, catalyst, sell-pressure, or Sufficiency factor is added.

Canonical Market Validation is approved only for five exact control/evidence fields whose current semantics directly match the declared mapping below. Its other similarly named analytical fields are not substitutes for Opportunity factors. Twelve factors therefore remain unowned and explicitly missing.

## Authority map

All approved rows use `MarketValidationProjectResultRecord` (`market-validation-project-result`) from the dedicated canonical store. Selection requires matching project identity, `effective_at <= effective_as_of`, `recorded_at <= known_by`, explicit `known_at <= known_by`, no known-time limitation, current immutable lineage, production authority classification, and an injected read-only repository. Source IDs/versions, evidence references, confidence, schema version, and all selected times are preserved unchanged.

| Factor | Status and semantic owner | Exact field / normalization | Missing, confidence, and anti-substitution rule |
| --- | --- | --- | --- |
| `valuation_discount` | Unowned — no approved persisted source | None | Missing; Market Validation `valuation` or `mispricing`, reports, and caller values cannot substitute. |
| `relative_valuation` | Unowned — no approved persisted source | None | Missing; `comparative_valuation` is not automatically the same semantic output. |
| `historical_discount` | Unowned — no approved persisted source | None | Missing; backtests, historical reports, or Market Validation fields cannot substitute. |
| `whale_accumulation` | Unowned — no approved persisted source | None | Missing; whale/on-chain observations cannot infer ownership, intent, or accumulation. |
| `smart_money_positioning` | Unowned — no approved persisted source | None | Missing; wallet, flow, corpus, or Market Validation fields cannot infer strategy. |
| `developer_momentum` | Unowned — no approved persisted source | None | Missing; ADR 0011 descriptive developer findings do not automatically define momentum. |
| `macro_tailwinds` | Unowned — no approved persisted source | None | Missing; macro observations and Market Validation `macro_intelligence` cannot substitute without a factor contract. |
| `future_demand` | Unowned — no approved persisted source | None | Missing; descriptive findings or a similarly named Market Validation field cannot substitute. |
| `sector_strength` | Unowned — no approved persisted source | None | Missing; sector labels, rankings, or current reports cannot substitute. |
| `capital_formation` | Unowned — no approved persisted source | None | Missing; capital-flow observations or Market Validation fields cannot substitute. |
| `validation_health` | Approved — canonical Market Validation owns its project-result validation health | Exact `validation_health`; identity mapping in `[0,1]` | Preserve record confidence/provenance. Missing/stale/invalid source fails closed to snapshot gate value `0.0` and remains diagnostically missing; no Dashboard/status health substitute. |
| `evidence_freshness` | Approved — canonical Market Validation owns project-result data freshness | Exact `data_freshness`; identity mapping in `[0,1]` | Preserve record confidence/provenance; stale or absent source supplies no value. File mtime and Dashboard freshness are forbidden. |
| `confidence` | Approved — canonical Market Validation owns project-result confidence | Exact `confidence`; identity mapping in `[0,1]` | Preserve rather than recalculate confidence; experimental Probability/Committee confidence cannot substitute. |
| `backtesting_quality` | Unowned — no approved persisted source | None | Missing; current backtest records lack the required strict-known factor contract and cannot be read from mutable/latest files. |
| `historical_opportunity_similarity` | Unowned — no approved persisted source | None | Missing; Pattern assessment and historical validation are distinct semantics. |
| `risk` | Approved — canonical Market Validation owns project-result risk | Exact `risk`; identity mapping in `[0,1]` | Preserve record confidence/provenance; security, tokenomics, timing, or standalone experimental risk cannot substitute. |
| `missing_evidence` | Approved — canonical Market Validation owns its project-result missing-evidence set | `len(missing_evidence) / 17`, clamped to `[0,1]`; exact strings also remain on the snapshot/diagnostic boundary | This is a deterministic penalty normalization, not fabricated evidence. Assembly also lists every unavailable/unowned factor so existing engine missingness semantics reduce confidence. |

## State and replay behavior

Each factor diagnostic has exactly one state:

- `available`: a valid strict-known canonical field was selected;
- `missing`: no eligible record exists or the factor is unowned;
- `unavailable`: the injected persisted source failed or could not be reached;
- `stale`: the canonical record explicitly marks the exact mapped field/factor stale;
- `legacy_non_strict`: records exist at the effective/recorded cutoff but lack trustworthy known-time provenance;
- `invalid`: a wrong record type, invalid lineage, absent production classification, or invalid field value was returned.

Only `available` factors enter snapshot values. Positive missing factors are omitted and therefore contribute no support under the existing pure engine. Missing `validation_health` is explicitly fail-closed at `0.0` because the existing engine otherwise treats an absent gate as fully healthy. This structural gate value is not an observed analytical value and the diagnostic remains non-available. All non-available factors are listed in `snapshot.missing_evidence`.

The service performs no `latest` lookup and reads no raw files. Post-cutoff records, unknown-known-time records, superseded records, and incompatible authority classifications cannot affect assembly. Identical target, cutoffs, configuration, and persisted inputs produce byte-identical structured output.

## Isolation

`OpportunityAssessmentService` accepts only an injected `OpportunityAuthoritySource`. The default source is empty and produces an all-missing, fail-closed snapshot. The optional canonical adapter requires an explicitly supplied repository and explicit canonical validation run identities. The service has no runtime database path, CLI, pipeline, Dashboard, Operational Corpus, report, automation, scheduler, alert, acquisition, scoring, ranking, or persistence wiring.
