# ADR 0035: Evidence Intelligence ResponseValidator Boundary

## Status

Accepted.

The base decision remains Accepted. The transient-isolation and reservation
amendment governed by Issue #338 is **Accepted** through this owner-authorized
acceptance transition on PR #339. It becomes binding when PR #339 is merged.
This acceptance changes no runtime behavior and does not itself authorize
implementation.

## Date

2026-08-24.

## Governing Preparation

[ADPR-0010](../architecture-records/ADPR-0010-evidence-intelligence-response-validator.md) v1.5 is the governing preparation record for the accepted base decision.

The preparation lineage has three distinct coordinates that must not be conflated:

- `8ee6fd57577fa322b87cba21bd381d05770edd29` is the profile-authority correction merge from PR #321 and is the intermediate correction baseline named inside ADPR-0010's v1.5 preparation history;
- `7ee04b4319aaf1eab961b59d61cbef735fdb3aa4` is the exact merged v1.5 correction baseline reviewed by the targeted independent re-audit after the replay/chronology hardening merged through PR #325;
- `3ecbecc2e54b492427b0e2f02ae80a12a34da87f` is the later squash merge of PR #327 that added the completed targeted audit report to `main`; it is the `main` baseline from which the ADR drafting lifecycle began.

The targeted independent re-audit in `docs/ARCHITECTURE_AUDITS/adpr-0010-response-validator-targeted-reaudit.md` returned `READY_FOR_ADR` for exact audited baseline `7ee04b4319aaf1eab961b59d61cbef735fdb3aa4`, closed original PR #319 finding `F-001`, and identified no new material blocker in the v1.5 correction lineage. The drafting lifecycle was tracked by Issue #328 and merged through PR #329 as `42ea611e9fcc99b3dd06adee1879da1278a5e21a`.

ADR 0035's base acceptance transition was carried by owner-authorized Issue #330 and PR #331 and merged at `5510277b60a852c77470418cb45c629af3bf6fe4`. That acceptance changed no architectural decision in the drafted record, introduced no materially new architecture, and did not authorize runtime implementation.

### Accepted transient-isolation and reservation amendment lineage

[ADPR-0011](../architecture-records/ADPR-0011-adr-0035-transient-handoff-isolation-reservation.md), governed by Issue #336 and merged through PR #337 at `0cea851917afd9579aeaf3bb6261a8177d1e8153`, is the governing preparation for this narrow amendment. The final independent exact-head re-audit returned `READY_FOR_ADR` on `c26a1dae9f4635f51fd70c65748c760fcb335808` after verifying the protected-worker topology, non-durable transfer, ADR 0035-owned durable reservation, refusal semantics, Phase-A-compatible re-validation, and authority separation.

Issue #338 and PR #339 govern the amendment transposition and owner-authorized acceptance transition. The amendment reaffirms and does not supersede the accepted base decision. The exact-head targeted independent review returned `READY_FOR_ACCEPTANCE` on `8a5837ff2ec7c09ddd57829304f1f14905ab36b2`. The amendment becomes binding when PR #339 is merged. PR #335 / Issue #334 remain blocked until that merge and separate runtime resumption authorization.

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

The current Phase B implementation starts this protected worker with a fresh
`multiprocessing` **spawn** interpreter rather than `fork`. The implementation
therefore does not inherit live caller threads, Python locks, validator objects,
authority-store snapshots, or repository connections into the worker process;
post-dispatch validation authority remains resolved by the caller and is passed
only as non-content governed execution coordinates.

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
phase governed by sections 7 and 9.

### 7. Deterministic validation execution

Validation executes only after authorization and consumes the exact resolved profile, requested-output contract, evidence/input lineage, Source Handling decision, and capture/attempt lineage bound into that authorization.

Provider success is never semantic success. The validation outcome is derived only from the installed validation rules and the governed response input.

Execution must be deterministic for identical authorized inputs. The validator must reject caller-supplied semantic overrides, ad hoc schema substitution, profile substitution, cutoff substitution, or response-capture substitution.

### 8. Closed validation-state vocabulary and precedence

The closed state vocabulary is:

`VALID`, `INVALID_SYNTAX`, `INVALID_SCHEMA`, `INVALID_OUTPUT_CONTRACT`, `INVALID_EVIDENCE_REFERENCE`, `SECURITY_BLOCKED`, `SOURCE_HANDLING_BLOCKED`, `INPUT_UNAVAILABLE`, `VALIDATOR_CAPABILITY_UNKNOWN`, `RULE_UNAVAILABLE`, `VALIDATOR_ERROR`.

When more than one state applies, Hunter uses the canonical precedence installed by the accepted preparation. No provider, caller, transport, downstream consumer, or persistence layer may alter that ordering.

### 9. Persistence and correction boundary

Persistence mechanically stores already-decided canonical validation and correction records. It may verify exact identity/lineage equality and reject contradictions, but it may not derive semantic validity, select a profile, choose Source Handling, allocate correction chronology, or reinterpret authority.

Phase B does not authorize terminal validation-record persistence, `validation_recorded_at`, correction-record append, `correction_recorded_at`, correction CAS, correction replay, or downstream canonical promotion. Those remain later phases.

### 10. Downstream boundary

`VALID` is necessary but not sufficient for downstream use. Extraction, claim formation, ranking, valuation, recommendations, opportunity scoring, portfolio logic, and canonical knowledge promotion remain separately governed and are outside ADR 0035 Phase B.

## Consequences

- Response validation becomes a distinct Evidence Intelligence authority boundary rather than an incidental provider/model-adapter behavior.
- Historical replay is bound to event/correction-owned cutoffs and strict-known authority resolution.
- Non-retained response bytes remain inside the protected worker and are not recoverable through caller-facing process state.
- One transient capture has one durable canonical validation-event owner; retry cannot silently reassign it.
- Persistence remains non-semantic and terminal record persistence/correction remain deferred beyond Phase B.
- The implementation uses a fresh spawn worker specifically to avoid inherited caller runtime state while preserving the ADR's technology-neutral isolation outcome.

## Out of scope

Terminal validation-record persistence, validation/correction recorded times, correction allocation/CAS/replay, downstream extraction or knowledge promotion, provider routing/fallback/multi-provider redesign, live production activation, Issue #315, DefiLlama, valuation, ranking, opportunity scoring, recommendations, portfolio logic, Dashboard, scheduler, and unrelated governance redesign remain out of scope.
