# Experimental Opportunity Snapshot and Assessment Persistence

## Boundary

ADR 0017 classifies Opportunity input snapshots, assessments, and future rankings as experimental research outputs. Phase 3.2 persists only input snapshots assembled by the Phase 3.1 `OpportunityAssessmentService` and assessments produced by the unchanged pure `OpportunityEngine`. It creates no production score, ranking, recommendation, Dashboard/API/UI output, alert, automation, scheduler job, or Operational Corpus authority.

Both records use the isolated experimental store configured by `configs/experimental_persistence.yaml`. They cannot be written to or read from canonical Market Validation, data-ops, Dashboard, Tokenomics, Evidence Intelligence, Sufficiency, or other production stores.

## Semantic record types

- `experimental.opportunity-metric-snapshot` stores one immutable Phase 3.1 assembly result.
- `experimental.opportunity-assessment` stores the exact pure-engine assessment linked to that snapshot.

The snapshot record preserves all 17 factor diagnostics, including null values for unavailable/unowned factors; authority state and reason; record/schema identity; source IDs and versions; evidence references; confidence; effective, recorded, and known times; requested effective-as-of and known-by cutoffs; configuration, model, methodology, and factor-authority fingerprints; identity schema version; and the canonical SHA-256 hash of the deterministic assembly JSON. An all-missing assembly remains all-missing and fail-closed after persistence.

The assessment record links to the exact snapshot record identity and canonical hash. It preserves the complete native `OpportunityAssessment`, all existing score/confidence/risk/supporting-evidence fields, and the pure factor trace used by the engine. Each trace row preserves the native weight, value, and contribution and joins the Phase 3.1 authority state, reason, missingness, confidence, source identities/versions, and evidence references. The trace contains weighted factor contributions and the existing risk, missingness, and validation-gate penalty contributions without changing their formulas.

## Authorization and identity

`OpportunityPersistenceService` accepts an `OpportunityAssemblyResult`, explicit recorded time, model version, and methodology fingerprint. It rejects inputs that are not experimental Phase 3.1 assemblies, do not contain exactly one diagnostic for each current factor, have inconsistent cutoffs, or would record before the known-by cutoff.

Snapshot identity binds project, canonical assembly hash, recorded time, configuration fingerprint, factor-authority fingerprint, and predecessor. Assessment identity additionally binds its exact snapshot record, native assessment, target, cutoffs, model/configuration, factor-authority version, and predecessor. Same authorized content is idempotent; conflicting reuse of an identity is rejected by immutable experimental persistence.

Corrections require both explicit snapshot and assessment predecessors plus a reason. Logical target identities remain stable, predecessor records remain immutable, and lineage stays queryable. Records are never updated in place.

## Reads and strict-known replay

`ExperimentalOpportunityRepository` supports exact identity lookup, semantic-type target history, logical lineage, and strict-known selection. Strict-known selection requires:

- compatible semantic type and target;
- effective, recorded, and known times no later than caller cutoffs;
- explicit known time with no legacy limitation;
- compatible configuration, methodology, and factor-authority fingerprints;
- current non-superseded lineage.

There is no latest fallback. Future-effective, future-recorded, future-known, legacy/unknown-known-time, superseded, or incompatible records return no strict-known result.

## Isolation and non-goals

The service is opt-in and requires an explicitly supplied experimental store. Existing manual snapshots and pure engine/ranking APIs remain independent. No CLI, Pipeline/Fusion, Dashboard, desktop, Operational Corpus, Market Validation, Timing, ranking, automation, scheduler, alert, acquisition, or runtime bootstrap wiring is introduced. Phase 3.2 does not persist Opportunity rankings and does not alter any factor, weight, score, validation gate, or authority mapping.
