# ADR 0035: Evidence Intelligence ResponseValidator Boundary

## Status

Accepted.

The base decision remains Accepted. The transient-isolation and reservation
amendment drafted under Issue #338 is **Proposed** and non-binding until a
separate owner-authorized acceptance transition is merged. This drafting
contribution does not mark that amendment Accepted and changes no runtime
behavior.

## Date

2026-08-24.

## Governing Preparation

[ADPR-0010](../architecture-records/ADPR-0010-evidence-intelligence-response-validator.md) v1.5 is the governing preparation record for the accepted base decision.

The preparation lineage has three distinct coordinates that must not be conflated:

- `8ee6fd57577fa322b87cba21bd381d05770edd29` is the profile-authority correction merge from PR #321 and is the intermediate correction baseline named inside ADPR-0010's v1.5 preparation history;
- `7ee04b4319aaf1eab961b59d61cbef735fdb3aa4` is the exact merged v1.5 correction baseline reviewed by the targeted independent re-audit after the replay/chronology hardening merged through PR #325;
- `3ecbecc2e54b492427b0e2f02ae80a12a34da87f` is the later squash merge of PR #327 that added the completed targeted audit report to `main`; it is the `main` baseline from which the ADR drafting lifecycle began.

The targeted independent re-audit in `docs/ARCHITECTURE_AUDITS/adpr-0010-response-validator-targeted-reaudit.md` returned `READY_FOR_ADR` for exact audited baseline `7ee04b4319aaf1eab961b59d61cbef735fdb3aa4`, closed original PR #319 finding `F-001`, and identified no new material blocker in the v1.5 correction lineage. The drafting lifecycle was tracked by Issue #328 and merged through PR #329 as `42ea611e9fcc99b3dd06adee1879da1278a5e21a`.

ADR 0035's acceptance transition is carried by owner-authorized Issue #330 and PR #331. It becomes binding only when the repository owner merges PR #331; before that merge, the `Proposed` state already on `main` remains authoritative. This acceptance contribution changes no architectural decision in the drafted record, introduces no materially new architecture, and does not authorize runtime implementation.

### Proposed transient-isolation and reservation amendment lineage

[ADPR-0011](../architecture-records/ADPR-0011-adr-0035-transient-handoff-isolation-reservation.md), governed by Issue #336 and merged through PR #337 at `0cea851917afd9579aeaf3bb6261a8177d1e8153`, is the governing preparation for this narrow amendment. The final independent exact-head re-audit returned `READY_FOR_ADR` on `c26a1dae9f4635f51fd70c65748c760fcb335808` after verifying the protected-worker topology, non-durable transfer, ADR 0035-owned durable reservation, refusal semantics, Phase-A-compatible re-validation, and authority separation.

Issue #338 and Draft PR #339 govern amendment drafting and transposition only. The amendment reaffirms and does not supersede the accepted base decision. PR #335 / Issue #334 remain blocked until the amendment receives a separate owner-authorized acceptance transition and runtime resumption is separately authorized.

## Context

Accepted ADR 0034 deliberately ends the Model Adapter boundary after governed provider-response capture. Transport success, response capture, provider identity, and successful network delivery do not establish semantic response validity. Accepted ADR 0016 likewise prohibits implementation existence or successful AI execution from promoting output into canonical analytical authority.

Hunter therefore needs a distinct post-response boundary that can decide whether a captured response conforms to the exact requested-output contract and the exact historically applicable validation policy, while preserving the authority boundaries already established by ADR 0009, ADR 0020, ADR 0031, ADR 0032, ADR 0033, and ADR 0034.

The governing preparation established two separate architecture questions: where validation executes and who owns canonical validation-profile publication/history. The independent audit found the original preparation incomplete because the profile-authority ownership topology had not been fairly compared. ADPR-0010 v1.5 corrected that gap and then hardened strict-known replay/chronology so both base and corrected validation results have trusted decision-time and durable-knowability coordinates.

Phase B review later proved that same-process privacy could expose exact
non-retained response bytes and that a one-shot capture could be authorized to
multiple canonical events. ADPR-0011 prepared and independently audited the
narrow correction transposed below. It selects security and reservation
outcomes without choosing a concrete sandbox, process manager, IPC primitive,
or storage technology.

This decision must preserve the following invariants:

- provider transport and Model Adapter remain evidence/execution boundaries, not semantic-validity authorities;
- requested-output/schema ownership remains upstream under ADR 0031 and is consumed by validation rather than duplicated;
- Source Handling remains exclusively governed by ADR 0033;
- project-neutral/shared authority remains evidence-gated by ADR 0032;
- persistence remains mechanical and non-semantic under ADR 0009-style separation;
- historical replay remains strict-known and must never substitute current/latest state for historically knowable state;
- validation stops before extraction, recommendation, ranking, valuation, opportunity scoring, or canonical promotion.

## Decision

### 1. Canonical validation ownership

Hunter establishes a separate Evidence Intelligence `ResponseValidator` downstream of the Model Adapter and upstream of extraction/knowledge proposal.

`ResponseValidator` owns canonical response-validity decisions, base validation-event allocation, correction-decision allocation, validation authorization, and success/refusal attestation issuance. It does not own provider routing, Source Handling policy, requested-output/schema truth, canonical source truth, claim truth, valuation truth, ranking, opportunity decisions, or downstream promotion.

Validation execution is therefore not embedded in Model Adapter, provider-specific transport, persistence, or downstream extraction/promotion.

### 2. Canonical validation-profile authority

Hunter establishes a dedicated Evidence Intelligence `ResponseValidationProfileAuthority` as the canonical owner of validation-profile publication, immutable version/history, applicability, correction/supersession, rule-set identity, and deterministic historical resolution.

This authority is distinct from `ResponseValidator` execution. The validator consumes the canonical profile resolution but may not publish or ad hoc select policy.

A profile may compose upstream requested-output/schema identities with validation-specific policy including parser/canonicalization identity, evidence-reference structural checks, bounded resource rules, assigned capability/security checks, required validation dimensions, and the closed result/reason vocabulary. It must not duplicate or override authority already owned upstream.

The dedicated authority is selected over validator-owned history, reuse of the upstream requested-output/schema owner, persistence-owned registry authority, and premature generic/shared authority. A future shared authority requires a separate governed decision satisfying ADR 0032's multi-consumer admission rule.

### 3. Base validation-event allocation

Base validation is deduplicated before any semantic worker execution through a stable base-validation key that excludes per-run cutoff and binds the governed response-capture and requested-output/profile-selection coordinates required by the preparation.

`ResponseValidator` atomically create-if-absent allocates one canonical `validation_event_id` and one trusted `validation_cutoff` for a base key. Concurrent workers join that allocation rather than minting parallel event identities or cutoffs.

Worker retry/restart resumes the same event and cutoff. Explicit re-validation is a distinct governed operation and receives a new event/cutoff with fresh profile and Source Handling resolution.

Explicit re-validation preserves the predecessor `base_validation_key` and its
original `response_capture_identity`. Neither coordinate may be replaced inside
the allocated re-validation event. A fresh ADR 0034 capture instead has a new
`response_capture_identity`, produces a new `base_validation_key`, and allocates
a new generation-0 base validation event. Any causal reference from that fresh
base event to an earlier validation attempt or result is non-identity-bearing
metadata outside `ValidationEventAllocation`; it must not populate or imply
`predecessor_validation_event_id` on the generation-0 event and cannot alter
its event identity, cutoff, base key, or capture identity.

### 4. Governed correction allocation and chronology

Every semantic mutation of a `ResponseValidationRecord` is a governed correction decision. There is no clerical bypass that can alter validation semantics inside the immutable correction chain.

Before a correction generation is claimed, the trusted `ResponseValidator` correction allocator must read the exact current predecessor and its trusted durable-acceptance coordinate:

- generation 0 predecessor: persistence-assigned `validation_recorded_at`;
- later predecessor: persistence-assigned predecessor `correction_recorded_at`.

The allocator derives a candidate trusted `correction_cutoff` and must mechanically prove the coordinates comparable and `predecessor durable-acceptance <= candidate correction_cutoff`. Only then may the same atomic operation claim the exact next generation and allocate immutable `correction_decision_id` plus `correction_cutoff`.

If the lower-bound check is inverted or unprovable, allocation fails closed without claiming the generation or creating a correction-decision allocation. A later valid attempt may retry the still-unclaimed generation. After a successful allocation, retries resume that same allocation; workers and callers cannot mint, replace, backdate, or reallocate the cutoff.

Administrative annotations that do not change validation semantics may exist only on a separate non-authoritative surface and cannot alter the validation record, generation, state, per-dimension outcome, authority coordinate, lineage coordinate, input-availability mode, durability meaning, or downstream eligibility.

### 5. Validation-time Source Handling

Every validation and re-validation independently resolves ADR 0033 Source Handling at the event-owned cutoff before response content is processed. Attempt-time or capture-time permission is not reusable authorization.

Every correction uses only its allocator-issued `correction_cutoff` for correction-time Source Handling and profile resolution. Current wall-clock time, worker time, or caller-proposed time cannot substitute.

Restrictive, unavailable, conflicting, or unresolved Source Handling yields the governed refusal state/evidence required by ADR 0033. `ResponseValidator` consumes and enforces Source Handling authority but never creates, weakens, overrides, or extends it.

### 6. Validation authorization and transient input

After successful event allocation and semantic-processing prerequisites, `ResponseValidator` may issue a single-use, non-caller-mintable validation authorization bound to the exact event/cutoff, canonical profile resolution, requested-output contract, Source Handling resolution, capture/attempt lineage, and input mode.

The input mode is explicitly `DURABLE` or `TRANSIENT_NOT_RETAINED`. Model Adapter may carry exact matching credential-screened transient bytes solely to satisfy an authorized validation handoff, but it cannot select profile, event, cutoff, correction allocation, or Source Handling.

Successful validation does not itself authorize persistence of transient response bytes. Retention remains governed only by Source Handling authority.

#### Protected worker for `TRANSIENT_NOT_RETAINED`

For `TRANSIENT_NOT_RETAINED`, the ADR 0034 response-capture component and the
ADR 0035 semantic consumer execute inside one protected isolated worker. Exact
response bytes pass from capture to semantic consumption entirely inside that
protected boundary. Colocation is a security topology only: ADR 0034 remains
capture/attempt/handoff authority and ADR 0035 remains authorization and
semantic-validity authority.

The caller-facing process receives only non-content capture/attempt metadata,
authorization or refusal metadata, governed validation results, and diagnostics
permitted by Source Handling. It must not receive or recover:

- exact response-body bytes;
- a readable response-body descriptor;
- caller-readable shared memory containing the body;
- a socket endpoint, object reference, callback, or equivalent capability that
  can yield the body; or
- debugger, `ptrace`, process-memory, `/proc`-style memory/descriptor, dump,
  inspection, logging, exception, or diagnostic access that recovers the body.

A distinct PID alone is insufficient. A same-UID subprocess or any other
worker that remains reflectively, debugger-, memory-, or descriptor-readable by
the caller-facing process is insufficient. The worker must operate behind an
OS-enforced security boundary—such as an appropriate distinct security
principal, sandbox, namespace boundary, or equivalent combination—whose
falsifiable outcome prevents all caller-side recovery paths above. No readable
body descriptor may be inherited by or transferred to the caller, no body
shared-memory region may be caller-readable, and the worker must be non-dumpable
or equivalently inspection-restricted where the platform supports that control.
Administrative or root compromise of the host is outside this threat model.
This decision requires the security outcomes, not one particular isolation
primitive.

#### Non-durable body transfer

The non-retained exact body must not be materialized in a named filesystem file,
an anonymous or temporary filesystem-backed file, caller-readable shared
memory, or any durable or reconstructable retained copy. Capture-to-consumer
transfer and consumption use only non-durable OS IPC or equivalent ephemeral
streaming entirely inside the protected worker boundary.

The worker may hold only the minimum ephemeral body state required for governed
capture screening and the one authorized semantic execution. ADR 0033 remains
the exclusive authority for retention, reconstruction, access, deletion, and
lifecycle. The protected topology and transfer mechanism grant none of those
permissions, and neither body bytes nor a body-recovery capability may return
to the caller-facing process.

#### ADR 0035-owned atomic reservation

Before exposing successful `TRANSIENT_NOT_RETAINED` authorization, ADR 0035
authorization atomically reserves the canonical capture identity through this
mapping:

`response_capture_identity -> owning validation_event_id`

The mapping uses atomic create-if-absent semantics. The first successful
canonical owner wins; the same `validation_event_id` retries or joins
idempotently; every different event conflicts. Reservation commits before
successful authorization is exposed and is never transferred, released, or
reassigned after timeout, cancellation, execution failure, successful
consumption, or cleanup.

No caller-selected alias, worker identity, authorization token, process
identity, or re-validation request identity may replace the canonical
`validation_event_id` as reservation owner.

ADR 0034 supplies the governed attempt, capture identity, response capture,
handoff evidence, and capture/attempt lineage. It does not own, publish, or
enforce a validation-event reservation registry. No companion amendment to ADR
0034 is required or authorized by this decision.

Committed reservation ownership is durable non-content authority metadata. A
canonical restart-surviving mapping or ownership tombstone must survive
authorization-authority restart, worker restart, timeout, cancellation,
execution failure, successful consumption, and cleanup. Process-local-only
storage is insufficient. The ownership record remains after bytes are consumed
or lost, contains no response bytes or reconstructable representation, grants
no body-reading capability, and does not claim that bytes remain available.
Persistence stores the already-decided mapping mechanically and may not choose
or re-decide ownership. This durability and uniqueness requirement does not by
itself mandate a globally distributed registry or any single storage primitive.

#### One capture, one event; retry and conflict

A `TRANSIENT_NOT_RETAINED` capture may be semantically consumed by at most one
canonical `validation_event_id`. This applies to every pair of distinct events,
including a base event versus explicit re-validation and two otherwise
legitimate base events whose requested-output or profile coordinates yield
different canonical events.

Same-event retry/join observes the durable owner and the same historically
bound event/cutoff/profile/Source Handling/output-contract/capture coordinates.
It does not mint sibling ownership, rerun the ownership race, or reinterpret
current/latest state. Retry may proceed to semantic execution only while the
exact transient body remains lawfully available. If the canonical event owns
the capture but the body is lost, exhausted, disappeared, unavailable after
consumption, or otherwise unavailable through the one-shot transfer at initial
execution or same-event retry time, that event deterministically fails closed
with the existing `INPUT_UNAVAILABLE` state.

Body unavailability is not `VALIDATOR_ERROR` merely because authorization
previously succeeded. `VALIDATOR_ERROR` is reserved for a failure after an
authorized semantic execution has actually begun. This rule adds no state and
does not alter section 8 precedence. The event must not search for a retained
copy, reconstruct the body, switch its authorized input mode from
`TRANSIENT_NOT_RETAINED` to `DURABLE`, or re-call a provider. A competing event
fails closed before semantic execution.

Explicit re-validation remains a distinct event over the predecessor base key
and original capture identity. If another event owns or consumed the capture,
or the exact non-retained bytes are unavailable, re-validation yields the
existing `INPUT_UNAVAILABLE` state. A fresh capture must not be substituted into
that allocated event. A separately governed fresh ADR 0034 observation creates
a new capture identity, new base key, and new generation-0 base event under the
identity rules in section 3; it is not replay or reconstruction and does not
claim equality with the earlier response.

#### Reservation refusal boundary

Reservation conflict uses only the existing top-level `INPUT_UNAVAILABLE`
state. This amendment adds no validation state and does not change section 8
precedence. In particular, the existing relative ordering
`SOURCE_HANDLING_BLOCKED > VALIDATOR_ERROR > INPUT_UNAVAILABLE` remains intact;
the full ordering, including `VALIDATOR_CAPABILITY_UNKNOWN`, remains exactly as
listed in section 8.

Phase B produces the canonical in-memory refusal outcome and state-compatible
refusal attestation for the losing canonical event. It does not append a
terminal durable `ResponseValidationRecord` or create
`validation_recorded_at`. Those operations remain in the later persistence
phase governed by sections 7 and 9. Same-losing-event retry/join deterministically
reproduces or joins the refusal from canonical event coordinates and durable
ownership. Future terminal persistence may mechanically preserve the already-
decided event/refusal/attestation/capture/reservation lineage but must not
re-decide the reservation conflict.

### 7. Immutable validation records and persistence-owned durable acceptance

A base `ResponseValidationRecord` is immutable and append-only. It binds the governed event/subject, response-capture and execution lineage, requested-output contract, validation cutoff, closed state, authorized per-dimension outcomes, input-availability mode, diagnostics allowed by policy, and re-validation lineage.

Neither caller, worker, proposal, authorization, nor attestation supplies authoritative durable-known time. In the same atomic operation that successfully appends the base terminal record, persistence assigns immutable `validation_recorded_at` from its trusted durable-acceptance clock.

Before accepting the base append, persistence must mechanically verify the trusted allocator-issued `validation_cutoff <= validation_recorded_at` in a governed comparable time domain. If the coordinates are incomparable or inverted, append fails closed.

For every correction, the successor names exact predecessor and generation and binds the exact allocator-issued `correction_decision_id` and `correction_cutoff`. In the successful atomic append, persistence verifies predecessor/CAS, allocation, attestation, and chronology; assigns immutable `correction_recorded_at`; and requires:

`predecessor durable-acceptance <= correction_cutoff <= correction_recorded_at`.

Persistence may verify identities, exact allocation, attestation, uniqueness, chronology, and CAS mechanics. It may not choose validation policy, decide semantic validity, rerun validation, or become profile authority.

### 8. Closed validation vocabulary and deterministic precedence

Canonical top-level validation states are closed and reject unknown values. The states are:

`VALID`, `INVALID_SYNTAX`, `INVALID_SCHEMA`, `INVALID_OUTPUT_CONTRACT`, `INVALID_LINEAGE`, `INVALID_EVIDENCE_REFERENCE_STRUCTURE`, `PARTIAL_RESPONSE`, `INPUT_UNAVAILABLE`, `RULE_UNAVAILABLE`, `VALIDATOR_CAPABILITY_UNKNOWN`, `EVIDENCE_AMBIGUOUS`, `SOURCE_HANDLING_BLOCKED`, `SECURITY_BLOCKED`, and `VALIDATOR_ERROR`.

When multiple conditions apply, deterministic highest-first precedence is:

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

### 9. Non-forgeable success and refusal attestations

Base semantic results require a validator-issued single-use success attestation bound to the exact proposed semantic payload and its governed event/profile/Source Handling/lineage coordinates. Pre-semantic refusal states require a distinct state-compatible refusal attestation.

Every correction similarly requires a state-compatible validator-issued attestation bound to exact corrected payload, predecessor/generation, and exact allocator-issued correction decision/cutoff.

Success and refusal attestations are non-substitutable. They cannot mint or choose durable-acceptance timestamps, and correction attestations cannot mint or alter correction cutoffs.

Persistence mechanically verifies and atomically consumes the required capability/attestation and stamps the applicable durable-acceptance coordinate only on successful append.

### 10. Strict-known replay and re-validation

Historical replay never invokes a provider and never substitutes current profile, current Source Handling state, current repository state, proposal time, attestation time, caller timestamps, or wall-clock time for accepted historical coordinates.

A base validation record is eligible at replay cutoff only when both `validation_cutoff` and persistence-assigned `validation_recorded_at` are at or before that replay cutoff.

A correction is eligible only when both its allocator-issued `correction_cutoff` and persistence-assigned `correction_recorded_at` are at or before replay cutoff and its governed decision/authority lineage matches the exact correction allocation.

Replay first filters by trusted decision-time and durable-knowability coordinates and only then chooses the highest eligible generation. Generation ordering does not by itself prove historical knowability.

Transient content that was not retainable replays only the durable validation result and explicit `TRANSIENT_NOT_RETAINED` condition; historical reconstruction is never satisfied by re-calling a provider.

Ordinary retry is not re-validation. Explicit re-validation receives a new event/cutoff and fresh authority resolution and does not rewrite prior history.

For `TRANSIENT_NOT_RETAINED`, explicit re-validation retains the predecessor
base key and original capture identity. Unavailable or already-owned/consumed
bytes produce `INPUT_UNAVAILABLE`; current/latest or fresh response content
never substitutes. A fresh capture begins a new generation-0 base event and may
carry only non-identity-bearing causal metadata outside
`ValidationEventAllocation`, never a `predecessor_validation_event_id` or other
identity implication on that base event.

### 11. Correction concurrency and CAS semantics

Corrections are append-only, governed, and non-branching. Each correction names the exact current predecessor and next generation.

The trusted correction allocator checks the predecessor durable-acceptance lower bound before generation claim. Once one allocation succeeds, concurrent workers cannot create sibling cutoffs for the same claimed generation and may only resume that allocation.

Persistence uses atomic compare-and-set so concurrent sibling successors cannot both durably succeed. Any attempt to mutate a validation record without the governed correction allocation is rejected.

### 12. Validation dimensions and authority ceiling

`ResponseValidator` may decide only profile-encoded and evidence-supported validation dimensions such as syntax, schema/shape, requested-output conformance, required/forbidden fields, bounded type/range/enum constraints, lineage consistency, evidence-reference structural integrity, partial/missing-response classification, and explicitly assigned forbidden-capability/security structure checks.

It cannot decide source truth, claim truth, valuation truth, ranking, opportunity, recommendation, portfolio action, or canonical promotion.

### 13. Downstream stop boundary

The validator creates no extraction proposal and performs no canonical promotion.

A separately governed downstream extraction/knowledge-proposal service may consume only validation states permitted by its own contract, normally `VALID`, while preserving exact validation identity and lineage. Canonical promotion remains owned by the separately accepted authority that already governs that domain.

### 14. Mandatory transient-validation conformance

Any later implementation of this amendment must prove at minimum:

1. the ADR 0034 capture component and ADR 0035 semantic consumer execute inside
   the same protected worker for `TRANSIENT_NOT_RETAINED`, while the caller-
   facing process receives only non-content metadata and results;
2. caller-facing code cannot recover the exact transient body through object
   inspection, inherited descriptors, shared memory, sockets, callbacks,
   exceptions, logs, diagnostics, debugger attachment, process-memory reads, or
   `/proc`-style memory and descriptor surfaces;
3. validator-only isolation is rejected because ADR 0034 response capture must
   also occur inside the protected worker;
4. a distinct-PID or same-UID subprocess that remains reflectively, `ptrace`-,
   debugger-, memory-, or descriptor-readable by the caller is rejected as
   non-conforming;
5. the protected worker is non-dumpable or equivalently inspection-restricted
   where supported, and no readable body descriptor or body shared-memory
   region is inherited by or exposed to the caller;
6. exact transient bytes are never materialized in named, anonymous, or
   temporary filesystem-backed files, caller-readable shared memory, or a
   durable/reconstructable copy, and internal handoff uses only non-durable IPC
   or equivalent ephemeral streaming inside the worker;
7. ADR 0035, not ADR 0034, atomically reserves the capture identity during
   authorization and exposes no successful authorization before ownership is
   confirmed;
8. the canonical `response_capture_identity -> owning validation_event_id`
   mapping uses atomic create-if-absent and survives authorization-authority and
   validator-worker restart without relying on a process-local registry;
9. two different canonical events racing for one capture produce exactly one
   owner, and the loser receives `INPUT_UNAVAILABLE` before semantics;
10. two legitimate distinct base events with different requested-output or
    profile coordinates cannot both consume the same non-retained capture;
11. authority restart, worker restart, timeout, cancellation, execution
    failure, successful consumption, and cleanup never remove or transfer the
    committed owner;
12. same-event retry/join after restart observes the same owner and historically
    bound coordinates, while a competing event after restart still receives
    `INPUT_UNAVAILABLE` without rerunning the race;
13. when an event owns the capture but exact bytes become unavailable before
    initial execution or same-event retry, the outcome is `INPUT_UNAVAILABLE`,
    not `VALIDATOR_ERROR`; no provider re-call, retained-copy fallback,
    reconstruction, ownership race, sibling reservation, or input-mode
    substitution occurs;
14. post-consumption ownership remains as a tombstone even though the exact
    transient body is unavailable;
15. the durable reservation representation contains only canonical capture and
    owner identities and provably contains no body bytes or body-reading
    capability;
16. a losing event emits the canonical Phase-B in-memory refusal outcome and
    refusal attestation without appending a terminal `ResponseValidationRecord`
    or minting `validation_recorded_at`;
17. same-losing-event retry/join deterministically reproduces or joins the same
    refusal, and later terminal persistence preserves its lineage without
    re-deciding ownership;
18. explicit re-validation preserves the original `base_validation_key` and
    `response_capture_identity` and yields `INPUT_UNAVAILABLE` when the original
    non-retained bytes are unavailable or owned/consumed by another event;
19. a fresh capture identity is rejected as substitution into an allocated
    re-validation event and cannot mutate its event identity, cutoff, base key,
    or capture lineage;
20. a fresh ADR 0034 capture creates a new base key and generation-0 event; any
    causal reference to an earlier validation is non-identity-bearing metadata
    outside `ValidationEventAllocation`, does not populate or imply
    `predecessor_validation_event_id`, and does not reuse the earlier event's
    identity, cutoff, or base key;
21. no reservation, refusal, IPC, process-boundary, or causal-lineage artifact
    stores or logs exact non-retained response bytes or permits downstream
    extraction/promotion; and
22. mutation-style or equivalent non-vacuity protection proves that each
    reusable isolation, reservation, refusal, and identity guard fails when its
    prohibited path is reintroduced.

## Consequences

### Positive consequences

- Transport success and response capture cannot silently become semantic validity.
- Rule publication is separated from rule execution, reducing policy self-authorization risk.
- Base and corrected results have explicit trusted decision-time and durable-knowability coordinates.
- Historical replay is deterministic and strict-known, including correction chronology and delayed durable acceptance.
- Caller, worker, transport, and persistence cannot mint canonical profiles, cutoffs, validity, or semantic correction history.
- Non-retainable but processable response content can be validated without granting retention authority.
- Non-retained response bytes are structurally isolated from caller-facing
  processes while capture and semantic authorities remain distinct.
- Durable one-event ownership makes authorization deterministic across worker
  and authority restarts without retaining response content.
- Fresh-capture handling preserves Phase-A event identity instead of mutating
  an allocated re-validation event.
- Failed or incomparable correction chronology does not wedge the next correction generation.
- Downstream extraction and promotion remain separately governed.

### Costs and trade-offs

- Hunter gains a narrow `ResponseValidator` boundary and a separate validation-profile authority rather than folding both into an existing component.
- Append-only event, correction, attestation, and chronology contracts add persistence and concurrency complexity.
- Comparable trusted time domains or an accepted monotonic ordering contract are mandatory for base/correction chronology.
- Historical replay requires preserving enough exact identity and authority lineage to prove eligibility without current-state substitution.
- Non-retained validation requires an OS-protected worker, non-durable internal
  transfer, and durable non-content reservation metadata.
- One-shot reservation intentionally means that another canonical event cannot
  semantically consume the same transient capture, even when that event is
  otherwise legitimate.

### Risks controlled by this decision

- validator self-selection of policy;
- persistence becoming semantic authority;
- caller-supplied validity or timestamps;
- correction chain branching or clock-skew wedging;
- replay exposure before the governing decision or durable acceptance became knowable;
- use of attempt-time Source Handling as later validation permission;
- caller recovery of policy-non-retained response bytes through process memory,
  descriptors, shared memory, files, debugging, or inspection surfaces;
- restart or cleanup erasing reservation ownership and reopening a consumed
  capture to another event;
- fresh response capture mutating an already allocated re-validation identity;
- accidental canonical promotion from a successful validation result.

## Alternatives Considered

### Validation execution placement

1. **Separate Evidence Intelligence `ResponseValidator` — selected.** Preserves the strongest boundary between provider execution, semantic validation, and downstream promotion while supporting governed replay/idempotency and transient validation.
2. **Embed validation in Model Adapter — rejected.** Collapses ADR 0034's transport/response-capture boundary into semantic authority.
3. **Validate in extraction/knowledge layer — rejected.** Lets the downstream consumer judge its own input and couples validation to promotion.
4. **Generic shared validator core — deferred.** Requires separate evidence-backed admission under ADR 0032.
5. **Provider-specific validation — rejected.** Creates provider lock-in and grants provider/transport code canonical Hunter validity authority.

### Validation-profile ownership

1. **Dedicated `ResponseValidationProfileAuthority` — selected.** Separates rule-making from execution and provides one append-only strict-known profile history.
2. **Validator-owned profile publication/history — rejected.** Concentrates rule-making and result issuance in the same authority.
3. **Reuse/delegate to upstream requested-output/schema owner — rejected for current scope.** Upstream remains authoritative for its own schema/contract identity, but validator-only policy would materially widen ADR 0031 ownership.
4. **Persistence-owned profile registry authority — rejected.** Violates repository/persistence separation by turning storage into policy authority.
5. **Generic/shared profile authority — deferred.** Requires a later governed decision and independent multi-consumer evidence under ADR 0032.

### Transient isolation and reservation

1. **Protected capture-and-consumer worker with ADR 0035 reservation — selected.** Enforces non-retention against the caller-facing process while preserving ADR 0034 capture authority and ADR 0035 semantic/authorization authority.
2. **Validator-only isolation or a bare subprocess — rejected.** Capture outside the boundary leaves exact bytes caller-reachable, while a distinct PID or inspectable same-UID child does not prevent debugger, memory, or descriptor recovery.
3. **Filesystem-backed or caller-readable shared-memory handoff — rejected.** It creates a retained, reconstructable, or caller-readable body surface inconsistent with `TRANSIENT_NOT_RETAINED`.
4. **ADR 0034-owned validation-event reservation — rejected.** ADR 0034 supplies capture identity and handoff lineage but does not own validation events or authorization.
5. **Process-local or releasable reservation — rejected.** Restart, cleanup, or execution order could erase first-owner truth and allow a second event to consume the one-shot capture.
6. **Fresh-capture substitution into re-validation — rejected.** Phase A binds re-validation to the predecessor base key and original capture identity; a fresh capture begins a new generation-0 base event.

## Falsification and Reconsideration

The dedicated `ResponseValidationProfileAuthority` recommendation must be revisited through a new governed architecture decision if evidence proves before activation that the canonical validation profile contains no policy beyond an already-authoritative upstream requested-output contract; or if multiple independent consumers establish genuinely common validation-profile semantics that satisfy ADR 0032 without semantic loss; or if another authority topology can preserve the same rule-maker/executor separation, strict-known history, anti-forgery, and replay guarantees with materially lower governance cost.

Reconsideration must preserve historical profile identities, existing validation/correction lineage, and strict-known replay. It cannot retroactively relabel prior records as if a future authority owned them earlier.

## Compatibility and Migration

This decision extends ADR 0034 after response capture and reaffirms ADR 0033 Source Handling exclusivity, ADR 0031 requested-output ownership, ADR 0032 shared-core admission gates, ADR 0020 strict-known replay, ADR 0016 promotion limits, and ADR 0009 repository/service separation. The proposed amendment co-locates ADR 0034 capture and ADR 0035 semantic consumption only as a protected execution topology; it transfers no authority and requires no ADR 0034 amendment. None is superseded by this ADR.

ADR 0031 remains owner of requested-output and `ExtractionSchema` semantics;
validation establishes conformance only and grants no canonical truth or
promotion authority. ADR 0033 remains exclusive owner of retention,
reconstruction, access, deletion, and lifecycle decisions. ADR 0034 remains
owner of provider invocation, attempts, response capture, capture identity,
handoff evidence, and capture/attempt lineage. ADR 0035 owns validation event
identity, authorization, semantic validation, and the capture-to-event
reservation introduced by this amendment.

The legacy `AIExtractionProvider` / `SecureAIProviderRunner` path predates this architecture and cannot be relabelled as the canonical `ResponseValidator` or treated as satisfying these contracts merely because it performs limited screening or creates extraction proposals.

No synthetic backfill may fabricate validation events, profile resolutions, Source Handling resolutions, attestations, trusted cutoffs, durable-acceptance timestamps, or correction lineage for legacy provider artifacts. Historical absence remains explicit.

Migration to runtime implementation, if later authorized, must be additive and gated by separate implementation scope, tests, persistence/schema changes, replay conformance, and activation controls. Acceptance of this ADR changes no runtime behavior.

The proposed amendment is additive to the Phase-A event allocator. It does not
change base-key construction, re-validation identity, generation semantics, or
correction CAS/replay. PR #335 remains blocked pending amendment acceptance and
separate authorization to resume implementation.

## Non-Goals

This ADR does not authorize or design:

- runtime `ResponseValidator` implementation;
- implementation of the isolated worker, sandbox/process primitive, IPC, or
  reservation store;
- modification of PR #335 code or resumption of Issue #334;
- terminal `ResponseValidationRecord` persistence or creation of
  `validation_recorded_at` by Phase B;
- amendment of ADR 0034;
- new validation states or any change to section 8 precedence;
- correction allocation, correction CAS, or correction replay redesign;
- Issue #315 work;
- provider routing, fallback, ranking, hedging, or multi-provider activation;
- provider-invocation redesign or live provider activation;
- extraction or knowledge promotion;
- DefiLlama integration;
- source/claim/valuation truth;
- ranking, opportunity, recommendation, timing, portfolio, Dashboard, or scheduler work;
- governance redesign;
- synthetic historical validation backfill.

## Implementation Status

**Architecture accepted. Phase A foundation is implemented under the separately authorized Issue #332 contribution and is not activated in production.**

**The Issue #338 transient-isolation and reservation amendment is Proposed,
documentation-only, and not implemented or accepted by this drafting
contribution.**

Phase A adds the provider-independent canonical `ResponseValidationProfileAuthority` publication/history/resolution contracts, strict-known profile resolution, atomic base-validation and explicit re-validation event allocation, allocator-owned `validation_event_id` and `validation_cutoff`, retry/join semantics, and the closed ADR 0035 validation-state vocabulary and precedence. The implementation is confined to `hunter.evidence_intelligence.response_validator` and its mechanical persistence boundary, with deterministic/adversarial tests.

No semantic validation worker, parser/schema engine, validation authorization, success/refusal attestation, terminal `ResponseValidationRecord` persistence, `validation_recorded_at`, correction allocation/CAS, transient response-byte handoff, downstream extraction or promotion, provider routing/fallback, live provider invocation, or production activation is implemented by Phase A. Those surfaces remain separately governed and deferred.

Issue #334 / PR #335 remain blocked. They may resume only after the amendment is
accepted through a separate owner-authorized lifecycle and implementation
resumption is separately authorized. This amendment drafting contribution adds
no runtime code, storage, IPC, sandbox, terminal persistence, provider work, or
production activation.
