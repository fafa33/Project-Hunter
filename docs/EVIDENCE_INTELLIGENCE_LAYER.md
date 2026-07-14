# Evidence Intelligence Layer

Status: v3.0.0 implementation in progress.

## Phase 3 Intake Boundary

The Evidence Intelligence Layer uses additive SQL storage for structured knowledge. It does not modify raw evidence, Candidate Registry identity semantics, Market Validation, scoring, committee logic, Opportunity Timing, or `EvidenceBackedProjectExecutor`.

Authoritative structured truth is claim-centered:

- `KnowledgeClaim` is the canonical evidence-backed truth record.
- `KnowledgeRelationship` is a projection of eligible claims and must link to `claim_id`.
- Document lifecycle, source authority, claim lifecycle, and conflict lifecycle are append-only and time-aware.
- Current status fields are denormalized projections from append-only events.

Evidence intake creates document references from existing persisted evidence references. It records deterministic content hashes, normalized rendition metadata, stable spans, one append-only document lifecycle event, and one append-only source authority verification event. Intake never edits or replaces the raw evidence that produced the reference.

## Normalized Lineage

SQL-authoritative lineage is stored in normalized association tables. Claims do not embed source evidence IDs, span IDs, conflict IDs, correction links, retraction links, or supersession links as list/blob fields.

The repository reconstructs claim lineage by joining:

- `knowledge_claims`
- `claim_source_evidence_links`
- `claim_evidence_span_links`
- `evidence_spans`
- `evidence_documents`
- `claim_lifecycle_events`
- `claim_conflict_links`
- `knowledge_conflicts`

This keeps lineage indexed and queryable without scanning JSON/list fields.

## Backward Compatibility

The Phase 3 repository and intake service are additive. Existing runtime paths do not depend on Evidence Intelligence storage, and no existing analytical behavior changes.

## Historical Replay

Historical document state is reconstructed from `document_lifecycle_events` using `effective_at <= cutoff`. Strict known-by-Hunter replay additionally requires `recorded_at <= cutoff`.

Historical source authority is reconstructed from `source_authority_verification_events` using the same rules. Current authority fields on `evidence_documents` are materialized projections only and must not be used for historical confidence when an authority event was not known by Hunter at the cutoff.

## Phase 4 Provider Boundary

AI providers are adapters behind `AIExtractionProvider`. They receive only an `ExtractionRequest` containing stable evidence spans and a versioned `ExtractionSchema`; they do not receive repository handles, tools, source-fetch capabilities, schema mutation rights, or configuration mutation rights.

Provider output is persisted only as an `ExtractionProposal` linked to an `AIProviderArtifact`. It is not a claim, relationship, score, or source of truth. Later phases must validate proposals before any canonical knowledge claim can be persisted.

Provider health is explicit and persisted in `ai_provider_health`. Provider failure writes an unavailable artifact and unavailable proposal instead of silently producing no knowledge.

Prompt-injection detection runs before provider execution. Detected untrusted instructions create `security_audit_events` and reject the provider run without calling the adapter. Provider responses that request tools, fetches, repository writes, schema changes, or configuration changes are also rejected at the security boundary.

## Phase 5 Validation Boundary

Extraction validation is deterministic and claim-safe. `ExtractionValidationService` classifies evidence spans, validates entity proposals, and validates claim proposals against the versioned `PredicateRegistry`.

Validated provider output remains a proposal. Phase 5 does not persist canonical `KnowledgeClaim` records and does not create relationship projections.

Validation rejects unsupported predicates, malformed entities, missing support spans, co-mentions without explicit claim support, weak or inferred support text, unsupported conclusion types, and predicate shapes rejected by the registry. Numerical values, dates, datetimes, URLs, addresses, repositories, and direct quotes require literal support in the cited evidence span. Semantic support is accepted only when the proposal declares explicit support and the cited support text appears in the span.

## Phase 6 Claim Persistence Boundary

Validated claim proposals may be persisted as canonical `KnowledgeClaim` records only through `ClaimPersistenceService`. Persistence writes the claim, the initial append-only `ClaimLifecycleEvent`, claim-to-source-evidence links, and claim-to-span links in one repository transaction.

Claim current-state fields are denormalized projections. Historical claim status is reconstructed from `claim_lifecycle_events` using `effective_at <= cutoff`; strict known-by-Hunter replay additionally requires `recorded_at <= cutoff`. Lifecycle appends update only the current projection fields such as `status`, `confidence`, `superseded_at`, and `retracted_at` without overwriting historical lifecycle events.

Confidence is deterministic and componentized from support level, source authority, and lineage depth. It is stored as a projection on the current claim and does not change scoring, ranking, Opportunity Timing, Market Validation, or committee behavior.

## Phase 7 Conflict Boundary

Conflict detection is predicate-aware. `PredicateAwareConflictDetector` only compares active claims with the same canonical subject, predicate, scope, compatible modality, overlapping validity period, usable authority, and usable document lifecycle state. Different values are not conflicts by default; they become conflicts only when the predicate definition declares an exclusive conflict rule or when the same scoped claim has opposite polarity.

`ConflictPersistenceService` persists `KnowledgeConflict`, the initial append-only `ConflictLifecycleEvent`, conflict-to-claim links, conflict-to-source-evidence links, conflict-to-span links, and claim-to-conflict backlinks in one repository transaction.

Conflict lifecycle is separate from claim lifecycle. Resolving a conflict updates only `KnowledgeConflict` current-state projections and appends a `ConflictLifecycleEvent`. If conflict resolution changes a claim, the claim must receive its own valid `ClaimLifecycleEvent`; conflict status values are never used as claim statuses.

## Phase 8 Relationship Projection Boundary

`KnowledgeRelationship` is a graph-ready projection of eligible entity-to-entity `KnowledgeClaim` records. It is never independent truth and has no separate lifecycle, confidence model, conflict model, or versioned truth semantics. Projection status and confidence are copied from the canonical claim current-state projection at refresh time.

`RelationshipProjectionService` supports incremental refresh and full rebuild of projections from claims and the versioned `PredicateRegistry`. Only predicates marked graph-projection eligible and requiring object entities can produce relationships.

Point-in-time relationship views reconstruct state through the canonical claim lifecycle, supporting document lifecycle, source authority events, and linked conflict lifecycle. A detected or disputed conflict makes the relationship view disputed; unavailable document or authority state makes it unavailable.

## Phase 9 CLI, Reporting, and Automation

The Evidence Intelligence CLI namespace is `hunter evidence-intelligence`. It exposes coverage, source authority, document lifecycle, claim lifecycle, conflict lifecycle, candidate explain, provider health, security audit, and automation status/install surfaces.

`hunter evidence-intelligence automation install` writes valid job definitions into the existing Hunter automation configuration format so the normal `hunter automation start` scheduler path can execute them. The Scheduler remains operational-only; Evidence Intelligence jobs disable scoring, ranking, Opportunity Timing, committee execution, and Market Validation changes.

Reports accept current mode, historical strict known-by-Hunter mode, and reconstructed-after-cutoff mode. Strict known-by-Hunter reports use both effective-time and recorded-time constraints. Reconstructed-after-cutoff reports are explicitly labeled and must not be interpreted as knowledge Hunter knew at the cutoff.

Automation installation uses the existing Scheduler job model. Installed Evidence Intelligence jobs are operational definitions only; analytical ordering remains owned by the Evidence Intelligence pipeline, not by Scheduler.
