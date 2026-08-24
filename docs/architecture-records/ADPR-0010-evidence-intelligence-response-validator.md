# ADPR-0010 — Evidence Intelligence ResponseValidator Boundary

## Metadata

- ADPR ID: `ADPR-0010`
- Status: `READY_FOR_REVIEW`
- Version: 1.2
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

The recommendation is `READY_FOR_ADR` only after independent architecture audit confirms the revised authority, Source Handling, idempotency, persistence, failure-state, evidence, and quality contracts below.

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
6. which closed outcome states exist and how simultaneous failures are reduced deterministically;
7. where validation stops before extraction or canonical promotion.

### In scope

- response-validity authority;
- validation-profile/rule authority and lifecycle;
- validation-time Source Handling re-resolution;
- durable and transient validation inputs;
- immutable validation result semantics;
- idempotency, concurrency, correction, replay, persistence anti-bypass;
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
- PR: #317, Draft during correction lifecycle
- exact-head hosted checks on review-start HEAD passed before review findings were raised, including CI/Quality Gates, Dependency Review, CodeQL, and Hunter Governance Review.
- Because v1.2 changes the reviewed head, the review-start check result is historical evidence only. Final readiness requires fresh hosted checks on the new exact head; no earlier green result may be substituted.

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
| Review evidence | PR #317 review-start HEAD `aa8c6fbd6db8a49cdf7ab36afe8dae2766ab7bc0` | Codex/CodeRabbit findings that drove v1.1/v1.2 revisions | Earlier head; final audit must review current head |

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

Falsification: reject this option if the required validity semantics demonstrably cannot be separated from provider execution without losing correctness, or if profile authority cannot be made independent of callers/persistence. No such evidence is currently present.

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
| Non-retainable live validation | explicit | possible but authority-coupled | awkward | possible | provider-specific |
| Implementation complexity | medium | low | low | high | medium |
| Migration risk | low/additive | high | high | medium | high |
| Reversibility before activation | high | medium/low | low | medium | low |

The recommendation is not based on implementation convenience; it is based on authority separation, accepted ADR compatibility, replay, and anti-bypass properties.

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

1. validator-side authorities establish cutoff, canonical profile, and Source Handling resolution;
2. validator issues the authorization;
3. Model Adapter supplies only the exact matching credential-screened bytes plus immutable response-capture lineage;
4. validator atomically consumes the authorization once;
5. transient bytes are never persisted merely because validation occurred.

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

### 7. Closed validation outcome vocabulary and precedence

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

Per-dimension outcomes are preserved so the top-level precedence does not erase evidence. Reason codes are from a closed, versioned registry bound by the profile. Free-form diagnostics are non-authority metadata only when durability is permitted.

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

A caller that constructs a structurally correct `VALID` record with genuine canonical identities but lacks the validator-issued attestation is rejected.

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

Strict-known historical reads choose the highest correction generation whose correction coordinates were knowable at the requested cutoff. Generation is primary ordering; record identity is only an integrity/tie check, not semantic precedence.

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
| Model Adapter tries to choose profile/cutoff | rejected; it supplies only capture lineage/bytes and may carry validator authorization |
| Two workers validate same subject | at most one base record accepted; repeat is idempotent or conflict-rejected |
| Two corrections race | compare-and-set permits one successor; no branching |
| Direct repo write submits canonical-looking `VALID` | rejected without validator attestation |
| Provider returned valid JSON but wrong contract | deterministic invalid contract state |
| Validator capability cannot be established | `VALIDATOR_CAPABILITY_UNKNOWN` |
| Evidence is ambiguous | `EVIDENCE_AMBIGUOUS` |
| Multiple failures occur | closed precedence selects one top-level state while per-dimension states remain recorded |
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
9. Model Adapter supplies only matching transient bytes/capture lineage and cannot select/alter profile, cutoff, or Source Handling policy.
10. Transient validation persists zero prohibited response bytes/hash/size/content-derived IDs.
11. Mismatched transient capture/authorization fails closed.
12. Canonical validation subject identity is deterministic from response capture, profile, output contract, validation cutoff, and Source Handling resolution.
13. Concurrent base validation produces at most one accepted record for one subject.
14. Identical duplicate validation is idempotent; conflicting duplicate state cannot branch history.
15. `VALIDATOR_ERROR`, `VALIDATOR_CAPABILITY_UNKNOWN`, and `EVIDENCE_AMBIGUOUS` remain distinct.
16. Only the closed top-level vocabulary is accepted; unknown state/reason code cannot be interpreted as `VALID`.
17. Simultaneous failures use the canonical precedence deterministically.
18. A structurally correct direct repository write using genuine canonical identities but no validator attestation is rejected.
19. Attestation reuse, record substitution, or subject substitution is rejected.
20. Correction is append-only, names exact predecessor, and increments generation.
21. Concurrent corrections cannot create sibling successors from one predecessor.
22. Strict-known replay selects latest applicable correction generation at cutoff, never oldest/current unconditionally.
23. Historical replay never invokes provider/network or regenerates prohibited bytes.
24. Re-validation creates a new cutoff, profile resolution, Source Handling resolution, authorization, subject/event, and attestation.
25. `VALID` grants no canonical truth or promotion authority and cannot itself create an extraction proposal.
26. Malformed/partial/contract-invalid responses cannot cross a downstream handoff that requires `VALID`.
27. Provider-specific transport cannot mint validation records, profiles, authorization, or attestation.
28. Legacy `ExtractionProposal` / `AIProviderArtifact` cannot be retroactively accepted as `ResponseValidationRecord`.
29. Hunter Governance Review and Merge Readiness acquire no provider/credential dependency from this architecture.
30. Issue #315 remains separately unresolved unless explicitly completed.
31. Deliberately weakening each reusable authority, replay, idempotency, Source Handling, durability, precedence, or attestation guard causes its named regression to fail.

## Persistence, Security, and Privacy

Validation-derived excerpts, diagnostics, normalized values, hashes, sizes, and content-derived identifiers are independently governed durability categories. Processing permission never grants persistence permission.

Credential-bearing response content rejected by Phase B cannot be laundered into durable validation evidence. The validator also fails closed if credential safety of a durable derived field cannot be established.

Authorization and attestation are operational capabilities, not content-retention workarounds. They bind identities and decisions but may not encode prohibited response content.

## Legacy and Migration

The existing legacy provider/extraction path remains historical. No backfill may fabricate validation records, profile resolutions, Source Handling decisions, authorization, or attestation for old artifacts. Downstream consumers must distinguish legacy/unvalidated data explicitly.

Migration is additive: no existing Model Adapter identity changes. Before activation, downstream consumers that require validated responses must explicitly switch to the new validation record/handoff and reject legacy-unvalidated state.

Rollback before activation is straightforward: do not activate the consumer handoff. After append-only validation history exists, rollback disables new production use but never deletes or rewrites historical validation records.

## Operational Quality

Validation is local and provider-free. A validator failure records `VALIDATOR_ERROR` or another closed state where evidence supports it; it never triggers network retry. Observability may report counts/latency/reason codes only within Source Handling and credential-safety constraints. Availability failure remains explicit and cannot default to `VALID`.

## Open Questions

Non-blocking for architecture selection, but required before activation where applicable:

- exact parser/schema library;
- exact database schema and indexes;
- exact durable diagnostic field-category mapping;
- whether forbidden-capability structural checks remain duplicated as independent capture and semantic-validation gates;
- future generic-core admission if ADR 0032 later obtains independent multi-consumer evidence.

The **top-level validation state vocabulary, precedence, canonical profile authority, validation-time Source Handling requirement, subject/idempotency rule, and anti-forgery attestation are not open questions** in this preparation.

## Constitution and Governance Review

The recommendation is evidence-first and fail-closed. Unknown validity remains unknown; provider output is never promoted merely because it arrived; prohibited evidence is never reconstructed. No trading, portfolio, recommendation, or autonomous-action authority is introduced.

This contribution remains architecture preparation only. No runtime code or accepted ADR is modified. PR #317 remains Draft while corrective exact-head checks run. Independent architecture audit is mandatory before ADR drafting. Merge remains owner-only.

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
| Falsifiability | EXCELLENT | Falsification table plus 31 adversarial conformance obligations covers authority, replay, Source Handling, concurrency, anti-forgery, precedence | Runtime mutation proof belongs to implementation |
| Authority and ownership clarity | EXCELLENT | Ownership diagram and forbidden edges distinguish Source Handling, profile authority, Model Adapter, validator, persistence, downstream, promotion | Independent audit must challenge new profile authority boundary |
| Persistence and replay quality | EXCELLENT | subject identity, idempotency, attestation, CAS correction, generation ordering, strict-known replay, transient non-retention are explicit | Physical schema/index choice deferred |
| Evidence and provenance quality | GOOD | exact capture/attempt/build/profile/Source Handling coordinates and transient-vs-durable state are required | Domain claim provenance is correctly out of validator scope |
| Operational quality | GOOD | local/provider-free validation, fail-closed availability, no retry authority, observability constraints, rollback posture recorded | Concrete SLOs are implementation/operations work |
| Implementation and migration impact | GOOD | additive migration, no legacy backfill, downstream opt-in, rollback semantics, new authority/record/capability boundaries identified | Effort estimate not fixed before ADR/implementation plan |
| Testability and validation | EXCELLENT | 31 deterministic adversarial cases define acceptance surface, including concurrent/idempotent and forged-write tests | Actual tests wait for implementation authority |
| Maintainability and extensibility | GOOD | Hunter-owned now, shared-core deferred by ADR 0032 gate, provider-neutral separation avoids hidden coupling | Future second-consumer evidence may justify later extraction |
| Risk quality | GOOD | material authority, privacy, replay, concurrency, legacy, operational and premature-abstraction risks have explicit mitigations throughout contract | Residual implementation mistakes require regression/mutation testing |
| Traceability | GOOD | Issue #316, #315, ADPR-0010, Draft PR #317, base, review-start/correction commits, current index lifecycle are explicit | ADR/merge/release remain legitimately unset |

No mandatory dimension is below `ACCEPTABLE`; Constitution/Governance-related consistency and authority dimensions are at least `GOOD`; evidence integrity, option completeness, comparative fairness, and falsifiability are at least `ACCEPTABLE`. Self-assessment therefore permits `READY_FOR_ADR` **only after** independent audit of the current exact head finds no blocking issue.

## Architecture Readiness

- Outcome: `READY`, subject to independent audit of v1.2.
- Canonical ownership is explicit: profile history belongs to `ResponseValidationProfileAuthority`; validity decisions belong to `ResponseValidator`; Source Handling remains ADR 0033-owned; persistence is mechanical; promotion remains downstream and separate.
- Validation-time authorization cannot reuse attempt-time Source Handling.
- Idempotency and non-branching correction are defined.
- Persistence cannot mint `VALID` without validator attestation.
- Closed top-level failure states and deterministic precedence are defined.
- Evidence coordinates and all mandatory quality dimensions are recorded.

## ADR Readiness

- Outcome: `READY_FOR_ADR` only if independent architecture audit returns no blocking finding on the exact current head.
- Proposed ADR title: Evidence Intelligence ResponseValidator Boundary.
- ADR must preserve every authority, cutoff, subject, attestation, replay, precedence, and closed-state invariant in this v1.2 preparation.
- Parser/library choice and physical database schema remain implementation details.

## Decision History

| Date | State | Change | Author or reviewer |
|---|---|---|---|
| 2026-08-24 | READY_FOR_REVIEW | Initial preparation completed from post-PR #314 `main` | OpenAI GPT-5.6 Sol |
| 2026-08-24 | READY_FOR_REVIEW | v1.1 resolves canonical profile authority, validation-time Source Handling, ownership diagram, transient authorization, subject/idempotency/correction, closed outcome vocabulary, non-forgeable validator attestation, and governance evidence | OpenAI GPT-5.6 Sol |
| 2026-08-24 | READY_FOR_REVIEW | v1.2 completes auditable repository coordinates, freezes deterministic outcome precedence, completes all 17 mandatory quality ratings, and synchronizes PR #317 traceability | OpenAI GPT-5.6 Sol |

## Traceability

- Issue: #316
- Follow-up: #315 (separate)
- ADPR: `ADPR-0010`
- PR: #317 (Draft during corrective lifecycle)
- Base: `b43be1007566faf5b0274c7bf3c8bb05a743ab10`
- Review-start HEAD: `aa8c6fbd6db8a49cdf7ab36afe8dae2766ab7bc0`
- First corrective commit: `8d9ec785dfdcdaa2d874656beb536006c58c7815`
- Architecture-index traceability commit: `ce5806e05b7b523afcee1c60a3ced4efdd0162dd`
- Current v1.2 commit: established by the commit containing this revision; hosted exact-head checks must bind that SHA before Ready for Review
- ADR: not yet created
- Implementation: not authorized by this record
- Merge commit: not yet created
- Release: not yet assigned

## Immutability and Supersession

After `APPROVED`, this record becomes historical evidence. Substantive later changes require a new ADPR that explicitly supersedes it. Non-substantive link completion and typographical corrections must remain auditable.