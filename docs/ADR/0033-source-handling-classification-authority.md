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

That gap is architectural, not a defect of a particular implementation. The registry rule is explicit: "Where authority or a unified semantic contract is absent, the owner is explicitly `none` and no component may claim that authority." An owner must therefore be assigned by an accepted decision before conformant implementation can proceed.

Evidence Intelligence already owns an accepted, implemented pattern for exactly this shape of fact. `SourceAuthorityVerificationEvent` is produced at document intake, drawn from closed vocabularies, carries provenance and a verifier type, records both `effective_at` and `recorded_at`, is appended immutably, and defaults to its least-privileged value. This ADR reuses that established pattern rather than introducing a new one.

Source-handling classification is a data-handling and security control fact. It is not an analytical or analytical-looking output, produces no analytical conclusion, and is therefore outside the scope of `docs/ANALYTICAL_AUTHORITY_REGISTRY.md` and the ADR 0016 analytical promotion rules. The accepted precedent is consistent: `SourceAuthorityVerificationEvent` carries no registry entry.

## Decision

Hunter assigns canonical ownership of source-handling classification facts to the Evidence Intelligence consumer-side intake and document-lifecycle boundary, and binds the minimum semantics required for conformant retention and replay.

This decision fills the producer authority that ADR 0031 left open. It creates no new analytical authority, defines no generic or cross-project classification service, and does not redefine any existing contract.

### 1. Canonical ownership

The Evidence Intelligence intake and document-lifecycle boundary — the same boundary that produces `SourceAuthorityVerificationEvent` — is the sole canonical producer of source-handling classification facts.

- **May create** classification events: the Evidence Intelligence intake/document-lifecycle service.
- **May consume** them read-only: source resolution into `EvidenceContextSourceReference`, pre-model build derivation, and persistence enforcement.
- **May not create, override, restate, or substitute** them: callers, orchestrators, prompt compilation, any Prompt Intelligence or Context Intelligence layer, any generic or project-neutral core, a future Model Adapter, Hunter Governance Review, and any repository-wide classification service.

A caller may identify a document, source, or policy by identity. A caller may never manufacture the authoritative classification fact or the retention outcome. Caller assertion is not an authority source and never substitutes for a missing classification.

### 2. Source handling classification event

The canonical record concept is an immutable, append-only, document-scoped source-handling classification event.

Minimum semantics:

- canonical document/source identity;
- handling-classification vocabulary version;
- handling classification value;
- classification evidence/provenance identity;
- verifier type, reusing the accepted `VerifierType` semantics;
- handling classification method;
- reason;
- processing/run identity where applicable;
- `effective_at`;
- `recorded_at`;
- predecessor identity where the event supersedes an earlier classification;
- deterministic content identity and schema version.

Deterministic identity, canonicalization, and persistence authorization follow Hunter's existing accepted patterns under ADR 0009 and the document-lifecycle precedent. This ADR does not prescribe SQL layouts, table names, or module paths.

### 3. Closed V1 handling vocabulary

The V1 handling classification vocabulary is closed and versioned:

- `retainable`;
- `licensed_restricted`;
- `personal_sensitive`;
- `ephemeral_non_retainable`;
- `source_removed`;
- `secret_bearing`;
- `unclassified`.

Arbitrary runtime string classifications are prohibited. `unclassified` is the least-privileged default and is never retainable. A vocabulary change requires a new accepted decision and a vocabulary version increment.

### 4. Verifier and method semantics

The accepted `VerifierType` semantics are reused unchanged. This ADR does not define a competing classifier-type taxonomy.

A minimal handling-classification method vocabulary is introduced, because every accepted `VerificationMethod` member expresses source-authority verification rather than handling determination and none applies:

- `intake_source_policy`;
- `source_type_default`;
- `provider_declaration`;
- `manual_verified_evidence`;
- `unclassified_default`.

`manual_verified_evidence` intentionally preserves the naming of the accepted `VerificationMethod` member of the same name to avoid a divergent convention for the same human-verified semantics.

### 5. Temporal semantics

Classification events are immutable and append-only. Reclassification appends a successor event with predecessor lineage; no historical classification record is mutated.

Strict-known selection under ADR 0020 and ADR 0031 applies. Only classification events satisfying both the applicable `effective_at` cutoff and the applicable `recorded_at` cutoff are eligible. Current classification must never substitute for historical unknown state.

### 6. Document-scoped classification and span inheritance

V1 classification is document-scoped.

`EvidenceContextSourceReference` inherits the classification applicable to its document or source at the historical cutoff. `EvidenceContextSourceReference` is not redefined by this ADR; ADR 0031 already defines it, and implementing it is conformance to ADR 0031 rather than new architecture.

`EvidenceSpan` is unchanged. Span-level classification override is an explicit non-goal of this decision.

### 7. Retention policy authority

A retention decision derives from authoritative classification event(s) together with a governed, versioned, content-addressed retention policy persisted under the canonical owner named in this decision.

A caller may reference a policy identity and version. A caller-supplied policy body is never retention authority.

### 8. Governed data categories

A retention policy must explicitly govern at least:

- prompt bytes;
- source bytes and excerpts;
- content hashes;
- locators;
- coordinates and offsets;
- section titles and other source-derived metadata;
- reconstruction eligibility.

When exact bytes are prohibited for a classification, hashes, locators, coordinates, and metadata are also prohibited for that classification unless the policy explicitly permits that specific category. Hashes are not assumed harmless: a hash of small or enumerable content can disclose the content it represents.

### 9. Fail-closed semantics

The following outcomes are binding:

- `unclassified` is not retainable;
- classification unavailable at the applicable cutoff fails closed;
- retention policy unavailable fails closed;
- `secret_bearing` is prohibited and the affected build is rejected;
- a retention decision whose identity does not match its authoritative inputs is refused at the persistence boundary;
- caller assertion cannot substitute for missing authority.

Fail-closed evaluation occurs before any model-facing artifact is treated as ready.

### 10. Rejected-build audit record

A rejected pre-model build must be able to persist an immutable, byte-free failed-build record identifying:

- the typed rejection reason;
- the governing classification event identity;
- the retention policy identity and version;
- execution and build lineage.

The record must retain no prohibited bytes, and no hashes of prohibited content where the governing policy prohibits that category. Rejection must not be silent.

### 11. Replay

A pre-model build record must identify the exact classification event(s), the exact retention policy identity and version, and the exact retention decision identity that produced its outcome.

Replay resolves through those historical identities only. Current policy and current classification are never substituted for the historical values.

### 12. Legacy compatibility boundary

This ADR binds the architectural rule and not its implementation:

- records written under an earlier schema version remain records of that version;
- fields introduced by a later schema version must never enter an earlier version's deterministic identity calculation;
- historical absence of governed classification remains explicit absence and is never backfilled with a policy or classification that did not govern the record;
- version-aware decoding is required.

### 13. Typed redaction state

An in-band magic string is never an authoritative redaction marker. Persisted redaction or omission state must be structurally distinguishable from legitimate source text, so that source text identical to a marker cannot be mistaken for redacted state and redacted state cannot be mistaken for content.

### 14. Security boundary

Transport and authentication credentials are structurally excluded from canonical artifacts: they are constructed outside those artifacts and injected only at the transport boundary of a future Model Adapter, whose architecture this ADR does not define.

Content secret detection over pre-model inputs is defence-in-depth only. Pattern or regular-expression scanning must never be described or relied upon as structural exclusion.

## Non-Goals

This ADR does not define or authorize:

- Model Adapter architecture;
- provider routing, selection, health, or failover;
- provider credential runtime implementation beyond the structural boundary statement in §14;
- Response Validator architecture or business rules;
- generic Prompt Intelligence or generic Context Intelligence ownership;
- a generic or cross-project classification core;
- span-level classification override;
- retroactive purge or deletion of already-persisted records;
- retention expiry or scheduling machinery;
- any LLM or provider dependency for Hunter Governance Review;
- trading, signalling, or portfolio-allocation behaviour;
- SaaS or public-product architecture;
- any new analytical authority or analytical-registry entry.

## Compatibility

ADR 0031 is reaffirmed and not superseded. This decision supplies the producer authority ADR 0031 requires and leaves every ADR 0031 contract, identity, and boundary unchanged.

ADR 0032 is reaffirmed. Ownership is consumer-side within Evidence Intelligence; no part of this classification authority may be admitted into a project-neutral core except through ADR 0032's evidence-gated admission rule.

ADR 0020 is reaffirmed and relied upon for strict-known selection without amendment.

ADR 0016 is unaffected. No analytical output, semantic owner, or promotion is created, and no `docs/ANALYTICAL_AUTHORITY_REGISTRY.md` entry is required, because source-handling classification is a data-handling control fact rather than an analytical or analytical-looking output.

ADR 0004 is unaffected. Epistemic trust and data handling remain separate dimensions; a source may be authoritative without being retainable, and retainable without being authoritative.

ADR 0009 is reaffirmed: producer service, repository persistence, and consuming service remain separate responsibilities.

No accepted ADR is superseded by this decision.

## Implementation Status

Architecture only. This acceptance authorizes no runtime implementation.

Implementation of source-handling classification, retention policy persistence, `EvidenceContextSourceReference`, version-aware decoding, typed redaction state, and the rejected-build audit record remains subject to the normal development lifecycle and to separately approved scope.

## Consequences

- Evidence Intelligence gains one named canonical producer for source-handling classification, closing the authority gap that blocks conformant retention work.
- Retention outcomes become derivable from authoritative facts and governed policy rather than from caller assertion, and become replayable through recorded identities.
- Consumers that previously supplied classification must instead read it; a consumer with no governing classification fails closed rather than defaulting to retention.
- Deny-by-default for hashes, locators, coordinates, and metadata will make some historical reconstruction unavailable where the governing policy does not explicitly permit those categories. Unavailability is recorded explicitly rather than worked around.
- Document-scoped classification cannot express a document whose spans differ in sensitivity. Such sources must be classified at their most restrictive applicable level until a later decision authorizes span-level override.
- Existing intake call sites remain valid: the fail-closed `unclassified` default preserves the accepted `unverified` precedent and does not require every caller to supply a classification immediately.
- Hunter Governance Review remains deterministic and free of any LLM or provider dependency.

## Alternatives Considered

### Attach mandatory classification fields directly to `EvidenceSpan`

Rejected. It would expand a canonical, widely constructed record across every construction site, carry no provenance or verifier semantics, provide no bitemporal history, and make classification mutable with the span. ADR 0031 explicitly permits the classification to be carried by "a governed reference from which it is derived", so the wider change is unnecessary as well as riskier.

### Keep classification and retention policy as consumer-supplied build inputs

Rejected. Independent review and empirical reproduction established that a caller able to supply the classification, its provenance string, and the policy body controls the retention outcome regardless of how those inputs are typed. That is the condition ADR 0031 forbids, and no amount of type discipline at the consumption boundary removes it.

### Define a new classifier-type taxonomy alongside the classification vocabulary

Rejected. The accepted `VerifierType` already expresses which kind of actor asserted a fact, and a parallel vocabulary would create two competing answers to the same question. Only the handling-classification method is introduced, because the accepted `VerificationMethod` members are all source-authority determinations and none expresses handling.

### Extend ADR 0004 to cover data handling

Rejected. Epistemic trust answers whether a source is reliable and authoritative; data handling answers whether its bytes may be processed, retained, and reconstructed. Merging them would let an authoritative source imply a retainable one, which is precisely the inference ADR 0031 prohibits.

### Register source-handling classification as an analytical output under ADR 0016

Rejected. It produces no analytical conclusion, and the accepted precedent for an equivalent evidence-layer control fact, `SourceAuthorityVerificationEvent`, carries no registry entry. Registering it would imply an analytical semantics it does not have and would invite an inapplicable promotion checklist.

### Span-level classification in V1

Rejected for this decision, and deferred rather than refused. It is a genuine future need for partially sensitive documents, but it is strictly larger than the boundary required to unblock conformant retention, and document-scoped classification with inheritance is sufficient and safe in the interim.

### Retroactive purge of already-persisted records

Rejected for this decision. Deletion of already-persisted material raises immutability, tombstone, and audit questions that are independent of establishing classification authority. Successor events preserve historical truth; retention deletion remains a separate future decision.

## Reasoning

Handling classification is a fact about a source, not a choice made by whoever consumes it. A consumer that can state the fact can determine its own permissions, which is why relocating the caller's boolean into typed caller inputs did not remove the authority problem.

Hunter already solved the equivalent problem once. `SourceAuthorityVerificationEvent` establishes a source-level fact at intake, immutably, from closed vocabularies, with provenance and bitemporal coordinates, defaulting to its least-privileged value, and downstream consumers read it rather than restate it. Reusing that accepted shape keeps this decision small, keeps ownership where evidence enters the system, and avoids inventing a second way to express the same kind of fact.
