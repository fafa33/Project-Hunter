# ADPR-0010 — Evidence Intelligence ResponseValidator Boundary

## Metadata

- ADPR ID: `ADPR-0010`
- Status: `READY_FOR_REVIEW`
- Version: 1.1
- Author: OpenAI GPT-5.6 Sol — architecture preparation agent
- Reviewers: independent architecture audit required
- Created: 2026-08-24
- Revised: 2026-08-24
- Approved: not yet approved
- Related Issue: #316
- Related follow-up: #315 (separate, non-blocking unless a concrete dependency is later proven)
- Planned ADR: Evidence Intelligence ResponseValidator Boundary

## Executive Summary

ADR 0034 Phase B gives Hunter a governed path through durable model-attempt lineage, a single-use handoff, one provider transport, append-only attempt outcomes, and governed provider-response capture. That architecture deliberately stops before semantic response validation. Hunter therefore has evidence of what a provider returned, but no accepted authority that may decide whether that response conforms to the exact requested output contract, validation rules, lineage constraints, or response-validation policy.

This preparation recommends a **separate Hunter Evidence Intelligence `ResponseValidator` service boundary**, downstream of the Model Adapter and upstream of any extraction or knowledge-proposal lifecycle. It additionally introduces a distinct **`ResponseValidationProfileAuthority`** as the canonical owner of validation-profile publication, applicability, version history, and supersession. `ResponseValidator` owns response-validity decisions but does not own profile policy history, transport execution, Source Handling, extraction, canonical truth, or promotion.

Every validation or re-validation performs an independent strict-known Source Handling resolution at its own `validation_cutoff`. Attempt-time Source Handling authorization is never reusable for validation. Validation may use durable response bytes or a single-use transient live view when processing is allowed but retention is prohibited. Persistence cannot forge `VALID`: every accepted validation record requires a validator-issued, non-caller-mintable persistence attestation bound to the exact record and validation subject.

The recommendation is `READY_FOR_ADR` only after independent architecture audit confirms the revised authority, Source Handling, idempotency, persistence, and failure-state contracts below.

## Problem Statement

### Current condition

Baseline `main` is `b43be1007566faf5b0274c7bf3c8bb05a743ab10`, the merge of PR #314. Current runtime explicitly ends the Model Adapter boundary after governed provider-response capture. There is no canonical `ResponseValidator`, semantic response validation, extraction promotion, or knowledge promotion in that path.

The legacy `SecureAIProviderRunner` directly turns provider output into `ExtractionProposal` after limited screening. That path predates ADR 0031/0033/0034 lineage and cannot be re-labelled as the new validator.

### Decision required

This lifecycle must decide:

1. who owns response-validity decisions;
2. who owns canonical validation-profile and rule history;
3. which exact response, contract, profile, Source Handling, and historical coordinates are authoritative;
4. how non-retainable but processable response content reaches validation without being persisted;
5. how a validation record is identified, deduplicated, corrected, replayed, and made non-forgeable;
6. which closed outcome states exist;
7. where validation stops before extraction or canonical promotion.

### In scope

- response-validity authority;
- validation-profile/rule authority and lifecycle;
- validation-time Source Handling re-resolution;
- durable and transient validation inputs;
- immutable validation result semantics;
- idempotency, concurrency, correction, replay, persistence anti-bypass;
- closed failure/missingness states;
- authority/ownership handoffs;
- downstream validated-response boundary;
- adversarial conformance obligations.

### Out of scope

- runtime implementation;
- provider routing, fallback, ranking, second provider, or dynamic model selection;
- canonical source/claim/valuation truth;
- extraction or knowledge promotion;
- valuation, mispricing, asymmetry, ranking, opportunity, timing, portfolio, or recommendation authority;
- Dashboard, scheduler, or governance redesign;
- Issue #315 implementation;
- synthetic validation backfill for legacy provider artifacts.

## Governance Evidence and Review Coordinates

Canonical sources checked:

- `docs/HUNTER_IMPLEMENTATION_CONTRACT.md`
- `docs/ARCHITECTURE_AUDIT_PROTOCOL.md`
- `docs/DEVELOPMENT_GOVERNANCE.md`
- `docs/DEFECT_REGISTRY.json` (including authority/review defects relevant to exact-head and governance evidence)
- `docs/ADR/0034-evidence-intelligence-model-adapter-provider-attempt-boundary.md`
- `docs/ADR/0033-source-handling-classification-authority.md`
- `docs/ADR/0031-ai-context-prompt-intelligence-foundation.md`
- `docs/ADR/0032-project-agnostic-prompt-intelligence-core.md`
- ADR 0020, ADR 0016, ADR 0009
- current `src/hunter/evidence_intelligence/model_adapter.py`
- current legacy `src/hunter/evidence_intelligence/provider.py`
- `docs/architecture-index.md`
- Issue #315 and Issue #316

Preparation/review coordinates:

- base: `main` at `b43be1007566faf5b0274c7bf3c8bb05a743ab10`
- branch: `architecture/316-response-validator-preparation`
- review-start HEAD: `aa8c6fbd6db8a49cdf7ab36afe8dae2766ab7bc0`
- PR: #317
- exact-head hosted checks on review-start HEAD passed before review findings were raised, including CI/Quality Gates, Dependency Review, CodeQL, and Hunter Governance Review.
- This v1.1 corrective commit supersedes the review-start HEAD as final-head evidence; therefore all exact-head checks must run again on the corrective head before the PR may leave Draft for independent architecture audit.

## Existing Architecture

Upstream lineage remains:

`EvidencePromptArtifact`
→ `EvidencePreModelBuildRecord`
→ `ModelExecutionProfile`
→ `ModelAttemptRecord`
→ `ModelHandoffRecord`
→ `ModelAttemptOutcomeRecord`
→ `ProviderResponseArtifact`

The Model Adapter owns execution and response-capture lineage. It does not own semantic response validity or validation policy.

## Authority and Ownership Diagram

```text
ADR 0033 Source Handling Authority
  owns: source-handling facts + policy + strict-known resolution
  permits -> processing/durability decisions at validation_cutoff
                    |
                    v
ResponseValidationProfileAuthority
  owns: canonical profile publication, applicability, version history,
        activation/supersession, rule-set identity
  permits -> exact profile resolution at validation_cutoff
                    |
                    v
Model Adapter ------------------------------+
  owns: attempt/outcome/response-capture    |
        lineage and exact live response     |
  may carry bytes but may NOT select        |
  validation profile or Source Handling     |
                    |                       |
                    +---- matching capture -+
                                            v
                                  ResponseValidator
                                  owns: response-validity decision,
                                        validation subject,
                                        validation authorization,
                                        validation attestation
                                            |
                                            v
                                  Validation Persistence
                                  owns: mechanical append-only storage,
                                        existence/lineage checks,
                                        attestation verification,
                                        uniqueness/CAS enforcement
                                            |
                                            v
                                  Validated-response handoff
                                            |
                                            v
                                  Future Extraction/Knowledge Proposal
                                  consumes validation identity only
                                            |
                                            v
                                  Separate Canonical Promotion Authority
```

Forbidden authority edges:

- provider/transport or Model Adapter → MUST NOT choose validation rules or mint validity;
- caller → MUST NOT mint canonical profile/rules, validation authorization, or `VALID`;
- persistence → MUST NOT select profile/rules, re-run validation, or infer validity;
- `ResponseValidator` → MUST NOT override Source Handling or promote to canonical truth;
- downstream extraction/knowledge code → MUST NOT reinterpret transport success as validity or mint validation records;
- canonical promotion authority → remains separate and downstream; validation alone never grants promotion.

## Candidate Options

### Option 1 — Separate Evidence Intelligence ResponseValidator — RECOMMENDED

A Hunter-owned validator consumes exact governed response-capture lineage, an exact canonical validation profile resolved by `ResponseValidationProfileAuthority`, and an independent validation-time Source Handling decision. It emits append-only validation evidence and stops before extraction/promotion.

Advantages: strongest authority separation, deterministic validation, replayable profile history, support for non-retainable live content, explicit anti-forgery boundary.

Costs: introduces one validator service, one canonical profile authority, and one transient authorization/attestation contract.

### Option 2 — Embed validation in Model Adapter — REJECTED

Rejected because ADR 0034 deliberately ends Model Adapter authority before semantic validation. It would collapse transport evidence and semantic validity.

### Option 3 — Let extraction/knowledge layer validate — REJECTED

Rejected because a downstream consumer must not become authority over whether its own input is valid; validation and promotion would collapse.

### Option 4 — Generic shared validator core — DEFERRED

Potentially viable only after ADR 0032's independent multi-consumer evidence gate is satisfied. Hunter-specific authority stays local for now.

### Option 5 — Provider-specific validation — REJECTED

Rejected because provider transports cannot acquire canonical Hunter response-validity authority.

## Recommended Contract

### 1. Response-validity owner

`ResponseValidator` is the sole owner of response-validity decisions. `VALID` means only: **conforms to this exact canonical validation contract under these exact historical coordinates**. It never means true, correct, authoritative, canonical, or promoted.

### 2. Canonical validation-profile authority

A Hunter Evidence Intelligence `ResponseValidationProfileAuthority` is the sole owner of canonical `ResponseValidationProfile` publication and historical rule applicability.

Its responsibilities are limited to:

- append-only publication of immutable profile versions;
- exact rule-set identity and profile identity;
- activation/applicability intervals or equivalent strict-known coordinates;
- append-only supersession/correction of profile metadata;
- deterministic resolution of the exact profile applicable at a validation cutoff.

It does **not** validate provider responses and does not own Source Handling.

A profile identity binds at least:

- profile schema/version;
- validator implementation-contract identity/version;
- requested-output-contract identity/version;
- schema/shape rule identity;
- canonicalization/parser contract identity/version;
- evidence-reference structural-validation rule identity/version;
- bounded resource/size policy identity;
- prohibited-capability/security validation rule identity where applicable;
- required validation dimensions;
- closed result/reason vocabulary version.

Caller-provided profile IDs, schemas, or rules are requests/evidence only. The validator resolves the canonical applicable profile through `ResponseValidationProfileAuthority`; neither caller nor persistence may choose the semantic policy.

Historical replay binds the profile resolution identity and profile history knowable at the recorded validation cutoff. Current/latest profile state cannot substitute.

### 3. Validation-time Source Handling

Every validation and re-validation has its own `validation_cutoff`.

Before any validation content is accessed or processed, `ResponseValidator` independently resolves ADR 0033 Source Handling using the strict-known facts and policy applicable at that **validation cutoff**. Attempt-time or capture-time `ALLOW` is never reusable authorization.

The validation record binds at least:

- validation cutoff;
- Source Handling fact identity/version;
- Source Handling policy identity/version;
- Source Handling resolution identity;
- processing decision;
- durable-category decisions for any validation-derived field.

If processing is prohibited or the historical authority cannot be resolved under strict-known rules, validation fails closed with the appropriate closed state. A later restrictive correction therefore cannot be bypassed by reusing an earlier attempt decision.

### 4. Validation authorization and transient handoff

Before validation, `ResponseValidator` resolves the canonical profile and validation-time Source Handling, then issues a single-use opaque `ResponseValidationAuthorization`.

That authorization binds:

- validation subject inputs;
- exact canonical profile resolution;
- requested-output-contract identity;
- validation cutoff;
- Source Handling resolution identity;
- exact response-capture/attempt lineage expected;
- input mode (`DURABLE` or `TRANSIENT_NOT_RETAINED`).

The authorization is service-owned and non-caller-mintable. The Model Adapter may **carry** this authorization when delivering matching transient response bytes, but it cannot choose, modify, or attest profile/cutoff/policy values.

For transient validation:

1. validator issues the authorization;
2. Model Adapter matches it to the exact captured response and supplies credential-screened in-memory bytes only;
3. validator atomically consumes the authorization once;
4. transient bytes are never persisted merely because validation occurred.

A mismatched capture, profile, cutoff, contract, or Source Handling resolution fails closed.

### 5. Canonical validation subject and idempotency

The canonical `validation_subject_id` is deterministically derived from:

- exact response-capture identity;
- canonical `ResponseValidationProfile` identity/version;
- requested-output-contract identity/version;
- validation cutoff;
- Source Handling resolution identity.

A base validation event for the same subject is unique. Concurrent workers validating the same subject may produce at most one accepted base `ResponseValidationRecord`.

Idempotency rules:

- identical repeated submission for an already accepted subject returns the existing record identity and creates no duplicate history;
- conflicting repeated results for the same subject are rejected as a validation consistency failure, never stored as parallel branches;
- persistence enforces subject uniqueness atomically.

### 6. Immutable `ResponseValidationRecord`

The record is append-only and binds at least:

- record identity/schema version;
- validation subject identity;
- response-capture identity;
- `ModelAttemptOutcomeRecord`, attempt, handoff, execution-profile, prompt/build lineage;
- canonical profile resolution + profile/rule identity/version;
- requested-output-contract identity/version;
- validation-time Source Handling resolution coordinates;
- validation cutoff and creation time;
- closed overall state;
- per-dimension closed states/reason codes;
- input availability mode;
- durable diagnostics only where independently authorized;
- supersedes identity and correction generation when applicable.

### 7. Closed validation outcome vocabulary

Top-level states are closed and versioned by the canonical validation profile contract:

- `VALID`
- `INVALID_SYNTAX`
- `INVALID_SCHEMA`
- `INVALID_OUTPUT_CONTRACT`
- `INVALID_LINEAGE`
- `INVALID_EVIDENCE_REFERENCE_STRUCTURE`
- `PARTIAL_RESPONSE`
- `INPUT_UNAVAILABLE`
- `RULE_UNAVAILABLE`
- `VALIDATOR_CAPABILITY_UNKNOWN`
- `EVIDENCE_AMBIGUOUS`
- `SOURCE_HANDLING_BLOCKED`
- `SECURITY_BLOCKED`
- `VALIDATOR_ERROR`

`VALIDATOR_ERROR` is reserved for operational validator failure. `VALIDATOR_CAPABILITY_UNKNOWN` represents a governed validator capability that cannot be established. `EVIDENCE_AMBIGUOUS` represents available evidence insufficient to choose a more specific deterministic validity state.

Reason codes are also from a closed, versioned registry bound by the profile. Free-form diagnostics may exist only as non-authority metadata when Source Handling permits durability; downstream consumers cannot branch on free-form text.

Unknown/unrecognized states or reason codes fail closed and cannot be treated as `VALID`.

### 8. Non-forgeable persistence

After evaluation, `ResponseValidator` produces an opaque, single-use `ResponseValidationAttestation` bound to:

- exact `ResponseValidationRecord` identity and canonical payload digest;
- validation subject;
- canonical profile resolution;
- validation-time Source Handling resolution;
- final closed validation state;
- correction predecessor/generation when applicable.

Persistence accepts a canonical validation record only when it can verify and atomically consume the corresponding validator-issued attestation. The attestation is not caller-constructible and cannot be minted by repository code.

Persistence also mechanically verifies lineage existence/matching, profile resolution identity, Source Handling coordinates, durable-field authorization, subject uniqueness, correction predecessor claims, and record structure.

A caller that constructs a structurally correct `VALID` record with genuine canonical identities but lacks the validator-issued attestation is rejected. This is a mandatory hostile conformance case.

### 9. Replay and re-validation

Historical replay selects recorded append-only validation history under strict-known coordinates. It never calls a provider and never substitutes current profile or Source Handling state for historical state.

If a validation result was produced live from transient content that was not retainable, replay returns the recorded result plus `TRANSIENT_NOT_RETAINED`; it does not regenerate or fetch bytes.

Re-validation is a new validation subject/event with a new validation cutoff, fresh canonical profile resolution, fresh Source Handling resolution, fresh authorization, and fresh attestation. It never rewrites an earlier result.

### 10. Correction and concurrent supersession

Corrections are append-only and non-branching within a validation subject lineage.

A correction:

- names the exact current predecessor record;
- increments a monotonic correction generation;
- has a new correction cutoff/creation time;
- receives a fresh validator attestation.

Persistence performs an atomic compare-and-set against the currently accepted predecessor. If two corrections race, at most one may claim that predecessor. The loser must re-resolve the current head and cannot create a sibling branch.

Strict-known historical reads choose the highest correction generation whose correction coordinates were knowable at the requested cutoff. Generation is primary ordering; record identity is only a deterministic integrity/tie check, not semantic precedence.

### 11. Validation dimensions

The validator may decide only dimensions encoded by the canonical profile and supported by available evidence, including:

- syntax/parse validity;
- top-level shape and schema conformance;
- requested-output-contract conformance;
- required/forbidden fields;
- bounded type/range/enum constraints;
- lineage consistency;
- evidence-reference structural integrity, not claim truth;
- partial/missing response classification;
- forbidden capability/action-request structure when validation policy assigns that check here.

It cannot decide source truth, claim truth, valuation truth, ranking, opportunity, recommendation, or canonical knowledge promotion.

### 12. Downstream boundary

A later extraction/knowledge-proposal service may consume only validation states explicitly accepted by its own separately governed contract, normally `VALID`. It must carry exact validation identity and lineage. `ResponseValidator` creates no extraction proposal and performs no canonical promotion.

## Falsification Results

| Scenario | Required result |
|---|---|
| Caller supplies permissive validation rules | rejected as authority; canonical profile authority resolves policy |
| Current profile differs from historical profile | historical resolution remains bound to historical cutoff |
| Source Handling became restrictive after attempt | validation-time re-resolution blocks/reclassifies validation; attempt ALLOW is ignored |
| Model Adapter tries to choose profile/cutoff | rejected; it may only carry validator-issued authorization |
| Two workers validate same subject | at most one base record accepted; repeat is idempotent or conflict-rejected |
| Two corrections race | compare-and-set permits one successor; no branching |
| Direct repo write submits canonical-looking `VALID` | rejected without validator attestation |
| Provider returned valid JSON but wrong contract | deterministic invalid contract state |
| Validator capability cannot be established | `VALIDATOR_CAPABILITY_UNKNOWN`, not `VALIDATOR_ERROR` or success |
| Evidence is ambiguous | `EVIDENCE_AMBIGUOUS`, not free-form unknown |
| Exact bytes were transient only | recorded validation may replay, bytes do not regenerate |
| Validation fails | no provider retry authority is created |
| Legacy artifact is presented as validated | rejected; no synthetic relabelling/backfill |
| #315 is assumed solved | rejected; remains separate unless completed explicitly |

## Mandatory Conformance Cases

A future ADR and implementation must make these deterministic and adversarially testable:

1. `SUCCEEDED_TRANSPORT` alone cannot produce `VALID`.
2. Caller-supplied schemas/rules cannot become canonical profile authority.
3. `ResponseValidationProfileAuthority` alone publishes/resolves canonical profile history.
4. Current profile/rules cannot substitute for historical profile resolution.
5. Every validation/re-validation independently re-resolves Source Handling at its own validation cutoff.
6. Attempt-time/capture-time Source Handling `ALLOW` cannot authorize later validation.
7. Validation records bind exact Source Handling fact/policy/resolution identities.
8. A validator-issued `ResponseValidationAuthorization` is single-use and non-caller-mintable.
9. Model Adapter can carry matching transient bytes but cannot select/alter profile, cutoff, or Source Handling policy.
10. Transient validation persists zero prohibited response bytes/hash/size/content-derived IDs.
11. Mismatched transient capture/authorization fails closed.
12. Canonical validation subject identity is deterministic from response capture, profile, output contract, validation cutoff, and Source Handling resolution.
13. Concurrent base validation produces at most one accepted record for one subject.
14. Identical duplicate validation is idempotent; conflicting duplicate state cannot branch history.
15. `VALIDATOR_ERROR`, `VALIDATOR_CAPABILITY_UNKNOWN`, and `EVIDENCE_AMBIGUOUS` remain distinct.
16. Unknown state/reason code cannot be interpreted as `VALID`.
17. A structurally correct direct repository write using genuine canonical identities but no validator attestation is rejected.
18. Attestation reuse, record substitution, or subject substitution is rejected.
19. Correction is append-only, names exact predecessor, and increments generation.
20. Concurrent corrections cannot create sibling successors from one predecessor.
21. Strict-known replay selects latest applicable correction generation at cutoff, never oldest/current unconditionally.
22. Historical replay never invokes provider/network or regenerates prohibited bytes.
23. Re-validation creates a new cutoff, profile resolution, Source Handling resolution, authorization, subject/event, and attestation.
24. `VALID` grants no canonical truth or promotion authority and cannot itself create an extraction proposal.
25. Malformed/partial/contract-invalid responses cannot cross a downstream validated-response handoff that requires `VALID`.
26. Provider-specific transport cannot mint validation records, profiles, authorization, or attestation.
27. Legacy `ExtractionProposal` / `AIProviderArtifact` cannot be retroactively accepted as `ResponseValidationRecord`.
28. Hunter Governance Review and Merge Readiness acquire no provider/credential dependency from this architecture.
29. Issue #315 remains separately unresolved unless explicitly completed.
30. Deliberately weakening each reusable authority, replay, idempotency, Source Handling, durability, or attestation guard causes its named regression to fail.

## Persistence, Security, and Privacy

Validation-derived excerpts, diagnostics, normalized values, hashes, sizes, and content-derived identifiers are independently governed durability categories. Processing permission never grants persistence permission.

Credential-bearing response content rejected by Phase B cannot be laundered into durable validation evidence. The validator must also fail closed if credential safety of a durable derived field cannot be established.

Authorization and attestation are operational capabilities, not content-retention workarounds. They bind identities and decisions but may not encode prohibited response content.

## Legacy and Migration

The existing legacy provider/extraction path remains historical. No backfill may fabricate validation records, profile resolutions, Source Handling decisions, authorization, or attestation for old artifacts. Downstream consumers must distinguish legacy/unvalidated data explicitly.

## Open Questions

Non-blocking for architecture selection, but required before activation where applicable:

- exact parser/schema library;
- exact database schema and indexes;
- exact durable diagnostic field-category mapping;
- whether forbidden-capability structural checks remain duplicated as independent capture and semantic-validation gates;
- future generic-core admission if ADR 0032 later obtains independent multi-consumer evidence.

The **top-level validation state vocabulary, canonical profile authority, validation-time Source Handling requirement, subject/idempotency rule, and anti-forgery attestation are not open questions** in this preparation.

## Constitution and Governance Review

The recommendation is evidence-first and fail-closed. Unknown validity remains unknown; provider output is never promoted merely because it arrived; prohibited evidence is never reconstructed. No trading, portfolio, recommendation, or autonomous-action authority is introduced.

This contribution remains architecture preparation only. No runtime code or accepted ADR is modified. PR #317 remains Draft while corrective exact-head checks run. Independent architecture audit is mandatory before ADR drafting. Merge remains owner-only.

## Architecture Readiness

- Outcome: `READY`, subject to independent audit of v1.1.
- Canonical ownership is explicit: profile history belongs to `ResponseValidationProfileAuthority`; validity decisions belong to `ResponseValidator`; Source Handling remains ADR 0033-owned; persistence is mechanical; promotion remains downstream and separate.
- Validation-time authorization cannot reuse attempt-time Source Handling.
- Idempotency and non-branching correction are defined.
- Persistence cannot mint `VALID` without validator attestation.
- Closed top-level failure states distinguish operational error, unknown capability, and ambiguous evidence.

## ADR Readiness

- Outcome: `READY_FOR_ADR` only if independent architecture audit returns no blocking finding.
- Proposed ADR title: Evidence Intelligence ResponseValidator Boundary.
- ADR must preserve every authority, cutoff, subject, attestation, replay, and closed-state invariant in this v1.1 preparation.
- Parser/library choice and physical database schema remain implementation details.

## Decision History

| Date | State | Change | Author or reviewer |
|---|---|---|---|
| 2026-08-24 | READY_FOR_REVIEW | Initial preparation completed from post-PR #314 `main` | OpenAI GPT-5.6 Sol |
| 2026-08-24 | READY_FOR_REVIEW | v1.1 resolves Codex/CodeRabbit findings: canonical profile authority, validation-time Source Handling, ownership diagram, transient authorization, subject/idempotency/correction, closed outcome vocabulary, non-forgeable validator attestation, and governance evidence | OpenAI GPT-5.6 Sol |

## Traceability

- Issue: #316
- Follow-up: #315 (separate)
- ADPR: `ADPR-0010`
- PR: #317
- Base: `b43be1007566faf5b0274c7bf3c8bb05a743ab10`
- Review-start HEAD: `aa8c6fbd6db8a49cdf7ab36afe8dae2766ab7bc0`
- ADR: not yet created
- Implementation: not authorized by this record
- Merge commit: not yet created

## Immutability and Supersession

After `APPROVED`, this record becomes historical evidence. Substantive later changes require a new ADPR that explicitly supersedes it. Non-substantive link completion and typographical corrections must remain auditable.