# ADPR-0011 — ADR 0035 transient handoff isolation and event reservation

## Metadata

- Preparation state: `READY_FOR_REVIEW`
- Self-assessment: `READY_FOR_ADR`
- Governing issue: #336
- Preparation PR: #337
- Exact baseline: `40b4bed1cee8568840feea6e8401da49d4fe6d67`
- Blocked implementation: Issue #334 / PR #335
- Affected accepted decision: ADR 0035

## Scope

This preparation is intentionally narrow. It exists only to close two
architecture gaps discovered during ADR 0035 Phase B exact-head review:

1. the mechanically enforceable isolation topology for exact provider response
   bytes when Source Handling forbids durable retention; and
2. the atomic reservation rule for one single-use transient capture across
   canonical validation events.

The owner has selected the direction recorded below. This preparation verifies
that direction against ADRs 0031, 0033, 0034, and 0035 and makes its required
failure and retry semantics explicit. It does not reopen the broader ADR 0035
decision or authorize implementation.

## Problem evidence

Two unresolved architecture-dependent findings on PR #335 establish the gap:

- PR #335 review thread `discussion_r3848869567` demonstrated that a
  caller-held validator could reach a live transient-response file descriptor
  through the same Python object graph and read the exact response body without
  consuming it. Naming conventions and name mangling are not access control.
- PR #335 review thread `discussion_r3848869576` demonstrated that a base event
  and an explicit re-validation event could both receive authorization for the
  same single-use capture before either executed. Execution order then decided
  which already-authorized event could consume the bytes.

These are architecture gaps rather than isolated implementation defects. ADR
0035 requires a non-caller-mintable authorization and a single-use transient
handoff, but does not yet specify the isolation topology or which event owns a
single-use capture when canonical events compete.

## Owner-selected correction

### 1. Real process isolation

For `TRANSIENT_NOT_RETAINED` input, the component that exposes the
caller-facing validation authorization/control surface and the component that
consumes the exact response body for semantic validation must execute in
separate operating-system processes with separate address spaces.

The caller-facing process must never receive or hold any of the following:

- a readable copy of the exact transient response body;
- a readable file descriptor, shared-memory mapping, socket endpoint, object
  reference, callback, or equivalent capability that can yield that body; or
- a debug, inspection, logging, exception, or diagnostic surface from which the
  exact body can be recovered.

An opaque response-capture identity is lineage, not a body-reading capability.
It may cross the caller-facing control surface only when that process cannot
dereference it into exact response material.

ADR 0034's capture-side boundary may transiently receive the provider response
because capture remains its accepted responsibility. Exact bytes needed for
validation must pass from that governed capture boundary to the isolated ADR
0035 validator consumer without becoming readable in the caller-facing
process. The isolated consumer may read them only for the one authorized
semantic execution and must not expose or return them. The returned result is
limited to the governed non-content validation outcome and diagnostics that
Source Handling permits.

A thread, coroutine, class boundary, private attribute, name-mangled member,
in-process socket pair, or other same-process convention does not satisfy this
rule. The exact inter-process transport and deployment mechanism remain
implementation choices, but conformance requires an operating-system-enforced
process boundary and the absence of an inherited or otherwise caller-readable
body capability.

This refinement does not externalize provider invocation into a new provider
gateway. Provider invocation, response capture, and response-capture lineage
remain within ADR 0034; only the ADR 0035 semantic consumer is isolated from the
caller-facing process.

### 2. Atomic event reservation

Authorization for `TRANSIENT_NOT_RETAINED` input must include one atomic
reservation operation over the canonical response-capture identity. The only
reservation owner is the Phase-A-allocated canonical `validation_event_id`.
No caller-selected alias, worker identity, authorization token, process
identity, or re-validation request identity may replace that owner.

The authorization sequence is fixed:

1. ADR 0035 allocates or joins the canonical validation event and its trusted
   cutoff.
2. ADR 0035 resolves all historically applicable authorization prerequisites,
   including the profile, requested-output contract, Source Handling decision,
   capture/attempt lineage, and input mode.
3. As part of authorization, the ADR 0034-owned capture boundary atomically
   records the canonical `validation_event_id` as owner if the capture is
   unreserved, returns the existing reservation if its owner is the same event,
   or refuses if another event already owns it.
4. ADR 0035 exposes a successful transient validation authorization only after
   same-event ownership has been confirmed.

The first successful canonical reservation owns the capture. One capture has
at most one event owner. Reservation is not transferable, releasable for use by
another event, or replaceable after failure, timeout, cancellation, or
consumption. This prevents execution order from changing which canonical event
was authorized to consume the one-shot input.

The operation stores no response body and grants no retention permission. Any
reservation metadata is non-content lineage only, remains subordinate to the
ADR 0034 capture and ADR 0035 event authorities, and cannot establish that
response bytes are still available.

### 3. Retry, join, and interrupted delivery

Retry or concurrent join of the same canonical `validation_event_id` observes
the same reservation owner and preserves the same authorization coordinates.
It does not mint a sibling reservation or reinterpret the event using current
profile, Source Handling, output-contract, attempt, capture, or cutoff state.

If reservation commits but authorization delivery or worker execution is
interrupted, the capture remains owned by that event. A same-event retry may
resume only if the exact transient body remains lawfully available to the
isolated consumer. If it is no longer available, the event fails closed under
the existing ADR 0035 input-unavailable refusal semantics. The capture is not
reassigned to another event.

### 4. Explicit re-validation

Explicit re-validation receives a new canonical event and cutoff under ADR
0035. Because its `validation_event_id` differs, it cannot reserve, read, or
consume a transient capture already reserved or consumed by the original event.

If the re-validation requires exact response bytes and the original bytes were
not durably retained, it requires a fresh upstream capture/observation governed
by ADR 0034. That observation is a new Model Adapter attempt/capture with its
own lineage and newly resolved upstream authority; it is not reconstruction,
replay, or proof that the new response equals the original. This rule does not
itself authorize a provider invocation, retry, route, or fallback. If no such
fresh governed capture exists, re-validation fails closed as input unavailable.

## Failure semantics

- A different event that loses the atomic reservation is refused before
  semantic execution with the existing ADR 0035 `INPUT_UNAVAILABLE` top-level
  state and state-compatible refusal semantics. No new top-level validation
  state is created.
- If Source Handling itself prohibits or cannot authorize processing, the
  existing ADR 0035 `SOURCE_HANDLING_BLOCKED` semantics continue to apply. A
  reservation conflict does not relabel Source Handling authority.
- Loss or exhaustion of the reserved transient body is input unavailability,
  not `VALIDATOR_ERROR`. `VALIDATOR_ERROR` remains reserved for a failure of an
  authorized semantic execution that actually began.
- No current/latest profile, Source Handling decision, requested-output
  contract, attempt, capture, or response may substitute for the event-bound
  coordinates.
- Authorization may not silently switch from `TRANSIENT_NOT_RETAINED` to
  `DURABLE`, search for a retained copy, reconstruct bytes, or re-call a
  provider. Input mode and lineage remain exactly those authorized for the
  canonical event.
- Reservation failure and unavailable input return only governed non-content
  refusal evidence. Exact response bytes must not appear in logs, errors,
  diagnostics, attestations, or caller-visible results.

## Authority consistency

### ADR 0031 — requested-output and promotion authority

ADR 0031 remains authoritative for the Evidence Intelligence intent and
consumer-owned requested-output/`ExtractionSchema` contract. The isolated
validator consumes the exact event-bound contract and may neither select nor
replace it. Validation still proves only permitted conformance; it creates no
`ExtractionProposal`, canonical truth, or promotion authority. A fresh upstream
observation is a new execution, never historical reconstruction, consistent
with ADR 0031.

### ADR 0033 — Source Handling authority

ADR 0033 remains the exclusive authority for processing, retention,
reconstruction, access, and deletion decisions. Process isolation and a
reservation do not grant any of those permissions. Durable retention of exact
bytes remains forbidden when the historically applicable Source Handling
decision forbids it, and the mere existence of transient bytes or reservation
metadata proves no authority. Unknown, ambiguous, conflicting, or missing
handling authority still fails closed without current or permissive fallback.

### ADR 0034 — Model Adapter and capture authority

ADR 0034 continues to own provider invocation, attempts, response capture,
single-use handoff evidence, and capture/attempt lineage. Its capture boundary
mechanically enforces the one-event reservation requested by ADR 0035
authorization and supplies exact transient bytes only to the isolated semantic
consumer. It does not choose validation event, cutoff, profile, output contract,
Source Handling decision, or semantic outcome. A fresh re-validation
observation is a new attempt/capture, matching ADR 0034's retry and
re-invocation semantics. No provider gateway or routing authority is added.

### ADR 0035 — validation authority

ADR 0035 continues to own canonical event allocation, trusted validation
cutoff, validation authorization, semantic execution, closed states,
precedence, and success/refusal attestations. The process boundary makes its
non-caller transient authorization mechanically enforceable; the reservation
binds the one-shot input to the already canonical event. Profile publication,
Source Handling, requested-output truth, capture lineage, persistence, and
downstream promotion remain outside validator authority.

The correction is therefore an extension of ADR 0035's transient handoff and
authorization mechanics. It neither supersedes nor transfers authority from
ADRs 0031, 0033, or 0034.

## Mandatory conformance cases

A later implementation must prove at minimum:

1. caller-facing code cannot recover the exact transient body through object
   inspection, inherited descriptors, shared memory, sockets, callbacks,
   exceptions, logs, or diagnostics;
2. exact transient bytes cross a real process boundary directly into the
   isolated validator consumer and are not returned with the outcome;
3. two different canonical events racing for one capture produce exactly one
   owner, and the loser receives `INPUT_UNAVAILABLE` refusal before semantics;
4. same-event retry/join observes the same owner and the same historically
   bound authorization coordinates;
5. a committed reservation is never transferred to a different event after
   interruption, execution failure, or consumption;
6. explicit re-validation cannot reuse the original event's transient capture;
7. re-validation without a fresh governed capture fails closed and does not
   substitute current/latest or retained content;
8. a fresh re-validation capture has new ADR 0034 attempt/capture lineage and
   makes no historical-reconstruction or response-equality claim; and
9. no reservation, refusal, or process-boundary artifact stores or logs exact
   non-retained response bytes or permits downstream extraction/promotion.

## Falsification and rejected alternatives

- **Same-process privacy:** rejected by owner selection and PR #335 evidence;
  language-level privacy does not prevent caller-readable exact bytes.
- **Reserve on execution:** rejected because two events can appear successfully
  authorized and execution order becomes authority.
- **Release to a competing event:** rejected because it makes ownership depend
  on worker failure/order and violates first-successful-reservation ownership.
- **Reuse for explicit re-validation:** rejected because re-validation is a new
  canonical event and a single-use capture has one owner.
- **Fallback to current, retained, reconstructed, or freshly invoked content:**
  rejected because it changes event-bound authority and historical meaning. A
  separately governed fresh observation may support a new re-validation event,
  but never substitutes for the old capture.

## Explicit non-goals

- runtime implementation or modification of PR #335 code;
- exact IPC protocol, process manager, deployment topology, or operating-system
  primitive selection beyond the required real process boundary;
- durable raw-response storage or any retention expansion;
- terminal `ResponseValidationRecord` persistence;
- `validation_recorded_at`;
- correction allocator, correction CAS, or correction replay;
- provider routing, provider fallback, or provider invocation redesign;
- downstream extraction or canonical promotion;
- new validation states or changes to ADR 0035 precedence;
- Issue #315;
- DefiLlama integration; or
- production activation.

## Implementation-adjacent findings outside this architecture correction

Two other open PR #335 findings remain ordinary implementation defects and do
not require architecture selection:

- JSON Schema `integer` must accept mathematically integral finite decimals
  such as `1.0`; and
- syntactically valid very-large JSON integers must map to resource/rule
  unavailability rather than `INVALID_SYNTAX`.

They are not addressed by this preparation PR.

## Risks and residual implementation choices

The principal residual risk is accidental descriptor or body leakage while an
implementation creates the process boundary. The conformance rule is
outcome-based: no caller-facing process may possess a readable capability,
regardless of the chosen IPC primitive.

Exact IPC framing, process supervision, non-content reservation storage, and
crash-recovery mechanics remain implementation choices. They are constrained
by the canonical rules above and may not weaken isolation, first-owner
reservation, same-event retry/join, strict-known authority, or non-retention.
No additional architectural ambiguity is identified.

## Readiness and next governed step

- Preparation self-assessment: `READY_FOR_ADR`.
- Independent architecture audit is required on the exact PR #337 head.
- This ADPR does not amend accepted ADR 0035. If the independent audit returns a
  readiness verdict permitting ADR drafting, the next governed contribution is
  a narrow ADR 0035 amendment/transposition that records only the process
  isolation, reservation, failure, retry/join, and re-validation rules above.
- Runtime correction of PR #335 remains blocked until that ADR amendment is
  accepted and a separately authorized implementation scope resumes it.
- Merge remains owner-only.

## Traceability

- Preparation issue: #336
- Preparation PR: #337
- Blocked implementation issue/PR: #334 / #335
- Architecture-dependent findings: PR #335
  `discussion_r3848869567` and `discussion_r3848869576`
- Governing accepted ADRs: 0031, 0033, 0034, and 0035
- ADR amendment: not yet created
- Runtime implementation: not authorized by this record
