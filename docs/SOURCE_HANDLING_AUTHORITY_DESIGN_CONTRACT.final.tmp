# Source Handling Authority Design / Implementation Contract

## Status

Draft for one final independent acceptance review under Issue #264.

## Governing authority

This contract is subordinate to ADR 0033 (Accepted), ADR 0031 (Accepted), ADR 0032 (Accepted), ADR 0020 strict-known replay, ADR 0009 producer/repository/consumer separation, and `docs/HUNTER_IMPLEMENTATION_CONTRACT.md`.

If this contract conflicts with an accepted ADR, the ADR wins. This document defines mechanics only; it does not change architectural ownership.

## 1. V1 scope

V1 is document-version scoped. A handling state applies to one immutable source document/version identity. Span-level override is out of scope. Mixed-content documents resolve by restrictive combination; no permissive span may weaken a restriction that applies elsewhere in the same governed document version.

## 2. Canonical authority and anti-laundering rule

The Evidence Intelligence Source Handling Authority is the only component allowed to publish authoritative source-handling facts or governed source-handling policy.

Callers, providers, orchestrators, prompt construction, repositories, persistence adapters, generic cores, and future model-facing components are evidence suppliers or consumers only. Routing their assertions through an authority method does not make those assertions authoritative.

Caller/provider evidence may, by itself, only preserve restriction, increase restriction, or leave authority unresolved. It may never, by itself, create a permissive genesis fact, release a restriction, publish a less-restrictive correction, publish canonical policy, or grant processing, retention, reconstruction, access, or deletion/lifecycle permission.

### 2.1 Closed evidence-strength classes

`EvidenceStrength` is closed in V1:

- `ASSERTION_ONLY`: caller/provider/request assertion with no independent governed verification.
- `OBSERVED_RESTRICTIVE_SIGNAL`: independently observed evidence that can only add or preserve restriction.
- `AUTHORITATIVE_SOURCE_EVIDENCE`: evidence from a governed source that is itself authorized to establish the relevant source-handling fact.
- `INDEPENDENT_VERIFIED_EVIDENCE`: evidence independently verified under an approved method and verifier combination.
- `UNKNOWN`: insufficiently classified evidence.

Only `AUTHORITATIVE_SOURCE_EVIDENCE` or `INDEPENDENT_VERIFIED_EVIDENCE` may support a permissive genesis, a restriction release, or a less-restrictive correction, and only after explicit Source Handling Authority authorization. `ASSERTION_ONLY`, `OBSERVED_RESTRICTIVE_SIGNAL`, and `UNKNOWN` can never support permission.

### 2.2 Closed evidence methods

`EvidenceMethod` is closed in V1:

- `SOURCE_DECLARATION_VERIFIED`
- `SOURCE_TERMS_VERIFIED`
- `SOURCE_ACCESS_POLICY_VERIFIED`
- `SECURITY_CLASSIFICATION_VERIFIED`
- `REMOVAL_OR_WITHDRAWAL_VERIFIED`
- `INDEPENDENT_DOCUMENT_REVIEW`
- `PROVIDER_OBSERVATION`
- `CALLER_ASSERTION`
- `AUTOMATED_RESTRICTIVE_DETECTOR`
- `UNKNOWN`

`PROVIDER_OBSERVATION`, `CALLER_ASSERTION`, `AUTOMATED_RESTRICTIVE_DETECTOR`, and `UNKNOWN` are never sufficient for a permission-increasing publication.

### 2.3 Verifier types

The contract reuses the accepted verifier-type semantics already present in Hunter. For this boundary, a verifier is admissible only when the exact verifier type is explicitly permitted for the selected evidence method by a versioned Source Handling Authority policy. No caller-selected verifier is authoritative merely because its identifier is syntactically valid.

### 2.4 Publication authorization

Every authoritative fact publication and every policy publication must carry immutable `PublicationAuthorization` provenance containing:

- `authorization_id`
- `authority_component_id`
- `publication_kind` (`FACT` or `POLICY`)
- exact evidence identities
- evidence strengths
- evidence methods
- verifier identities/types
- `effective_from`
- `recorded_at`
- `known_at`
- authorizing policy/rule version
- predecessor/supersession identity where applicable

A permissive genesis, restriction release, or less-restrictive correction is invalid unless the authorization proves that every released restriction is supported by admissible evidence under the exact historical authorization rule. Policy publication likewise requires Source Handling Authority authorization and immutable authorizing provenance; callers cannot supply a policy body that becomes canonical by being passed to a publication API.

## 3. Normalized fact model

V1 facts are a product of independent fields. A permission is never encoded as a fact.

### 3.1 Sensitivity

Single ordered value:

`PUBLIC < INTERNAL < CONFIDENTIAL < RESTRICTED < UNKNOWN`

Join is `max` under this restrictive order. `UNKNOWN` is absorbing and yields unresolved authority.

### 3.2 Operation restrictions

A closed set, allowing simultaneous restrictions:

- `MODEL_PROCESSING_PROHIBITED`
- `RECONSTRUCTION_PROHIBITED`
- `ACCESS_RESTRICTED`

Join is set union. Empty set means no restriction is established by this dimension; it is not permission by itself. Unknown operation-restriction knowledge is represented separately by `operation_restrictions_known=false` and causes `BLOCKED`.

### 3.3 Persistence restriction

Single ordered value:

`FULL_CONTENT_ALLOWED < DERIVED_ONLY < METADATA_ONLY < NO_PERSISTENCE < UNKNOWN`

Join is `max`. `UNKNOWN` is absorbing and yields `BLOCKED`.

This field is the sole owner of persistence restriction semantics; no other dimension duplicates `NO_PERSISTENCE`.

### 3.4 Secret presence

Closed set:

- `SECRET_PRESENT`
- `CREDENTIAL_PRESENT`

Join is set union, so both may be present simultaneously. `secret_presence_known=false` causes `BLOCKED`. Empty set is valid only when governed evidence establishes absence under the selected historical rule.

### 3.5 Availability state

Independent booleans plus knowledge marker:

- `withdrawn`
- `deleted_at_source`
- `historically_unavailable`
- `availability_known`

Join is boolean OR for each restriction flag; `availability_known=false` causes `BLOCKED`. Withdrawal, deletion, and historical unavailability may therefore coexist without erasure.

### 3.6 Restrictive product join

Fact-set join is component-wise using the exact rules above. It is associative, commutative, and idempotent. No less-restrictive incoming fact can remove an existing restriction. Any unknown required knowledge marker or absorbing `UNKNOWN` produces unresolved authority and `BLOCKED`.

## 4. Canonical record families

### 4.1 `SourceHandlingFactRecord`

Immutable fields:

- `fact_record_id`
- `document_version_id`
- `fact_schema_version`
- complete normalized fact product
- provenance identities
- `publication_authorization_id`
- `effective_from`
- `recorded_at`
- `known_at`
- `supersedes_fact_record_id` when applicable
- `record_status`

No update in place.

### 4.2 `SourceHandlingPolicyRecord`

Immutable fields:

- `policy_record_id`
- `policy_schema_version`
- exact policy scope selector
- deterministic policy body
- `publication_authorization_id`
- `effective_from`
- `recorded_at`
- `known_at`
- `supersedes_policy_record_id` when applicable
- `record_status`

The policy body must derive all five top-level handling decisions and all durable field/category dispositions described below. Caller-supplied policy bodies or expected identities are comparison inputs only.

### 4.3 `SourceHandlingDecision`

Derived, non-authoritative output containing:

- resolved fact identity
- resolved policy identity
- `processing_decision`
- `retention_decision`
- `reconstruction_decision`
- `access_decision`
- `deletion_lifecycle_decision`
- complete `durable_dispositions` map keyed by governed data category and operation
- decision schema/version

No omission implies permission.

## 5. Strict-known historical selection

Historical resolution receives a document/version identity, target effective context, and replay cutoff.

A fact, policy, authorization, or required provenance record is eligible only when all are true:

- it is effective for the target context;
- `recorded_at <= cutoff`;
- `known_at <= cutoff`;
- every required referenced provenance record was also recorded and known by the cutoff.

Missing or unknown `known_at` is not guessed and yields `BLOCKED`.

Backdated effective time does not make later-known information historically eligible.

### 5.1 Successor selection

For each authority family, build the eligible supersession graph using only eligible records. An eligible record may supersede only the exact predecessor identity it names.

Resolution succeeds only when there is exactly one eligible authoritative head and that head unambiguously descends from/supersedes every other eligible record in the same scope. Multiple eligible heads, divergent branches, overlapping policies with no single dominating successor, cycles, missing predecessor linkage, or scope ambiguity yield `BLOCKED`.

Never resolve ambiguity by current/latest state, greatest timestamp, sequence number, insertion order, lexical ID, repository order, or provider preference.

Historical absence remains absence and may not be backfilled from present-day authority.

## 6. Policy derivation

The policy engine consumes only the exact strict-known fact record, the exact strict-known policy record, and non-authoritative operation context that may narrow the requested action but may not relax authority.

A permissive outcome requires explicit support in both resolved facts and exact governed policy. Absence of a prohibition is not permission. Any unresolved required input yields `BLOCKED` before model-facing processing.

## 7. Governed durable data categories

Every durable field must be mapped by a versioned `FieldCategoryRegistry` owned by the Source Handling Authority design boundary. V1 categories are:

- `SOURCE_BYTES`
- `SOURCE_DERIVED_TEXT`
- `CONTENT_DERIVED_ID`
- `LOCATOR_URL`
- `COORDINATE`
- `OPERATIONAL_METADATA`
- `DIAGNOSTIC`
- `PROVENANCE_ID`
- `AUDIT_FIELD`
- `SAFE_CONTROL_ID`
- `RECONSTRUCTION_METADATA`
- `ACCESS_CONTROLLED_REPRESENTATION`
- `LIFECYCLE_STATE`
- `UNKNOWN_CATEGORY`

A field may map to multiple categories. Its effective disposition is the most restrictive disposition across all applicable categories. Unknown, omitted, or ambiguous mapping is `UNKNOWN_CATEGORY` and is non-persistable; if the field is required for the requested operation, the build is `BLOCKED`.

`SAFE_CONTROL_ID` is allowed only for an identity whose construction is governed and proven not to encode, hash, derive from, or reveal prohibited source content. A name such as “ID”, “hash”, “locator”, “coordinate”, “metadata”, “diagnostic”, or “audit” never makes a value safe.

## 8. Durable disposition matrix

For each governed category, `SourceHandlingDecision.durable_dispositions` must explicitly provide dispositions for the operations that can affect durable state:

- `PERSIST`
- `READ_ACCESS`
- `RECONSTRUCT`
- `DELETE_OR_EXPIRE`

Closed disposition values:

- `ALLOW`
- `OMIT`
- `REDACT`
- `DENY`
- `DELETE`
- `EXPIRE`
- `BLOCKED`

There is no implicit default to `ALLOW`. Missing or ambiguous disposition is `BLOCKED`.

Restriction order for persistence/access/reconstruction is:

`ALLOW < REDACT < OMIT < DENY < BLOCKED`

For lifecycle operations, `DELETE` and `EXPIRE` are mandatory actions when selected by policy and cannot be weakened to retention. When several dispositions apply, choose the most restrictive applicable result; mandatory deletion/expiry obligations are preserved independently and are never erased by an `ALLOW` on another axis.

The complete disposition map must cover source bytes, excerpts/source-derived text, content-derived IDs/hashes, locators/URLs, coordinates, metadata, diagnostics, provenance identifiers, audit fields, access-controlled representations, reconstruction metadata, and lifecycle state.

## 9. Persistence enforcement

Persistence is enforcement, never authority. Before any durable write, it must independently:

1. strict-known resolve facts and policy;
2. verify publication authorizations and provenance eligibility;
3. rederive the complete `SourceHandlingDecision`;
4. remap every actual field to all governed categories;
5. compute the effective disposition for the exact representation;
6. reject any caller/provider identity, classification, policy, decision, disposition, or lineage mismatch;
7. write only representations explicitly permitted by the complete map.

No prohibited data may be written first and cleaned up later.

## 10. Rejected-build audit

A blocked build may create a durable audit record only if every audit field is mapped and explicitly permitted by the complete disposition map. Otherwise the failure remains transient operational state only.

Audit persistence must never contain source bytes, source-derived free text, excerpts, credentials, secrets, prohibited hashes/content-derived IDs, restricted locators/coordinates, or caller/provider diagnostics unless the exact category disposition permits that exact representation.

## 11. Secrets and credentials

Authentication credentials are transport-only and are structurally excluded from canonical provider/model request artifacts, source-handling records, identities, diagnostics, audit records, and persistence payloads.

Source content may independently be `SECRET_PRESENT`, `CREDENTIAL_PRESENT`, or both. Unknown secret presence blocks model-facing processing. Heuristic/regex detectors are defense in depth only: a hit may add restriction, but absence of a hit never grants permission and cannot establish governed absence.

## 12. Typed content state

Content-bearing durable fields use explicit state:

- `PRESENT`
- `OMITTED_BY_POLICY`
- `REDACTED_BY_POLICY`
- `DELETED_BY_POLICY`
- `NEVER_RETAINED`
- `UNAVAILABLE_HISTORICALLY`

State records outcome only; it never creates permission. `PRESENT` requires `ALLOW` for the exact field/category/operation representation.

## 13. Legacy and migration

Pre-authority rows gain no retroactive authority. Migration must preserve historical absence, must not fabricate `known_at`, `recorded_at`, verifier, provenance, policy, publication authorization, or classification, and must not use current facts/policy as historical substitutes.

Existing historical bytes are not proof that processing, retention, access, or reconstruction was governed. Missing historical authority remains unavailable/blocked. Migration is deterministic, repeatable, and idempotent where applicable.

## 14. Module/API boundaries

Separate interfaces must exist for evidence submission, authoritative fact publication, authoritative policy publication, strict-known fact resolution, strict-known policy resolution, deterministic decision derivation, and persistence enforcement.

Evidence-submission APIs accept non-authoritative evidence only. Fact/policy publication APIs require validated `PublicationAuthorization`; repositories cannot mint one. Repositories persist/query immutable records only. Providers acquire evidence only. Prompt/model-facing consumers receive only derived decisions after successful authority resolution.

## 15. Failure semantics

V1 terminal outcome is either:

- `READY`: all required authority resolved and requested operation explicitly permitted; or
- `BLOCKED`: any required authority/fact/policy/provenance/category/disposition is missing, unknown, unavailable, conflicting, partial, ambiguous, or prohibitive.

There is no permission-bearing `DEGRADED` state.

## 16. Required tests before implementation

Tests are written before runtime implementation and must cover all root semantics.

### Authority and laundering

- caller/provider assertion passed through an authority method cannot create permissive genesis;
- provider/caller evidence cannot release a restriction;
- source/source-type derivation cannot turn assertion-only evidence into permission;
- unauthorized policy publication rejects;
- repository/API direct write cannot create canonical fact/policy without publication authorization;
- admissible restrictive evidence can add restriction;
- permissive publication requires admissible strength, method, verifier, authorization, and immutable provenance.

### Fact product and joins

- secret and credential presence coexist;
- model-processing and persistence restrictions coexist;
- withdrawn/deleted/historically-unavailable coexist;
- no persistence semantics are owned only by persistence restriction;
- every pair of incomparable set-valued restrictions preserves both members;
- product join is associative, commutative, idempotent;
- every unknown knowledge marker blocks.

### Strict-known replay

- unknown/missing `known_at` blocks;
- backdated but later-known evidence is invisible before `known_at`;
- later-recorded or later-known fact/policy/authorization is invisible before cutoff;
- current state cannot satisfy historical absence;
- overlapping policies with multiple heads block;
- divergent successors block;
- one eligible successor that unambiguously supersedes all earlier eligible records resolves;
- timestamp/sequence/lexical/insertion-order tie-breakers are not used.

### Persistence and complete dispositions

- every durable category has explicit operation dispositions;
- source bytes, excerpts, hashes, IDs, locators, URLs, coordinates, metadata, diagnostics, provenance IDs, audit fields, reconstruction metadata, and access-controlled representations are enforced individually;
- multi-category field receives the most restrictive result;
- unknown/ambiguous field category is non-persistable and blocks when required;
- tampered caller decision/disposition/policy identity rejects before write;
- access restrictions are enforced independently of persistence;
- reconstruction restrictions are enforced independently of retention;
- deletion/expiry obligations cannot be weakened by another allow;
- blocked-audit representation leaks nothing prohibited.

### Secrets, legacy, and counterfactual proof

- credentials cannot enter canonical artifacts through content, metadata, diagnostics, identities, hashes, or audit fields;
- detector hit can only add restriction; detector miss cannot grant permission;
- pre-authority record remains historically unresolved;
- migration cannot manufacture historical authority.

For every blocking regression class above, disable/remove the root rule and demonstrate that its regression test fails, per the Non-Vacuous Regression Tests and Harness Fidelity requirements in `docs/HUNTER_IMPLEMENTATION_CONTRACT.md`.

## 17. Implementation order

1. obtain one final independent acceptance review of this contract;
2. freeze the accepted contract;
3. write the deterministic tests above first;
4. implement record models/repositories mechanically;
5. implement publication authorization and strict-known resolution;
6. implement policy derivation and complete disposition map;
7. implement persistence enforcement and migration behavior;
8. integrate with the existing pre-model pipeline;
9. run repository-wide verification and counterfactual tests;
10. independent Codex implementation review against the frozen contract/tests;
11. only then resume or replace PR #260 as appropriate.

No implementation step may weaken ADR 0033 or invent authority to preserve existing code.

## 18. Non-goals

No generic Prompt/Context authority, Model Adapter, ResponseValidator, Governance Review redesign, analytical authority, trading/signalling/portfolio behavior, SaaS/public-product architecture, span-level V1 handling authority, or runtime implementation is authorized by this design contribution.

## 19. Completion condition

This contract is ready for tests-before-implementation only when one final independent review finds no P1/P2 design defect against the accepted architecture and the closed semantics above, Issue #264 acceptance criteria are satisfied, PR #260 remains untouched, and the repository owner approves the design contribution.
