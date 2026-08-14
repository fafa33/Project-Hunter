# Source Handling Authority Design / Implementation Contract

## Status

Draft for review under Issue #264.

## Governing authority

This contract is subordinate to and must conform to:

- ADR 0033 — Source Handling Classification Authority (`Accepted`);
- ADR 0031 — AI Context and Prompt Intelligence Foundation (`Accepted`);
- ADR 0032 — Project-Agnostic Prompt Intelligence Core and Project Adapter Boundary (`Accepted`);
- ADR 0020 — strict-known replay;
- ADR 0009 — producer / repository / consumer separation; and
- `docs/HUNTER_IMPLEMENTATION_CONTRACT.md`.

If this contract conflicts with any accepted ADR, the ADR wins and this contract must be corrected before implementation.

This document defines mechanics only. It does not change architectural ownership.

---

## 1. Design goal

Provide one deterministic, fail-closed implementation contract for the Evidence Intelligence Source Handling Authority so that:

1. authoritative source-handling facts are produced only by the canonical authority;
2. governed source-handling policy is produced only by the canonical authority;
3. all handling permissions are derived from authoritative facts plus the exact governed policy;
4. persistence independently rederives and verifies those permissions;
5. historical replay uses only authority knowable at the requested cutoff; and
6. callers, providers, orchestrators, prompt construction, repositories, persistence adapters, generic cores, and future Model Adapter components cannot manufacture permission.

---

## 2. V1 classification scope

V1 classification is document-version scoped.

A classification applies to one immutable source document/version identity, not to a mutable URL, mutable provider object, or logical source family.

Span-level override is not implemented in V1.

If one document version contains material with different handling requirements, the document version resolves to the most restrictive applicable facts and policy outcome. Mixed-content handling must never make a restricted span less restrictive because another span is permissive.

A future span-level design requires separate governed approval and must preserve the same fail-closed authority rules.

---

## 3. Authoritative handling facts

### 3.1 Fact dimensions

V1 uses orthogonal dimensions so simultaneous restrictions are preserved rather than collapsed into one enum.

Each dimension is closed and versioned.

Required dimensions:

- `sensitivity`
- `rights_restriction`
- `secret_status`
- `availability_status`
- `persistence_constraint`

Each authoritative fact set must contain one explicit value for every required dimension. Missing dimensions are not filled with defaults; the authority result is unresolved and therefore `BLOCKED`.

### 3.2 V1 vocabularies

`sensitivity`:
- `public`
- `internal`
- `confidential`
- `restricted`
- `unknown`

`rights_restriction`:
- `unrestricted`
- `restricted_use`
- `no_model_processing`
- `no_persistence`
- `unknown`

`secret_status`:
- `none_detected_by_governed_evidence`
- `secret_bearing`
- `credential_bearing`
- `unknown`

`availability_status`:
- `available`
- `withdrawn`
- `deleted_at_source`
- `unavailable`
- `unknown`

`persistence_constraint`:
- `persistence_allowed_by_source`
- `metadata_only`
- `no_content_persistence`
- `no_persistence`
- `unknown`

These values are source-handling facts or source-policy-relevant restrictions only. They do not assert final permission. Terms such as `retainable`, `processable`, or `reconstructable` are prohibited as source facts because they are derived outcomes.

### 3.3 Restrictive combination

When multiple admissible authoritative facts apply to the same document version, combination is monotonic toward restriction.

A less restrictive fact may not erase a more restrictive simultaneously applicable fact.

The implementation shall define one deterministic restrictive ordering per dimension and a pointwise join function. The join function must be associative, commutative, idempotent, and tested as such.

`unknown` never means permissive. If `unknown` remains in any required dimension after authoritative resolution, the result is unresolved and therefore `BLOCKED`.

---

## 4. Evidence admissibility and provenance

### 4.1 Evidence is not authority

Provider data, caller assertions, request metadata, repository contents, inferred labels, and policy hints may be evidence presented to the Source Handling Authority. None of them are authoritative by themselves.

### 4.2 Required provenance

Every accepted fact event must record sufficient provenance to identify:

- document/version identity;
- evidence source identity;
- evidence method;
- verifier type;
- evidence observation identity where available;
- effective time;
- recorded time;
- vocabulary/schema version; and
- predecessor/supersession linkage where applicable.

### 4.3 Restriction release

A more restrictive authoritative state may only be relaxed by a later authoritative correction or successor that:

1. identifies the restriction being changed;
2. supplies admissible evidence sufficient under the governed method/verifier rules;
3. preserves the prior record unchanged;
4. records explicit supersession; and
5. is independently resolvable at its own historical recorded time.

A caller request to relax a restriction is evidence only and cannot itself change authority.

---

## 5. Canonical record families

### 5.1 SourceHandlingFactRecord

Immutable authoritative record for one document version and one complete fact set.

Minimum logical fields:

- `fact_record_id`
- `document_version_id`
- `fact_schema_version`
- complete fact dimensions
- provenance references
- `effective_from`
- `recorded_at`
- `supersedes_fact_record_id` when corrective/successor
- `record_status`

No update-in-place path is allowed.

### 5.2 SourceHandlingPolicyRecord

Immutable authoritative policy version governing one explicit policy scope.

Minimum logical fields:

- `policy_record_id`
- `policy_schema_version`
- policy scope selector
- deterministic policy body
- `effective_from`
- `recorded_at`
- `supersedes_policy_record_id` when applicable
- `record_status`

The policy body must define derivation for all five handling decisions:

- processing
- retention
- reconstruction
- access
- deletion/lifecycle

A caller-supplied policy object or expected policy identity may be used only as an assertion to compare against the canonical result; it cannot become canonical because the caller supplied it.

### 5.3 SourceHandlingDecision

Derived, non-authoritative decision object produced only from an exact resolved fact record plus an exact resolved policy record.

Required decisions:

- `processing_decision`
- `retention_decision`
- `reconstruction_decision`
- `access_decision`
- `deletion_lifecycle_decision`

Each decision must be explicit and typed. No decision may be inferred from omission.

A decision object is reproducible output, not independent authority.

---

## 6. Deterministic identities and canonicalization

Every canonical record identity must be derived from a versioned canonical serialization of the identity-defining fields.

Requirements:

- canonical field order;
- explicit schema/version tag in the identity domain;
- normalized timestamps and enum spellings;
- no locale-dependent serialization;
- no map/dict iteration ambiguity;
- no mutable transport metadata in canonical identity;
- no credential or secret bytes in identity preimages.

Identity derivation must be documented in code and covered by golden-vector tests.

Changing identity-defining fields or canonicalization requires a new schema/version; existing identities are never silently reinterpreted.

---

## 7. Historical applicability and strict-known resolution

Resolution accepts a document/version identity plus a historical cutoff.

For each required authority family, only records satisfying both conditions are eligible:

- effective for the target historical context; and
- `recorded_at <= cutoff`.

Later-recorded corrections are invisible before their recorded time.

Current/latest state is never substituted when no eligible historical record exists.

If no unique authoritative fact record or no unique authoritative policy record is resolvable at the cutoff, result is `BLOCKED`.

If two eligible non-superseded records conflict and the conflict cannot be resolved deterministically from authoritative succession metadata, result is `BLOCKED`.

Historical absence is a first-class result. It must not be backfilled from present-day authority.

---

## 8. Policy derivation

The policy engine consumes only:

- the exact authoritative historical fact record;
- the exact authoritative historical policy record; and
- explicit non-authoritative operation context required by the policy, such as requested operation type.

The operation context can narrow what is requested but cannot relax authority.

The derived result must contain all five handling decisions.

A permissive decision requires explicit authoritative support. Lack of a prohibition is not permission.

Any unresolved required input yields `BLOCKED` before model-facing processing.

---

## 9. Persistence enforcement

Persistence is an enforcement boundary, not an authority producer.

Before durable acceptance of any pre-model build or handling-related payload, persistence must independently:

1. resolve the authoritative historical fact record by document/version and cutoff;
2. resolve the exact governed historical policy record;
3. rederive all five handling decisions;
4. compare any supplied expected identities/decisions against the rederived result;
5. inspect every durable payload field by governed data category;
6. reject any field whose persistence is not explicitly permitted;
7. reject any mismatch between claimed lineage and canonical lineage; and
8. persist only the permitted representation.

The persistence layer must not trust:

- caller classifications;
- caller policy bodies;
- caller policy selections;
- caller decisions;
- caller decision identities;
- provider permission labels.

A mismatch fails closed and no prohibited bytes may be written first and cleaned up later.

---

## 10. Durable data categories

Every persistable field in a governed build bundle must map to one explicit category.

V1 categories:

- `SAFE_CONTROL_ID`
- `CONTENT_DERIVED_ID`
- `SOURCE_CONTENT`
- `SOURCE_DERIVED_TEXT`
- `OPERATIONAL_METADATA`
- `UNKNOWN_CATEGORY`

Rules:

- `UNKNOWN_CATEGORY` is non-persistable.
- `SOURCE_CONTENT` and `SOURCE_DERIVED_TEXT` require explicit retention permission.
- `CONTENT_DERIVED_ID` is not assumed safe merely because it is a hash/locator; policy must explicitly permit it.
- `SAFE_CONTROL_ID` is limited to identities proven not to encode or derive from prohibited content.
- `OPERATIONAL_METADATA` requires explicit category-level permission when it can reveal source-derived information.

There is no blanket exemption for hashes, coordinates, URLs, locators, excerpts, diagnostics, or metadata.

---

## 11. Rejected-build audit

Rejected or blocked builds may produce a durable audit/control record only if the record can be created without violating handling policy.

The rejected-build audit record must contain only policy-permitted control information.

It must not contain:

- source bytes;
- source-derived free text;
- excerpts;
- secret/credential values;
- caller-supplied diagnostic text derived from restricted content;
- content-derived identifiers unless explicitly permitted.

If even the intended audit representation is not permitted, durable audit persistence is unavailable; the failure may exist only as transient operational state.

---

## 12. Typed content state

Persisted/reconstructed content-bearing fields use explicit content state rather than null/empty ambiguity.

V1 states:

- `PRESENT`
- `OMITTED_BY_POLICY`
- `DELETED_BY_POLICY`
- `NEVER_RETAINED`
- `UNAVAILABLE_HISTORICALLY`

A state does not imply permission by itself. It records what happened after policy derivation.

`PRESENT` requires the corresponding bytes to be policy-permitted.

---

## 13. Secrets and credentials

Credential-bearing material is structurally excluded from canonical provider/model request artifacts and from canonical source-handling artifacts.

Authentication credentials are transport concerns and must be injected only at the transport boundary.

For source content, governed secret/credential classification must exist before model-facing processing.

Content-scanning regex or heuristic detection may be used only as defense in depth. It is not the authority mechanism and cannot convert unclassified input into permitted input.

A detector hit can only maintain or increase restriction unless resolved by authoritative correction.

---

## 14. Legacy and migration behavior

Existing persisted rows created before Source Handling Authority implementation do not gain retroactive authority.

Migration rules:

- preserve original bytes and metadata exactly where already persisted; do not rewrite historical payload merely to appear conformant;
- mark authority state for pre-authority records as historically unresolved unless real historical authority can be proven from contemporaneously recorded evidence;
- do not derive past permission from current policy or current classification;
- do not fabricate `recorded_at`, verifier, provenance, or source-policy history;
- historical reconstruction that lacks authority remains explicitly unavailable/blocked.

Schema migration must be deterministic, repeatable, idempotent where applicable, and covered by migration tests.

---

## 15. Module and API boundaries

Target ownership remains under Evidence Intelligence.

Implementation should expose separate interfaces for:

- authoritative fact ingestion/verification;
- authoritative policy publication/versioning;
- strict-known fact resolution;
- strict-known policy resolution;
- deterministic decision derivation;
- persistence enforcement/rederivation.

Repository classes persist and query records only. They do not decide permission.

Providers acquire evidence only. They do not decide permission.

Prompt compilation and future Model Adapter code consume a derived decision only after the Source Handling Authority has resolved it; they do not create or alter authority.

Exact module names may follow existing Evidence Intelligence structure, but ownership must remain explicit and non-duplicated.

---

## 16. Failure semantics

V1 terminal authority outcomes:

- `READY` — all required authority resolved and requested operation explicitly permitted;
- `BLOCKED` — any required authority is missing, unknown, unavailable, conflicting, ambiguous, or operation is prohibited.

There is no permission-bearing `DEGRADED` state in V1.

A diagnostic may describe partial evidence, but partial evidence cannot enable model-facing processing.

---

## 17. Required test matrix before implementation is accepted

Tests must be specified first and must cover at minimum:

### Authority boundary

- caller classification cannot grant permission;
- provider classification cannot grant permission;
- caller policy body cannot become canonical;
- caller-selected policy identity cannot override canonical resolution;
- caller decision cannot bypass persistence rederivation.

### Fact model

- simultaneous restrictions survive restrictive join;
- join is associative/commutative/idempotent;
- missing required dimension => BLOCKED;
- unknown required dimension => BLOCKED.

### Historical replay

- later-recorded fact correction invisible before cutoff;
- later-recorded policy invisible before cutoff;
- current state cannot satisfy historical absence;
- conflicting eligible authority => BLOCKED;
- exact historical selection is stable under replay.

### Persistence

- prohibited source bytes never written;
- prohibited source-derived text never written;
- prohibited hash/locator/metadata never written merely because it is non-raw content;
- mismatched supplied decision rejects before persistence;
- mismatched supplied policy identity rejects;
- permitted durable representation round-trips exactly.

### Secrets

- credential input cannot enter canonical artifacts;
- secret-bearing evidence blocks processing until authoritative resolution;
- heuristic detector is defense in depth only and cannot grant permission.

### Legacy

- pre-authority record remains historically unresolved;
- migration does not manufacture historical authority;
- missing historical authority stays missing after replay.

### Counterfactual / non-vacuous verification

For every blocking regression from PR #260/#262 represented by a behavioral test, demonstrate that removing or disabling the root fix makes the test fail.

At minimum reproduce the defect classes:

1. caller-controlled policy/classification granting retention;
2. persistence trusting a caller-supplied decision;
3. secret-bearing content leaking through a non-excerpt field;
4. metadata/hash/locator persistence escaping retention policy;
5. current-state or fabricated compatibility data making historical missing authority appear known.

---

## 18. Implementation order

Implementation must proceed in this order:

1. freeze this contract after independent review;
2. add deterministic tests for the contract invariants and defect classes;
3. implement authoritative record models and repositories;
4. implement strict-known resolution;
5. implement policy derivation;
6. implement persistence enforcement and migration/legacy behavior;
7. integrate with the existing pre-model pipeline;
8. run full repository verification;
9. independent Codex review against this contract and the tests;
10. only then resume/replace PR #260 as appropriate.

No step may weaken ADR 0033 to make existing code easier to preserve.

---

## 19. Non-goals

This contract does not define or authorize:

- generic Prompt Intelligence ownership;
- generic Context Intelligence ownership;
- Model Adapter architecture or implementation;
- ResponseValidator architecture or implementation;
- provider routing or model credentials;
- Hunter Governance Review redesign;
- analytical authority;
- trading, signalling, or portfolio allocation;
- SaaS/public-product behavior;
- span-level source-handling authority in V1.

---

## 20. Completion condition

This design contract is ready for implementation only when:

- independent review finds no architecture-contract contradiction;
- every Issue #264 acceptance criterion is satisfied;
- test obligations are explicit and mechanically implementable;
- PR #260 remains untouched; and
- repository owner approves the design contribution.

Acceptance of this contract still does not authorize merge of any implementation PR without the normal Hunter gates and explicit repository-owner approval.
