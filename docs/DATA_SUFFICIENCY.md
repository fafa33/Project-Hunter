# Data Sufficiency and Degraded Mode

Data Sufficiency is an additive Hunter layer introduced by Sprint `v3.2.0`. It records whether the evidence required for a specific engine, analysis purpose, and output field is available, stale, partial, unavailable, direct, or proxy-only.

Phase 1 establishes the domain models, deterministic ids, default requirement registry, degraded-mode policy, and proxy-signal policy.

Phase 2 adds SQL-authoritative persistence for versioned requirements, availability, assessments, source validation, disagreements, normalized lineage links, processing runs, checkpoints, and degraded-mode policies.

Phase 3 adds deterministic availability evaluation for selected candidates and requirements. It remains additive and does not add cross-source disagreement detection, sufficiency aggregation, CLI commands, automation jobs, scoring integration, or runtime behavior changes.

Phase 4 adds cross-source validation and disagreement handling for compatible source observations. It remains additive and does not add sufficiency aggregation, CLI commands, automation jobs, scoring integration, or runtime behavior changes.

Phase 5 adds sufficiency aggregation and degraded-mode decisions from persisted requirement availability and cross-source disagreement records. It remains additive and does not add CLI commands, automation jobs, scoring integration, or runtime behavior changes.

Phase 6 adds the `hunter sufficiency` CLI namespace, report payloads, and idempotent Scheduler job installation for operational sufficiency refreshes. It remains additive and keeps Data Sufficiency output separate from analytical and scoring output.

## Semantics

- Missing evidence is an availability state, not negative project evidence.
- Stale evidence is not live evidence.
- Partial evidence preserves exactly which requirement is incomplete.
- Proxy signals are context only unless a requirement explicitly allows them.
- A proxy signal never satisfies a requirement that requires a direct observation.
- Data sufficiency and analytical output remain separate from scoring, ranking, committee decisions, Opportunity Timing, Market Validation, valuation, and the canonical runtime.

## Phase 1 Models

- `DataRequirement` declares the versioned evidence required by an engine, analysis purpose, and output field.
- `DataAvailability` represents current or point-in-time availability for one requirement and candidate.
- `DataSufficiencyAssessment` aggregates requirement availability into reportable sufficiency state.
- `SourceDisagreement` records data-quality disagreement without treating disagreement as project weakness.
- `DegradedModePolicy` decides whether output is normal, degraded, blocked, or unavailable.
- `ProxySignalPolicy` defines allowed proxy types and their limitations.

## Direct Observations and Proxy Signals

Direct observations measure the target concept itself, such as accepted Evidence Intelligence claims, authoritative identity records, verified market observations, or validated on-chain observations.

Proxy signals are indirect context. They must retain proxy type, policy version, limitation text, and lineage when persisted in later phases. They must not be reported as the missing direct observation.

## Historical Discipline

All Phase 1 models include `effective_at`, `recorded_at`, and replay-mode fields where point-in-time use is required. Later persistence phases must use both effective time and recorded time for strict known-by-Hunter replay.

## Phase 2 SQL Persistence

`DataSufficiencyRepository` stores structured sufficiency state in SQLite under the `hunter.sufficiency` package. The authoritative tables are:

- `data_requirements`
- `data_requirement_source_types`
- `data_requirement_proxy_types`
- `data_availability`
- `data_sufficiency_assessments`
- `degraded_mode_policies`
- `data_source_validation_results`
- `data_disagreement_records`
- `data_sufficiency_evidence_links`
- `data_sufficiency_span_links`
- `data_sufficiency_claim_links`
- `data_sufficiency_conflict_links`
- `data_sufficiency_processing_runs`
- `data_sufficiency_checkpoints`

Requirement source types and proxy types are normalized into association tables. Evidence, span, claim, and conflict lineage is stored in indexed link tables keyed by owner type and owner id. JSON metadata is allowed only for small non-authoritative context after indexed columns exist.

Point-in-time reads support current mode, strict known-by-Hunter replay using both `effective_at <= cutoff` and `recorded_at <= cutoff`, and reconstruction mode through effective-time-only queries. Later-recorded records preserve prior historical state rather than replacing it.

## Phase 3 Availability Evaluation

`DataRequirementSelector` selects requirements by engine, analysis purpose, output field, explicit candidate ids, and optional checkpoints. Candidate ids are required so normal evaluation cannot become an accidental full-registry scan.

`DataAvailabilityEvaluator` consumes read-only input records:

- `CandidateTrustState` from Candidate Registry or Identity/Trust;
- `SourceObservation` from Evidence Intelligence claims, Competitive Intelligence outputs, provider observations, or other existing evidence surfaces;
- `ProviderAvailabilityState` for unavailable, stale, rate-limited, forbidden, or failed sources.

The evaluator emits `DataAvailability` plus normalized evidence, span, claim, and conflict lineage links. It does not write unless callers explicitly persist through `DataSufficiencyRepository`.

Evaluation rules are explicit:

- untrusted, unresolved, conflicted, rejected, unavailable, or future-recorded identity returns `unavailable`;
- missing required observations returns `unavailable`;
- unavailable providers return `unavailable` with provider-specific reason text;
- stale observations return `stale`;
- incomplete required source coverage, incomplete lineage, low authority, or related conflicts return `partial`;
- direct observations that satisfy freshness, authority, confidence, lineage, and source-type requirements return `available`;
- proxy signals remain `proxy_signal` and cannot fully satisfy required direct observations by default;
- strict known-by-Hunter replay requires both `effective_at <= cutoff` and `recorded_at <= cutoff`;
- reconstructed-after-cutoff mode may use later-recorded observations only when their effective time is within the cutoff and remains labeled `reconstructed_after_cutoff`.

## Phase Boundary

## Phase 4 Cross-Source Validation

`CrossSourceValidationService` compares only source observations that describe the same metric with compatible units, scope, chain, product, and time period. Different scopes, chains, products, periods, or units produce `incompatible_scope` validation records and do not create disagreement records.

Validation results distinguish:

- `agreement` for compatible observations with the same canonical value;
- `disagreement` for compatible observations with different canonical values;
- `missing_provider` for unavailable, rate-limited, forbidden, failed, or stale required providers;
- `stale_source` for observations outside the requirement freshness policy;
- `conflict` for observations already linked to source conflicts;
- `incompatible_scope` for non-equivalent observations that must not be compared;
- `unavailable` for missing comparable observations or insufficient source count.

Disagreement records are data-quality states. They preserve compared source lineage and authority context, and they explicitly do not become project-negative evidence or analytical conclusions. The service does not choose a winning source, overwrite source observations, or use proxy signals to conceal missing direct observations.

Strict known-by-Hunter replay excludes later-recorded observations with `recorded_at > cutoff_at`. Reconstructed-after-cutoff mode may include later-recorded observations only when their effective period is within the cutoff and remains labeled by replay mode.

`DataSufficiencyRepository` persists validation and disagreement records with normalized evidence, span, claim, and conflict lineage. Phase 4 adds indexes for validation status and disagreement time queries.

## Phase Boundary

## Phase 5 Sufficiency Assessment and Degraded Mode

`DataSufficiencyAssessor` creates explicit `DataSufficiencyAssessment` records from selected requirements, availability states, freshness, source quality, lineage, direct-observation coverage, proxy-signal coverage, and cross-source disagreements.

`DegradedModePolicyEngine` applies the versioned degraded-mode policy per requirement. It records what is missing, what remains supportable, and which output fields are not supportable. Missing data lowers sufficiency and supportability only; it is never persisted as candidate weakness, project-negative evidence, or a scoring/ranking input.

Assessment metadata includes deterministic report-field gating and annotation data:

- `missing_requirements`
- `supportable_conclusions`
- `unsupported_conclusions`
- `disagreement_ids`
- `preserves_score`
- `treats_missing_as_negative`
- per-output-field availability, directness, policy outcome, and reason

Proxy signals remain labeled as `proxy_signal`. A proxy-only availability cannot satisfy a required direct observation unless a future explicit policy permits that behavior; the default policy blocks proxy substitution for direct data.

Current, strict historical, and reconstructed assessments retain the requested replay mode. Strict known-by-Hunter assessments filter out later-recorded availability and disagreement records. Reconstructed assessments are explicitly labeled `reconstructed_after_cutoff`.

## Phase Boundary

## Phase 6 Reporting, CLI, and Automation

The `hunter sufficiency` CLI exposes:

- `coverage`
- `requirements`
- `assess <candidate>`
- `missing <candidate>`
- `disagreements`
- `report`
- `automation install`
- `automation status`

Reports are marked `data_sufficiency_only` and include required data, availability states, directness, proxy usage, stale/partial/unavailable counts, source disagreement records, coverage, freshness, confidence limits, degraded-mode outcomes, material missing evidence, and replay mode.

Strict historical reports require both effective-time and recorded-time eligibility. Reconstructed reports are labeled `reconstructed_after_cutoff` so they cannot be mistaken for known-at-cutoff state.

Requirement source/proxy policy rows and disagreement reports use the same cutoff semantics as availability and assessments. Strict known-by-Hunter reports exclude later-recorded requirement policy and disagreement state; reconstructed reports may include later-recorded state only when labeled `reconstructed_after_cutoff`.

Automation installs Scheduler-compatible operational jobs for requirement validation, availability refresh, stale evidence detection, cross-source disagreement detection, assessment refresh, and degraded-mode report refresh. The jobs disable canonical analytical execution flags and mark `provider_dependency=false` and `fabricates_evidence=false`; Scheduler remains operational-only and does not own analytical logic.

## Completion Boundary

Sprint `v3.2.0` is complete at Phase 6. The implementation remains additive and does not change scoring, ranking, valuation, committee decisions, Opportunity Timing, Market Validation, or canonical runtime behavior.
