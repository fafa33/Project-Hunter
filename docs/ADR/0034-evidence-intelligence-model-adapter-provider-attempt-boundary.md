# ADR 0034: Evidence Intelligence Model Adapter and Provider Attempt Boundary

## Status

Proposed.

## Date

2026-08-22.

## Governing Preparation

[ADPR-0009](../architecture-records/ADPR-0009-evidence-intelligence-model-adapter.md) — Evidence Intelligence Model Adapter and Provider Attempt Boundary.

The independent architecture audit of that preparation record, `docs/ARCHITECTURE_AUDITS/adpr-0009-model-adapter-independent-audit.md`, returned final verdict `READY_FOR_ADR` with no blocking finding and permitted clean progression to a dedicated ADR 0034 drafting lifecycle. That audit merged into `main` through PR #290 as commit `09603c7b0ee3076f902190a0c1c1d223f9d71d8b`. The audited preparation record itself merged through PR #288 as commit `cd1ef1981975f15dd26d48031b00c8b55c28f3d5`.

This ADR formalizes the architecture ADPR-0009 recommended and the audit validated. It introduces no materially new architecture beyond that audited basis. It is drafted under Issue #299.

`Proposed` status means this decision is not yet binding. Acceptance is a separate owner decision and, per `docs/ADR/README.md`, acceptance of architecture is not authorization of runtime implementation.

## Context

Accepted ADR 0031 establishes Hunter's provider-independent pre-model foundation and ends it deliberately at two immutable artifacts: `EvidencePromptArtifact`, the exact canonical pre-model payload, and `EvidencePreModelBuildRecord`, the immutable record of one completed or failed build. ADR 0031 explicitly defers model invocation, provider attempts, provider request artifacts, response semantics, provider routing, credentials, retries, quotas, billing, and response validation to later architecture. That runtime exists today in `src/hunter/evidence_intelligence/pre_model.py` and `pre_model_persistence.py`.

Accepted ADR 0033 assigns exclusive ownership of authoritative source-handling facts and of governed source-handling policy to the Evidence Intelligence consumer-side Source Handling Authority, and names any future Model Adapter component a consumer only. Its runtime mechanics exist in `src/hunter/evidence_intelligence/source_handling.py`, which derives separate processing, retention, reconstruction, access, and deletion/lifecycle decisions, binds durable dispositions to a versioned field-category registry, resolves strict-known authority at an explicit cutoff, and rederives every handling decision independently at persistence rather than trusting a caller-supplied decision.

Accepted ADR 0032 establishes the project-neutral ownership boundary and its evidence-gated admission rule. It explicitly withholds provider/model selection, routing, credentials, invocation, and response validation from the project-neutral core, and requires demonstrated evidence from at least two independent consumers before a concrete shared contract is admitted.

Accepted ADR 0009 separates provider integration, service authority, repository mechanics, and persistence. Accepted ADR 0020 binds strict-known historical selection and prohibits current/latest fallback during historical reconstruction. Accepted ADR 0016 establishes that the existence of an AI artifact, a successful provider call, or a successful validation does not promote output to canonical authority.

Separately, `src/hunter/evidence_intelligence/provider.py` contains an older execution path: the `AIExtractionProvider` protocol, an `ExtractionRequest` carrying `document_id`, spans, and schema rather than a prompt artifact, a `SecureAIProviderRunner` with health, error, and prompt-injection handling, and direct persistence of `AIProviderArtifact` and `ExtractionProposal`. That path predates the ADR 0031/0033 handoff. It cannot prove which exact canonical prompt content was sent, has no provider-request artifact linked to an `EvidencePromptArtifact`, and creates proposals directly, crossing the Model Adapter/Response Validator separation ADR 0031 now requires.

What Hunter therefore lacks is an accepted architecture for one specific boundary: converting a completed, authorized pre-model build into an exact provider request, executing a model attempt, recording attempt, authorization, delivery, failure, and response lineage, and handing captured response evidence to a later validator — without letting the provider, the transport, the caller, or a successful network call acquire authority it must not have.

## Problem

Without an accepted boundary, five failure modes are architecturally reachable:

1. **Opaque execution.** A response exists without proof of the exact canonical prompt and exact provider request that produced it where such proof is durably authorized, and without an explicit governed unavailability state where it is not.
2. **Authority laundering.** Provider choice, transport transformation, a caller flag, a prior build-time `ALLOW`, or the mere success of a network call silently acquires authority over source handling, prompt content, durable retention, response validity, or canonical promotion.
3. **Historical false replay.** A later provider re-call is mistaken for reconstruction of an original attempt, or current provider configuration, current policy, or current model capability is substituted for historically unavailable state.
4. **Revocation race.** Handling authority changes between a loose pre-send check and the actual network handoff, so bytes are transmitted under authority that no longer holds.
5. **Duplicate or unknown execution.** A crash, timeout, or ambiguous transport error after the provider may have accepted a request lets a blind retry issue a second billable invocation while the first remains unresolved.

The decision required is therefore who owns model-attempt semantics, what the adapter contract is neutral over, what immutable records represent execution, how Source Handling applies atomically at the send boundary, how uncertainty and persistence failure behave, and where this boundary ends.

## Decision

### Canonical ownership

Hunter creates an **Evidence Intelligence Model Adapter** boundary. The canonical adapter contract is owned by Hunter Evidence Intelligence. It is not owned by the ADR 0032 project-neutral core, and it is not owned by any provider-specific transport.

The Model Adapter is the sole owner of canonical semantics for:

- execution-profile identity;
- provider-request lineage and any durable provider-request evidence;
- model-attempt identity and lineage;
- attempt-time Source Handling enforcement at the model boundary;
- immutable single-use handoff semantics;
- attempt outcome, delivery-certainty, and failure lineage;
- response-capture lineage and any durable provider-response evidence;
- the canonical persistence semantics of those record families, where their durable categories are authorized.

The Model Adapter is a **consumer** of Source Handling Authority. It resolves, enforces, and records handling authority; it never creates, selects, overrides, substitutes, or extends it. ADR 0033 remains the sole owner of source-handling facts and policy, and is reaffirmed, not amended, by this decision.

### Provider transport boundary

A provider-specific transport is a subordinate implementation detail of the Model Adapter. It owns provider-specific transformation and network mechanics only, and may:

- deterministically translate the adapter's canonical prompt content and execution-profile inputs into a **non-secret transport transformation result** held in memory;
- construct the provider-specific wire request;
- perform the network invocation, and only when the Model Adapter supplies a valid single-use dispatch handoff;
- carry provider correlation and idempotency mechanics the provider itself defines;
- normalize a transport-level result — raw response payload, transport error class, correlation metadata — and return it to the Model Adapter.

A transport must not own:

- canonical Hunter artifact authority, including creating or persisting any record family named in this ADR;
- Source Handling decisions, dispositions, or retention outcomes;
- response truth or validation;
- canonical knowledge promotion;
- provider or model routing authority;
- repository authority;
- context selection, prompt content, or any mutation of `EvidencePromptArtifact`;
- capability-constraint selection or silent capability substitution;
- approval of tools or autonomous model actions;
- any Hunter governance state.

A transport returns evidence. The Model Adapter decides what that evidence means and what, if anything, may become durable.

### Upstream prompt identity

`EvidencePromptArtifact` and `EvidencePreModelBuildRecord` remain immutable and upstream-owned. The Model Adapter consumes them. It may not mutate them, re-canonicalize them, reinterpret their identity, or rewrite their historical meaning.

Capability constraints are upstream build inputs. If the execution profile's capability contract does not exactly satisfy the capability-constraint identity recorded by the governing `EvidencePreModelBuildRecord`, invocation fails closed with a `CAPABILITY_UNSUPPORTED` outcome. A provider or model choice that changes the effective capability constraint requires a new upstream allocation, prompt, and build identity. Silent reuse of an incompatible pre-model build is prohibited.

### Execution profile

The Model Adapter owns an immutable, versioned `ModelExecutionProfile` identity sufficient to establish the exact execution semantics relevant to an attempt without persisting credentials. It identifies at least:

- profile identity and schema version;
- provider transport identity and version;
- the provider and model identifier, version, and endpoint-class identity that are safe to persist;
- request protocol identity and version;
- the required capability-constraint identity or exact compatibility requirement;
- deterministic non-secret request parameters that affect model execution;
- prohibited capabilities and tools;
- response-format expectation identity;
- provider idempotency/correlation capability classification, as `SUPPORTED`, `UNAVAILABLE`, or an explicit unknown state that blocks retry.

A profile is immutable. A changed parameter is a new profile version, never an edit of an existing one.

The first authorized architecture supports **at most one explicitly configured execution profile at a time**. The authorized caller or workflow is bound to that one profile and the adapter verifies it. Dynamic selection among profiles is not part of this boundary.

Credentials are not part of a profile. A non-secret credential-slot or configuration identity may exist where separately authorized; secret values never do.

### Provider request representation and evidence

The transport transformation result is a transient, non-secret, in-memory representation. It is not a Hunter durable artifact and carries no Source Handling or persistence authority.

Provider-specific transformation must never mutate `EvidencePromptArtifact`. Where the exact provider-facing representation differs from the canonical prompt artifact, it is a **distinct representation**, not a new version of the prompt.

Where durable request evidence is authorized, the **Model Adapter**, never the transport, constructs and persists a `ProviderRequestArtifact` through the mechanical repository boundary. Subject to per-category authorization it may link and record:

- `EvidencePreModelBuildRecord` identity;
- `EvidencePromptArtifact` identity;
- `ModelExecutionProfile` identity and version;
- transport and request protocol identity and version;
- exact non-secret provider-facing message or body bytes, **only when exact-content retention is authorized**;
- content hashes, measured size, encoding or canonicalization metadata, or any other content-derived field, **only when that specific field's durable category is authorized**;
- a content-derived deterministic request identity, **only when its durable category is authorized**;
- explicit per-category reconstruction availability or unavailability with stable reason codes.

A hash is not automatically safe merely because it is a hash. No digest, measured size, canonical-byte fingerprint, or content-derived identifier is presumed retainable because model processing was allowed. `processing_decision == ALLOW` grants no durability whatsoever; durability comes only from the exact durable disposition of each field's category, resolved against the exact historical field-category registry.

When durable request categories are denied, live transmission may still proceed if processing is authorized, but no prohibited evidence is persisted. The attempt instead records an authorized non-content-derived request-evidence outcome such as `REQUEST_EVIDENCE_UNAVAILABLE_BY_POLICY`. If even that metadata category is not authorized, no substitute identifier is fabricated. Explicit governed unavailability is the recorded state; regeneration of prohibited evidence from current state at any later time is prohibited.

A durable request artifact, where one exists, is always semantically distinct from `EvidencePromptArtifact`, even when its authorized content mapping is byte-equivalent. Where no durable request artifact is permitted, that absence is explicit and never collapses back into the prompt artifact.

### Attempt-time Source Handling

Every live provider invocation and every retry is a **new processing event**, not a continuation of an earlier authorization.

Two Source Handling coordinates exist and must never be conflated:

1. **Build lineage coordinate.** The build cutoff and the authority resolved at it are historical lineage and reconstruction evidence only. They authorize nothing prospectively.
2. **Attempt authorization coordinate.** Before each send, the Model Adapter performs strict-known Source Handling resolution for the governed subject using the new attempt's own effective context and **attempt cutoff**, admitting only authority applicable and knowable at that cutoff. The live send is authorized only by this resolution.

None of the following may ever authorize a later provider send: a build-time `ALLOW`; an earlier attempt's `ALLOW`; a cached decision; a caller-supplied decision, flag, classification, or decision identity; the presence of prompt bytes; or the fact that a previous attempt succeeded.

Any unknown, missing, unavailable, ambiguous, conflicting, or otherwise unresolved required authority blocks model-facing processing. Blocking is the outcome; a permissive default, a partial classification treated as permissive, or a fallback to current state is prohibited. A restrictive successor applicable and knowable at the attempt cutoff must be admitted and must block.

### Durable-before-send attempt

Every possible external invocation begins with an immutable `ModelAttemptRecord` **durably established before any network transmission**. If the attempt cannot be durably recorded, no send occurs.

The attempt record establishes identity and lineage, not result. Subject to durable dispositions it links at least:

- execution owner identity, subordinate to the existing canonical run/workflow/task owner;
- pre-model build identity;
- prompt artifact identity;
- provider-request artifact identity where one durably exists, otherwise the explicit authorized request-evidence-unavailable outcome and reason;
- execution profile identity and version;
- attempt ordinal;
- predecessor attempt identity where the attempt is a retry;
- attempt effective context and attempt cutoff, and the build cutoff, as distinct coordinates;
- recorded creation time;
- provider idempotency capability classification and, where supported and safe to persist, an opaque attempt-scoped idempotency or correlation slot.

The attempt record is immutable. It is never mutated into a terminal state.

Its identity and lineage are deterministic from canonical inputs under Hunter's control. Remote model output is **not** deterministic, is not under Hunter's control, and is never described or recorded as deterministic. Determinism claims apply to Hunter-side identity, canonicalization, and record construction only.

### Single-use handoff

Before any network call, the Model Adapter establishes an immutable `ModelHandoffRecord`. The handoff is **execution evidence, not Source Handling authority**: its facts and decisions are valid only because they are bound to the exact canonical Source Handling resolution at the attempt cutoff.

The handoff binds, subject to per-category authorization:

- handoff identity and schema version;
- the exact pre-send `ModelAttemptRecord` identity;
- build, prompt, and execution-profile identities;
- the durable `ProviderRequestArtifact` identity where permitted, otherwise the explicit request-evidence-unavailable state;
- the exact fact, policy, field-category-registry, authorization-rule, and required provenance identities resolved for this attempt;
- the attempt effective context and strict-known attempt cutoff;
- `processing_decision == ALLOW`;
- the exact durable-disposition identities and results applicable to request and response categories;
- an opaque single-use dispatch capability identity that is not content-derived, persisted only where its metadata category is authorized;
- the dispatch-validity or expiry bound required by the implementation contract.

The loose pattern `resolve, then later send` is prohibited. Handoff creation and authority resolution must occur from one serializable or atomic Source Handling snapshot, or an equivalent Source Handling-issued snapshot or capability primitive. Where the storage or authority substrate cannot provide that guarantee, Model Adapter activation remains blocked rather than downgraded.

Dispatch consumes the handoff **single-use**, through a unique constraint, compare-and-set, or transactional-outbox equivalent, so that two workers cannot dispatch the same attempt twice. A stale, mismatched, expired, already-consumed, or concurrently consumed handoff is rejected deterministically.

A transport never holds independent authorization authority. It can send only what a valid, matching, unconsumed handoff authorizes.

A restrictive successor already applicable and knowable at the handoff cutoff must be included and can block handoff creation. A successor that becomes applicable or knowable only after the handoff's committed cutoff does not retroactively rewrite that already-created execution evidence; every retry or new attempt creates a new handoff and therefore observes the later authority. This defines the atomic authorization point instead of pretending Hunter can continuously re-check authority during an external network call.

### Append-only attempt outcome

Terminal execution state belongs to a separate, append-only `ModelAttemptOutcomeRecord` family. The pre-send attempt is never updated in place, and no outcome record is mutated after it is written. A correction is a new record with explicit lineage, never a rewrite.

An outcome links the attempt and handoff identities and records terminal or uncertainty state, the response-artifact identity where one exists, governed provider correlation metadata, timestamps, and per-category reconstruction availability.

The outcome family must distinguish at least:

| Outcome | Meaning |
|---|---|
| `SUCCEEDED_TRANSPORT` | A response was received and captured. This is transport evidence only and says nothing about semantic validity. |
| `PROVIDER_REFUSED` | The provider explicitly refused or blocked the request. |
| `PROVIDER_UNAVAILABLE` | The provider or endpoint was unavailable. |
| `TIMEOUT_CONFIRMED_NO_DELIVERY` | A timeout occurred and the provider contract proves no dispatch or acceptance took place. |
| `DELIVERY_UNKNOWN` / `OUTCOME_UNKNOWN` | Delivery or result is genuinely uncertain; the provider may have accepted the request. |
| `RATE_LIMITED` | The provider rate-limited the request, recorded with explicit delivery certainty. |
| `QUOTA_UNAVAILABLE` | Quota was exhausted or unavailable. |
| `BILLING_UNAVAILABLE` | Billing or account state prevented execution. |
| `CAPABILITY_UNSUPPORTED` | The profile capability contract did not satisfy the build's capability constraint, or the provider rejected the capability. |
| `MALFORMED_TRANSPORT_RESPONSE` | A transport result was received but was malformed or partial. |
| `SECURITY_BLOCKED` | An adapter security control blocked the operation. |
| `SOURCE_HANDLING_BLOCKED` | Attempt-time handling authority did not permit model-facing processing. |
| `RESPONSE_CAPTURED_PERSISTENCE_FAILED` | Response capture succeeded but canonical terminal persistence failed. |
| `INTERNAL_ADAPTER_ERROR` | An adapter-internal error occurred. |

Materially different failure states must not be collapsed into a generic failure. In particular, execution failure, response-capture success followed by persistence failure, and uncertain execution or delivery are three distinct states and must remain distinguishable in the recorded outcome family.

### Uncertain delivery, idempotency, and retry

Where a timeout, connection loss, process crash, or ambiguous provider error after dispatch means the provider may have accepted the request, Hunter records `DELIVERY_UNKNOWN` / `OUTCOME_UNKNOWN`. Such a state is never classified as safe non-delivery.

An uncertain attempt is **not automatically retryable**. Recovery must first reconcile using provider correlation, status, or idempotency facilities where the execution profile classifies them as available. Where reconciliation cannot establish non-delivery or a definitive terminal result, the attempt remains unknown and automated retry stays blocked; a separately governed operator or policy decision may later authorize a new attempt.

Automatic retry may proceed only from outcomes whose semantics prove that no provider execution occurred, or after reconciliation establishes a safe retry condition. Rate-limit and validation errors are not assumed to be non-delivery unless the provider contract proves that property. An `UNAVAILABLE` or unknown idempotency classification never defaults to retry-safe.

**A retry is always a new `ModelAttemptRecord`** with its own attempt cutoff, its own Source Handling resolution, its own handoff, and explicit predecessor lineage. It is never a mutation, reuse, or re-dispatch of a previous attempt, and it never inherits the predecessor's authorization.

Crash recovery scans durable pre-send attempts that lack a terminal outcome and reconstructs them as uncertain pending reconciliation. A nonterminal attempt surviving a crash is evidence of uncertainty, never permission to retry, and is never rewritten into success or failure for convenience.

### Response-capture persistence failure

Where a provider response was captured but durable response or outcome persistence fails, Hunter must not call the provider again.

While the process is alive it records `RESPONSE_CAPTURED_PERSISTENCE_FAILED` and retains response content only within the handling rules already authorized for that content. Where the canonical store is itself unavailable and therefore cannot record that failure, the durable pre-send attempt intentionally remains nonterminal; recovery treats it as `OUTCOME_UNKNOWN`, performs reconciliation, and never fabricates a terminal result.

Recovery and reconciliation operate on the recorded attempt identity. They never proceed as though the invocation had not happened. A later recovery record may state that exact response evidence is unavailable where it could not be lawfully or durably retained.

### Provider response evidence

A captured provider response creates a distinct `ProviderResponseArtifact`, or an explicit governed unavailable state, **under Model Adapter ownership**. The transport returns raw response evidence; it does not create canonical artifacts.

Subject to per-category authorization the adapter records:

- attempt and outcome identities;
- provider-request artifact identity where one durably exists, otherwise the authorized request-evidence-unavailable linkage;
- execution profile identity;
- exact response protocol and version;
- exact raw or canonical response bytes, only where retention of that category is authorized;
- a content hash, only where the hash's own durable category is authorized;
- measured size, encoding or canonicalization metadata, or other content-derived fields, only where each category is authorized;
- governed, safe provider finish and status metadata;
- per-category reconstruction availability or unavailability with stable reason codes.

Response durability is governed independently of request durability and independently of processing. A response that echoes protected source content is subject to the same per-category enforcement, and unresolved disposition fails closed.

Provider response content is **untrusted external data**. It may not request tools, repository or canonical-record writes, schema or migration changes, unauthorized retrieval, configuration or credential mutation, governance-rule changes, or any capability outside the authorized adapter contract.

### Credentials and secrets

Credentials, API keys, bearer tokens, authorization headers, cookies, client secrets, signing secrets, provider authentication material, and equivalent transport secrets are **structurally excluded** from every canonical record family defined by this ADR, from every canonical hash, and from adapter logs and diagnostics.

Structural exclusion means the canonical record construction path cannot represent such material, not that a convention discourages it. Documentation alone does not satisfy this decision. The implementation must place a structural boundary — type, constructor, or equivalent enforced construction path — between credential material and canonical request, attempt, handoff, outcome, and response records, and that boundary must be provable by test rather than by review.

Where a credential's existence must be represented, only a non-secret credential-slot or configuration identity may appear, and only where that category is separately authorized. Secret values never appear. Secret- or credential-derived secondary representations, including digests of secret material, are equally excluded.

### Response authority boundary

Provider success is **transport evidence only**. It does not establish response truth, extraction validity, evidence validity, canonical claim authority, or canonical knowledge promotion authority. Persisting response bytes does not make them an `ExtractionProposal` and does not validate schema, citations, truth, evidence use, prohibited capabilities, or domain eligibility.

The Model Adapter terminates after transport-level response capture, normalization, and authorized outcome and response persistence. A separately governed future `ResponseValidator` consumes response evidence and may validate syntax, schema, prohibited capabilities, evidence references, and proposal eligibility. Only after that separate boundary may Evidence Intelligence produce an `ExtractionProposal` through its existing authorized service path.

`ResponseValidator` is neither defined, implemented, nor architecturally absorbed by this ADR. Its architecture requires a separate governed decision.

### Routing boundary

Multi-provider or multi-model ranking, scoring, failover selection, optimization, cost routing, quality routing, health-based selection, quota-based selection, and dynamic provider choice are **not part of this decision**.

The first implementation uses one explicitly configured execution profile per attempt and performs no selection among alternatives. Any future routing authority — covering selection inputs, cost, latency, quality criteria, failure fallback, capability compatibility, and deterministic audit — requires a separate ADPR and ADR or an explicit accepted amendment.

### Historical reconstruction versus re-invocation

Historical reconstruction of a build, request, attempt, handoff, outcome, or response uses only evidence that was durably authorized and actually persisted at the relevant historical cutoff, read under that artifact's recorded authority state.

Current provider configuration, current policy, current model capability, current request transformation, current registry state, and a fresh provider invocation must never be substituted for unavailable historical state. Where exact bytes, hashes, measured sizes, or content-derived identifiers were not durably authorized, reconstruction is explicitly unavailable for those categories and is never regenerated.

Re-invoking a provider — even with an identical transient request representation, identical profile, and identical model identifier — is a **new live execution**, not replay. It creates a new attempt, resolves new attempt-time authority, and makes no claim of response equality with any prior attempt. A persisted pre-send attempt without a terminal outcome is reconstructed as uncertain state, never rewritten into success or failure.

### Legacy provider path

`AIExtractionProvider`, `SecureAIProviderRunner`, `AIProviderArtifact`, `AIProviderHealth`, and the `ExtractionProposal` records those produced are **historical/legacy execution architecture**. They are not the Model Adapter and are not retroactively promoted into it.

Coexistence and migration rules:

- Existing legacy records retain their original schema, identity, and meaning. They are never relabeled, re-identified, or backfilled as though they possessed Model Adapter request, attempt, handoff, or outcome lineage they never captured.
- New Model Adapter record families coexist with legacy records rather than overwriting them. A unified audit view may present both, provided it states which lineage each record actually carries and marks legacy records as lacking the newer guarantees.
- The legacy runner is not adopted as the canonical Model Adapter, and its direct proposal creation is not adopted as the new rule. Its health, error-normalization, prompt-injection detection, and forbidden-capability checks may be reused later as implementation details behind the new boundary, or the path may be deprecated; either is a later governed change.
- Once the Model Adapter is authorized and active, new executions proceed through the new boundary. Any migration must preserve historical truth: no synthetic backfill, no retroactive lineage claim, and no rewrite of historical bytes or identities.

### Repository and persistence separation

ADR 0009 separation is reaffirmed. The Model Adapter owns execution semantics and decides what may become durable; repositories persist mechanically. A repository never becomes an authority: persistence code may not choose providers, alter prompts, validate meaning, or grant permissions.

Per ADR 0033, persistence independently resolves the authoritative historical handling facts and policy, rederives every relevant handling decision, verifies every durable payload element against those decisions, and rejects missing authority, mismatched inputs, mismatched decisions, or contradictory payload state. A Model Adapter-supplied decision is evidence to be re-verified, never a permission to be trusted.

### Governance isolation

No Model Adapter capability, provider transport, provider dependency, credential, model invocation, or LLM dependency of any kind may enter Hunter Governance Review or Hunter Merge Readiness. Those governance surfaces remain deterministic and independent of this runtime capability, and their availability must never depend on provider availability, quota, billing, or model behavior.

## Architectural Boundaries

```text
EvidencePreModelBuildRecord + EvidencePromptArtifact        (ADR 0031, immutable, upstream-owned)
  -> ModelAdapter
       -> capability compatibility check against ModelExecutionProfile   (fail closed)
       -> transport transformation result                                (transient, non-secret, no authority)
       -> attempt-time strict-known Source Handling resolution           (ADR 0033, attempt cutoff)
       -> ProviderRequestArtifact  OR  explicit request-evidence-unavailable state
       -> durable pre-send ModelAttemptRecord                            (immutable, before any network I/O)
       -> single-use ModelHandoffRecord                                  (atomic snapshot binding)
       -> transport network send                                         (consumes handoff exactly once)
       -> append-only ModelAttemptOutcomeRecord
          + ProviderResponseArtifact OR explicit response-evidence-unavailable state
  -> [boundary ends here]
  -> future ResponseValidator                                            (separately governed)
  -> ExtractionProposal via existing authorized Evidence Intelligence service path
```

## Canonical Record Families

| Family | Owner | Mutability | Purpose |
|---|---|---|---|
| `ModelExecutionProfile` | Model Adapter | Immutable, versioned | Exact non-secret provider/model/protocol/capability/configuration semantics for an attempt |
| `ProviderRequestArtifact` | Model Adapter | Immutable, conditional on durable dispositions | Authorized durable evidence of the provider-facing request representation, distinct from `EvidencePromptArtifact` |
| `ModelAttemptRecord` | Model Adapter | Immutable, durable before send | Attempt identity and lineage; never carries terminal state |
| `ModelHandoffRecord` | Model Adapter | Immutable, single-use | Attempt-bound authorization snapshot consumed atomically by dispatch |
| `ModelAttemptOutcomeRecord` | Model Adapter | Append-only | Terminal or uncertainty state, delivery certainty, failure lineage |
| `ProviderResponseArtifact` | Model Adapter | Immutable, conditional on durable dispositions | Authorized durable response evidence, or explicit unavailability |

Names above are architectural semantics. Concrete module paths, type names, storage schemas, and field spellings are implementation choices, provided the semantics and invariants of this decision hold.

## Design and Implementation Contract — Deferred

The following are deliberately outside this ADR and belong to later design, implementation, and provider-specific work. Every deferred item remains constrained by the invariants above; deferral is not permission.

- concrete Python module paths, type names, and storage or SQL layout;
- the exact closed vocabulary of outcome and reason codes beyond the families this ADR requires to be distinguishable;
- the concrete serializable transaction, capability, or transactional-outbox mechanism satisfying the atomic snapshot-to-handoff and single-use dispatch invariants;
- retry counts, timeouts, and backoff for outcomes already proven retry-safe;
- provider-specific idempotency, correlation, and reconciliation API mechanics;
- provider SDK or library choice and the identity of the first provider;
- response validation rules, which belong to the future `ResponseValidator`;
- the exact Source Handling field-category mapping for request and response derived metadata, and any registry extension required to express response-specific categories;
- future routing architecture;
- future ADR 0032 shared-core admission of any Model Adapter contract.

Three deferred items are hard runtime gates rather than open options. Before any provider activation, the implementation must prove the atomic snapshot-to-handoff and single-use dispatch mechanism; must validate that the governed field-category registry can express every request and response durable category the adapter needs, extending Source Handling design first where it cannot; and must classify each provider's idempotency, correlation, and reconciliation semantics.

## Conformance Obligations

The obligations below are permanent regression obligations for this boundary. Each must be encoded as a deterministic test that can prove or fail without a live provider. A later implementation that cannot encode CO-01 through CO-22 deterministically is not authorized for provider activation.

| ID | Obligation | Deterministic test that proves or fails it |
|---|---|---|
| CO-01 | Immutable upstream prompt identity | Given a completed build, running the adapter over an `EvidencePromptArtifact` leaves its content, hashes, measured size, and `artifact_id` byte-identical, and the `EvidencePreModelBuildRecord` identity unchanged. A profile whose capability contract does not exactly satisfy the build's recorded capability-constraint identity yields `CAPABILITY_UNSUPPORTED`, transmits nothing, and creates no substitute build. |
| CO-02 | Provider-neutral adapter ownership | The adapter contract compiles and its full attempt lifecycle executes against at least two distinct fake transports with materially different wire shapes, producing identical canonical record structure and identical Source Handling enforcement. No canonical record field is typed in provider-specific terms. |
| CO-03 | Provider-specific transport isolation | A fake transport attempting to construct or persist any of the six canonical record families, or to supply a Source Handling decision, disposition, or retention outcome, is rejected. Transport-returned data reaches persistence only through adapter-constructed records. Repository write paths reject any caller identity other than the adapter. |
| CO-04 | Immutable, versioned execution profile | Mutating any profile field after construction fails. Two profiles differing in any execution-relevant field have different profile identities. A profile carrying credential material fails construction. An attempt records the exact profile identity and version used. |
| CO-05 | Request representation separation | The transport transformation result is never persisted directly, and a `ProviderRequestArtifact`, where authorized, has an identity distinct from the `EvidencePromptArtifact` even when its authorized content mapping is byte-equivalent. Where no durable request artifact is permitted, the recorded state is the explicit unavailable outcome, and no code path substitutes the prompt artifact for it. |
| CO-06 | Strict-known attempt-time Source Handling | Build-time `ALLOW` followed by a restrictive successor applicable and knowable at the attempt cutoff blocks handoff creation and transmits zero bytes. Attempt 1 `ALLOW` followed by a restrictive successor before a retry blocks attempt 2. A caller-supplied decision, flag, classification, or decision identity that does not match independently rederived authority is rejected. Unknown, missing, ambiguous, or conflicting authority yields `SOURCE_HANDLING_BLOCKED`, never a permissive default. |
| CO-07 | Durable-before-send attempt | The transport's send entry point is unreachable until a `ModelAttemptRecord` is durably committed. Simulated persistence failure before send produces no network call. Any attempt to mutate a committed attempt record into a terminal state fails. Attempt identity is reproducible from canonical inputs; no test asserts determinism of remote model output. |
| CO-08 | Single-use handoff | Dispatch with a stale, mismatched, expired, already-consumed, or absent handoff is rejected. Two concurrent workers holding the same handoff produce exactly one dispatch. A handoff bound to a different attempt, profile, or request-evidence state is rejected. Authority resolution and handoff creation are proven to derive from one atomic snapshot, so an interleaved authority change cannot slip between check and send. |
| CO-09 | Append-only outcome records | The pre-send attempt is never updated in place. Every terminal state is a separate `ModelAttemptOutcomeRecord`. Mutating an existing outcome record fails. A correction appends a superseding record preserving predecessor lineage. Each outcome family in the outcome table is separately representable and round-trips distinctly. |
| CO-10 | Uncertain-delivery handling | A timeout, connection loss, or ambiguous provider error after dispatch, with idempotency `UNAVAILABLE` or unknown, yields `DELIVERY_UNKNOWN`/`OUTCOME_UNKNOWN`, never `TIMEOUT_CONFIRMED_NO_DELIVERY`. No automatic retry is emitted. Crash recovery over a nonterminal durable attempt reconstructs it as uncertain, never as failed-safe-to-retry and never as success. |
| CO-11 | Retry as new attempt | An authorized retry creates a new `ModelAttemptRecord` with a new attempt cutoff, a new Source Handling resolution, a new handoff, and explicit predecessor lineage. No code path re-dispatches, re-uses, or mutates a predecessor attempt or its handoff, and no predecessor authorization is inherited. |
| CO-12 | Idempotency, correlation, and reconciliation semantics | Where the profile classifies idempotency `SUPPORTED`, one stable opaque attempt-scoped key is used for reconciliation of that attempt and is not silently reused by a new attempt. Where classified `UNAVAILABLE` or unknown, retry is blocked until reconciliation establishes a safe condition. Reconciliation that cannot establish non-delivery or a definitive result leaves the attempt unknown and retry blocked. |
| CO-13 | Response-capture persistence failure | With response capture succeeding and terminal persistence failing, no second provider call is emitted; the outcome is `RESPONSE_CAPTURED_PERSISTENCE_FAILED`. With the canonical store unavailable, the pre-send attempt remains nonterminal and recovery classifies it `OUTCOME_UNKNOWN` without fabricating a terminal result. Execution failure, response-captured-persistence-failure, and uncertain delivery remain three distinguishable recorded states. Recovery operates on the recorded attempt identity. |
| CO-14 | Source Handling-controlled durable request evidence | With `processing_decision == ALLOW` while exact-content, `CONTENT_DERIVED_ID`, hash, or measured-size categories are denied, transmission may proceed but no prohibited bytes, digest, size, or derived identity is persisted, and the attempt record does not require a prohibited request-artifact identity. Denial of every request-evidence metadata category yields explicit unavailability with no fabricated substitute identifier. No later code path regenerates the prohibited evidence. |
| CO-15 | Source Handling-controlled durable response evidence | With a response echoing protected source content while response retention or hash categories are denied, only authorized metadata or an explicit unavailable state is persisted, and no prohibited response bytes or derived digest are written. An unresolved response-category disposition fails closed rather than defaulting to persist. |
| CO-16 | Structural secret exclusion | Constructing any of the six canonical record families with credential, API-key, bearer-token, authorization-header, cookie, or client-secret material is structurally rejected rather than sanitized. Secret- or credential-derived secondary representations, including digests, are equally rejected. A scan of durable payloads, adapter logs, and diagnostics for seeded secret values finds none. |
| CO-17 | `ResponseValidator` authority separation | The adapter never constructs an `ExtractionProposal`, never asserts response validity, and never promotes canonical knowledge. A `SUCCEEDED_TRANSPORT` outcome over a semantically invalid or schema-violating response is still recorded as transport success with no validity claim. No `ResponseValidator` type, interface, or behavior is introduced by this boundary. |
| CO-18 | Routing deferral | With more than one profile present in configuration, the adapter refuses to select among them rather than choosing one. No score-based, health-based, cost-based, quota-based, latency-based, or fallback selection path exists. A `PROVIDER_UNAVAILABLE`, `RATE_LIMITED`, or `QUOTA_UNAVAILABLE` outcome never triggers an alternate-profile attempt. |
| CO-19 | Historical reconstruction versus re-invocation | Reconstruction reads only persisted evidence admissible at the historical cutoff. Current profile, current policy, current registry, current capability, and current transformation are rejected as substitutes, and unavailable categories report explicit unavailability with stable reason codes. A re-invocation test asserts a new attempt identity, a new attempt cutoff, and no claim of response equality with the prior attempt. |
| CO-20 | Legacy provider-path migration truthfulness | Legacy `AIProviderArtifact`, `AIProviderHealth`, and `ExtractionProposal` records retain their original schema and identity after the adapter exists. No migration path writes Model Adapter lineage onto a legacy record, and no combined audit view reports a legacy record as carrying request, attempt, handoff, or outcome lineage it never captured. `SecureAIProviderRunner` is not registered or resolvable as the canonical Model Adapter. |
| CO-21 | Repository and persistence authority separation | Repository code contains no provider selection, prompt alteration, meaning validation, or permission granting. Persistence independently rederives every relevant handling decision and rejects an adapter-supplied decision that does not match, along with missing authority, mismatched inputs, and contradictory payload state. A repository cannot be substituted for the adapter as a decision authority. |
| CO-22 | Governance-review isolation | An architecture regression test proves the Hunter Governance Review and Hunter Merge Readiness packages and workflows import no Model Adapter, provider transport, provider SDK, credential, or LLM dependency, directly or transitively, and that both surfaces execute deterministically with every provider path absent. |

CO-01 through CO-19 subsume ADPR-0009 mandatory conformance cases 1 through 12. ADPR-0009 cases 13 and 14 — accepted-ADR applicability accounting and architecture-index lifecycle/runtime status consistency — are already implemented and enforced in the shared Pre-PR chain by the Artifact Guard and the Architecture Index Guard, with the latter registered as guarded defect class `ARCH-AUD-008`; the merged independent audit closed that finding on evidence. They are process guards over architecture maintenance and are not re-stated as Model Adapter runtime obligations here.

## Compatibility

- ADR 0031 is **extended and reaffirmed**, not superseded. It remains the governing pre-model foundation. This ADR occupies the `ModelAdapter` boundary ADR 0031 explicitly deferred and changes no upstream intent, resolution, selection, allocation, prompt-compilation, or build semantics. `EvidencePromptArtifact` and `EvidencePreModelBuildRecord` identities remain unchanged and historically valid.
- ADR 0033 is **reaffirmed** and remains the sole Source Handling authority. The Model Adapter is a consumer. `ModelHandoffRecord` is execution evidence bound to an exact Source Handling snapshot; it publishes no handling facts or policy and never becomes a second authority.
- ADR 0032 is **reaffirmed** and unamended. The Model Adapter contract is deliberately kept Hunter-owned. No part of it is admitted to the project-neutral core, and its admission would require ADR 0032's two-consumer evidence gate and a separate decision.
- ADR 0020 strict-known semantics are **relied upon without amendment** for historical execution audit; historical cutoffs never serve as live authorization.
- ADR 0009 provider, service, repository, and persistence separation is **extended and reaffirmed**: provider transports are explicitly prohibited from owning canonical durable execution artifacts.
- ADR 0016 is **reaffirmed**: a successful provider call creates no canonical or analytical authority and no `ANALYTICAL_AUTHORITY_REGISTRY` entry.
- ADR 0002 and ADR 0004 are **reaffirmed**: execution evidence inherits evidence-first provenance and explicit-missingness obligations, and provider responses remain untrusted context rather than instruction or authority.
- ADRs 0025, 0026, and 0028 were reviewed and are **out of scope**: they own valuation evidence assembly, comparative valuation methodology, and evidence-assembly supporting authorities respectively, not Evidence Intelligence model transport, provider attempts, or response handling. This ADR creates no valuation or evidence-assembly authority, record family, or dependency, and changes none of their activation gates.
- ADR 0027, ADR 0029, and ADR 0030 remain `Proposed` and are neither accepted, amended, nor superseded here.
- Existing legacy provider records remain historically valid under their original schema.

No accepted ADR is superseded by this decision.

## Non-Goals

Acceptance of this ADR is architecture authority. It is not runtime activation. This ADR does not define or authorize:

- runtime Model Adapter implementation;
- provider SDK or library integration;
- live provider or model invocation of any kind;
- credentials, API keys, provider accounts, or authentication material;
- production provider endpoints or configuration;
- `ResponseValidator` architecture or implementation;
- provider or model routing, ranking, scoring, failover, or dynamic selection;
- canonical claim creation or canonical knowledge promotion;
- autonomous model tool use or model-side tools;
- any change to Source Handling ownership, facts, policy, or mechanics;
- any change to Hunter Governance Review or Hunter Merge Readiness architecture, and no LLM or provider dependency for either;
- admission of any Model Adapter contract into the ADR 0032 project-neutral core;
- retroactive relabeling, purge, or rewriting of already-persisted legacy provider records;
- any new analytical authority or `ANALYTICAL_AUTHORITY_REGISTRY` entry;
- trading, signalling, portfolio-allocation, dashboard, scheduler, SaaS, or unrelated Hunter roadmap work.

## Implementation Status

Architecture only. Not started, and not authorized by this ADR.

Implementation requires a separately authorized issue and lifecycle after this ADR is accepted. That implementation must freeze CO-01 through CO-22 as deterministic contract tests before concrete provider code, and must additionally prove the atomic snapshot-to-handoff and single-use dispatch mechanism, the field-category coverage for request and response durable evidence, and each provider's idempotency, correlation, and reconciliation classification before any provider is activated.

## Consequences

- Evidence Intelligence gains one named owner for model-attempt semantics, closing the boundary ADR 0031 left open without collapsing prompt compilation, transport, routing, validation, persistence, and domain authority into a generic AI service.
- Provider substitution becomes possible without rewriting pre-model or execution history, because provider-specific code owns transformation and network mechanics only.
- Every possible external invocation costs a durable write before it can occur. That is an intentional cost: it is what makes orphan sends, duplicate billing, and unattributable responses architecturally unreachable.
- Some provider outcomes will remain permanently unknown. Where a provider exposes no reconciliation facility, an uncertain attempt stays uncertain and automated retry stays blocked rather than risking duplicate execution.
- Exact request and response reconstruction becomes conditional rather than universal. Where durable categories are denied, audit answers "explicitly unavailable, and why", never a regenerated approximation.
- Recorded failure semantics become more granular than a generic failure flag, and implementations must carry that granularity rather than collapsing it.
- The legacy provider path remains in the repository as historical architecture with weaker guarantees. Any audit surface spanning both generations must state which lineage each record carries.
- Provider activation remains blocked after acceptance until the deferred runtime gates are proven, so accepting this ADR does not shorten the path to a live model call — it makes that path auditable.
- Hunter Governance Review and Hunter Merge Readiness remain deterministic and insulated from provider availability, quota, billing, and model behavior.

## Alternatives Considered

### Extend the legacy `AIExtractionProvider` / `SecureAIProviderRunner` path in place

Rejected. That path consumes `ExtractionRequest` built from spans and schema rather than the canonical `EvidencePromptArtifact`, creates `ExtractionProposal` directly across the validation boundary ADR 0031 requires, and has no pre-send attempt, handoff, or uncertain-delivery semantics. Declaring it the Model Adapter would either leave those gaps open or require redesigning it so heavily that preserving it as the abstraction buys nothing while inviting old records to be read as though they carried new guarantees. Reconsider only as an internal compatibility adapter behind this boundary, never as evidence that historical records possess lineage they never captured.

### Promote a Model Adapter into the ADR 0032 project-neutral core now

Rejected under current authority. ADR 0032 explicitly withholds provider and model selection, routing, credentials, invocation, and response validation from the shared core, and requires evidence from two independent consumers before admitting a concrete shared contract. No second consumer with equivalent versioned execution semantics exists. Reconsider when two such consumers exist and an architecture review admits the common semantics.

### Build a standalone provider gateway service from day one

Rejected as disproportionate. Externalizing invocation would immediately require a new service identity, authentication protocol, durable cross-service contract, availability dependency, deployment lifecycle, and version-compatibility matrix, and would risk the gateway becoming a de facto routing, credential, or retention authority. Nothing in current evidence requires that isolation. Reconsider when multiple real consumers or providers, independent release cadence, or deployment economics make a service boundary cheaper and safer than in-process isolation.

### Implement one concrete provider directly with no provider-neutral adapter contract

Rejected as architecture, though viable as a shortcut. Even a single provider needs stable distinctions among canonical prompt, transport transformation, Source Handling-owned durability, pre-send attempt, handoff, idempotency and correlation, uncertain outcome, raw response, and validation. If those concepts exist only inside provider-specific types, the first provider's protocol becomes the accidental architecture and later substitution requires refactoring canonical execution code. Reconsider only if a later accepted decision deliberately makes one provider a permanent architectural dependency and accepts the migration cost explicitly.

### Include multi-provider routing in this boundary

Rejected. Routing is a separate authority question involving selection inputs, cost, latency, quality criteria, failure fallback, capability compatibility, and deterministic audit, and no current evidence establishes an operational need. Including it now would let provider selection acquire authority over capability constraints and retry semantics before the underlying attempt boundary is proven. Reconsider through a separate ADPR and ADR when a real workflow cannot operate with one configured profile.

## Supersession and Relationship to Existing ADRs

- ADR 0031 is extended and reaffirmed. This ADR occupies its explicitly deferred `ModelAdapter` boundary and stops before its equally deferred `ResponseValidator` boundary.
- ADR 0033 is reaffirmed. Source Handling ownership is unchanged; the Model Adapter is a consumer that enforces and records, never an authority that decides.
- ADR 0032 is reaffirmed and unamended. No shared-core admission occurs.
- ADR 0020, ADR 0016, ADR 0009, ADR 0004, and ADR 0002 are reaffirmed and relied upon without amendment.
- ADRs 0025, 0026, and 0028 were reviewed and explicitly determined non-governing for this boundary; their owners, record families, and activation gates are unchanged. This is a recorded negative-scope determination, not a claim that those decisions are unimportant.
- ADR 0027, ADR 0029, and ADR 0030 remain `Proposed` and are unaffected.
- No accepted ADR is superseded, amended, or deprecated by this decision.
