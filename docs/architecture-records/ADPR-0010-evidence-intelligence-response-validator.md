# ADPR-0010 — Evidence Intelligence ResponseValidator Boundary

## Metadata

- ADPR ID: `ADPR-0010`
- Status: `READY_FOR_REVIEW`
- Version: 1.3
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

Every validation or re-validation performs an independent strict-known Source Handling resolution at its own `validation_cutoff`. Attempt-time Source Handling authorization is never reusable for validation. Validation may use durable response bytes or a single-use transient live view when processing is allowed but retention is prohibited. Base validation concurrency is deduplicated **before** a per-event cutoff is assigned: the validator atomically allocates one canonical validation event from a stable cutoff-free base key, and all ordinary retries join that event. Persistence cannot forge `VALID`: successful semantic records require a validator-issued, non-caller-mintable success attestation. Fail-closed states caused by unavailable profile or Source Handling authority use a separate validator-issued refusal attestation that proves the failed/blocked resolution attempt without pretending the missing authority resolved successfully.

The recommendation is `READY_FOR_ADR` only after independent architecture audit confirms the revised authority, Source Handling, event-allocation, persistence, refusal, failure-state, evidence, and quality contracts below.

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
5. how a validation event is allocated before execution, identified, deduplicated, corrected, replayed, and made non-forgeable;
6. how failure records remain attestable when canonical profile or Source Handling resolution itself is unavailable;
7. which closed outcome states exist and how simultaneous failures are reduced deterministically;
8. where validation stops before extraction or canonical promotion.

### In scope

- response-validity authority;
- validation-profile/rule authority and lifecycle;
- validation-time Source Handling re-resolution;
- durable and transient validation inputs;
- atomic validation-event allocation and idempotency;
- immutable validation result semantics;
- concurrency, correction, replay, persistence anti-bypass;
- success and refusal attestation contracts;
- closed failure/missingness states and deterministic precedence;
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
- `docs/DEFECT_REGISTRY.json`
- `docs/ARCHITECTURE_DECISION_PREPARATION_GUIDE.md`
- `docs/ARCHITECTURE_DECISION_QUALITY_STANDARD.md`
- `docs/ADR/0034-evidence-intelligence-model-adapter-provider-attempt-boundary.md`
- `docs/ADR/0033-source-handling-classification-authority.md`
- `docs/ADR/0031-ai-context-prompt-intelligence-foundation.md`
- `docs/ADR/0032-project-agnostic-prompt-intelligence-core.md`
- `docs/ADR/0020-historical-replay-and-strict-known-semantics.md` if present under the canonical ADR 0020 path recorded by the repository index; the accepted ADR 0020 identity is authoritative even where filename spelling is repository-defined
- accepted ADR 0016 and ADR 0009 at their canonical repository paths
- `src/hunter/evidence_intelligence/model_adapter.py`
- `src/hunter/evidence_intelligence/provider.py`
- `docs/architecture-index.md`
- Issue #315 and Issue #316

Preparation/review coordinates:

- base: `main` at `b43be1007566faf5b0274c7bf3c8bb05a743ab10`
- branch: `architecture/316-response-validator-preparation`
- review-start HEAD: `aa8c6fbd6db8a49cdf7ab36afe8dae2766ab7bc0`
- first substantive correction: `8d9ec785dfdcdaa2d874656beb536006c58c7815`
- traceability correction: `ce5806e05b7b523afcee1c60a3ced4efdd0162dd`
- v1.2 correction: `e953ca288ac375f45e9087a223d03aa824cae1dc`
- PR: #317, Ready for Review after the corrective Draft cycle
- exact-head hosted checks on review-start HEAD passed before review findings were raised, including CI/Quality Gates, Dependency Review, CodeQL, and Hunter Governance Review.
- Earlier green results are historical evidence only. Final readiness requires fresh hosted checks on the current exact head; no earlier green result may be substituted.

### Auditable evidence coordinates

| Evidence | Exact repository coordinate | Material claim supported | Limitation |
|---|---|---|---|
| ADR 0034 | `docs/ADR/0034-evidence-intelligence-model-adapter-provider-attempt-boundary.md` at base `b43be1007566faf5b0274c7bf3c8bb05a743ab10` | Model Adapter owns execution/response capture and stops before ResponseValidator; transport success is not semantic validity | Binding architecture; later accepted amendments would supersede only if explicitly recorded |
| ADR 0033 | `docs/ADR/0033-source-handling-classification-authority.md` at base `b43be1007566faf5b0274c7bf3c8bb05a743ab10` | Source Handling is exclusive authority for processing/durability and must be resolved under historical coordinates | Binding architecture |
| ADR 0031 | `docs/ADR/0031-ai-context-prompt-intelligence-foundation.md` at base `b43be1007566faf5b0274c7bf3c8bb05a743ab10` | prompt/build and requested-output contract are upstream-owned; response validation remained deferred | Binding architecture |
| ADR 0032 | `docs/ADR/0032-project-agnostic-prompt-intelligence-core.md` at base `b43be1007566faf5b0274c7bf3c8bb05a743ab10` | no shared generic validator ownership without independent multi-consumer evidence | Binding architecture |
| Model Adapter runtime | `src/hunter/evidence_intelligence/model_adapter.py` at base `b43be1007566faf5b0274c7bf3c8bb05a743ab10` | Phase B ends after governed response capture; no ResponseValidator exists | Runtime evidence, not architecture authority |
| Legacy provider runtime | `src/hunter/evidence_intelligence/provider.py` at base `b43be1007566faf5b0274c7bf3c8bb05a743ab10` | legacy runner couples provider result to extraction proposal and cannot be re-labelled as canonical validator | Runtime evidence, legacy path only |
| Governance contract | `docs/HUNTER_IMPLEMENTATION_CONTRACT.md` at base `b43be1007566faf5b0274c7bf3c8bb05a743ab10` | architecture-only contribution and exact-head discipline | Governance authority |
| Audit protocol | `docs/ARCHITECTURE_AUDIT_PROTOCOL.md` at base `b43be1007566faf5b0274c7bf3c8bb05a743ab10` | independent audit/materiality/readiness protocol | Governance authority |
| Quality standard | `docs/ARCHITECTURE_DECISION_QUALITY_STANDARD.md` at base `b43be1007566faf5b0274c7bf3c8bb05a743ab10`, especially Rating Scale, Quality Dimensions, Mandatory Decision Gate, Assessment Record | exact self-assessment scale and 17 mandatory dimensions | Governs preparation self-assessment, not independent audit verdict |
| Review evidence | PR #317 review comments on `aa8c6fbd...`, `e953ca28...`, and later exact heads | Codex/CodeRabbit findings that drove v1.1-v1.3 revisions | Each review is exact-head evidence only; final audit must review current head |

Where a canonical ADR filename is not asserted above, the repository's architecture index is the navigation authority. No abbreviated invented path is used as primary evidence.

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
ResponseValidator event allocator
  owns: stable cutoff-free validation key, atomic base-event creation,
        one canonical event_id + validation_cutoff per base validation
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
                                        validation authorization,
                                        success/refusal attestation
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
- caller → MUST NOT mint canonical profile/rules, validation event identity, validation authorization, success/refusal attestation, or `VALID`;
- worker → MUST NOT choose its own validation cutoff for an already allocated base event;
- persistence → MUST NOT select profile/rules, re-run validation, or infer validity;
- `ResponseValidator` → MUST NOT override Source Handling or promote to canonical truth;
- downstream extraction/knowledge code → MUST NOT reinterpret transport success as validity or mint validation records;
- canonical promotion authority → remains separate and downstream; validation alone never grants promotion.

## Candidate Options

### Option 1 — Separate Evidence Intelligence ResponseValidator — RECOMMENDED

A Hunter-owned validator consumes exact governed response-capture lineage, an exact canonical validation profile resolved by `ResponseValidationProfileAuthority`, and an independent validation-time Source Handling decision. A validator-owned atomic event allocator deduplicates base validation before assigning its canonical cutoff. The validator emits append-only success or refusal evidence and stops before extraction/promotion.

Advantages: strongest authority separation, deterministic validation, replayable profile history, support for non-retainable live content, concurrency-safe event identity, explicit anti-forgery and fail-closed refusal boundaries.

Costs: introduces one validator service, one canonical profile authority, one event-allocation primitive, and transient authorization/attestation contracts.

Falsification: reject this option if the required validity semantics demonstrably cannot be separated from provider execution without losing correctness, if profile authority cannot be made independent of callers/persistence, or if stable event allocation cannot prevent duplicate base validation under concurrency. No such evidence is currently present.

### Option 2 — Embed validation in Model Adapter — REJECTED

Benefit: simplest implementation and direct access to capture lineage. Cost: authority concentration. Rejected because ADR 0034 deliberately ends Model Adapter authority before semantic validation and this option would collapse transport evidence and validity.

### Option 3 — Let extraction/knowledge layer validate — REJECTED

Benefit: fewer intermediate components. Cost: consumer self-validation and promotion coupling. Rejected because a downstream consumer must not become authority over whether its own input is valid.

### Option 4 — Generic shared validator core — DEFERRED

Benefit: future reuse. Cost: premature abstraction and ownership ambiguity. Viable only after ADR 0032's independent multi-consumer evidence gate is satisfied. Hunter-specific authority stays local now.

### Option 5 — Provider-specific validation — REJECTED

Benefit: provider-specific schema knowledge is local. Cost: provider lock-in and authority laundering. Rejected because provider transports cannot acquire canonical Hunter response-validity authority.

### Comparative analysis

| Criterion | Option 1 | Option 2 | Option 3 | Option 4 | Option 5 |
|---|---|---|---|---|---|
| Authority separation | strongest | weak | weak | strong if admitted | unacceptable |
| Compatibility with ADR 0034 | additive | conflicts | indirect conflict | additive if later admitted | conflicts |
| Replay clarity | strong | coupled to execution | coupled to promotion | strong | provider-dependent |
| Concurrency/idempotency clarity | explicit atomic event allocation | implicit/coupled | weak | possible | provider-dependent |
| Non-retainable live validation | explicit | possible but authority-coupled | awkward | possible | provider-specific |
| Implementation complexity | medium | low | low | high | medium |
| Migration risk | low/additive | high | high | medium | high |
| Reversibility before activation | high | medium/low | low | medium | low |

The recommendation is not based on implementation convenience; it is based on authority separation, accepted ADR compatibility, replay, idempotency, and anti-bypass properties.

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

### 3. Atomic validation-event allocation and cutoff ownership

Base-validation deduplication occurs **before** a validation cutoff is assigned and before semantic worker execution begins.

The stable `base_validation_key` excludes any per-run cutoff and is deterministically derived from:

- exact response-capture identity;
- requested-output-contract identity/version;
- canonical validation-profile selector/family identity requested for this validation purpose;
- validation-purpose identity (`BASE_RESPONSE_VALIDATION`).

`ResponseValidator` owns an atomic event-allocation primitive. For one `base_validation_key`, it performs create-if-absent and returns exactly one canonical `validation_event_id` plus exactly one `validation_cutoff`. Concurrent workers presenting the same base key therefore join the same allocated event; they do not receive independent cutoffs and cannot create distinct subjects by racing.

The event allocator records the requested canonical profile selector/family identity, but semantic profile applicability is still resolved by `ResponseValidationProfileAuthority` at the allocator-assigned `validation_cutoff`. The allocator cannot invent or admit a profile.

Ordinary retries/restarts after local worker loss reuse the existing event identity and cutoff and may only resume the same event under its persisted state machine. They are not re-validation.

An explicit **re-validation** is a different event family. It requires the exact predecessor validation record/event identity and an atomically claimed next `revalidation_generation`; that new generation receives a new event ID and new cutoff. Two concurrent attempts to create the same next generation are compare-and-set competitors, so at most one succeeds.

### 4. Validation-time Source Handling

Every allocated validation or re-validation event has its own canonical `validation_cutoff`.

Before any validation content is accessed or processed, `ResponseValidator` independently resolves ADR 0033 Source Handling using the strict-known facts and policy applicable at that **event-owned validation cutoff**. Attempt-time or capture-time `ALLOW` is never reusable authorization.

A successful Source Handling resolution binds at least:

- validation cutoff;
- Source Handling fact identity/version;
- Source Handling policy identity/version;
- Source Handling resolution identity;
- processing decision;
- durable-category decisions for any validation-derived field.

If processing is prohibited, the restrictive resolution is evidence for `SOURCE_HANDLING_BLOCKED`. If historical Source Handling authority cannot be resolved under strict-known rules, the failed resolution attempt itself is recorded as governed refusal evidence; Hunter must not fabricate a successful resolution identity.

### 5. Validation authorization and transient handoff

Only after the event exists and required authorities resolve successfully for semantic processing does `ResponseValidator` issue a single-use opaque `ResponseValidationAuthorization`.

That authorization binds:

- canonical validation event ID and event-owned cutoff;
- exact canonical profile resolution;
- requested-output-contract identity;
- successful Source Handling resolution identity;
- exact response-capture/attempt lineage expected;
- input mode (`DURABLE` or `TRANSIENT_NOT_RETAINED`).

The authorization is service-owned and non-caller-mintable. The Model Adapter may **carry** this authorization when delivering matching transient response bytes, but it cannot choose, modify, or attest profile/cutoff/policy values.

For transient validation:

1. validator atomically obtains or joins the canonical event;
2. profile authority and Source Handling are resolved at that event cutoff;
3. validator issues the authorization only if semantic processing is authorized;
4. Model Adapter supplies only the exact matching credential-screened bytes plus immutable response-capture lineage;
5. validator atomically consumes the authorization once;
6. transient bytes are never persisted merely because validation occurred.

A mismatched capture, profile, cutoff, contract, Source Handling resolution, or event identity fails closed.

### 6. Canonical validation event, subject, and idempotency

`validation_event_id` is the canonical execution identity allocated atomically before work. `validation_subject_id` is a deterministic content-independent identity derived from the already allocated event plus its governed semantic authorities; it is **not** used to deduplicate concurrent workers before event allocation.

For a base event, `validation_subject_id` binds:

- canonical `validation_event_id`;
- response-capture identity;
- resolved canonical `ResponseValidationProfile` identity/version, or explicit profile-resolution-failure marker for a refusal record;
- requested-output-contract identity/version;
- event-owned validation cutoff;
- successful Source Handling resolution identity, restrictive resolution identity, or explicit Source Handling-resolution-failure marker as appropriate.

Idempotency rules:

- one `base_validation_key` has at most one canonical base `validation_event_id`;
- one event has at most one accepted base terminal validation/refusal record;
- repeated workers/retries for the same event return or resume the existing event rather than allocate a new cutoff;
- conflicting terminal submissions for one event are rejected as a validation consistency failure, never stored as parallel history;
- persistence enforces event and terminal-record uniqueness atomically.

### 7. Immutable `ResponseValidationRecord`

The append-only record binds at least:

- record identity/schema version;
- canonical validation event identity;
- validation subject identity;
- response-capture identity;
- `ModelAttemptOutcomeRecord`, attempt, handoff, execution-profile, prompt/build lineage;
- requested-output-contract identity/version;
- event-owned validation cutoff and creation time;
- closed overall state;
- per-dimension closed states/reason codes where semantic evaluation occurred;
- input availability mode;
- durable diagnostics only where independently authorized;
- supersedes identity and correction/revalidation generation when applicable.

Authority-specific fields are conditional on state:

- semantic-success/semantic-invalid states bind successful canonical profile resolution and successful Source Handling processing authorization;
- `RULE_UNAVAILABLE` binds the governed profile-resolution attempt/refusal evidence and MUST NOT invent a successful profile resolution;
- `SOURCE_HANDLING_BLOCKED` binds either the exact restrictive Source Handling resolution or governed unresolved-authority attempt evidence, and MUST NOT invent an `ALLOW` resolution;
- `VALIDATOR_CAPABILITY_UNKNOWN`/`VALIDATOR_ERROR` bind the exact event and available authority coordinates plus governed failure evidence; unavailable coordinates remain explicitly absent.

### 8. Closed validation outcome vocabulary and precedence

The persisted top-level state vocabulary is **exactly**:

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

No other top-level value is canonical. Unknown/unrecognized values are rejected at persistence and cannot be interpreted downstream.

`VALIDATOR_ERROR` is operational failure. `VALIDATOR_CAPABILITY_UNKNOWN` is inability to establish a required validator capability. `EVIDENCE_AMBIGUOUS` is evidence insufficient to select a more specific deterministic result.

When multiple conditions apply to one validation event, the overall state is selected by this deterministic precedence, highest first:

1. `SECURITY_BLOCKED`
2. `SOURCE_HANDLING_BLOCKED`
3. `VALIDATOR_ERROR`
4. `VALIDATOR_CAPABILITY_UNKNOWN`
5. `INPUT_UNAVAILABLE`
6. `RULE_UNAVAILABLE`
7. `EVIDENCE_AMBIGUOUS`
8. `INVALID_LINEAGE`
9. `INVALID_SYNTAX`
10. `INVALID_SCHEMA`
11. `INVALID_OUTPUT_CONTRACT`
12. `INVALID_EVIDENCE_REFERENCE_STRUCTURE`
13. `PARTIAL_RESPONSE`
14. `VALID`

Per-dimension outcomes are preserved when semantic evaluation occurred so the top-level precedence does not erase evidence. Reason codes are from a closed, versioned registry where a canonical profile is available; pre-profile refusal reason codes belong to a separate closed validator-refusal vocabulary. Free-form diagnostics are non-authority metadata only when durability is permitted.

### 9. Non-forgeable success and refusal persistence

There are two validator-issued, single-use persistence capabilities; persistence never substitutes one for the other.

#### Successful-resolution attestation

For records whose state required a successful canonical profile resolution and successful Source Handling processing authority, `ResponseValidator` produces `ResponseValidationAttestation` bound to:

- exact `ResponseValidationRecord` identity and canonical payload digest;
- canonical validation event and subject;
- canonical profile resolution;
- successful validation-time Source Handling resolution;
- final closed validation state;
- correction/revalidation predecessor and generation when applicable.

This attestation is mandatory for `VALID` and every semantic content-validity result. A caller that constructs a structurally correct `VALID` record with genuine canonical identities but lacks the validator-issued attestation is rejected.

#### Refusal attestation

When semantic validation cannot lawfully start because profile authority or Source Handling authority is unavailable/blocked, `ResponseValidator` issues a distinct `ResponseValidationRefusalAttestation`. It binds:

- canonical validation event ID and cutoff;
- response-capture/requested-output-contract lineage available before the failed step;
- exact authority-resolution attempt identity;
- authority type attempted (`PROFILE` or `SOURCE_HANDLING`);
- stable refusal state/reason (`RULE_UNAVAILABLE`, `SOURCE_HANDLING_BLOCKED`, or another explicitly authorized pre-semantic failure);
- successful restrictive resolution identity when one exists, otherwise an explicit `resolution_unavailable` marker plus governed provenance of the failed strict-known lookup;
- canonical payload digest of the refusal record.

A refusal attestation MUST NOT contain or imply a canonical profile resolution when profile resolution failed, and MUST NOT contain or imply Source Handling `ALLOW` when Source Handling was restrictive or unresolved. It authorizes persistence of the refusal evidence only; it grants no semantic-validity authority and can never attest `VALID`.

Persistence verifies and atomically consumes the attestation type required by the record state. It also mechanically verifies lineage existence/matching, event uniqueness, available authority coordinates, durable-field authorization, correction predecessor claims, and record structure. Missing authority is verified as explicit governed missingness, not treated as successful resolution.

### 10. Replay and re-validation

Historical replay selects recorded append-only validation history under strict-known coordinates. It never calls a provider and never substitutes current profile or Source Handling state for historical state.

If a validation result was produced live from transient content that was not retainable, replay returns the recorded result plus `TRANSIENT_NOT_RETAINED`; it does not regenerate or fetch bytes.

Worker retry/restart for an unfinished event is not re-validation and does not get a new cutoff. Explicit re-validation atomically creates the next revalidation generation from the exact predecessor, receives a new event ID and cutoff, performs fresh canonical profile and Source Handling resolution, and obtains fresh success/refusal capabilities as applicable. It never rewrites an earlier result.

### 11. Correction and concurrent supersession

Corrections are append-only and non-branching within a validation event/result lineage.

A correction:

- names the exact current predecessor record;
- increments a monotonic correction generation;
- has a new correction cutoff/creation time where the correction itself requires a new governed decision;
- receives the attestation type appropriate to its corrected state.

Persistence performs an atomic compare-and-set against the currently accepted predecessor. If two corrections race, at most one may claim that predecessor. The loser must re-resolve the current head and cannot create a sibling branch.

Strict-known historical reads choose the highest correction generation whose correction coordinates were knowable at the requested cutoff. Generation is primary ordering; record identity is only an integrity/tie check, not semantic precedence.

### 12. Validation dimensions

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

### 13. Downstream boundary

A later extraction/knowledge-proposal service may consume only validation states explicitly accepted by its own separately governed contract, normally `VALID`. It must carry exact validation identity and lineage. `ResponseValidator` creates no extraction proposal and performs no canonical promotion.

## Falsification Results

| Scenario | Required result |
|---|---|
| Caller supplies permissive validation rules | rejected as authority; canonical profile authority resolves policy |
| Current profile differs from historical profile | historical resolution remains bound to historical cutoff |
| Source Handling became restrictive after attempt | validation-time re-resolution blocks/reclassifies validation; attempt ALLOW is ignored |
| Two workers start same base validation simultaneously | atomic cutoff-free base key returns one event ID and one cutoff; both cannot create distinct subjects |
| Worker crashes and retries unfinished validation | retry joins/resumes same event and cutoff; no accidental re-validation |
| Model Adapter tries to choose profile/cutoff | rejected; it supplies only capture lineage/bytes and may carry validator authorization |
| Profile resolution unavailable | `RULE_UNAVAILABLE` refusal record persists via refusal attestation without fake profile resolution |
| Source Handling restrictive | `SOURCE_HANDLING_BLOCKED` refusal binds restrictive resolution; no semantic authorization is issued |
| Source Handling strict-known lookup unresolved | refusal binds failed lookup provenance and explicit missingness; no fake ALLOW/resolution identity |
| Two corrections or re-validations race for same predecessor generation | compare-and-set permits one successor/event; no branching |
| Direct repo write submits canonical-looking `VALID` | rejected without successful-resolution validator attestation |
| Refusal attestation is presented for `VALID` | rejected by attestation/state compatibility rule |
| Provider returned valid JSON but wrong contract | deterministic invalid contract state |
| Validator capability cannot be established | `VALIDATOR_CAPABILITY_UNKNOWN` with governed available-coordinate failure evidence |
| Evidence is ambiguous | `EVIDENCE_AMBIGUOUS` |
| Multiple failures occur | closed precedence selects one top-level state while available per-dimension states remain recorded |
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
5. Base validation concurrency is deduplicated using a stable key that excludes per-run cutoff.
6. Concurrent workers for one base key receive one canonical event ID and one canonical validation cutoff.
7. Worker retry/restart resumes the same unfinished event; it cannot silently mint a new cutoff.
8. Explicit re-validation requires exact predecessor + next generation and atomically creates a new event/cutoff.
9. Every validation/re-validation event independently re-resolves Source Handling at its event-owned cutoff.
10. Attempt-time/capture-time Source Handling `ALLOW` cannot authorize later validation.
11. Successful semantic validation records bind exact Source Handling fact/policy/resolution identities.
12. A restrictive Source Handling resolution produces fail-closed refusal evidence and never semantic authorization.
13. An unresolved strict-known Source Handling lookup persists explicit governed missingness without fabricated resolution identity.
14. `RULE_UNAVAILABLE` persists through a refusal attestation that contains no fake canonical profile resolution.
15. A validator-issued `ResponseValidationAuthorization` is single-use and non-caller-mintable and is issued only after semantic-processing prerequisites succeed.
16. Model Adapter supplies only matching transient bytes/capture lineage and cannot select/alter profile, cutoff, event, or Source Handling policy.
17. Transient validation persists zero prohibited response bytes/hash/size/content-derived IDs.
18. Mismatched transient capture/authorization/event fails closed.
19. One canonical event has at most one accepted base terminal validation/refusal record.
20. Conflicting duplicate terminal results for one event cannot branch history.
21. `VALIDATOR_ERROR`, `VALIDATOR_CAPABILITY_UNKNOWN`, and `EVIDENCE_AMBIGUOUS` remain distinct.
22. Only the closed top-level vocabulary is accepted; unknown state/reason code cannot be interpreted as `VALID`.
23. Simultaneous failures use the canonical precedence deterministically.
24. A structurally correct direct repository write using genuine canonical identities but no validator success attestation is rejected.
25. A refusal attestation cannot attest `VALID` or any state requiring successful semantic authority.
26. Success/refusal attestation reuse, record substitution, event substitution, or subject substitution is rejected.
27. Persistence conditionally verifies successful authority coordinates or explicit governed missingness according to state; it never requires unavailable authority to have resolved successfully.
28. Correction is append-only, names exact predecessor, and increments generation.
29. Concurrent corrections cannot create sibling successors from one predecessor.
30. Strict-known replay selects latest applicable correction generation at cutoff, never oldest/current unconditionally.
31. Historical replay never invokes provider/network or regenerates prohibited bytes.
32. Explicit re-validation creates a new event/cutoff/profile resolution/Source Handling resolution and new capabilities; ordinary worker retry does not.
33. `VALID` grants no canonical truth or promotion authority and cannot itself create an extraction proposal.
34. Malformed/partial/contract-invalid responses cannot cross a downstream handoff that requires `VALID`.
35. Provider-specific transport cannot mint validation records, profiles, event IDs, authorization, or attestation.
36. Legacy `ExtractionProposal` / `AIProviderArtifact` cannot be retroactively accepted as `ResponseValidationRecord`.
37. Hunter Governance Review and Merge Readiness acquire no provider/credential dependency from this architecture.
38. Issue #315 remains separately unresolved unless explicitly completed.
39. Deliberately weakening each reusable authority, replay, event-allocation, idempotency, Source Handling, durability, precedence, or attestation guard causes its named regression to fail.

## Persistence, Security, and Privacy

Validation-derived excerpts, diagnostics, normalized values, hashes, sizes, and content-derived identifiers are independently governed durability categories. Processing permission never grants persistence permission.

Credential-bearing response content rejected by Phase B cannot be laundered into durable validation evidence. The validator also fails closed if credential safety of a durable derived field cannot be established.

Authorization, event-allocation records, success attestations, and refusal attestations are operational authority artifacts, not content-retention workarounds. They bind identities, attempts, decisions, or governed missingness but may not encode prohibited response content.

## Legacy and Migration

The existing legacy provider/extraction path remains historical. No backfill may fabricate validation records, profile resolutions, Source Handling decisions, event allocations, authorization, or attestations for old artifacts. Downstream consumers must distinguish legacy/unvalidated data explicitly.

Migration is additive: no existing Model Adapter identity changes. Before activation, downstream consumers that require validated responses must explicitly switch to the new validation record/handoff and reject legacy-unvalidated state.

Rollback before activation is straightforward: do not activate the consumer handoff. After append-only validation history exists, rollback disables new production use but never deletes or rewrites historical validation records.

## Operational Quality

Validation is local and provider-free. A validator failure records `VALIDATOR_ERROR` or another closed state where evidence supports it; it never triggers network retry. Atomic event allocation ensures local worker restarts do not accidentally become re-validation. Observability may report counts/latency/reason codes only within Source Handling and credential-safety constraints. Availability failure remains explicit and cannot default to `VALID`.

## Open Questions

Non-blocking for architecture selection, but required before activation where applicable:

- exact parser/schema library;
- exact database schema and indexes implementing event allocation/CAS;
- exact durable diagnostic field-category mapping;
- exact cryptographic/opaque implementation mechanism for non-caller-mintable capabilities while preserving the authority contract;
- whether forbidden-capability structural checks remain duplicated as independent capture and semantic-validation gates;
- future generic-core admission if ADR 0032 later obtains independent multi-consumer evidence.

The **top-level validation state vocabulary, precedence, canonical profile authority, event-before-cutoff rule, validation-time Source Handling requirement, idempotency rule, and state-compatible success/refusal attestation split are not open questions** in this preparation.

## Constitution and Governance Review

The recommendation is evidence-first and fail-closed. Unknown validity remains unknown; provider output is never promoted merely because it arrived; prohibited evidence is never reconstructed. Unresolved authority is recorded as explicit governed missingness rather than fabricated success. No trading, portfolio, recommendation, or autonomous-action authority is introduced.

This contribution remains architecture preparation only. No runtime code or accepted ADR is modified. PR #317 is Ready for Review; any new exact-head finding or failed check blocks progression. Independent architecture audit is mandatory before ADR drafting. Merge remains owner-only.

## Quality Assessment

Ratings use only the scale from `docs/ARCHITECTURE_DECISION_QUALITY_STANDARD.md`: `EXCELLENT`, `GOOD`, `ACCEPTABLE`, `NEEDS_IMPROVEMENT`, `UNACCEPTABLE`.

| Dimension | Rating | Evidence and rationale | Blocking limitation |
|---|---|---|---|
| Problem correctness | EXCELLENT | Gap is explicitly left after ADR 0034 response capture and confirmed by current Model Adapter runtime | None identified |
| Scope completeness | GOOD | In/out scope, dependencies, stop boundaries, #315 separation, downstream boundary are explicit | Physical implementation details intentionally deferred |
| Canonical consistency | GOOD | ADR 0031/0032/0033/0034/0020/0016/0009 and governance boundaries are reconciled; no accepted ADR is amended by this preparation | Independent audit still required |
| Evidence integrity | GOOD | Full repository paths, baseline SHA, review heads, evidence types, and limitations are recorded | Final exact-head checks/audit must use latest head |
| Assumption discipline | GOOD | Key assumptions are converted into falsification/conformance cases and authority constraints rather than hidden defaults | Provider-independent parser choice remains deferred |
| Option completeness | GOOD | Separate validator, embedded adapter, downstream owner, generic core, provider-specific owner are considered | No additional materially distinct owner found |
| Comparative fairness | GOOD | Same authority, compatibility, replay, complexity, migration, reversibility criteria applied across options; benefits as well as costs recorded | Quantitative cost is not meaningful at architecture-prep stage |
| Falsifiability | EXCELLENT | Falsification table plus 39 adversarial conformance obligations covers authority, replay, Source Handling, event allocation, concurrency, refusal, anti-forgery, precedence | Runtime mutation proof belongs to implementation |
| Authority and ownership clarity | EXCELLENT | Ownership diagram and forbidden edges distinguish Source Handling, profile authority, event allocation, Model Adapter, validator, persistence, downstream, promotion | Independent audit must challenge new profile/event authority boundaries |
| Persistence and replay quality | EXCELLENT | cutoff-free event key, atomic event allocation, idempotency, state-compatible attestations, CAS correction/revalidation, generation ordering, strict-known replay, transient non-retention are explicit | Physical schema/index choice deferred |
| Evidence and provenance quality | GOOD | exact capture/attempt/build/event/profile/Source Handling coordinates and explicit resolution missingness are required | Domain claim provenance is correctly out of validator scope |
| Operational quality | GOOD | local/provider-free validation, fail-closed availability, worker-restart semantics, no retry authority, observability constraints, rollback posture recorded | Concrete SLOs are implementation/operations work |
| Implementation and migration impact | GOOD | additive migration, no legacy backfill, downstream opt-in, rollback semantics, new authority/event/record/capability boundaries identified | Effort estimate not fixed before ADR/implementation plan |
| Testability and validation | EXCELLENT | 39 deterministic adversarial cases define acceptance surface, including same-key concurrent allocation and unresolved-authority refusal tests | Actual tests wait for implementation authority |
| Maintainability and extensibility | GOOD | Hunter-owned now, shared-core deferred by ADR 0032 gate, provider-neutral separation avoids hidden coupling | Future second-consumer evidence may justify later extraction |
| Risk quality | GOOD | material authority, privacy, replay, event race, refusal, legacy, operational and premature-abstraction risks have explicit mitigations throughout contract | Residual implementation mistakes require regression/mutation testing |
| Traceability | GOOD | Issue #316, #315, ADPR-0010, PR #317, base, review/correction commits, current lifecycle are explicit | ADR/merge/release remain legitimately unset |

No mandatory dimension is below `ACCEPTABLE`; Constitution/Governance-related consistency and authority dimensions are at least `GOOD`; evidence integrity, option completeness, comparative fairness, and falsifiability are at least `ACCEPTABLE`. Self-assessment therefore permits `READY_FOR_ADR` **only after** independent audit of the current exact head finds no blocking issue.

## Architecture Readiness

- Outcome: `READY`, subject to independent audit of v1.3.
- Canonical ownership is explicit: profile history belongs to `ResponseValidationProfileAuthority`; validity decisions and atomic validation-event allocation belong to `ResponseValidator`; Source Handling remains ADR 0033-owned; persistence is mechanical; promotion remains downstream and separate.
- Base validation is deduplicated before cutoff assignment, so concurrent workers cannot create distinct subjects merely by racing cutoffs.
- Worker retry/restart is distinguished from explicit re-validation.
- Validation-time authorization cannot reuse attempt-time Source Handling.
- Unavailable profile/Source Handling authority remains persistable through state-compatible refusal attestation without fabricated successful resolution.
- Persistence cannot mint `VALID` without validator success attestation.
- Closed top-level failure states and deterministic precedence are defined.
- Evidence coordinates and all mandatory quality dimensions are recorded.

## ADR Readiness

- Outcome: `READY_FOR_ADR` only if independent architecture audit returns no blocking finding on the exact current head.
- Proposed ADR title: Evidence Intelligence ResponseValidator Boundary.
- ADR must preserve every authority, event-allocation, cutoff, subject, success/refusal attestation, replay, precedence, and closed-state invariant in this v1.3 preparation.
- Parser/library choice, capability mechanism, and physical database schema remain implementation details.

## Decision History

| Date | State | Change | Author or reviewer |
|---|---|---|---|
| 2026-08-24 | READY_FOR_REVIEW | Initial preparation completed from post-PR #314 `main` | OpenAI GPT-5.6 Sol |
| 2026-08-24 | READY_FOR_REVIEW | v1.1 resolves canonical profile authority, validation-time Source Handling, ownership diagram, transient authorization, subject/idempotency/correction, closed outcome vocabulary, non-forgeable validator attestation, and governance evidence | OpenAI GPT-5.6 Sol |
| 2026-08-24 | READY_FOR_REVIEW | v1.2 completes auditable repository coordinates, freezes deterministic outcome precedence, completes all 17 mandatory quality ratings, and synchronizes PR #317 traceability | OpenAI GPT-5.6 Sol |
| 2026-08-24 | READY_FOR_REVIEW | v1.3 closes exact-head Codex findings by allocating/deduplicating validation events before cutoff assignment and splitting successful-resolution attestation from attestable unresolved-authority refusal evidence | OpenAI GPT-5.6 Sol |

## Traceability

- Issue: #316
- Follow-up: #315 (separate)
- ADPR: `ADPR-0010`
- PR: #317 (Ready for Review; merge remains owner-only)
- Base: `b43be1007566faf5b0274c7bf3c8bb05a743ab10`
- Review-start HEAD: `aa8c6fbd6db8a49cdf7ab36afe8dae2766ab7bc0`
- First corrective commit: `8d9ec785dfdcdaa2d874656beb536006c58c7815`
- Architecture-index traceability commit: `ce5806e05b7b523afcee1c60a3ced4efdd0162dd`
- v1.2 commit: `e953ca288ac375f45e9087a223d03aa824cae1dc`
- Current v1.3 commit: established by the commit containing this revision; fresh hosted exact-head checks and review must bind that SHA
- ADR: not yet created
- Implementation: not authorized by this record
- Merge commit: not yet created
- Release: not yet assigned

## Immutability and Supersession

After `APPROVED`, this record becomes historical evidence. Substantive later changes require a new ADPR that explicitly supersedes it. Non-substantive link completion and typographical corrections must remain auditable.