# ADR 0033: Source Handling Classification Authority

## Status

Proposed.

## Date

2026-08-14.

## Governing Preparation

[ADPR-0008](../architecture-records/ADPR-0008-source-handling-classification-authority.md).

Independent architecture audit of that preparation has not yet been performed. This ADR cannot advance to acceptance until the preparation lifecycle required by `docs/ARCHITECTURE_DECISION_PREPARATION_GUIDE.md` and `docs/DEVELOPMENT_GOVERNANCE.md` is complete.

## Context

ADR 0031 requires that, before inclusion or durable persistence, every source reference must carry, or deterministically derive from governed source policy, a handling classification sufficient to decide whether its exact bytes may be processed, retained, and reconstructed. ADR 0031 further requires that artifact access controls and retention or deletion behaviour be at least as restrictive as the governing source classification and policy.

ADR 0031 imposes that obligation but does not assign the canonical owner of the handling facts or of the policy that governs them. No accepted decision names a producer, and the repository has no implementation of one. Without a named owner, the only participant able to supply the facts is the consumer that wants to use them, which makes the consumer the effective authority over its own permissions.

ADR 0033 closes that authority gap and nothing else.

## Decision

### Canonical ownership

The Evidence Intelligence consumer-side Source Handling Authority is the sole canonical owner of:

1. authoritative source-handling facts; and
2. governed source-handling policy, used to decide
   - whether source material may be processed,
   - whether it may be retained,
   - whether it may be reconstructed,
   - what access restrictions apply, and
   - what deletion and lifecycle restrictions apply.

Retention is one derived outcome of that policy. It is not the extent of the authority.

Callers, providers, orchestrators, prompt construction, persistence adapters, repositories, generic cores, Prompt Intelligence, Context Intelligence, and any future Model Adapter component are consumers only. They may not create, select, override, or substitute canonical source-handling policy authority. Persistence enforces this authority but does not acquire it.

### Binding safety invariants

- Caller and provider inputs are evidence or expectations only. They are never authority.
- Caller and provider inputs may never independently establish a less-restrictive handling state, and may never grant processing, retention, reconstruction, access, or deletion permission.
- Retainability is derived. It is not a source fact and is not assertable. The same holds for every other handling permission.
- Simultaneous restrictions must not be collapsed into a single mutually exclusive handling value.
- Any unknown, missing, unavailable, conflicting, or ambiguous required source-handling fact or policy authority yields `BLOCKED`, and no model-facing processing occurs.
- Partial or unclassified handling can never become permissive or processing-capable.
- No default, current state, caller reference, provider assertion, or permissive fallback may substitute for unresolved authority.
- Every handling decision — processing, retention, reconstruction, access, and deletion — derives only from authoritative historical handling facts together with the exact governed historical source-handling policy.

### Historical and replay invariants

- Authoritative handling facts and policies are immutable, versioned historical records.
- Corrections supersede. They never rewrite prior history.
- Replay uses only the authority applicable and knowable at the requested historical cutoff.
- Current, latest, or later-recorded state never substitutes for historical facts, historical policy, or historical absence.
- Historical absence remains explicit absence.
- The existence of bytes does not retroactively prove governed processing, retention, or reconstruction authority.

### Persistence invariant

Persistence must independently resolve the authoritative historical handling facts, resolve the exact governed historical source-handling policy, rederive every relevant handling decision rather than retention alone, verify every durable payload element against those decisions, and reject missing authority, mismatched inputs, mismatched decisions, or contradictory payload state.

Persistence must never trust a caller-created classification, policy object, policy selection, handling or retention decision, or claimed decision identity.

## Design / Implementation Contract — Deferred

The following are deliberately outside this ADR and belong to a separate design and implementation contract. This ADR binds the authority and the invariants above; it does not specify mechanics.

- exact handling dimensions and vocabularies;
- classification scope and mixed-content mechanics;
- admissible evidence and restriction-release rules;
- source and source-type derivation;
- record schemas;
- event and policy lifecycle mechanics;
- deterministic identities and canonicalization;
- historical applicability and selection algorithms;
- conflict and reconciliation mechanics;
- retention categories, dispositions, and build statuses;
- persistence mechanics;
- rejected-build audit details;
- legacy decoding;
- typed omission and redaction representation;
- secret and credential mechanics;
- APIs, modules, storage, and migrations;
- conformance and counterfactual tests.

A deferred topic is not an unconstrained one. Every item above must satisfy the invariants in this decision.

## Compatibility

- `EvidenceSpan` is unchanged.
- `EvidenceContextSourceReference` is not redefined. ADR 0031 already defines it.
- ADR 0031 remains the governing pre-model foundation and is reaffirmed, not superseded.
- ADR 0032 consumer-side ownership is unchanged; no part of this authority may enter a project-neutral core except through ADR 0032's evidence-gated admission rule.
- ADR 0020 strict-known replay is relied upon without amendment.
- ADR 0009 producer, repository, and consumer separation is reaffirmed.
- ADR 0004 is unaffected. Epistemic trust and data handling remain separate: a source may be authoritative without being retainable, and retainable without being authoritative.
- ADR 0016 is unaffected. No analytical output or promotion is created.

No accepted ADR is superseded by this decision.

## Non-Goals

This ADR does not define or authorize:

- span-level classification;
- exact runtime schemas or algorithms;
- generic Prompt Intelligence or generic Context Intelligence ownership;
- Model Adapter architecture or implementation;
- Response Validator architecture or business rules;
- provider routing or credential implementation;
- any redesign of Hunter Governance Review, or any LLM or provider dependency for it;
- retroactive purge or deletion of already-persisted records;
- any new analytical authority or `ANALYTICAL_AUTHORITY_REGISTRY` entry;
- trading, signalling, or portfolio-allocation behaviour;
- SaaS or public-product architecture.

## Implementation Status

Architecture only. This acceptance authorizes no runtime implementation.

Implementation remains subject to the separate design and implementation contract identified above, and to separately approved scope under the normal development lifecycle.

## Consequences

- Evidence Intelligence gains one named canonical owner for source-handling facts and the policy that governs them, closing the authority gap that blocks conformant retention work.
- Consumers that previously supplied handling inputs must instead resolve them from authority; a consumer without governing authority is blocked rather than defaulted into permission.
- Retention outcomes become derived and independently rederived at persistence rather than trusted, so a supplied decision carries no weight.
- Unresolved, partial, or conflicting authority stops model-facing work instead of receiving a permissive interpretation. Sources will require real handling coverage before such work proceeds.
- Historical replay is constrained to authority knowable at the cutoff, so some historical reconstruction will be explicitly unavailable rather than satisfied from current state.
- Mechanics are deferred, so this decision alone is not sufficient to implement against; the separate design and implementation contract must be produced and reviewed before implementation begins.
- Hunter Governance Review remains deterministic and free of any LLM or provider dependency.

## Alternatives Considered

### Leave handling facts and policy as consumer-supplied inputs

Rejected. Independent review and empirical reproduction established that a consumer able to supply the classification, its provenance, and the policy body controls the retention outcome regardless of how those inputs are typed. That is the condition ADR 0031 forbids, and type discipline at the consumption boundary does not remove it.

### Express handling as a single mutually exclusive value

Rejected. A single value cannot represent a source bearing several restrictions at once, so recording one restriction silently discards the others. This ADR therefore binds the invariant that simultaneous restrictions must not be collapsed, and defers the representation to the design contract.

### Extend ADR 0004 to cover data handling

Rejected. Epistemic trust answers whether a source is reliable and authoritative; data handling answers whether its bytes may be processed, retained, and reconstructed. Merging them would let an authoritative source imply a retainable one, which is the inference ADR 0031 prohibits.

### Register source-handling classification as an analytical output under ADR 0016

Rejected. It produces no analytical conclusion, and the accepted precedent for an equivalent evidence-layer control fact carries no registry entry. Registering it would imply an analytical semantics it does not have.

### Specify the full mechanics in this ADR

Rejected. Vocabularies, schemas, identities, selection and reconciliation algorithms, category dispositions, audit representations, and decoding rules are design and implementation concerns. Binding them here would make the ADR a runtime specification, would couple the authority decision to revisable mechanics, and would require an architecture decision to change details that ought to evolve under the invariants. The authority and its invariants are binding; the mechanics are deferred and constrained by them.
