# Operational Corpus Authority Boundary

## Status and purpose

Operational Corpus is a downstream operational/audit store under ADR 0016. It records what a caller or runtime emitted; it never owns, calculates, validates, corrects, ranks, recommends, or promotes analytical content. Closure does not establish prediction correctness, and corpus readiness/counts measure operational presence only—not validity, quality, accuracy, calibration, or investment significance.

Phase 4.2 adds an additive versioned observation contract. It does not rewrite existing corpus files, change existing readers, add a Dashboard provider, or make Operational Corpus an input to any production or experimental analytical service.

## Versioned observation envelope

New authority-aware observations use schema `operational-corpus-authority-observation-v1` and contain:

- deterministic observation identity and recorded time;
- observation category;
- declared authority classification;
- exact immutable authority references;
- target and entity scope;
- referenced effective and known times when available;
- status: `authority_referenced`, `authority_not_required`, `unverified`, `legacy-unverified`, `unavailable`, or `error`;
- the fixed statement `downstream operational observation; not analytical authority`;
- the caller payload nested unchanged under `observation_payload`.

Nesting prevents caller fields such as `observation_id`, `schema_version`, `authority_classification`, `authority_references`, score, rank, correctness, or lifecycle state from replacing envelope or referenced-authority fields.

Existing Operational Corpus write paths remain compatible and additive. Newly written pipeline executions, predictions, outcomes, validation samples, closures, and opportunity observations now include boundary metadata. Analytical-looking rows without validated immutable references are labeled `unverified`; operational closures are labeled `authority_not_required`. Existing record identities and operational behavior are unchanged.

## Authority references

`AuthorityReference` preserves:

- source store;
- semantic type;
- immutable record identity and version;
- optional canonical hash;
- declared authority classification;
- target/entity scope;
- effective, recorded, and known times when supplied.

Reference validation is exact and read-only through an injected `AuthorityReferenceResolver`. A resolver may perform exact reads only. It cannot bootstrap or write stores, acquire data, call networks, run scoring/ranking/evaluation, advance lifecycle, or infer missing fields.

The deterministic allowlist is:

| Classification | Referenceable semantic types |
| --- | --- |
| Production | `market-validation-run`, `market-validation-project-result` |
| Canonical evaluation | `canonical.prediction-evaluation-policy`, `canonical.prediction-publication`, `canonical.prediction-evaluation`, `canonical.prediction-accuracy-snapshot`, `canonical.prediction-calibration-snapshot` |
| Experimental | `experimental.probability-assessment`, `experimental.pattern-assessment`, `experimental.technology-necessity-assessment`, `experimental.standalone-committee-assessment`, `experimental.opportunity-metric-snapshot`, `experimental.opportunity-assessment`, `fused-intelligence`, `opportunity-timing-assessment` |

Experimental references always remain experimental. Their existence, persistence, tests, or corpus display cannot be represented as production authority. Semantic types whose record classification is ambiguous are not allowlisted merely because a similarly named record exists.

## Validation outcomes

- A non-analytical operational observation needs no analytical reference and receives `authority_not_required`.
- An analytical-looking observation with exact allowlisted references whose store, type, identity, version, hash, classification, target/entity, and times match receives `authority_referenced`.
- A missing resolver or reference, unknown type, malformed/mismatched identity, target mismatch, classification mismatch, or non-exact resolved record receives `unverified`.
- A missing or failing authority store/resolver receives `unavailable`.
- Resolver failure never creates a metric, zero, substitute identity, or fallback reference.

The observation payload is preserved for audit in every case. `unverified` and `unavailable` mean the corpus observed the caller payload but did not establish its analytical truth.

## Legacy behavior

Existing rows are never migrated, backfilled, rewritten, or assigned fabricated references. Read-time normalization can project them as:

- `legacy-unverified`;
- authority classification `unverified`;
- no authority references; and
- downstream/non-authoritative ownership.

The original row remains nested unchanged in the read projection. Existing raw read paths continue to work for backward compatibility.

## Consumers and prohibitions

Dashboard API v1, desktop console, CLI/status, Automation, and Scheduler remain operational consumers. No Dashboard top-level key or provider is added. Existing projections may ignore additive boundary fields without behavior changes.

Market Validation, canonical prediction evaluation, Opportunity, Timing, ranking, Backtest, Probability, Pattern, Necessity, Committee, and other analytical services must not accept Operational Corpus as semantic input or authority. No corpus record can replace `hunter_score`, Market Validation ranking, canonical Timing, Opportunity, a committee field, correctness, accuracy, calibration, recommendation, assessment, or ranking.
