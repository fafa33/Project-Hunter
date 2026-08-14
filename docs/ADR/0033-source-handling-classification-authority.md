# ADR 0033: Source Handling Classification Authority

## Status

Proposed.

## Date

2026-08-14.

## Context

ADR 0031 requires that, before inclusion or durable persistence, "every source reference must carry, or deterministically derive from governed source policy, a handling classification sufficient to decide whether its exact bytes may be processed, retained, and reconstructed." It names the categories that boundary must cover: credentials/secrets, personal or sensitive material where applicable, licensed or restricted material, ephemeral/non-retainable content, and repository material later removed from current `HEAD`.

ADR 0031 imposes that obligation but deliberately names no producer of the classification fact. No accepted decision assigns ownership of source-handling classification, and no implementation exists:

- `EvidenceContextSourceReference`, the ADR 0031 contract defined to carry the classification, has no concrete implementation;
- no data-handling, sensitivity, or retention classification exists in the runtime;
- `EvidenceClassification` in Evidence Intelligence is a topical classifier (repository, deployment, governance, integration, market) and carries no handling semantics;
- ADR 0004 governs epistemic trust — source reliability, identity confidence, conflict status, freshness, and unavailable states — not data handling;
- `docs/ANALYTICAL_AUTHORITY_REGISTRY.md` §Cross-Cutting Input Authority enumerates the upstream owners a consumer must preserve and includes no data-handling classification owner.

An attempt to satisfy the ADR 0031 obligation without an owner was made and independently reviewed. Because no authoritative producer existed, the classification fact and the retention policy body were supplied by the calling consumer at build time. Independent review established that this leaves the caller in control of the retention outcome: a caller able to state the classification, the provenance string, and the policy body is the de facto authority regardless of how the values are typed. Empirical reproduction confirmed that a tampered retention decision was accepted at the persistence boundary and that prohibited source text was durably retained.

A first draft of this decision assigned the missing ownership but expressed handling as a single closed enumeration. Independent review established that a single-value vocabulary is itself unsafe. It conflates orthogonal facts about a source, it cannot represent a source that is simultaneously secret-bearing, licensed, and removed, it silently loses one restriction when another is recorded, and it admits `retainable` — a retention *outcome* — into the fact vocabulary, allowing a producer or provider to assert a conclusion that only policy may derive. This decision therefore normalizes handling into orthogonal dimensions and separates facts from outcomes throughout.

Source-handling classification is a data-handling and security control fact. It is not an analytical or analytical-looking output, produces no analytical conclusion, and is therefore outside the scope of `docs/ANALYTICAL_AUTHORITY_REGISTRY.md` and the ADR 0016 analytical promotion rules. The accepted precedent is consistent: `SourceAuthorityVerificationEvent` carries no registry entry.

Evidence Intelligence already owns an accepted, implemented pattern of the right shape. `SourceAuthorityVerificationEvent` is produced at document intake, drawn from closed vocabularies, carries provenance and a verifier type, records both `effective_at` and `recorded_at`, is appended immutably, and defaults to its least-privileged value. This ADR reuses that established pattern rather than introducing a new one.

## Decision

Hunter assigns canonical ownership of source-handling classification facts to the Evidence Intelligence consumer-side Source Handling Authority, and binds the minimum semantics required for conformant retention, readiness, and replay.

The decision establishes four strictly separated concepts and one direction of flow:

```text
governed evidence
    -> Evidence Intelligence intake/document-lifecycle producer
    -> immutable SourceHandlingClassificationEvent
    -> strict-known SourceHandlingFactSet
       + exact immutable RetentionPolicyVersion
    -> rederived RetentionDecision
    -> readiness and persistence enforcement
```

A fact describes a source. A policy governs what may be done with a source bearing those facts. A decision is derived from exactly one fact set and exactly one policy version. No concept may be substituted for another, and no participant may assert a downstream concept in place of its inputs.

This decision fills the producer authority that ADR 0031 left open. It creates no new analytical authority, defines no generic or cross-project handling service, and does not redefine any existing contract.

### 1. Canonical ownership and authority roles

The Evidence Intelligence consumer-side Source Handling Authority is the sole canonical owner of source-handling facts and of the policies that govern them. It has exactly two explicit roles:

- **Role A — intake and document lifecycle.** Produces immutable source-handling classification events. This is the boundary that already produces `SourceAuthorityVerificationEvent`.
- **Role B — Source Handling Policy Service.** Authorizes and resolves retention policy versions.

This is not a generic Prompt Intelligence, Context Intelligence, or cross-project handling service, and it acquires no authority beyond source handling.

- **May create** classification events: Role A.
- **May authorize and resolve** retention policy versions: Role B.
- **May consume** read-only: source resolution into `EvidenceContextSourceReference`, pre-model build derivation, and persistence enforcement.
- **May not create, override, restate, or substitute** facts, policies, or decisions: callers, orchestrators, prompt compilation, any Prompt Intelligence or Context Intelligence layer, any generic or project-neutral core, a future Model Adapter, Hunter Governance Review, and any repository-wide classification service.

A caller may identify a document or a document version by identity, and may state an expected policy content identity. A caller may never manufacture a handling fact, a policy body, or a retention outcome. Caller assertion is inadmissible as evidence and never substitutes for missing authority.

### 2. Normalized source-handling fact model

A `SourceHandlingFactSet` is the V1 unit of handling truth. It has exactly five orthogonal dimensions, each a closed, versioned vocabulary with a defined restrictive order.

| Dimension | Values, least to most restrictive |
| --- | --- |
| `sensitivity` | `ordinary` < `unknown` < `personal_sensitive` |
| `rights_restriction` | `unrestricted` < `unknown` < `licensed_restricted` |
| `secret_status` | `not_secret_bearing` < `unknown` < `secret_bearing` |
| `availability_status` | `available` < `unknown` < `removed` |
| `persistence_constraint` | `no_source_specific_prohibition` < `unknown` < `ephemeral_non_retainable` |

Arbitrary runtime string values are prohibited. Each dimension carries its own vocabulary version; a vocabulary change requires a new accepted decision and a version increment.

Three values require explicit definition because their names could otherwise be read as permissions:

- `not_secret_bearing` is an affirmative finding and requires authoritative source policy or verified evidence. A negative regular-expression or pattern scan is insufficient to establish it.
- `no_source_specific_prohibition` states only that the source itself imposes no additional prohibition. It is not retention permission; permission comes only from policy.
- `removed` is an availability and lifecycle fact about the source. It is not a retention outcome and does not by itself decide whether retained bytes may persist.

`retainable` is not a fact and has no representation in this model. Retention is a derived outcome under §7, never an asserted property of a source.

**Completeness** is derived metadata over a fact set and carries no independent authority:

- `complete` — no dimension is `unknown`;
- `partial` — at least one but not all dimensions are `unknown`;
- `unclassified` — every dimension is `unknown`.

**Restrictive join.** Combining fact sets is pointwise by each dimension's restrictive order, taking the more restrictive value. Therefore `unknown` joined with a restrictive value yields the restrictive value, and `unknown` joined with a permissive value yields `unknown`. No permissive value ever cancels a restrictive value.

### 3. Source handling classification event

The canonical record concept is an immutable, append-only, document-scoped source-handling classification event. Its architectural shape binds semantics equivalent to:

```text
SourceHandlingClassificationEventV1
    event_id
    document_id
    subject_document_version_id
    stream_sequence
    predecessor_event_ids
    fact_set
    changed_dimensions
    vocabulary_versions
    classification_method
    verifier_type
    evidence_provenance_ids
    restriction_release_authorization_id | null
    authorizer_identity
    reason
    processing_run_id
    effective_at
    recorded_at
    schema_version
```

Deterministic identity covers all replay-significant fields. Canonicalization, identity derivation, and persistence authorization follow Hunter's existing accepted patterns under ADR 0009 and the document-lifecycle precedent. This ADR does not prescribe SQL layouts, table names, or module paths.

### 4. Producer and admissibility contract

Role A must derive or validate every fact it records. It must not mechanically persist a caller-supplied final fact set.

Evidence supporting a fact belongs to exactly one strength class:

| Class | Sources |
| --- | --- |
| `weak` | provider declarations; caller material; unverified metadata; pattern-detection hits |
| `governed_deterministic` | exact immutable source policy; exact immutable source-type policy |
| `verified_manual` | exact immutable reviewed evidence |
| `unsupported` | caller assertion; missing evidence; mismatched verifier/method combination |

Admissibility is bound per method and verifier pairing. `verifier_type` reuses the accepted `VerifierType` semantics unchanged.

| Method + verifier | Admissible effect |
| --- | --- |
| `intake_source_policy` + `deterministic_system` | Only dimensions explicitly governed by exact immutable policy. May establish restrictive or permissive values. Any reduction additionally requires explicit release authorization under §5. |
| `source_type_default` + `deterministic_system` | Only dimensions explicitly covered by exact source-type policy. May establish permissive genesis or `unknown` values, and may increase restriction. May not reduce restriction. |
| `provider_declaration` + `provider_claim` | Restrictive values or `unknown` only. May not establish permissive values. May not reduce restriction. |
| `manual_verified_evidence` + `manual_review` | Only dimensions specifically addressed by immutable reviewed evidence. May establish facts. Any reduction additionally requires release authorization under §5 and exact predecessor and document-version binding. |
| `unclassified_default` + `deterministic_system` | `unknown` only. |
| `identity_trust_layer`, `candidate_registry` | Supporting provenance only. Not independently admissible V1 handling verifiers. |

The following are binding:

- caller assertion is inadmissible;
- provider and caller claims may never independently establish `ordinary`, `unrestricted`, `not_secret_bearing`, `available`, or `no_source_specific_prohibition`;
- `weak` evidence may increase restriction, preserve restriction, or leave a dimension `unknown`, and may never reduce restriction;
- a provider claim of retainability has no representation in the fact model and is not recordable.

### 5. Restriction release contract

Any transition toward a less restrictive value in any dimension requires all of:

- exact predecessor lineage;
- evidence specifically addressing the dimension being reduced;
- an admissible strong method under §4;
- an immutable `restriction_release_authorization_id`;
- a named Evidence Intelligence handling-release authorizer;
- binding to the exact document version.

A negative secret scan can never authorize a reduction from `secret_bearing`.

### 6. Stream succession, conflict, and strict-known selection

Classification events form one append-only stream per document.

Normal append requires a unique genesis event, a normal successor that references exactly the current unique head, an incrementing sequence, and atomic compare-and-append. A stale append is rejected. No event is overwritten.

Conflict is a first-class outcome, not something to be resolved by preference:

- **Concurrent genesis.** Only one genesis event is canonical. If historical corruption produces multiple genesis events, the classification stream is conflicted and consumption is `BLOCKED`.
- **Divergent branches.** No branch is selected. There is no lexical, sequence, or timestamp tie-break. The stream is `BLOCKED` until reconciled. A reconciliation event must reference every conflicting head, and its fact set must preserve at least the pointwise restrictive join of those heads. Any reduction relative to that join still requires release authorization under §5.

**Strict-known selection** under ADR 0020 and ADR 0031 applies. An event is eligible only when both `effective_at <= effective_cutoff` and `recorded_at <= recorded_cutoff`. Among eligible events:

- zero heads — classification is unavailable;
- exactly one head — that head is selected;
- more than one head — conflict, and consumption is `BLOCKED`.

Current or later-recorded state never substitutes for an unavailable historical classification.

Corrections append a successor. A correction may carry an earlier `effective_at`, becomes visible only once the `recorded_at` cutoff admits it, and never rewrites what was knowable earlier.

### 7. Canonical V1 scope and document-version applicability

V1 has exactly one canonical scope: a document-scoped fact stream keyed by `document_id`.

Each event binds an exact `subject_document_version_id`. That binding expresses applicability; it does not create a second, source-scoped stream. There is no source-scoped stream and no span-level override in V1.

A new document version has no classification until an applicable event exists for it. Absence is not inherited permission.

`EvidenceContextSourceReference` resolves, read-only, the exact document, the exact document version, the strict-known fact set, and the exact event identity that produced it. It does not create, override, or reinterpret the fact set. `EvidenceContextSourceReference` is not redefined by this ADR; ADR 0031 already defines it, and implementing it is conformance to ADR 0031 rather than new architecture.

`EvidenceSpan` is unchanged. Span-level classification override is an explicit non-goal.

**Mixed-content documents.** Because V1 classification is document-scoped, a document whose portions differ is resolved per dimension as follows:

- if any covered portion establishes a restrictive value, the document takes that value;
- if no restrictive value applies but any relevant portion is `unknown`, the dimension remains `unknown`;
- a permissive value requires complete authoritative coverage of the document.

Consequently one secret-bearing portion makes the entire document `secret_bearing`, and one ephemeral portion makes the entire document `ephemeral_non_retainable`. Selective span retention is deferred.

### 8. Retention policy authority

A retention policy version is an immutable record authorized and resolved by Role B. It binds:

```text
logical_policy_id
semantic_version
schema_version
policy_content_identity
immutable_body
applicability_rules
authorizing_provenance_ids
authorizing_actor_identity
authorizing_authority_role
effective_at
recorded_at
predecessor_policy_content_identity | null
correction_reason | null
```

`policy_content_identity` is deterministic over the full canonical policy record except explicitly operational storage metadata.

Lifecycle is append-only and immutable. No policy record is overwritten. One logical policy identity and semantic version map to exactly one content identity; a changed body requires both a new semantic version and a new content identity. A correction creates a successor. A historical policy body remains retrievable by its exact content identity. The repository rejects a write presenting the same identity with different bytes.

A caller may supply an expected policy content identity and nothing more. Role B independently resolves the applicable strict-known policy. If the caller's expected identity differs from the canonical applicable policy, the build is `BLOCKED`. A caller may not select an older, more permissive policy, invent a policy identity, or supply a policy body as authority.

### 9. Retention derivation

A `RetentionDecision` is defined only as:

```text
derive(strict-known SourceHandlingFactSet, exact immutable RetentionPolicyVersion)
```

Policy governs at least these categories:

- model-facing processing eligibility;
- prompt bytes;
- source bytes and excerpts;
- content hashes;
- locators;
- coordinates and offsets;
- section titles and other source-derived metadata;
- reconstruction eligibility.

Each category resolves to exactly one disposition, ordered `PERMIT` < `PROHIBIT` < `REJECT_BUILD`. Where several constraints apply to one category, they combine to the most restrictive disposition.

**Mandatory safety floor.** The following are binding regardless of policy body:

- any `unknown` dimension prevents `READY`;
- missing or conflicted classification prevents `READY`;
- missing or conflicted policy prevents `READY`;
- `secret_bearing` yields `REJECT_BUILD`;
- caller assertion, or any identity mismatch between expected and canonical inputs, yields `REJECT_BUILD`;
- `ephemeral_non_retainable` prohibits prompt-byte and source-byte persistence;
- a category omitted by the policy resolves to `PROHIBIT`;
- where prompt bytes or source bytes are prohibited, hashes, locators, coordinates, metadata, and reconstruction default to `PROHIBIT` unless the policy explicitly permits that specific category and no other constraint prohibits it;
- reconstruction eligibility requires every required exact byte, identity, and compiler input, and every required metadata category, to be permitted and available.

Hashes are not assumed harmless: a hash of small or enumerable content can disclose the content it represents.

**Build status** is one of:

- `READY` — processing is permitted and every required retained and replay category is permitted and available;
- `DEGRADED` — processing is explicitly permitted, some retention or reconstruction categories are prohibited, and that unavailability is explicit;
- `BLOCKED` — processing is rejected, required authority is unavailable or conflicted, or an invariant failed.

The decision record binds `fact_event_id`, `fact_set_identity`, `policy_content_identity`, `effective_cutoff`, `recorded_cutoff`, `per_category_dispositions`, `build_status`, `reason_codes`, `schema_version`, and `decision_identity`, where `decision_identity` covers all of those fields.

### 10. Persistence enforcement

The persistence boundary does not trust a supplied decision. Before writing, it must reload the authoritative inputs, verify the exact cutoffs and document version, recompute the fact set, recompute the decision, compare the full canonical decision and its identity, and validate that the categories actually present or absent in the payload match the recomputed dispositions. Any mismatch is rejected.

A caller-supplied retention decision is never authority.

### 11. Rejected pre-model build audit record

A rejected pre-model build must be able to persist an immutable audit record whose architectural shape binds:

```text
RejectedPreModelBuildRecord
    rejected_attempt_id
    execution_owner_reference | null
    effective_cutoff
    recorded_cutoff
    recorded_at
    typed_rejection_reason
    handling_event_reference | null
    policy_content_identity | null
    safe_predecessor_attempt_id | null
    omitted_reference_categories
    reference_safety_results
    schema_version
```

`rejected_attempt_id` is an opaque random operational identity. It is not content-addressed and carries no semantic authority.

The record's identity must contain no prompt material, source material, artifact material, locator, coordinate, metadata, or hash of prohibited content.

Absence of raw bytes is not by itself sufficient, because an identifier derived from protected content can disclose that content. Every prospective reference must therefore be typed as `SAFE_CONTROL_ID`, `CONTENT_DERIVED_ID`, or `UNKNOWN_IDENTITY_TYPE`. Only `SAFE_CONTROL_ID` references persist. Unsafe and unknown references are omitted and the omission is recorded with a typed code, including codes equivalent to `PROMPT_ID_OMITTED_CONTENT_DERIVED`, `SOURCE_ID_OMITTED_CONTENT_DERIVED`, `BUILD_LINEAGE_OMITTED_UNSAFE`, `HANDLING_EVENT_ID_OMITTED_UNSAFE`, `PROVENANCE_ID_OMITTED_UNSAFE`, and `REFERENCE_IDENTITY_TYPE_UNKNOWN`.

Rejection must never be silent.

### 12. Historical and legacy boundary

This ADR binds the architectural rule and not its implementation:

- a record written under an earlier schema version remains a record of that version;
- fields introduced by a later schema version must never enter an earlier version's deterministic identity calculation;
- a decoder must dispatch on the stored schema version before constructing any object;
- each schema version retains its own canonicalization and identity rules;
- no current classification or policy is inserted into an older record.

**Builds that predate this authority.** Such a build records `historical_governance_status = GOVERNANCE_NOT_RECORDED`, with classification event, policy content identity, and retention decision identity all explicitly absent. Where exact historical bytes exist, the build records `HISTORICAL_BYTES_PRESENT_GOVERNANCE_UNKNOWN`; where they do not, it records `EXACT_RECONSTRUCTION_UNAVAILABLE`. Neither state may be described as governed exact reconstruction.

The presence of historical bytes under an older storage contract is not retroactive proof that their retention was authorized. A pre-authority build cannot become `READY` through ADR 0033 replay.

A later classification or policy correction does not rewrite an older build. The older build retains its exact historical identities.

### 13. Typed content state

Persisted content state is one of `PRESENT`, `OMITTED_BY_POLICY`, `DELETED_BY_POLICY`, or `NEVER_RETAINED`.

In-band magic strings are never authoritative content or redaction markers. Persisted omission state must be structurally distinguishable from legitimate source text, so that source text resembling a marker cannot be mistaken for omitted state and omitted state cannot be mistaken for content.

### 14. Security boundary

This ADR binds now:

- canonical pre-model artifacts exclude transport headers, authentication fields, provider credentials, and transport configuration;
- source-handling policy covers every rendered and persisted pre-model field, not only source excerpts;
- content secret detection is defence-in-depth only;
- a positive detection may increase restriction or reject a build;
- a negative detection may never establish `not_secret_bearing`;
- prohibited secret material must not appear in errors, hashes, logs, audit records, or debug payloads.

Deferred to a future Model Adapter decision: credential acquisition, credential storage, transport injection mechanics, provider request wrapping, authentication retries, and provider routing or invocation. This ADR binds only the exclusion boundary.

## Non-Goals

This ADR does not define or authorize:

- any change to `EvidenceSpan`;
- any redefinition of `EvidenceContextSourceReference`;
- span-level classification override;
- generic Prompt Intelligence or generic Context Intelligence ownership;
- a generic or cross-project source-handling core;
- Model Adapter architecture or implementation;
- provider routing, selection, health, or failover;
- provider credential runtime implementation beyond the structural exclusion boundary in §14;
- Response Validator architecture or business rules;
- retroactive purge or deletion of already-persisted records;
- retention expiry or scheduling machinery;
- selective span retention;
- any LLM or provider dependency for Hunter Governance Review, or any redesign of it;
- retroactive reinterpretation of legacy history;
- trading, signalling, or portfolio-allocation behaviour;
- SaaS or public-product architecture;
- any new analytical authority or `ANALYTICAL_AUTHORITY_REGISTRY` entry.

## Compatibility

ADR 0031 is reaffirmed and not superseded. This decision supplies the producer authority ADR 0031 requires and leaves every ADR 0031 contract, identity, and boundary unchanged.

ADR 0032 is reaffirmed. Ownership is consumer-side within Evidence Intelligence; no part of this authority may be admitted into a project-neutral core except through ADR 0032's evidence-gated admission rule.

ADR 0020 is reaffirmed and relied upon for strict-known selection without amendment.

ADR 0016 is unaffected. No analytical output, semantic owner, or promotion is created, and no `docs/ANALYTICAL_AUTHORITY_REGISTRY.md` entry is required, because source-handling classification is a data-handling control fact rather than an analytical or analytical-looking output.

ADR 0004 is unaffected. Epistemic trust and data handling remain separate dimensions; a source may be authoritative without being retainable, and retainable without being authoritative.

ADR 0009 is reaffirmed: producer service, repository persistence, and consuming service remain separate responsibilities.

No accepted ADR is superseded by this decision.

## Implementation Status

Architecture only. This acceptance authorizes no runtime implementation.

Implementation of the fact model, classification events, retention policy versions, retention derivation, persistence enforcement, `EvidenceContextSourceReference`, version-aware decoding, typed content state, and the rejected-build audit record remains subject to the normal development lifecycle and to separately approved scope.

## Consequences

- Evidence Intelligence gains one named canonical owner for source-handling facts and policies, closing the authority gap that blocks conformant retention work.
- Orthogonal dimensions make simultaneous restrictions representable, so a source that is secret-bearing, licensed, and removed retains all three facts instead of losing two to a single-value vocabulary.
- Separating facts from outcomes removes retainability from anything a producer or provider can assert; retention exists only as a derived decision over an exact policy version.
- Retention outcomes become replayable through recorded fact, policy, and decision identities, and are independently rederived at the persistence boundary rather than trusted.
- Conflicted classification streams and policy mismatches surface as `BLOCKED` rather than being silently resolved, so an ambiguous source stops work instead of receiving a preferred interpretation.
- `unknown` in any dimension prevents `READY`. Sources will require real classification coverage before model-facing work proceeds, and partial coverage produces explicit degradation rather than optimistic permission.
- Deny-by-default for hashes, locators, coordinates, and metadata will make some historical reconstruction unavailable. Unavailability is recorded explicitly rather than worked around.
- Document-scoped classification cannot express a document whose portions differ in sensitivity; such sources are resolved to their most restrictive applicable value until span-level override is authorized by a later decision.
- Builds predating this authority remain readable as history but can never be presented as governed exact reconstruction and cannot become `READY` through ADR 0033 replay.
- Hunter Governance Review remains deterministic and free of any LLM or provider dependency.

## Alternatives Considered

### Express handling as a single closed enumeration

Rejected after independent review of the first draft of this ADR. A single value cannot represent a source that is simultaneously secret-bearing, licensed, and removed; recording one restriction silently discards the others. It also admitted `retainable` — a derived outcome — into a vocabulary of facts, which would let a producer or a provider claim assert a conclusion that only policy may derive. The normalized five-dimension model preserves every restriction independently and keeps facts and outcomes separate.

### Attach mandatory classification fields directly to `EvidenceSpan`

Rejected. It would expand a canonical, widely constructed record across every construction site, carry no provenance or verifier semantics, provide no bitemporal history, and make classification mutable with the span. ADR 0031 explicitly permits the classification to be carried by "a governed reference from which it is derived", so the wider change is unnecessary as well as riskier.

### Keep classification and retention policy as consumer-supplied build inputs

Rejected. Independent review and empirical reproduction established that a caller able to supply the classification, its provenance string, and the policy body controls the retention outcome regardless of how those inputs are typed. That is the condition ADR 0031 forbids, and no amount of type discipline at the consumption boundary removes it.

### Define a new classifier-type taxonomy alongside the fact model

Rejected. The accepted `VerifierType` already expresses which kind of actor asserted a fact, and a parallel vocabulary would create two competing answers to the same question. Only the handling-classification method is introduced, because the accepted `VerificationMethod` members are all source-authority determinations and none expresses handling.

### Resolve divergent classification branches by timestamp or sequence preference

Rejected. Any automatic tie-break silently selects one interpretation of a source's handling, which is precisely the situation where a wrong choice is least recoverable. Divergence is therefore `BLOCKED` until an explicit reconciliation event preserves at least the restrictive join.

### Treat absence of raw bytes as sufficient safety for a rejected-build record

Rejected. An identifier derived from protected content can disclose that content, so a record containing no bytes may still leak through a content-addressed reference. Reference safety must be typed, and only control identities persist.

### Extend ADR 0004 to cover data handling

Rejected. Epistemic trust answers whether a source is reliable and authoritative; data handling answers whether its bytes may be processed, retained, and reconstructed. Merging them would let an authoritative source imply a retainable one, which is precisely the inference ADR 0031 prohibits.

### Register source-handling classification as an analytical output under ADR 0016

Rejected. It produces no analytical conclusion, and the accepted precedent for an equivalent evidence-layer control fact, `SourceAuthorityVerificationEvent`, carries no registry entry. Registering it would imply an analytical semantics it does not have and would invite an inapplicable promotion checklist.

### Span-level classification in V1

Rejected for this decision, and deferred rather than refused. It is a genuine future need for partially sensitive documents, but it is strictly larger than the boundary required to unblock conformant retention, and document-scoped classification with a most-restrictive mixed-content rule is sufficient and safe in the interim.

### Retroactive purge of already-persisted records

Rejected for this decision. Deletion of already-persisted material raises immutability, tombstone, and audit questions that are independent of establishing classification authority. Successor events preserve historical truth; retention deletion remains a separate future decision.

## Reasoning

Handling classification is a fact about a source, not a choice made by whoever consumes it. A consumer that can state the fact can determine its own permissions, which is why relocating a caller's boolean into typed caller inputs did not remove the authority problem.

Facts about a source are also plural and independent. Sensitivity, rights, secrecy, availability, and source-imposed persistence constraints can each hold or not hold without the others, so a model that admits only one value per source must discard information the moment two constraints apply at once. Normalizing the dimensions and joining them restrictively keeps every constraint that was ever established.

Separating facts from outcomes is what makes the authority hold. Once retainability exists only as a derivation over an exact policy version, no producer, provider, or caller has a vocabulary in which to assert it, and the persistence boundary can rederive the answer instead of trusting it.

Hunter already solved the ownership half of this problem once. `SourceAuthorityVerificationEvent` establishes a source-level fact at intake, immutably, from closed vocabularies, with provenance and bitemporal coordinates, and downstream consumers read it rather than restate it. Reusing that accepted shape keeps this decision small, keeps ownership where evidence enters the system, and avoids inventing a second way to express the same kind of fact.
