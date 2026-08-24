# ADPR-0010 — Evidence Intelligence ResponseValidator Boundary

## Metadata

- ADPR ID: `ADPR-0010`
- Status: `READY_FOR_REVIEW`
- Version: 1.5
- Author: OpenAI GPT-5.6 Sol — architecture preparation agent
- Reviewers: targeted independent architecture re-audit required
- Created: 2026-08-24
- Revised: 2026-08-24
- Approved: not yet approved
- Related Issue: #316
- Correction Issue: #320
- Blocking audit finding addressed by v1.4: PR #319 `F-001` (Class C)
- Post-merge review correction addressed by v1.5: PR #321 P1 — correction time coordinates for strict-known replay
- Related follow-up: #315 (separate, non-blocking unless a concrete dependency is later proven)
- Planned ADR: Evidence Intelligence ResponseValidator Boundary

## Executive Summary

ADR 0034 Phase B gives Hunter a governed path through durable model-attempt lineage, single-use handoff, one provider transport, append-only outcomes, and governed response capture, and deliberately stops before semantic response validation. Hunter therefore needs a separate authority that may decide whether an answered provider response conforms to the exact requested-output contract and exact historical validation policy without turning transport success into semantic validity or canonical truth.

This preparation recommends a separate Hunter Evidence Intelligence `ResponseValidator` downstream of the Model Adapter and upstream of extraction/knowledge proposal. `ResponseValidator` owns response-validity decisions, event allocation, validation authorization, and success/refusal attestations. ADR 0033 Source Handling remains the sole processing/durability authority. Persistence remains mechanical and non-authoritative. Extraction and promotion remain separate downstream authorities.

Version 1.4 closes audit finding `F-001` by treating **canonical validation-profile ownership as an independent decision dimension** rather than an incidental property of validator placement. The materially distinct ownership models are explicitly compared: a dedicated `ResponseValidationProfileAuthority`, validator-owned profile history, reuse/delegation to the upstream requested-output/schema owner, persistence-owned registry authority, and a future generic/shared profile authority. The recommendation remains a dedicated Hunter `ResponseValidationProfileAuthority`, but only after the normalized comparison below.

Version 1.5 restores and hardens the replay invariant caught by exact-head review after PR #321 merged: every correction receives an immutable `correction_recorded_at` from the trusted persistence boundary **at the same atomic durable-acceptance operation that appends the correction**. Callers, validators, and attestations cannot choose or backdate that value. Whenever a correction performs a fresh governed decision, `ResponseValidator` first uses its trusted correction allocator to atomically claim the exact predecessor/next generation and allocate an immutable `correction_decision_id` plus `correction_cutoff` before any correction-time authority resolution or worker execution. The worker cannot choose, backdate, or substitute that cutoff. Persistence then mechanically verifies the trusted allocation and predecessor/decision/acceptance chronology before the correction becomes durable. Strict-known replay therefore filters by trusted decision and durable-knowability coordinates before generation ordering and cannot leak a later correction backward in time.

Progression to `READY_FOR_ADR` is prohibited until a targeted independent re-audit verifies that `F-001` is closed on the exact merged correction revision and that this post-merge replay correction preserves the audited contract.

## Problem Statement

### Current condition

The merged architecture baseline after the profile-authority correction is `main` at `8ee6fd57577fa322b87cba21bd381d05770edd29`, the squash merge of PR #321. Runtime still ends the Model Adapter boundary after governed provider-response capture. There is no accepted `ResponseValidator`, semantic response validation, extraction promotion, or knowledge promotion in that path.

The legacy `SecureAIProviderRunner` directly turns provider output into `ExtractionProposal` after limited screening. That path predates ADR 0031/0033/0034 lineage and cannot be relabelled as the new validator.

### Decision required

This lifecycle must decide:

1. who owns response-validity decisions;
2. who owns canonical validation-profile publication, rule identity, applicability, correction, supersession, and historical resolution;
3. which response, contract, profile, Source Handling, and historical coordinates are authoritative;
4. how non-retainable but processable response content reaches validation without being persisted;
5. how a validation event is allocated before execution, deduplicated, corrected, replayed, and made non-forgeable;
6. how refusal evidence remains attestable when profile or Source Handling authority is unavailable;
7. which closed outcome states exist and how simultaneous failures are reduced deterministically;
8. how a later correction proves when its fresh decision occurred and when its successor became historically knowable;
9. where validation stops before extraction or canonical promotion.

### In scope

- response-validity authority;
- canonical validation-profile/rule authority and lifecycle;
- validation-time Source Handling re-resolution;
- durable and transient validation inputs;
- atomic validation-event allocation and idempotency;
- immutable validation result semantics;
- concurrency, correction, replay, persistence anti-bypass;
- success and refusal attestation contracts;
- closed failure/missingness states and deterministic precedence;
- downstream validated-response boundary;
- adversarial conformance obligations.

### Out of scope

- runtime implementation;
- provider routing, fallback, ranking, second provider, dynamic model selection, load balancing, or hedging;
- canonical source/claim/valuation truth;
- extraction or knowledge promotion;
- valuation, ranking, opportunity, timing, portfolio, recommendation, Dashboard, or scheduler work;
- Issue #315 implementation;
- governance redesign;
- synthetic validation backfill for legacy provider artifacts.

## Governing Evidence and Coordinates

This preparation is constrained by:

- `docs/PROJECT_CONSTITUTION.md`
- `docs/CANONICAL_ARCHITECTURE_MAP.md`
- `docs/HUNTER_IMPLEMENTATION_CONTRACT.md`
- `docs/ARCHITECTURE_AUDIT_PROTOCOL.md`
- `docs/ARCHITECTURE_AUDIT_TEMPLATE.md`
- `docs/ARCHITECTURE_DECISION_PREPARATION_GUIDE.md`
- `docs/ARCHITECTURE_DECISION_QUALITY_STANDARD.md`
- `docs/DEVELOPMENT_GOVERNANCE.md`
- ADR 0034 — Model Adapter/provider attempt boundary
- ADR 0033 — Source Handling classification authority
- ADR 0031 — prompt/requested-output authority
- ADR 0032 — project-agnostic prompt-intelligence core admission gate
- ADR 0020 — historical replay and strict-known semantics
- ADR 0016 and ADR 0009 — authority/repository separation
- current Model Adapter/provider runtime
- Issue #315, Issue #316, Issue #318, Issue #320
- PR #317 preparation history, PR #319 independent-audit finding history, and PR #321 correction/review history

Correction coordinates:

- independent-audit baseline: `5840849d81039ba4bd3dff5910db2907c1ff2780`
- profile-authority correction PR: #321
- profile-authority correction merge: `8ee6fd57577fa322b87cba21bd381d05770edd29`
- governing correction Issue: #320
- targeted finding: PR #319 `F-001`, Class C, `Blocks ADR = YES`
- post-merge exact-head review finding: PR #321 P1, correction-time coordinate omission

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

## Authority and Ownership

```text
ADR 0033 Source Handling Authority
  owns processing/durability facts + policy + strict-known resolution
                    |
                    v
ResponseValidationProfileAuthority
  owns canonical validation-profile publication, applicability,
  version history, correction/supersession, and rule-set identity
                    |
                    v
ResponseValidator event/correction allocator
  owns stable base allocation plus trusted correction-decision
  generation/cutoff allocation before worker execution
                    |
                    v
Model Adapter ------------------------------+
  owns attempt/outcome/response capture     |
  may carry exact matching transient bytes  |
  but cannot select validation policy       |
                                            v
                                  ResponseValidator
                                  owns response-validity decision,
                                  authorization, success/refusal attestation
                                            |
                                            v
                                  Validation Persistence
                                  mechanical append-only storage,
                                  existence/lineage/attestation/CAS checks only
                                            |
                                            v
                                  Validated-response handoff
                                            |
                                            v
                                  Future extraction/knowledge proposal
                                            |
                                            v
                                  Separate canonical promotion authority
```

Forbidden authority edges:

- provider/transport or Model Adapter must not choose validation rules or mint validity;
- caller must not mint canonical profile/rules, event identity, correction-decision identity/cutoff, authorization, attestation, or `VALID`;
- worker must not choose a new cutoff for an already allocated event or correction decision;
- persistence must not select policy, rerun validation, infer validity, or become profile authority;
- `ResponseValidator` must not override Source Handling or promote to canonical truth;
- downstream extraction/knowledge must not reinterpret transport success as validity or mint validation records;
- validation never grants canonical-promotion authority.

## Decision Dimension A — Validation Execution Placement

### Option A1 — Separate Evidence Intelligence `ResponseValidator` — RECOMMENDED

Consumes governed response-capture lineage, independently resolved validation policy, and validation-time Source Handling. Owns event allocation, semantic validity decision, and validator attestations. Stops before extraction/promotion.

Advantages: strongest separation from transport and downstream consumers; deterministic replay/idempotency; supports transient non-retainable validation; explicit anti-forgery and refusal boundaries.

Costs: adds one validator service and event-allocation/attestation contracts.

### Option A2 — Embed validation in Model Adapter — REJECTED

Simpler and local to response capture, but conflicts with ADR 0034's deliberate stop before semantic validation and collapses transport evidence into semantic authority.

### Option A3 — Let extraction/knowledge layer validate — REJECTED

Reduces intermediate components but makes the downstream consumer judge its own input and couples validation to promotion.

### Option A4 — Generic shared validator core — DEFERRED

Potential future reuse, but ADR 0032 requires independent multi-consumer evidence before extracting a shared generic authority.

### Option A5 — Provider-specific validation — REJECTED

Keeps provider-specific schema knowledge local but creates provider lock-in and lets transport/provider code acquire canonical Hunter validity authority.

| Criterion | A1 Separate validator | A2 Model Adapter | A3 downstream | A4 shared core | A5 provider-specific |
|---|---|---|---|---|---|
| Authority separation | strong | weak | weak | strong if admitted | unacceptable |
| ADR 0034 compatibility | additive | conflicts | indirect conflict | additive if later admitted | conflicts |
| Replay clarity | strong | execution-coupled | promotion-coupled | strong | provider-dependent |
| Idempotency clarity | explicit allocator | coupled | weak | possible | provider-dependent |
| Non-retainable live validation | explicit | possible but coupled | awkward | possible | provider-specific |
| Complexity | medium | low | low | high | medium |
| Migration risk | low/additive | high | high | medium | high |
| Reversibility | high pre-activation | medium/low | low | medium | low |

## Decision Dimension B — Canonical Validation-Profile Ownership

This is a separate authority decision from where validation executes. A profile contains policy that is broader than the upstream requested-output schema: parser/canonicalization identity, structural evidence-reference checks, bounded resource policy, validator capability/security checks where assigned here, required dimensions, and the closed result/reason vocabulary. The owner must support append-only publication, applicability intervals or equivalent strict-known coordinates, correction/supersession, and deterministic historical resolution.

### Option B1 — Dedicated Hunter `ResponseValidationProfileAuthority` — RECOMMENDED

A distinct Evidence Intelligence authority owns immutable profile versions, rule-set identity, applicability, correction/supersession, and historical resolution. `ResponseValidator` consumes its resolution but cannot publish or select policy ad hoc.

Benefits:

- separates rule-making from rule execution;
- prevents validator self-authorization and caller/persistence policy injection;
- gives profile history one canonical append-only owner with strict-known replay semantics;
- keeps upstream requested-output contracts as inputs rather than expanding their authority into validator-only concerns;
- permits profile lifecycle to evolve independently while preserving exact historical resolution.

Costs:

- adds a narrow service/authority boundary and profile-history persistence surface;
- requires explicit coordination with requested-output contract identities and validator implementation-contract identities.

### Option B2 — `ResponseValidator` owns profile publication/history — REJECTED

The validator would both define the rules and issue the validity result. This reduces components and can simplify deployment, but materially concentrates semantic authority: the same boundary could change policy, select the changed policy, and attest its own result. Replay also becomes coupled to validator deployment/history rather than an independently governed policy lineage.

Rejected because Hunter already separates evidence authority from execution where authority laundering is possible. Mechanical anti-forgery at persistence would not cure rule-maker/judge concentration.

### Option B3 — Reuse/delegate to upstream requested-output/schema owner — REJECTED FOR CURRENT SCOPE

ADR 0031 upstream ownership correctly controls the requested-output contract and schema expectations that validation consumes. Reusing that owner would reduce new authority surfaces and keep request/response shape close together.

However, the canonical validation profile also owns validator-specific policy that the upstream requested-output owner does not currently own: parser/canonicalization contract, evidence-reference structural rules, validator capability/security checks where applicable, bounded validation resource policy, required validation dimensions, and closed result/reason vocabulary. Giving those concerns to the upstream owner would silently widen ADR 0031 authority and couple prompt/request construction to downstream semantic-validation policy.

Delegation remains allowed only for immutable **inputs** already canonically owned upstream. The profile authority must reference those exact identities; it must not duplicate or override them.

### Option B4 — Validation Persistence owns the profile registry — REJECTED

This can make lookup/storage implementation simple, but violates ADR 0009-style repository/authority separation. Persistence must verify and store already-authorized profile/history records; it must not decide which profile is canonical or applicable. Otherwise direct repository usage becomes a policy-authority bypass.

### Option B5 — Generic/shared profile authority — DEFERRED

A cross-project or project-agnostic profile authority could become attractive if multiple independent consumers need the same validation-policy lifecycle. Today that evidence is absent and ADR 0032 explicitly blocks premature shared-core authority. Hunter-specific profile ownership therefore remains local. Future admission requires a new governed decision and must preserve existing historical profile identities.

### Normalized comparison

| Criterion | B1 Dedicated authority | B2 Validator-owned | B3 Upstream owner | B4 Persistence-owned | B5 Shared authority |
|---|---|---|---|---|---|
| Rule-maker vs executor separation | strongest | poor | medium | poor | strong if admitted |
| Canonical rule ownership fit | exact/local | conflated with execution | partial; schema only | invalid authority placement | plausible only with future evidence |
| Append-only profile history | explicit owner | validator-coupled | would widen upstream owner | storage-centric and bypass-prone | explicit if later governed |
| Strict-known replay | independent profile lineage | deployment/history coupled | mixes request and validation policy histories | repository state risks authority laundering | strong if future identity migration is governed |
| Correction/supersession | dedicated append-only lifecycle | coupled to validator release | would require new upstream semantics | repository would decide policy | possible after new ADR |
| Caller anti-forgery | strong | strong only if validator uncompromised; policy self-selection remains | strong for schema inputs, incomplete for validator policy | weak authority boundary | strong if admitted |
| Governance impact | narrow new authority | concentrates existing validator authority | materially widens ADR 0031 owner | conflicts with repository separation | materially widens shared-core authority |
| Implementation complexity | medium | low | medium | low | high |
| Migration | additive | additive but coupled | requires upstream contract expansion | unsafe semantic migration | future migration decision required |
| Reversibility | high before activation | medium | medium/low after widening | low | medium |

### Recommendation rationale

B1 is selected because it is the only current option that simultaneously preserves ADR 0031 requested-output ownership, ADR 0032's shared-core admission gate, ADR 0033 Source Handling exclusivity, ADR 0034 Model Adapter limits, repository non-authority, strict-known historical replay, and separation between rule publication and rule execution.

B1 does **not** duplicate upstream authority. The requested-output/schema owner remains authoritative for its own exact contract identity; `ResponseValidationProfileAuthority` composes that identity with validation-only policy identities. If the requested-output contract is unresolved, the profile authority cannot fabricate it.

### Falsification conditions for B1

The dedicated authority recommendation must be reconsidered through a new governed revision if any of the following is proven before activation:

1. the canonical validation profile contains no policy beyond an already-governed upstream requested-output contract, making the separate authority semantically empty;
2. rule publication and historical applicability cannot be separated from validator execution without losing correctness or deterministic replay;
3. a previously accepted authority already owns the full validation-policy lifecycle, including validator-specific rules, without widening its charter;
4. independent multi-consumer evidence satisfies ADR 0032 and demonstrates that a shared profile authority is materially safer and simpler while preserving historical identities;
5. the dedicated boundary creates an unresolvable split-brain in which two authorities can canonically select conflicting profiles for the same cutoff.

Absent such evidence, convenience or fewer services is insufficient reason to collapse rule publication into execution, upstream request ownership, or persistence.

## Recommended Contract

### 1. Response-validity owner

`ResponseValidator` is the sole owner of response-validity decisions. `VALID` means only **conforms to this exact canonical validation contract under these exact historical coordinates**. It never means true, authoritative, canonical, or promoted.

### 2. Canonical validation-profile authority

`ResponseValidationProfileAuthority` solely owns:

- append-only publication of immutable profile versions;
- exact profile and rule-set identity;
- activation/applicability intervals or equivalent strict-known coordinates;
- append-only correction/supersession metadata;
- deterministic resolution of the exact profile applicable at a validation cutoff.

It does not validate responses, own Source Handling, own requested-output/schema truth, or perform persistence. Upstream requested-output identities remain authoritative inputs. Caller-provided profile IDs, schemas, or rules are requests/evidence only.

A profile binds at least:

- profile schema/version;
- validator implementation-contract identity/version;
- requested-output-contract identity/version;
- syntax/schema/shape rule identity;
- parser/canonicalization contract identity/version;
- evidence-reference structural-validation rule identity/version;
- bounded resource/size policy identity;
- prohibited-capability/security validation rule identity where assigned;
- required validation dimensions;
- closed result/reason vocabulary version.

Historical replay binds profile history knowable at the recorded cutoff; current/latest state cannot substitute.

### 3. Atomic validation-event and correction-decision allocation

Base-validation deduplication occurs before a cutoff is assigned and before semantic worker execution. A stable `base_validation_key` excludes per-run cutoff and binds response-capture identity, requested-output-contract identity/version, requested canonical profile selector/family, and purpose `BASE_RESPONSE_VALIDATION`.

`ResponseValidator` atomically create-if-absent allocates exactly one canonical `validation_event_id` and one `validation_cutoff` for a base key. Concurrent workers join the same event. The allocator records the requested profile selector but cannot invent/admit a canonical profile; applicability is resolved by `ResponseValidationProfileAuthority` at the event cutoff.

Worker retry/restart resumes the same event/cutoff. Explicit re-validation requires exact predecessor plus atomically claimed next `revalidation_generation` and receives a new event/cutoff.

A **correction that performs a fresh governed decision** follows the same event-before-worker rule. Before any correction-time profile/Source Handling resolution or semantic execution, the `ResponseValidator` correction allocator atomically verifies the exact current predecessor, claims exactly one next `correction_generation`, and allocates an immutable `correction_decision_id` plus `correction_cutoff` from its trusted clock. Concurrent contenders for the same predecessor/generation join or lose the same allocation; a worker/caller cannot supply, backdate, replace, or reallocate the cutoff. Every correction-time authority resolution, authorization, attestation, and successor proposal that belongs to that fresh decision must bind the exact allocation. Ordinary retry resumes the same correction allocation. A genuinely new correction decision requires a new successful predecessor/generation claim and therefore a new trusted cutoff. A clerical correction that performs no fresh governed decision carries no `correction_decision_id` or `correction_cutoff` and may not perform fresh authority resolution under a fabricated time coordinate.

### 4. Validation-time Source Handling

Every validation/re-validation independently resolves ADR 0033 Source Handling at its event-owned cutoff before content is processed. Attempt-time/capture-time `ALLOW` is never reusable authorization.

A successful resolution binds cutoff, fact identity/version, policy identity/version, resolution identity, processing decision, and durable-category decisions. Restrictive or unresolved authority yields governed refusal evidence without fabricated `ALLOW` or fabricated resolution identity.

A fresh correction decision that requires Source Handling or profile re-resolution uses only its allocator-issued `correction_cutoff`; current wall-clock time or any worker-proposed time is forbidden. A clerical correction without a fresh governed decision may not re-resolve authority at all.

### 5. Validation authorization and transient input

Only after event allocation and successful semantic-processing prerequisites may `ResponseValidator` issue a single-use, non-caller-mintable `ResponseValidationAuthorization` bound to event/cutoff, canonical profile resolution, requested-output contract, successful Source Handling resolution, capture/attempt lineage, and input mode `DURABLE` or `TRANSIENT_NOT_RETAINED`.

Model Adapter may carry the authorization and exact matching credential-screened transient bytes; it cannot select/alter profile, event, cutoff, correction-decision allocation, or Source Handling. Mismatch fails closed. Transient bytes are never persisted merely because validation occurred.

### 6. Validation subject and idempotency

`validation_subject_id` derives from the already allocated event plus governed semantic coordinates and is not used to deduplicate workers before allocation. One base key has at most one base event; one event has at most one accepted base terminal validation/refusal record. Conflicting terminal submissions are rejected, not stored as parallel history.

### 7. Immutable `ResponseValidationRecord`

The append-only record binds event/subject, response capture, attempt/handoff/execution-profile/prompt lineage, requested-output contract, event cutoff/time, closed state, authorized per-dimension outcomes, input-availability mode, authorized diagnostics, and correction/revalidation predecessor/generation where applicable.

For every **correction**, the submitted successor names the exact predecessor and generation. If it represents a fresh governed decision, it must bind the exact allocator-issued `correction_decision_id` and `correction_cutoff`; the successor never supplies a caller/worker-chosen cutoff. If it is clerical-only, both fields are explicitly absent and no fresh authority resolution is permitted. In either case the proposal never supplies an authoritative `correction_recorded_at`. At successful append, persistence atomically verifies the predecessor/CAS, state-compatible attestation, and any trusted correction allocation, assigns `correction_recorded_at` from its trusted durable-acceptance clock, and writes that timestamp into the immutable successor record in the same transaction. Any caller-supplied, worker-supplied, or attestation-supplied proposed cutoff/recorded-at value outside the trusted allocation contract is rejected rather than trusted or copied.

Chronology is mechanical and fail-closed. The accepted successor must satisfy `predecessor.correction_recorded_at <= correction_recorded_at` (or the predecessor's base durable-acceptance coordinate for the first correction). When a fresh governed decision exists, persistence additionally requires `predecessor durable-acceptance <= allocated correction_cutoff <= correction_recorded_at` and verifies that the allocation names the exact predecessor/generation. A correction allocation earlier than the predecessor's durable knowability, a substituted cutoff, or an allocation later than the atomic acceptance that records the successor is invalid and the append fails. The original validation event cutoff/time remains immutable event identity and is never reused as correction decision or knowability time.

State-specific authority fields are conditional: semantic states require successful profile and Source Handling resolution; `RULE_UNAVAILABLE` carries profile-resolution refusal evidence without a fake profile; `SOURCE_HANDLING_BLOCKED` carries restrictive/unresolved Source Handling evidence without fake `ALLOW`.

### 8. Closed validation vocabulary and precedence

Canonical top-level states are exactly:

`VALID`, `INVALID_SYNTAX`, `INVALID_SCHEMA`, `INVALID_OUTPUT_CONTRACT`, `INVALID_LINEAGE`, `INVALID_EVIDENCE_REFERENCE_STRUCTURE`, `PARTIAL_RESPONSE`, `INPUT_UNAVAILABLE`, `RULE_UNAVAILABLE`, `VALIDATOR_CAPABILITY_UNKNOWN`, `EVIDENCE_AMBIGUOUS`, `SOURCE_HANDLING_BLOCKED`, `SECURITY_BLOCKED`, `VALIDATOR_ERROR`.

Unknown states are rejected. Deterministic highest-first precedence is:

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

### 9. Non-forgeable success and refusal persistence

Semantic results require a validator-issued single-use `ResponseValidationAttestation` bound to exact proposed semantic record payload, event/subject, canonical profile resolution, successful validation-time Source Handling resolution, state, exact correction predecessor/generation, and, for a fresh correction decision, the exact allocator-issued `correction_decision_id` and `correction_cutoff`. A correction attestation cannot mint, choose, alter, or backdate that cutoff. For every correction, the attestation also binds the rule that `correction_recorded_at` is **persistence-assigned at atomic durable acceptance**; it cannot bind or authorize a caller-chosen timestamp that does not yet exist.

Pre-semantic refusal requires a distinct `ResponseValidationRefusalAttestation` bound to event/cutoff, available capture/requested-output lineage, exact authority-resolution attempt, attempted authority type, stable refusal state/reason, restrictive resolution if available or explicit governed `resolution_unavailable`, canonical refusal payload, exact correction predecessor/generation, and any trusted correction-decision allocation. It uses the same allocator-issued cutoff contract and persistence-assigned recorded-at contract for corrected refusals.

The two attestations are non-substitutable. Persistence verifies and atomically consumes the required capability, checks lineage/uniqueness/authority coordinates/durability authorization/correction predecessor and chronology, verifies any correction-decision allocation against exact predecessor/generation/cutoff, then stamps the trusted `correction_recorded_at` as part of the successful append. The resulting immutable durable record, not a pre-persistence proposal, binds the actual accepted correction timestamp. This keeps persistence mechanical: it verifies trusted decision-time lineage and supplies a trusted storage fact, but it does not choose semantic decision time, policy, or validity.

### 10. Replay and re-validation

Historical replay never invokes a provider or substitutes current profile/Source Handling state. Transient content that was not retainable replays only the recorded validation result plus `TRANSIENT_NOT_RETAINED`.

For a historical replay cutoff, a correction is eligible only if its persistence-assigned `correction_recorded_at` is at or before that replay cutoff and every fresh governed decision required by that successor is bound to the allocator-issued `correction_decision_id`/`correction_cutoff` under which its authority resolutions were actually performed. A delayed worker, attestation, or submission retains that original trusted correction cutoff but cannot make the successor visible before the later atomic durable acceptance that actually recorded it. Replay first filters by trusted correction decision and durable-knowability coordinates and only then chooses the highest eligible generation. Current wall-clock time, proposal time, attestation time, caller/worker-provided timestamps, substituted cutoffs, or latest repository state can never substitute for the accepted coordinates.

Ordinary worker retry is not re-validation. Explicit re-validation receives a new event/cutoff and fresh profile and Source Handling resolution and fresh capabilities; it never rewrites history.

### 11. Correction and concurrent supersession

Corrections are append-only and non-branching. Each names the exact current predecessor and next generation. A fresh governed correction decision must first use the trusted correction allocator to claim that predecessor/generation and receive exactly one `correction_decision_id`/`correction_cutoff`; concurrent workers cannot create sibling cutoffs for the same claimed generation. Workers may only resume the allocated decision. The state-compatible success or refusal attestation binds the allocation but cannot choose or change it.

Persistence uses atomic compare-and-set so concurrent sibling successors cannot both succeed and, in that same successful append, assigns the immutable trusted `correction_recorded_at`. Strict-known historical reads first exclude successors whose trusted durable `correction_recorded_at` is later than the requested replay cutoff or whose fresh decision/authority lineage does not match the allocator-issued correction cutoff; only then do they choose the highest eligible correction generation. Thus generation orders eligible successors but never proves historical knowability by itself.

### 12. Validation dimensions

The validator may decide only profile-encoded and evidence-supported dimensions such as syntax, schema/shape, requested-output conformance, required/forbidden fields, bounded type/range/enum constraints, lineage consistency, evidence-reference structural integrity, partial/missing response classification, and explicitly assigned forbidden-capability structure checks.

It cannot decide source truth, claim truth, valuation truth, ranking, opportunity, recommendation, or canonical promotion.

### 13. Downstream stop boundary

A later extraction/knowledge-proposal service may consume only states allowed by its own separately governed contract, normally `VALID`, while carrying exact validation identity/lineage. `ResponseValidator` creates no extraction proposal and performs no canonical promotion.

## Falsification and Hostile Cases

| Scenario | Required result |
|---|---|
| Caller supplies permissive rules | rejected; canonical profile authority resolves policy |
| Validator tries to publish/select its own ungoverned profile | rejected; rule publication belongs to profile authority |
| Upstream requested-output owner tries to define validator-only policy without new authority | rejected; upstream identity remains an input only |
| Persistence marks a profile canonical/applicable | rejected; storage is non-authoritative |
| Current profile differs from historical profile | replay uses historical cutoff resolution |
| Source Handling became restrictive after attempt | validation-time re-resolution blocks/reclassifies; attempt `ALLOW` ignored |
| Two workers start the same base validation | one event ID and cutoff; both cannot create distinct subjects |
| Worker crashes/retries | rejoins the same event/cutoff |
| Profile resolution unavailable | `RULE_UNAVAILABLE` refusal without fake profile resolution |
| Source Handling restrictive/unresolved | `SOURCE_HANDLING_BLOCKED` with restrictive/failed-resolution provenance, no fake `ALLOW` |
| Direct repository write submits canonical-looking `VALID` | rejected without validator success attestation |
| Refusal attestation is used for `VALID` | rejected |
| Later profile/Source Handling substituted into replay | rejected |
| Worker/caller proposes a correction cutoff | rejected; fresh governed correction cutoffs come only from the trusted correction allocator |
| Worker allocates at T2 but supplies/backdates T1 | rejected; authorization/attestation/record must bind the allocator-issued T2 cutoff |
| Correction allocation is created, then worker/attestation/submission is delayed | all correction-time authority uses the original allocated cutoff; successor remains invisible until later durable acceptance |
| Correction uses a different cutoff than its allocation | rejected before persistence |
| Correction attestation is minted, then submission is delayed past a historical replay cutoff | excluded until persistence's later atomic durable-acceptance timestamp; attestation time cannot backdate knowability |
| Caller proposes a backdated `correction_recorded_at` | rejected; persistence assigns the trusted value itself |
| Allocated correction cutoff predates predecessor durable acceptance | allocation/append fails closed as chronology-invalid |
| Allocated correction cutoff is later than successor durable acceptance | append rejected as chronology-invalid |
| Correction created after a historical replay cutoff has higher generation | excluded by trusted `correction_recorded_at`; it cannot leak backward into that replay |
| Correction-time governed decision uses a later/current authority instead of its allocated cutoff | rejected; resolution, replay, and attestation bind the trusted allocated cutoff |
| Two correction workers race | at most one predecessor/generation allocation exists; workers join/lose it, and CAS permits at most one durable successor |
| Transport succeeds with wrong output contract | deterministic semantic invalid state, never implicit `VALID` |
| Validation succeeds | grants no canonical truth or promotion authority |
| Legacy artifact is presented as validated | rejected; no synthetic relabelling/backfill |
| #315 is assumed solved | rejected; remains separate |

## Mandatory Conformance Cases

A future ADR and implementation must mechanically prove at minimum:

1. transport success alone cannot produce `VALID`;
2. caller-supplied profile/schema/rules cannot become canonical validation policy;
3. only `ResponseValidationProfileAuthority` publishes/resolves canonical profile history;
4. `ResponseValidator` cannot publish an ungoverned profile and then attest under it;
5. upstream requested-output/schema authority remains authoritative only for its own contract and cannot acquire validator-only policy by delegation without a new governed decision;
6. persistence cannot select profile applicability or become a policy bypass;
7. current profile/rules cannot substitute for historical resolution;
8. base concurrency yields one event and cutoff;
9. worker retry resumes the same event; explicit re-validation creates a new governed generation/event/cutoff;
10. every validation/re-validation independently resolves Source Handling at its event cutoff;
11. attempt-time `ALLOW` cannot authorize validation;
12. restrictive/unresolved Source Handling and unresolved profile authority persist truthful refusal evidence without fabricated successful identities;
13. validator authorization is single-use and non-caller-mintable;
14. transient validation persists zero prohibited response bytes/hash/size/content-derived IDs;
15. one event has at most one accepted base terminal record;
16. unknown validation states are rejected and simultaneous failures use canonical precedence;
17. canonical-looking direct persistence without the state-compatible validator attestation is rejected;
18. attestation reuse, record/event/subject substitution, or success/refusal substitution is rejected;
19. every fresh governed correction decision receives exactly one trusted allocator-issued `correction_decision_id` and `correction_cutoff` before authority resolution/worker execution; caller/worker/attestation proposed cutoffs are rejected;
20. retry/restart of the same correction decision resumes the same allocation, while a genuinely new decision requires a new predecessor/generation claim and cutoff;
21. every correction receives `correction_recorded_at` only from the same trusted atomic durable-acceptance operation that successfully appends it; caller/validator/attestation proposed timestamps cannot become authoritative;
22. delayed execution or submission cannot change the allocated correction cutoff and cannot make a correction knowable before durable acceptance;
23. persistence rejects allocation/predecessor chronology violations and enforces `predecessor durable-acceptance <= allocated correction_cutoff <= correction_recorded_at` whenever a fresh governed correction decision exists;
24. substituting a different correction cutoff into authority resolution, authorization, attestation, record, or replay is rejected;
25. a successor durably recorded after a replay cutoff is excluded even when it has the highest generation;
26. correction-time current authority cannot substitute for authority knowable at the allocator-issued correction cutoff;
27. corrections are append-only and allocation plus CAS prevents sibling decision/successor branches;
28. strict-known replay never invokes provider/network or substitutes current authority;
29. `VALID` grants no truth/promotion authority and cannot create extraction proposal;
30. legacy artifacts cannot be retroactively accepted as validation records;
31. Issue #315 remains independently unresolved unless explicitly completed;
32. deliberately weakening each reusable authority, replay, event-allocation, trusted correction-decision cutoff allocation, persistence-owned correction timestamp, correction chronology, Source Handling, durability, precedence, or attestation guard makes its named regression fail.

## Persistence, Security, and Privacy

Validation-derived excerpts, diagnostics, normalized values, hashes, sizes, and content-derived identifiers are independently governed durability categories. Processing permission never grants persistence permission. Credential-bearing response material rejected by Phase B cannot be laundered into durable validation evidence.

Profile/event/correction-allocation/authorization/attestation records are operational authority artifacts, not content-retention workarounds. They may bind identities, decisions, trusted cutoff/storage-time coordinates, or governed missingness but may not encode prohibited response content.

## Legacy, Migration, and Rollback

Legacy provider/extraction history remains explicitly unvalidated. No backfill may fabricate profile resolutions, Source Handling decisions, events, correction allocations, authorization, attestations, correction-time coordinates, or validation records.

Migration is additive. Existing Model Adapter identity does not change. Before activation, downstream consumers that require validated responses must opt into the new validated-response handoff and reject legacy-unvalidated state.

Before activation, rollback is simply non-activation. After append-only validation history exists, rollback stops new production use but never deletes or rewrites history.

## Operational Quality

Validation is local and provider-free. Validator failure remains an explicit closed state and never creates provider retry authority. Atomic base/correction allocation prevents worker restarts from accidentally becoming re-validation or silently selecting a new historical cutoff. Observability is bounded by Source Handling and credential safety. Availability failure cannot default to `VALID`.

## Open Questions

Non-blocking implementation details include exact parser/schema library, physical database schema/indexes for allocation/CAS, durable diagnostic category mapping, concrete opaque/cryptographic capability mechanism, and future shared-core admission if ADR 0032 later obtains independent multi-consumer evidence.

The canonical top-level vocabulary, precedence, dedicated profile authority recommendation, event-before-cutoff rule, trusted correction-decision cutoff allocation, validation-time Source Handling, retry/re-validation distinction, persistence-owned correction knowability and chronology, and state-compatible success/refusal attestation split are not implementation defaults; they are architecture decisions subject to targeted independent re-audit before ADR drafting.

## Constitution and Governance Review

The design remains evidence-first and fail-closed. Unknown validity remains unknown; provider output is not promoted because it arrived; prohibited evidence is not reconstructed; unresolved authority is recorded as explicit missingness. No trading, portfolio, recommendation, or autonomous-action authority is introduced.

This contribution is architecture preparation only. It changes no runtime code and accepts no ADR. PR #321 supplied the profile-authority correction; PR #325 hardens the post-merge replay-coordinate correction. The correction must pass exact-head checks and independent review before the targeted F-001 re-audit begins. Merge remains owner-only.

## Quality Assessment

Ratings use the repository scale: `EXCELLENT`, `GOOD`, `ACCEPTABLE`, `NEEDS_IMPROVEMENT`, `UNACCEPTABLE`.

| Dimension | Rating | Evidence and rationale | Blocking limitation |
|---|---|---|---|
| Problem correctness | EXCELLENT | ResponseValidator gap remains explicitly downstream of ADR 0034 capture | None identified |
| Scope completeness | GOOD | validation, profile ownership, replay, persistence, transient input, refusal, trusted correction decision/knowability/chronology, downstream stop, #315 separation explicit | runtime details deferred |
| Canonical consistency | GOOD | ADR 0031/0032/0033/0034/0020/0016/0009 reconciled without widening their owners | targeted re-audit required |
| Evidence integrity | GOOD | exact merged correction baseline and governing issues/PRs recorded | final review must bind exact head |
| Assumption discipline | GOOD | profile-owner choice and correction replay both have explicit hostile/falsification cases | future shared-core evidence may change recommendation |
| Option completeness | GOOD | execution placement and profile ownership are separate decision dimensions; B1-B5 cover dedicated, validator, upstream, persistence, and shared authority models | targeted auditor must confirm no material owner class omitted |
| Comparative fairness | GOOD | all profile-owner models use authority separation, ownership fit, history, replay, correction, anti-forgery, governance, complexity, migration, reversibility | quantitative cost not meaningful here |
| Falsifiability | EXCELLENT | dedicated-authority recommendation and correction-time replay semantics have explicit disconfirming/adversarial cases | runtime mutation proof waits for implementation |
| Authority and ownership clarity | EXCELLENT | rule-maker, executor, event/correction allocator, Source Handling, transport, persistence, and promotion are separated | targeted audit must close F-001 |
| Persistence and replay quality | EXCELLENT | append-only profile history, strict-known resolution, base/correction allocation, persistence-assigned correction acceptance time, chronology, CAS, attestation, transient non-retention explicit | physical schema deferred |
| Evidence and provenance quality | GOOD | capture/attempt/build/event/profile/Source Handling/correction-decision/correction-time coordinates required | claim truth remains correctly out of scope |
| Operational quality | GOOD | provider-free local validation, fail-closed availability, retry/re-validation/correction-resume distinction | SLOs deferred |
| Implementation and migration impact | GOOD | additive migration and explicit authority/event/allocation/record/capability surfaces | effort estimate deferred |
| Testability and validation | EXCELLENT | hostile cases cover profile-owner bypasses, untrusted/backdated cutoff, delayed/backdated correction leakage, chronology, concurrency/replay/attestation cases | implementation tests not yet authorized |
| Maintainability and extensibility | GOOD | Hunter-local authority now; shared extraction deferred by ADR 0032 | future consumer evidence may justify supersession |
| Risk quality | GOOD | authority concentration, upstream widening, repository laundering, temporal replay leakage, cutoff forgery, privacy, race, premature abstraction risks explicitly mitigated | residual implementation risk remains |
| Traceability | GOOD | #316, #318, #319 F-001, #320, PR #321, PR #325, merged baseline, and exact-head P1 lineage are explicit | ADR not yet created |

No mandatory quality dimension is below `ACCEPTABLE`. This self-assessment permits **targeted re-audit**, not ADR drafting.

## Architecture Readiness

- Outcome: `READY_FOR_REVIEW` for v1.5 correction.
- The previously omitted profile-authority option space remains explicit and normalized.
- Dedicated `ResponseValidationProfileAuthority` remains recommended after comparison, not by assumption.
- Fresh governed corrections now allocate their decision ID/cutoff at the trusted `ResponseValidator` correction-allocation boundary before worker execution; caller/worker/attestation cutoff injection is prohibited.
- Correction records use the trusted atomic durable-acceptance timestamp required to establish historical knowability before generation ordering is applied; proposed/backdated timestamps cannot substitute.
- Correction chronology is mechanically constrained against predecessor durable acceptance, allocator-issued decision cutoff, and durable acceptance.
- Source Handling remains ADR 0033-owned; Model Adapter remains transport/capture-only; persistence remains non-authoritative except for its mechanical trusted storage-time fact; promotion remains downstream.
- Event-before-cutoff allocation, retry vs re-validation/correction-resume, truthful unresolved-authority refusal, anti-forgery attestation, closed vocabulary, and strict-known replay remain explicit.

## ADR Readiness

- Outcome: `TARGETED_REAUDIT_REQUIRED`.
- ADR drafting is prohibited until a targeted independent audit verifies `F-001` is closed on the exact merged v1.5 correction and returns the canonical readiness verdict permitted by the audit protocol.
- Proposed ADR title remains: Evidence Intelligence ResponseValidator Boundary.

## Decision History

| Date | State | Change | Author or reviewer |
|---|---|---|---|
| 2026-08-24 | READY_FOR_REVIEW | Initial preparation from post-PR #314 architecture | OpenAI GPT-5.6 Sol |
| 2026-08-24 | READY_FOR_REVIEW | v1.1 established profile authority, validation-time Source Handling, transient authorization, closed outcomes, and persistence anti-forgery | OpenAI GPT-5.6 Sol |
| 2026-08-24 | READY_FOR_REVIEW | v1.2 completed auditable coordinates, precedence, quality ratings, and traceability | OpenAI GPT-5.6 Sol |
| 2026-08-24 | READY_FOR_REVIEW | v1.3 allocated/deduplicated validation events before cutoff and split success from unresolved-authority refusal attestation | OpenAI GPT-5.6 Sol |
| 2026-08-24 | READY_FOR_REVIEW | v1.4 independently evaluated/normalized profile-authority ownership models to address PR #319 F-001 | OpenAI GPT-5.6 Sol |
| 2026-08-24 | READY_FOR_REVIEW | v1.5 restores correction replay coordinates and hardens them to trusted pre-worker correction-decision cutoff allocation plus persistence-assigned atomic durable acceptance with enforced predecessor/decision chronology after PR #321/PR #325 exact-head review | OpenAI GPT-5.6 Sol |

## Traceability

- Preparation Issue: #316
- Independent audit Issue: #318
- Blocking audit contribution: PR #319, merged at `5840849d81039ba4bd3dff5910db2907c1ff2780`
- Blocking finding: `F-001`, Class C
- Correction Issue: #320
- Profile-authority correction PR: #321, merged at `8ee6fd57577fa322b87cba21bd381d05770edd29`
- Replay-coordinate correction PR: #325
- Post-merge review finding: PR #321 P1 — restore correction time coordinates for strict-known replay
- Follow-up exact-head review findings on PR #325: persistence-owned durable-acceptance time/chronology; trusted correction-cutoff allocation
- Related follow-up: #315 (separate)
- ADPR: `ADPR-0010` v1.5
- ADR: not yet created
- Runtime implementation: not authorized
- Release: not assigned

## Immutability and Supersession

After `APPROVED`, this record becomes historical evidence. Substantive later changes require a governed superseding decision. Until approval, corrections remain auditable through version, issue, PR, commit, and independent-audit lineage.