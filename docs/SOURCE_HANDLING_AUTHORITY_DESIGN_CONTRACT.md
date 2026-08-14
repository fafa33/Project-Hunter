# Source Handling Authority Design / Implementation Contract

## Status

Ready for review under Issue #264.

## Governing authority

This contract is subordinate to ADR 0033 (Accepted), ADR 0031 (Accepted), ADR 0032 (Accepted), ADR 0020 strict-known replay, ADR 0009 producer/repository/consumer separation, and `docs/HUNTER_IMPLEMENTATION_CONTRACT.md`.

If this contract conflicts with an accepted ADR, the ADR wins. This document defines mechanics only; it does not change architectural ownership.

## 1. V1 scope

V1 is document-version scoped. A handling state applies to one immutable source document/version identity. Span-level override is out of scope. Mixed-content documents resolve by restrictive combination; no permissive span may weaken a restriction that applies elsewhere in the same governed document version.

## 2. Canonical authority and anti-laundering rule

The Evidence Intelligence Source Handling Authority is the only component allowed to publish authoritative source-handling facts, governed source-handling policy, governed field-category registry versions, or governed authorization-rule versions.

Callers, providers, orchestrators, prompt construction, repositories, persistence adapters, generic cores, and future model-facing components are evidence suppliers or consumers only. Routing their assertions through an authority method does not make those assertions authoritative.

Caller/provider evidence may, by itself, only preserve restriction, increase restriction, or leave authority unresolved. It may never, by itself, create a permissive genesis fact, release a restriction, publish a less-restrictive correction, publish canonical policy, publish a canonical field-category registry, publish a successor authorization rule, or grant processing, retention, reconstruction, access, or deletion/lifecycle permission.

### 2.1 Closed evidence-strength classes

`EvidenceStrength` is closed in V1:

- `ASSERTION_ONLY`
- `OBSERVED_RESTRICTIVE_SIGNAL`
- `AUTHORITATIVE_SOURCE_EVIDENCE`
- `INDEPENDENT_VERIFIED_EVIDENCE`
- `UNKNOWN`

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

The contract reuses the accepted verifier-type semantics already present in Hunter. A verifier is admissible only when the exact verifier type is explicitly permitted for the selected evidence method by the exact strict-known `AuthorizationRuleRecord`. No caller-selected verifier is authoritative merely because its identifier is syntactically valid.

### 2.4 Publication authorization and exact payload binding

Every authoritative fact publication, policy publication, field-category-registry publication, and successor authorization-rule publication must carry immutable `PublicationAuthorization` provenance containing:

- `authorization_id`
- `authority_component_id`
- `publication_kind` (`FACT`, `POLICY`, `FIELD_CATEGORY_REGISTRY`, or `AUTHORIZATION_RULE`)
- exact governed scope/subject identity
- `authorized_payload_sha256`
- exact evidence identities
- evidence strengths
- evidence methods
- verifier identities/types
- exact `authorization_rule_id`
- `effective_from`
- `recorded_at`
- `known_at`
- predecessor/supersession identity or identities where applicable

`authorized_payload_sha256` is SHA-256 over the versioned canonical serialization of the exact publication payload, excluding only the publication record's own generated record ID and the `PublicationAuthorization` envelope/ID. The canonical payload includes the publication kind, governed scope, full fact/policy/registry/rule body, temporal fields that define the publication, and predecessor/supersession linkage. Canonicalization is schema-versioned and domain-separated by publication kind.

At publication, the Source Handling Authority must recompute the payload digest from the exact candidate record and require equality with `authorized_payload_sha256`, exact subject/scope equality, and exact publication-kind equality. At persistence and replay, the same binding is recomputed and reverified. An authorization valid for one payload, scope, or publication kind is invalid for every other payload, scope, or kind; it cannot be reused to launder a different canonical record.

A permissive genesis, restriction release, or less-restrictive correction is invalid unless the authorization proves that every released restriction is supported by admissible evidence under the exact historical authorization rule. Policy, field-category-registry, and successor authorization-rule publication likewise require Source Handling Authority authorization and immutable provenance; caller-supplied bodies or mappings cannot become canonical merely by passing through publication APIs.

### 2.5 Authorization-rule authority and bootstrap

`AuthorizationRuleRecord` is the immutable canonical owner of the evidence-strength/method/verifier admissibility matrix and of publication/restriction-release authorization requirements.

The V1 bootstrap is closed and singular: `AUTHORIZATION_RULE_V1` is the only genesis authorization rule. Its canonical body is exactly the minimum V1 admissibility invariants listed below plus the implementation's closed method-to-verifier matrix. The canonical serialization is versioned as `authorization-rule/v1`, domain-separated as `HUNTER_SOURCE_HANDLING_AUTHORIZATION_RULE`, UTF-8 encoded, and SHA-256 hashed. The implementation must ship one golden fixture containing that exact canonical body and one golden digest derived from it; migration recomputes the digest from the fixture and refuses to materialize the bootstrap record on mismatch. The fixture and digest are reviewed as part of the tests-before-implementation contribution before runtime code is accepted. There is no caller/provider input to this bootstrap path.

Every successor `AuthorizationRuleRecord` requires a `PublicationAuthorization` evaluated under the exact strict-known predecessor authorization rule and bound to the successor rule payload as defined in §2.4. A successor may not authorize itself. There is no implicit current-code/configuration fallback.

`AUTHORIZATION_RULE_V1` defines these minimum V1 admissibility invariants, which successors may strengthen but may not weaken without a separately accepted architecture change:

- `ASSERTION_ONLY`, `OBSERVED_RESTRICTIVE_SIGNAL`, and `UNKNOWN` never authorize a permission-increasing publication;
- `PROVIDER_OBSERVATION`, `CALLER_ASSERTION`, `AUTOMATED_RESTRICTIVE_DETECTOR`, and `UNKNOWN` never authorize a permission-increasing publication;
- permission-increasing fact publication requires `AUTHORITATIVE_SOURCE_EVIDENCE` or `INDEPENDENT_VERIFIED_EVIDENCE`, an allowed governed evidence method, and an allowed verifier type;
- policy, registry, and successor authorization-rule publication require Source Handling Authority authorization with immutable evidence/provenance and exact payload binding;
- restriction release must identify each released restriction and prove admissible support for that release.

The exact closed method-to-verifier matrix is a versioned body of `AUTHORIZATION_RULE_V1` and is covered by golden-vector tests; callers cannot choose or modify it.

## 3. Normalized fact model

V1 facts are a product of independent fields. A permission is never encoded as a fact.

### 3.1 Sensitivity

Single ordered value:

`PUBLIC < INTERNAL < CONFIDENTIAL < RESTRICTED < UNKNOWN`

Join is `max` under this restrictive order. `UNKNOWN` is absorbing and yields unresolved authority.

### 3.2 Operation restrictions

Closed set:

- `MODEL_PROCESSING_PROHIBITED`
- `RECONSTRUCTION_PROHIBITED`
- `ACCESS_RESTRICTED`

Join is set union. Empty set means no restriction is established by this dimension; it is not permission by itself. `operation_restrictions_known=false` yields `BLOCKED`.

### 3.3 Persistence restriction

Single ordered value:

`FULL_CONTENT_ALLOWED < DERIVED_ONLY < METADATA_ONLY < NO_PERSISTENCE < UNKNOWN`

Join is `max`. `UNKNOWN` is absorbing and yields `BLOCKED`. This field is the sole owner of persistence-restriction semantics.

### 3.4 Secret presence

Closed set:

- `SECRET_PRESENT`
- `CREDENTIAL_PRESENT`

Join is set union, so both may be present simultaneously. `secret_presence_known=false` yields `BLOCKED`. Empty set is valid only when governed evidence establishes absence under the selected historical rule.

### 3.5 Availability state

Independent booleans plus knowledge marker:

- `withdrawn`
- `deleted_at_source`
- `historically_unavailable`
- `availability_known`

Join is boolean OR for each restriction flag; `availability_known=false` yields `BLOCKED`.

### 3.6 Restrictive product join

Fact-set join is component-wise using the exact rules above. It is associative, commutative, and idempotent. No less-restrictive incoming fact can remove an existing restriction. Any unknown required knowledge marker or absorbing `UNKNOWN` yields `BLOCKED`.

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
- exact `field_category_registry_id`
- `publication_authorization_id`
- `effective_from`
- `recorded_at`
- `known_at`
- `supersedes_policy_record_id` when applicable
- `record_status`

The policy body must derive all five top-level handling decisions and all durable field/category dispositions. Caller-supplied policy bodies, registry identities, or expected identities are comparison inputs only.

### 4.3 `FieldCategoryRegistryRecord`

Immutable authoritative registry version containing:

- `field_category_registry_id`
- registry schema/version
- exact governed field-to-category mapping rules
- `publication_authorization_id`
- `effective_from`
- `recorded_at`
- `known_at`
- `supersedes_field_category_registry_id` when applicable
- `record_status`

Registry history follows the same immutable, versioned, superseding, strict-known rules as facts and policy. Current/latest registry state is never a historical fallback.

### 4.4 `AuthorizationRuleRecord`

Immutable authoritative authorization-rule version containing:

- `authorization_rule_id`
- rule schema/version
- exact evidence-strength/method/verifier admissibility matrix
- exact publication and restriction-release rules
- `publication_authorization_id` for non-genesis successors
- `effective_from`
- `recorded_at`
- `known_at`
- `supersedes_authorization_rule_id` when applicable
- `record_status`

Authorization-rule history follows the same immutable, versioned, strict-known rules. The only exception is the singular contract-pinned `AUTHORIZATION_RULE_V1` bootstrap described in §2.5. Current/latest rule state is never a historical fallback.

### 4.5 `SourceHandlingDecision`

Derived, non-authoritative output containing:

- resolved fact identity
- resolved policy identity
- resolved `field_category_registry_id`
- resolved `authorization_rule_id`
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

A fact, policy, field-category registry, authorization rule, publication authorization, or required provenance record is eligible only when all are true:

- it is effective for the target context;
- `recorded_at <= cutoff`;
- `known_at <= cutoff`;
- every required referenced provenance record was also recorded and known by the cutoff.

Missing or unknown `known_at` yields `BLOCKED`. Backdated effective time does not make later-known information historically eligible.

Every `PublicationAuthorization` is valid at replay only if its exact referenced `authorization_rule_id` is strict-known eligible at the cutoff and the authorization's payload/subject/kind binding re-verifies against the exact historical candidate record. Current code/configuration cannot substitute for a missing historical authorization rule.

### 5.1 Successor selection and branch prevention

Each authority family is append-only per governed scope. Publication is an atomic compare-and-append operation:

- genesis publication supplies `expected_current_head_id = null` and succeeds only when the governed scope has no canonical head;
- successor publication supplies exactly one `expected_current_head_id`, which must equal the repository's exact current canonical head for that scope at commit time;
- the candidate successor must name that same head in its supersession field;
- the repository atomically rejects the write if the expected head no longer matches.

The compare-and-append precondition is persistence concurrency control, not authority selection: the repository cannot choose a different head, relax evidence requirements, or mint authorization. A publication that loses the race must re-resolve the new head and obtain a new payload-bound authorization before retrying. This prevents new divergent canonical heads in V1.

For historical resolution, build the eligible supersession chain using only strict-known eligible records. Resolution succeeds only when there is exactly one eligible authoritative head and every earlier eligible record in that scope is its unambiguous ancestor.

Legacy/imported multiple heads, divergent branches, overlapping policies/registries/rules with no single dominating chain, cycles, missing predecessor linkage, or scope ambiguity yield `BLOCKED`; V1 does not silently reconcile pre-existing contradictory history.

Never resolve ambiguity by current/latest state, greatest timestamp, sequence number, insertion order, lexical ID, repository order, or provider preference.

Historical absence remains absence and may not be backfilled from present-day authority.

## 6. Policy derivation

The policy engine consumes only the exact strict-known fact record, exact strict-known policy record, exact strict-known field-category registry identified by that policy, exact strict-known authorization rule required by their authorizations, and non-authoritative operation context that may narrow the requested action but may not relax authority.

A permissive outcome requires explicit support in resolved facts and exact governed policy. Absence of a prohibition is not permission. Any unresolved required input yields `BLOCKED` before model-facing processing.

## 7. Governed durable data categories

Every durable field must be mapped by the exact strict-known `FieldCategoryRegistryRecord` bound to the resolved policy. V1 categories are:

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

`SAFE_CONTROL_ID` is allowed only for an identity whose construction is governed and proven not to encode, hash, derive from, or reveal prohibited source content. Names such as ID, hash, locator, coordinate, metadata, diagnostic, or audit never make a value safe.

## 8. Durable disposition matrix

For each governed category, `SourceHandlingDecision.durable_dispositions` explicitly provides dispositions for:

- `PERSIST`
- `READ_ACCESS`
- `RECONSTRUCT`
- `DELETE_OR_EXPIRE`

Closed disposition values are operation-constrained.

For `PERSIST`, `READ_ACCESS`, and `RECONSTRUCT`, the only valid values are:

- `ALLOW`
- `REDACT`
- `OMIT`
- `DENY`
- `BLOCKED`

with exact restrictive order:

`ALLOW < REDACT < OMIT < DENY < BLOCKED`

For `DELETE_OR_EXPIRE`, the only valid values are:

- `ALLOW` — no deletion/expiry obligation is established by this category;
- `EXPIRE` — a deterministic governed expiry obligation applies;
- `DELETE` — deletion is required and dominates expiry;
- `BLOCKED` — lifecycle authority is unresolved or contradictory.

with exact restrictive order:

`ALLOW < EXPIRE < DELETE < BLOCKED`

Therefore, when one applicable category yields `DELETE` and another yields `EXPIRE`, the lifecycle join is deterministically `DELETE`. Any disposition value that is invalid for the operation is treated as `BLOCKED`. Missing or ambiguous disposition is `BLOCKED`.

When a field maps to multiple categories, the operation-specific join above is applied across every applicable category. Mandatory deletion/expiry obligations cannot be erased by an `ALLOW` on another category or axis.

The complete disposition map covers source bytes, excerpts/source-derived text, content-derived IDs/hashes, locators/URLs, coordinates, metadata, diagnostics, provenance identifiers, audit fields, access-controlled representations, reconstruction metadata, and lifecycle state.

## 9. Persistence enforcement

Persistence is enforcement, never authority. Before any durable write, it must independently:

1. strict-known resolve facts, policy, the policy-bound field-category registry, and every authorization rule referenced by their publication authorizations;
2. verify publication authorizations and provenance eligibility for all authority families;
3. recompute and verify exact publication kind, subject/scope, and `authorized_payload_sha256` bindings;
4. rederive the complete `SourceHandlingDecision` including exact registry and authorization-rule identities;
5. remap every actual field using that exact historical registry version;
6. compute the operation-specific effective disposition for the exact representation;
7. reject caller/provider identity, classification, policy, registry, authorization-rule, decision, disposition, or lineage mismatch;
8. reject current/latest policy, registry, or authorization-rule substitution for a historical decision;
9. write only representations explicitly permitted by the complete map and structural exclusions below.

No prohibited data may be written first and cleaned up later.

## 10. Structural secret and credential exclusion

`SECRET_PRESENT` and `CREDENTIAL_PRESENT` are absolute structural exclusions for canonical secondary durable representations that could carry, encode, derive from, summarize, identify, or reveal the secret/credential material.

No policy, category mapping, `ALLOW` disposition, caller input, provider assertion, or audit exception may override this prohibition.

When the governed source material contains a secret or credential, the following canonical durable surfaces must not contain that material or a content-derived representation of it:

- excerpts or source-derived free text;
- operational metadata;
- diagnostics;
- content-derived IDs or hashes;
- locators/URLs when derived from or embedding the protected material;
- coordinates when derived from or embedding the protected material;
- provenance identifiers when content-derived;
- audit fields;
- reconstruction metadata;
- access-controlled representations;
- any `SAFE_CONTROL_ID` candidate whose construction encodes, hashes, derives from, or reveals protected material.

Authentication credentials are additionally transport-only and are structurally excluded from canonical provider/model request artifacts, source-handling records, identities, diagnostics, audit records, and persistence payloads.

Heuristic/regex detectors are defense in depth only: a hit may add restriction; absence of a hit never grants permission and cannot establish governed absence.

## 11. Rejected-build audit

A blocked build may create a durable audit record only if every audit field is mapped by the exact historical registry, permitted by the complete disposition map, and passes the structural secret/credential exclusion.

Audit persistence must never contain source bytes, source-derived free text, excerpts, secrets, credentials, protected content-derived hashes/IDs, restricted locators/coordinates, or source-derived diagnostics. There is no policy-permission exception for secret- or credential-bearing material.

If a safe audit representation cannot be produced without those surfaces, the failure remains transient operational state only.

## 12. Typed content state

Content-bearing durable fields use explicit state:

- `PRESENT`
- `OMITTED_BY_POLICY`
- `REDACTED_BY_POLICY`
- `DELETED_BY_POLICY`
- `NEVER_RETAINED`
- `UNAVAILABLE_HISTORICALLY`

State records outcome only; it never creates permission. `PRESENT` requires `ALLOW` for the exact field/category/operation representation and must still satisfy structural secret/credential exclusion.

## 13. Legacy and migration

Pre-authority rows gain no retroactive authority. Migration must preserve historical absence and must not fabricate `known_at`, `recorded_at`, verifier, provenance, policy, field-category registry, authorization rule, publication authorization, or classification.

Migration must not use current facts, policy, current field-category registry, or current authorization rule as historical substitutes. Existing historical bytes are not proof that processing, retention, access, or reconstruction was governed. Missing historical authority remains unavailable/blocked. Migration is deterministic, repeatable, and idempotent where applicable.

The only bootstrap exception is the singular contract-pinned `AUTHORIZATION_RULE_V1` genesis record described in §2.5; this does not confer authority on pre-existing source-handling rows.

## 14. Module/API boundaries

Separate interfaces must exist for evidence submission, authoritative fact publication, authoritative policy publication, authoritative field-category-registry publication, authoritative authorization-rule publication, strict-known fact resolution, strict-known policy resolution, strict-known registry resolution, strict-known authorization-rule resolution, deterministic decision derivation, and persistence enforcement.

Evidence-submission APIs accept non-authoritative evidence only. Fact/policy/registry/rule publication APIs require validated payload-bound `PublicationAuthorization`, except only the contract-pinned genesis materialization of `AUTHORIZATION_RULE_V1`. Repositories cannot mint an authorization. Repositories persist/query immutable records and atomically enforce the compare-and-append expected-head precondition only; they do not select authority. Providers acquire evidence only. Prompt/model-facing consumers receive only derived decisions after successful authority resolution.

## 15. Failure semantics

V1 terminal outcome is either:

- `READY`: all required authority resolved and requested operation explicitly permitted; or
- `BLOCKED`: any required authority/fact/policy/registry/authorization-rule/provenance/category/disposition is missing, unknown, unavailable, conflicting, partial, ambiguous, or prohibitive.

There is no permission-bearing `DEGRADED` state.

## 16. Required tests before implementation

Tests are written before runtime implementation and must cover all root semantics.

### Authority and laundering

- caller/provider assertion passed through an authority method cannot create permissive genesis;
- provider/caller evidence cannot release a restriction;
- source/source-type derivation cannot turn assertion-only evidence into permission;
- unauthorized policy publication rejects;
- unauthorized field-category-registry publication rejects;
- unauthorized authorization-rule successor publication rejects;
- repository/API direct write cannot create canonical fact/policy/registry/rule without required publication authorization;
- admissible restrictive evidence can add restriction;
- permissive publication requires admissible strength, method, verifier, authorization, and immutable provenance;
- a valid authorization for one fact payload rejects when reused for a different fact payload;
- a valid authorization for one policy body rejects when reused for a different body or scope;
- a valid authorization for one registry mapping rejects when reused for a different mapping;
- publication-kind mismatch rejects;
- tampered `authorized_payload_sha256` rejects at publication and persistence.

### Authorization-rule history and bootstrap

- the `AUTHORIZATION_RULE_V1` canonical fixture recomputes to its reviewed golden digest;
- migration refuses bootstrap materialization when fixture/digest mismatch;
- no second genesis authorization rule can be created;
- successor authorization rule cannot authorize itself;
- successor rule publication is evaluated under the exact strict-known predecessor rule;
- later/current authorization rule cannot validate an earlier historical publication;
- missing/unknown historical authorization rule blocks replay and persistence;
- caller-selected rule ID cannot substitute for canonical strict-known resolution.

### Fact product and joins

- secret and credential presence coexist;
- model-processing and persistence restrictions coexist;
- withdrawn/deleted/historically-unavailable coexist;
- persistence semantics are owned only by persistence restriction;
- every pair of incomparable set-valued restrictions preserves both members;
- product join is associative, commutative, idempotent;
- every unknown knowledge marker blocks.

### Strict-known replay, registry binding, and publication concurrency

- unknown/missing `known_at` blocks;
- backdated but later-known evidence is invisible before `known_at`;
- later-recorded or later-known fact/policy/authorization/registry/rule is invisible before cutoff;
- current state cannot satisfy historical absence;
- legacy/imported overlapping authority heads block;
- timestamp/sequence/lexical/insertion-order tie-breakers are not used;
- later registry version cannot change historical field classification;
- tampered registry identity in a supplied decision rejects;
- persistence rejects current-registry substitution when replay requires an older registry;
- two concurrent successor publications with the same expected head cannot both commit;
- losing compare-and-append publication must re-resolve and obtain a new payload-bound authorization;
- expected-head mismatch rejects without creating a divergent head.

### Persistence and complete dispositions

- every durable category has explicit operation dispositions;
- source bytes, excerpts, hashes, IDs, locators, URLs, coordinates, metadata, diagnostics, provenance IDs, audit fields, reconstruction metadata, and access-controlled representations are enforced individually;
- multi-category field receives the most restrictive operation-specific result;
- unknown/ambiguous field category is non-persistable and blocks when required;
- tampered caller decision/disposition/policy/registry/rule identity rejects before write;
- access restrictions are enforced independently of persistence;
- reconstruction restrictions are enforced independently of retention;
- deletion/expiry obligations cannot be weakened by another allow;
- `DELETE` joined with `EXPIRE` yields `DELETE` regardless of category order;
- lifecycle join is associative, commutative, and idempotent;
- a disposition value invalid for its operation yields `BLOCKED`.

### Secrets, credentials, audit, legacy, and counterfactual proof

- source secrets cannot enter canonical excerpts, metadata, diagnostics, identities, hashes, locators, coordinates, provenance IDs, audit fields, reconstruction metadata, or access-controlled representations;
- credentials cannot enter those same channels;
- no `ALLOW` disposition can override structural secret/credential exclusion;
- blocked-audit representation leaks neither source secrets nor credentials;
- detector hit can only add restriction; detector miss cannot grant permission;
- pre-authority record remains historically unresolved;
- migration cannot manufacture historical source-handling authority or registry/rule history beyond the singular contract-pinned rule bootstrap.

For every blocking regression class above, disable/remove the root rule and demonstrate that its regression test fails, per the Non-Vacuous Regression Tests and Harness Fidelity requirements in `docs/HUNTER_IMPLEMENTATION_CONTRACT.md`.

## 17. Implementation order

1. obtain final independent acceptance review of this contract;
2. freeze the accepted contract;
3. write the deterministic tests above first, including the canonical `AUTHORIZATION_RULE_V1` fixture and reviewed golden digest;
4. materialize and golden-verify the contract-pinned `AUTHORIZATION_RULE_V1` bootstrap;
5. implement record models/repositories and atomic compare-and-append mechanically;
6. implement payload-bound publication authorization and strict-known resolution for facts, policy, registry, rules, authorizations, and provenance;
7. implement policy derivation, operation-specific disposition joins, complete disposition map, and structural secret/credential exclusion;
8. implement persistence enforcement and migration behavior;
9. integrate with the existing pre-model pipeline;
10. run repository-wide verification and counterfactual tests;
11. independent Codex implementation review against the frozen contract/tests;
12. only then resume or replace PR #260 as appropriate.

No implementation step may weaken ADR 0033 or invent authority to preserve existing code.

## 18. Non-goals

No generic Prompt/Context authority, Model Adapter, ResponseValidator, Governance Review redesign, analytical authority, trading/signalling/portfolio behavior, SaaS/public-product architecture, span-level V1 handling authority, or runtime implementation is authorized by this design contribution.

## 19. Completion condition

This contract is ready for tests-before-implementation only when final independent review finds no P1/P2 design defect against the accepted architecture and the closed semantics above, Issue #264 acceptance criteria are satisfied, PR #260 remains untouched, and the repository owner approves the design contribution.