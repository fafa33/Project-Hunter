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

Independent audit of the first PR #337 revision then identified five required
corrections: isolate capture together with consumption, define an OS security
boundary stronger than a subprocess, keep event reservation under ADR 0035,
exclude filesystem-backed transient handoff, and state the one-capture/one-event
consequence for every pair of distinct canonical events. The owner decisions
below resolve those findings without reopening any other architecture.

## Owner-selected correction

### 1. Isolated capture-and-validation worker

For `TRANSIENT_NOT_RETAINED` input, the ADR 0034 response-capture boundary and
the ADR 0035 semantic consumer execute together inside an isolated worker
process. The component that exposes the caller-facing validation
authorization/control surface executes outside that worker and never receives
the exact response body. The caller-facing process receives only non-content
capture/attempt metadata, authorization/refusal metadata, and the governed
non-content validation result and permitted diagnostics.

The caller-facing process must never receive or hold any of the following:

- a readable copy of the exact transient response body;
- a readable file descriptor, shared-memory mapping, socket endpoint, object
  reference, callback, or equivalent capability that can yield that body; or
- a debug, inspection, logging, exception, or diagnostic surface from which the
  exact body can be recovered.

An opaque response-capture identity is lineage, not a body-reading capability.
It may cross the caller-facing control surface only when that process cannot
dereference it into exact response material.

ADR 0034's capture-side component inside the worker may transiently receive the
provider response because capture remains its accepted responsibility. Exact
bytes needed for validation pass from that capture component to the ADR 0035
semantic consumer entirely inside the isolated worker. The semantic consumer
may read them only for the one authorized execution and must not expose or
return them. This topology explicitly rejects placing ADR 0034 capture in the
caller-facing process while isolating only the ADR 0035 consumer.

A thread, coroutine, class boundary, private attribute, name-mangled member,
in-process socket pair, or other same-process convention does not satisfy this
rule.

A distinct process identifier or subprocess alone is also insufficient. The
worker must run behind an operating-system-enforced security boundary that
prevents the caller-facing process from inspecting worker memory or readable
descriptors. The implementation must provide an enforceable equivalent of a
distinct OS security principal, sandbox, namespace boundary, or combination
whose effective properties include:

- the caller-facing process cannot attach with `ptrace`, a debugger, or an
  equivalent process-inspection facility;
- the caller-facing process cannot read `/proc`-style worker memory,
  descriptor, or equivalent inspection surfaces;
- no readable response-body descriptor is inherited by or transferred to the
  caller-facing process;
- no shared-memory region containing the body is readable by the caller-facing
  process; and
- the worker is non-dumpable or equivalently inspection-restricted where the
  platform supports that control.

A same-UID subprocess that remains reflectively or debugger-readable by the
caller-facing process does not qualify. Exact sandbox, principal, namespace,
and process-management mechanisms remain implementation choices only when they
prove all required isolation properties. Administrative or root compromise of
the host is outside this threat model.

This refinement does not externalize provider invocation into a new provider
gateway. Provider invocation, response capture, and response-capture lineage
remain within ADR 0034, while both the ADR 0034 capture component and ADR 0035
semantic consumer are isolated from the caller-facing process for
`TRANSIENT_NOT_RETAINED` input.

### 2. Non-durable transfer and consumption

For `TRANSIENT_NOT_RETAINED`, the exact response body must not be materialized
in a named or anonymous filesystem file, persisted in a temporary file, exposed
through caller-readable shared memory, or copied into any durable or
reconstructable retained representation. Transfer from capture to semantic
consumption must use non-durable operating-system IPC or equivalent ephemeral
streaming entirely inside the isolated worker boundary.

The isolated worker may hold the minimum ephemeral body state necessary for
capture screening and the one authorized semantic execution, subject to the
historically applicable ADR 0033 decision. It must not return that state to the
caller-facing process or convert it into a retained artifact. ADR 0033 remains
the exclusive authority for every retention, reconstruction, access, deletion,
and lifecycle permission; neither the worker topology nor the IPC mechanism
grants or extends those permissions.

### 3. ADR 0035-owned atomic event reservation

Authorization for `TRANSIENT_NOT_RETAINED` input must include one atomic
reservation operation over the canonical response-capture identity. The only
reservation owner is the Phase-A-allocated canonical `validation_event_id`.
No caller-selected alias, worker identity, authorization token, process
identity, or re-validation request identity may replace that owner.

The reservation mechanism is part of ADR 0035 validation authorization and is
owned by ADR 0035. ADR 0034 owns the provider attempt, response capture,
canonical capture identity, and governed handoff; it supplies those exact
non-content coordinates but does not own, publish, or enforce a
`validation_event_id` reservation registry.

The authorization sequence is fixed:

1. ADR 0035 allocates or joins the canonical validation event and its trusted
   cutoff.
2. ADR 0035 resolves all historically applicable authorization prerequisites,
   including the profile, requested-output contract, Source Handling decision,
   capture/attempt lineage, and input mode.
3. As part of authorization, the ADR 0035 authorization authority atomically
   records the canonical `validation_event_id` as owner if the capture identity
   is unreserved, returns the existing reservation if its owner is the same
   event, or refuses if another event already owns it.
4. ADR 0035 exposes a successful transient validation authorization only after
   same-event ownership has been confirmed.

The first successful canonical reservation owns the capture. One capture has
at most one event owner. Reservation is not transferable, releasable for use by
another event, or replaceable after failure, timeout, cancellation, or
consumption. This prevents execution order from changing which canonical event
was authorized to consume the one-shot input.

A non-retained transient capture can therefore be semantically consumed by at
most one canonical `validation_event_id`. This rule applies to every pair of
distinct events, including a base event versus re-validation and two otherwise
legitimate base validation events whose requested-output or profile coordinates
produce different canonical events. If another event requires the exact bytes,
it requires a fresh ADR 0034-governed observation and capture, which creates a
new base validation key and event as specified below. This is an intentional
consequence of non-retention combined with one-shot reservation.

Committed reservation ownership is durable non-content authority metadata. ADR
0035 requires one canonical restart-surviving mapping or ownership tombstone:

`response_capture_identity -> owning validation_event_id`

The mapping uses atomic create-if-absent semantics. Creation succeeds only for
an unowned capture identity; the same owner is an idempotent join; every other
event is a conflict. A process-local registry alone is insufficient.

Once committed, the ownership mapping must not disappear on authorization-
authority restart, validator-worker restart, timeout, cancellation, execution
failure, successful consumption, or cleanup. It remains as an ownership
tombstone after the transient body has been consumed or lost so a same-event
retry/join still observes the owner and a competing event after restart still
fails closed. The mapping stores only the canonical capture identity and owning
event identity needed to prove reservation; it contains no exact response bytes
and does not prove that bytes remain available.

Durability of this non-content ownership metadata grants no response retention
or reconstruction permission. It is distinct from both the transient body and
the later terminal `ResponseValidationRecord`; persistence mechanics remain
mechanical and cannot choose or re-decide reservation ownership.

### 4. Retry, join, and interrupted delivery

Retry or concurrent join of the same canonical `validation_event_id` observes
the same reservation owner and preserves the same authorization coordinates.
It does not mint a sibling reservation or reinterpret the event using current
profile, Source Handling, output-contract, attempt, capture, or cutoff state.

If reservation commits but authorization delivery or worker execution is
interrupted, the capture remains owned by that event. A same-event retry may
resume only if the exact transient body remains lawfully available to the
isolated consumer. If it is no longer available, the event fails closed under
the existing ADR 0035 input-unavailable refusal semantics. The capture is not
reassigned to another event. Authority or worker restart re-resolves this
behavior from the canonical event and durable ownership mapping, never from
process-local state.

### 5. Explicit re-validation

Explicit re-validation of the same capture receives a distinct canonical event
and cutoff under ADR 0035 while preserving the predecessor
`base_validation_key` and its original `response_capture_identity`. It remains
a new validation decision over the same original capture lineage; it does not
mutate that lineage.

If another event already owns the non-retained capture or the exact bytes are
no longer available, that re-validation event fails closed with
`INPUT_UNAVAILABLE`. A fresh ADR 0034 observation with a new
`response_capture_identity` must not be substituted into the allocated
re-validation event. No event identity, cutoff, base key, or capture identity
inside that event may be replaced, and no current/latest state may stand in for
the original coordinates.

A fresh ADR 0034 observation/capture instead creates a new base validation event
through a new `base_validation_key` bound to the new capture identity. Where a
governed consumer requires causal traceability, that new base event may refer
to the earlier validation attempt or result as predecessor/causal lineage, but
it neither is nor resumes the earlier re-validation event and does not reuse
its event identity, cutoff, or base key. The fresh response is a new observation
and makes no historical-reconstruction or response-equality claim. This rule
does not itself authorize provider invocation, retry, routing, or fallback.

## Failure semantics

- A different event that loses the atomic reservation, including an otherwise
  legitimate base event with different requested-output/profile coordinates,
  is refused before semantic execution with the existing ADR 0035
  `INPUT_UNAVAILABLE` top-level state and state-compatible refusal semantics.
  No new top-level validation state is created.
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

Reservation conflict uses only the existing top-level `INPUT_UNAVAILABLE`
state; this correction creates no new validation state. During Phase B the
losing canonical event produces the canonical in-memory refusal outcome and
the state-compatible refusal attestation authorized by ADR 0035. It does not
append a terminal durable `ResponseValidationRecord`, create
`validation_recorded_at`, or otherwise perform the later persistence phase.

Retry or join of that same losing event deterministically reproduces or joins
the same refusal semantics from the canonical event coordinates and the durable
reservation owner. It does not rerun the ownership race or reinterpret current
state. When terminal record persistence is implemented in its separately
governed phase, it must preserve the event, refusal, attestation, capture, and
reservation-owner lineage mechanically without re-deciding the conflict.

## Authority consistency

### ADR 0031 — requested-output and promotion authority

ADR 0031 remains authoritative for the Evidence Intelligence intent and
consumer-owned requested-output/`ExtractionSchema` contract. The isolated
validator consumes the exact event-bound contract and may neither select nor
replace it. Validation still proves only permitted conformance; it creates no
`ExtractionProposal`, canonical truth, or promotion authority. A fresh upstream
observation is a new execution and new base validation lineage, never a
substitute inside an allocated re-validation event or historical
reconstruction, consistent with ADR 0031.

### ADR 0033 — Source Handling authority

ADR 0033 remains the exclusive authority for processing, retention,
reconstruction, access, and deletion decisions. Process isolation and a
reservation do not grant any of those permissions. Durable retention of exact
bytes remains forbidden when the historically applicable Source Handling
decision forbids it, and the mere existence of transient bytes or reservation
metadata proves no authority. Unknown, ambiguous, conflicting, or missing
handling authority still fails closed without current or permissive fallback.
The durable capture-to-event ownership tombstone contains only already
canonical non-content identities and never response bytes or a reconstructable
body representation.

### ADR 0034 — Model Adapter and capture authority

ADR 0034 continues to own provider invocation, attempts, response capture,
single-use handoff evidence, canonical capture identity, and capture/attempt
lineage. For `TRANSIENT_NOT_RETAINED`, its capture component executes inside
the isolated worker and supplies exact transient bytes only to the colocated
semantic consumer through non-durable ephemeral transfer. ADR 0034 does not
own or enforce the mapping from capture identity to `validation_event_id` and
does not acquire a validation reservation registry. It does not choose
validation event, cutoff, profile, output contract, Source Handling decision,
or semantic outcome. A fresh observation is a new attempt/capture and can begin
a new base validation lineage; it cannot be substituted into a previously
allocated re-validation event. This matches ADR 0034's retry and re-invocation
semantics. No companion ADR 0034 amendment, provider gateway, or routing
authority is added.

### ADR 0035 — validation authority

ADR 0035 continues to own canonical event allocation, trusted validation
cutoff, validation authorization, semantic execution, closed states,
precedence, and success/refusal attestations. The process boundary makes its
non-caller transient authorization mechanically enforceable. As part of that
authorization authority, ADR 0035 atomically reserves one exact capture identity
to the already canonical event before exposing successful authorization. The
reservation is idempotent only for the same event and refuses every different
event. ADR 0035 also owns the durable non-content ownership mapping; its
repository stores that mapping mechanically and cannot re-decide it. Profile
publication, Source Handling, requested-output truth, capture lineage, terminal
validation-record persistence, and downstream promotion remain outside
validator execution authority.

The correction is therefore an extension of ADR 0035's transient handoff and
authorization mechanics. It neither supersedes nor transfers authority from
ADRs 0031, 0033, or 0034.

## Mandatory conformance cases

A later implementation must prove at minimum:

1. the ADR 0034 capture component and ADR 0035 semantic consumer execute inside
   the same protected worker for `TRANSIENT_NOT_RETAINED`, while the
   caller-facing process receives only non-content metadata and results;
2. caller-facing code cannot recover the exact transient body through object
   inspection, inherited descriptors, shared memory, sockets, callbacks,
   exceptions, logs, diagnostics, debugger attachment, process-memory reads, or
   `/proc`-style memory and descriptor surfaces;
3. a distinct-PID or same-UID subprocess that remains reflectively,
   `ptrace`-, debugger-, memory-, or descriptor-readable by the caller is
   rejected as non-conforming rather than treated as an isolation boundary;
4. the protected worker is non-dumpable or equivalently
   inspection-restricted where supported, and no readable body descriptor or
   shared-memory body region is inherited by or exposed to the caller;
5. exact transient bytes are never materialized in named or anonymous
   filesystem files, temporary files, caller-readable shared memory, or a
   durable/reconstructable copy, and their internal handoff uses only
   non-durable IPC or equivalent ephemeral streaming inside the worker;
6. ADR 0035, not ADR 0034, atomically reserves the capture identity during
   authorization and exposes no successful authorization before ownership is
   confirmed;
7. the canonical `response_capture_identity -> owning validation_event_id`
   mapping uses atomic create-if-absent and survives authorization-authority and
   validator-worker restart without relying on a process-local registry;
8. two different canonical events racing for one capture produce exactly one
   owner, and the loser receives `INPUT_UNAVAILABLE` refusal before semantics;
9. two legitimate distinct base events with different requested-output or
   profile coordinates cannot both consume the same non-retained capture;
10. authority restart, worker restart, timeout, cancellation, execution
    failure, successful consumption, and cleanup never remove or transfer the
    committed owner;
11. same-event retry/join after restart observes the same owner and the same
    historically bound authorization coordinates;
12. a competing event after restart still receives `INPUT_UNAVAILABLE` refusal
    and cannot rerun the reservation race;
13. post-consumption ownership remains as a tombstone even though the exact
    transient body is unavailable;
14. the durable reservation representation contains only the exact canonical
    capture and owner identities and provably contains no response body bytes;
15. a losing event emits the canonical Phase-B in-memory refusal outcome and
    refusal attestation without appending a terminal `ResponseValidationRecord`;
16. same-losing-event retry/join deterministically reproduces or joins the same
    refusal from canonical event and ownership state;
17. later terminal persistence preserves refusal/reservation lineage without
    re-deciding the conflict;
18. explicit re-validation preserves the original `base_validation_key` and
    `response_capture_identity`, and fails `INPUT_UNAVAILABLE` when the original
    non-retained bytes are unavailable or owned by another event;
19. a fresh capture identity is rejected as a substitution into an allocated
    re-validation event and cannot mutate its event identity, cutoff, base key,
    or capture lineage;
20. a fresh ADR 0034 capture creates a new base validation key and event, with
    optional governed causal lineage to an earlier attempt/result but no reuse
    of that earlier event's identity, cutoff, or base key; and
21. no reservation, refusal, IPC, or process-boundary artifact stores or logs
    exact non-retained response bytes or permits downstream
    extraction/promotion.

## Falsification and rejected alternatives

- **Same-process privacy:** rejected by owner selection and PR #335 evidence;
  language-level privacy does not prevent caller-readable exact bytes.
- **Validator-only isolation:** rejected because ADR 0034 capture outside the
  protected worker would still place exact non-retained bytes or a readable
  handoff capability in the caller-facing process.
- **Bare subprocess separation:** rejected because a same-UID or otherwise
  inspectable child can remain readable through debugger, process-memory, and
  descriptor-inspection facilities.
- **Filesystem or caller-readable shared-memory handoff:** rejected because it
  creates a retained, reconstructable, or caller-readable exact-body surface
  inconsistent with `TRANSIENT_NOT_RETAINED`.
- **ADR 0034-owned event reservation:** rejected because validation-event
  ownership belongs to ADR 0035 authorization; ADR 0034 supplies capture
  identity and governed handoff without acquiring an event registry.
- **Reserve on execution:** rejected because two events can appear successfully
  authorized and execution order becomes authority.
- **Release to a competing event:** rejected because it makes ownership depend
  on worker failure/order and violates first-successful-reservation ownership.
- **Process-local reservation only:** rejected because restart or cleanup would
  erase ownership and allow a competing event to consume a one-shot capture.
- **Fresh-capture substitution into re-validation:** rejected because Phase A
  binds re-validation to the predecessor base key and original capture identity;
  mutation would replace already allocated event authority.
- **Second consumption for explicit re-validation:** rejected once another
  canonical event owns or has consumed the original capture; same-capture
  re-validation then fails `INPUT_UNAVAILABLE`.
- **Reuse across distinct base events:** rejected even when both events are
  otherwise legitimate; different requested-output/profile coordinates create
  different canonical events, and one non-retained capture has one owner.
- **Fallback to current, retained, reconstructed, or freshly invoked content:**
  rejected because it changes event-bound authority and historical meaning. A
  separately governed fresh observation may support a new base validation
  event, but never substitutes for the old capture inside re-validation.

## Explicit non-goals

- runtime implementation or modification of PR #335 code;
- exact non-durable IPC protocol, process manager, deployment topology, or
  operating-system primitive selection beyond the required protected-worker
  properties;
- durable raw-response storage or any retention expansion;
- terminal `ResponseValidationRecord` persistence (the required durable
  non-content reservation ownership tombstone is not a terminal validation
  record);
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
outcome-based: no caller-facing process may possess a readable capability or OS
inspection path, regardless of distinct process IDs or the chosen IPC
primitive.

Exact non-durable IPC framing, qualifying sandbox/principal/namespace controls,
process supervision, physical storage for ADR 0035-owned non-content
reservation metadata, and crash-recovery mechanics remain implementation
choices. Restart-surviving ownership, permanent non-reassignment, and the
post-consumption tombstone are not optional implementation choices. All
mechanics are constrained by the canonical rules above and may not weaken
capture-and-consumer isolation, OS inspection resistance, first-owner
reservation, same-event retry/join, strict-known authority, or non-retention.
No additional architectural ambiguity is identified.

## Readiness and next governed step

- Preparation self-assessment: `READY_FOR_ADR`.
- Independent exact-head re-audit completed on
  `c26a1dae9f4635f51fd70c65748c760fcb335808` and returned `READY_FOR_ADR`.
- PR #337 merged to `main` as
  `0cea851917afd9579aeaf3bb6261a8177d1e8153`.
- This ADPR does not itself amend accepted ADR 0035. Issue #338 / Draft PR #339
  now govern the narrow amendment/transposition of only the process isolation,
  reservation, failure, retry/join, and re-validation rules above.
- Runtime correction of PR #335 remains blocked until that ADR amendment is
  accepted and a separately authorized implementation scope resumes it.
- Merge remains owner-only.

## Traceability

- Preparation issue: #336
- Preparation PR: #337
- Blocked implementation issue/PR: #334 / #335
- Architecture-dependent findings: PR #335
  `discussion_r3848869567` and `discussion_r3848869576`
- PR #337 independent audit correction: capture-and-consumer topology, OS
  inspection boundary, ADR 0035 reservation ownership, non-durable IPC, and
  all-distinct-event single ownership
- PR #337 exact-head review correction: Phase-A-compatible fresh-capture/base-
  event lineage, architecture-index registration, Phase-B in-memory refusal
  contract, and restart-surviving non-content reservation ownership
- Final independent re-audit: `READY_FOR_ADR` on
  `c26a1dae9f4635f51fd70c65748c760fcb335808`
- Preparation merge: PR #337 at
  `0cea851917afd9579aeaf3bb6261a8177d1e8153`
- ADR amendment drafting: Issue #338 / Draft PR #339
- Governing accepted ADRs: 0031, 0033, 0034, and 0035
- ADR amendment: Proposed under Issue #338 / Draft PR #339; not yet accepted
- Runtime implementation: not authorized by this record
