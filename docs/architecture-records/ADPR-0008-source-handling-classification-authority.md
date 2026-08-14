# ADPR-0008: Source Handling Classification Authority

## Metadata

- ADPR ID: `ADPR-0008`
- Status: `READY_FOR_REVIEW`
- Version: 1
- Author: Claude Code / repository owner-directed architecture work
- Reviewers: independent architecture audit not yet performed
- Created: 2026-08-14
- Approved: not yet approved
- Related Epic: not yet created
- Related Issue: not yet created; owner-directed under PR #262
- Planned or produced ADR: ADR 0033 — Source Handling Classification Authority
- Supersedes: none
- Superseded by: none

## Executive Summary

ADR 0031 requires that, before inclusion or durable persistence, every source reference carry, or deterministically derive from governed source policy, a handling classification sufficient to decide whether its exact bytes may be processed, retained, and reconstructed. ADR 0031 imposes that obligation but does not assign an owner for the facts or for the policy that governs them.

With no owner assigned, the only participant able to supply those inputs is the consumer that wants to use them, which makes the consumer the effective authority over its own permissions. PR #260 demonstrated this empirically: a tampered retention decision was accepted at the persistence boundary and prohibited source text was durably retained.

Five materially distinct options were enumerated and compared. The recommendation is to assign exclusive canonical ownership of both source-handling facts and governed source-handling policy to an Evidence Intelligence consumer-side Source Handling Authority, with all other components as consumers only, fail-closed behaviour on unresolved authority, immutable and versioned historical authority, and independent rederivation and enforcement at persistence.

Self-assessed ADR readiness is `READY_FOR_ADR`. Independent architecture audit has not yet been performed.

## Problem Statement

### Current condition

ADR 0031 requires a governed handling classification but names no producer. No accepted decision assigns ownership of source-handling facts or of the policy governing their use. `EvidenceContextSourceReference`, the ADR 0031 contract defined to carry the classification, has no implementation. Consumers therefore supply the inputs that determine their own permissions.

### Desired condition

One named canonical owner produces authoritative source-handling facts and governs the policy applied to them. Every other component consumes those facts and that policy read-only. Unresolved authority fails closed rather than defaulting to permission, historical authority is immutable and selected strictly by cutoff, and persistence independently rederives and enforces the resulting decisions rather than trusting supplied ones.

### Decision required

Which component canonically owns authoritative source-handling facts, and which component canonically owns the governed source-handling policy applied to them.

### In scope

- assignment of canonical ownership for source-handling facts;
- assignment of canonical ownership for governed source-handling policy;
- the consumer boundary for every other component;
- fail-closed behaviour on unresolved, missing, conflicting, or ambiguous authority;
- immutability, versioning, and strict-known historical selection of that authority;
- independent rederivation and enforcement at the persistence boundary.

### Out of scope

Representation and mechanics of every kind: handling dimensions and vocabularies, classification scope and mixed-content mechanics, admissible evidence and restriction-release rules, record schemas, event and policy lifecycle mechanics, deterministic identities and canonicalization, historical applicability and selection algorithms, conflict and reconciliation mechanics, policy categories and dispositions, persistence mechanics, rejected-build audit details, legacy decoding, typed omission representation, secret and credential mechanics, APIs, modules, storage, migrations, and conformance tests. Also out of scope: Model Adapter, Response Validator, generic Prompt or Context Intelligence, provider routing, span-level classification, retroactive purge or deletion, and any analytical authority.

## Problem Validation

The problem is validated by direct repository evidence rather than inference. ADR 0031 states the obligation and names no producer. No handling classification exists in the runtime. An implementation attempt that satisfied the obligation without an owner was independently reviewed and its failure reproduced empirically against a real branch, confirming that typed consumer-supplied inputs do not remove consumer authority.

## Motivation

Handling classification is a fact about a source, not a choice belonging to whoever consumes it. A consumer able to state the fact, its provenance, and the governing policy determines its own permissions. Without an assigned owner, every conformant implementation of ADR 0031's obligation reduces to that condition, so no amount of implementation care closes the gap. The gap also blocks dependent work: PR #260 cannot be corrected until the owner exists.

## Existing Architecture

- ADR 0031 defines the pre-model foundation, the handling obligation, and `EvidenceContextSourceReference`.
- ADR 0032 requires consumer-side ownership for trust, sensitivity, and data-handling classifications and prohibits project-neutral core ownership absent evidence-gated admission.
- ADR 0020 governs strict-known selection and prohibits latest or current fallback.
- ADR 0009 separates provider, service, repository, and persistence responsibilities.
- ADR 0004 governs epistemic trust: source reliability, identity confidence, conflict status, freshness, and unavailable states.
- Evidence Intelligence already owns an accepted, implemented precedent of the required shape: a source-level fact established at document intake from closed vocabularies, carrying provenance, recorded with both effective and recorded coordinates, appended immutably, and defaulted to its least-privileged value.
- `docs/ANALYTICAL_AUTHORITY_REGISTRY.md` governs analytical and analytical-looking outputs and lists no data-handling owner; the equivalent evidence-layer control fact carries no registry entry.

## Constraints

### Constitutional

Evidence-first and replay-first obligations apply. Missing evidence must remain missing and must not be represented as neutral or permissive.

### Governance and accepted ADRs

ADR 0031's obligation must be satisfied without amending it. ADR 0032 consumer-side ownership must be preserved. ADR 0020 strict-known semantics apply without amendment. ADR 0016 must not be engaged: no analytical authority may be created. No accepted ADR may be superseded.

### Technical

`EvidenceSpan` is a canonical record constructed in many places; expanding it carries wide blast radius. `EvidenceContextSourceReference` is already defined by ADR 0031 and must not be redefined.

### Operational

Existing intake paths must remain valid; a change that invalidates every current call site at once is not acceptable for a preparation whose purpose is to unblock work.

### Persistence and migration

Authority records must be immutable and versioned. Corrections must supersede rather than rewrite. Records written under an earlier schema must remain records of that schema, and later fields must not enter an earlier deterministic identity.

### Replay and historical reconstruction

Only authority applicable and knowable at the requested cutoff may be used. Current, latest, or later-recorded state must never substitute for historical facts, historical policy, or historical absence. Existing bytes are not proof of governed authority.

### Compatibility

ADR 0031 and ADR 0032 boundaries must remain intact. No duplicate canonical owner may be introduced.

### Security and privacy

The boundary must cover at least credentials and secrets, personal or sensitive material, licensed or restricted material, ephemeral or non-retainable content, and material removed from current `HEAD`. Transport and authentication credentials must be excluded from canonical artifacts structurally rather than by detection.

### Performance and scalability

Not material to this decision. Authority resolution is per document and per policy version, not per byte.

### Evidence and provenance

Every authoritative fact must carry provenance sufficient to audit how it was established.

## Evidence Inventory

| Evidence | Source | Quality | Limitation |
|---|---|---|---|
| Handling obligation with no named producer | ADR 0031 §"Data handling and retention" | Accepted ADR; direct | None |
| `EvidenceContextSourceReference` defined but unimplemented | ADR 0031 §Canonical Concepts; repository search returning no occurrences | Direct repository observation | None |
| No handling classification in the runtime | Repository search across `src/` | Direct repository observation | Absence evidence; bounded by search terms used |
| Existing classification is topical, not handling | Evidence Intelligence validation module | Direct repository observation | None |
| ADR 0004 covers epistemic trust only | ADR 0004 §Reasoning | Accepted ADR; direct | None |
| No data-handling owner among cross-cutting input authorities | `ANALYTICAL_AUTHORITY_REGISTRY.md` §Cross-Cutting Input Authority | Canonical document; direct | Registry governs analytical outputs; used here as absence evidence only |
| Consumer-supplied inputs leave consumer in control | Independent review of PR #260 plus empirical reproduction on head `003a211` | Reproduced behaviour | Specific to that implementation; generalized by reasoning, not by measurement |
| Accepted precedent for a provenance-bearing source-level fact | Evidence Intelligence intake and document-lifecycle records | Direct repository observation | Precedent is for authority verification, not handling |
| Consumer-side ownership required | ADR 0032 §Project Adapter Ownership | Accepted ADR; direct | Assigns category, creates nothing |

## Assumptions

These are assumptions, not evidence:

1. Handling facts are stable enough to be established at or near intake and corrected by supersession, rather than requiring recomputation per consumption.
2. Document-level granularity is sufficient for the initial decision; sources whose portions differ can be treated at their most restrictive applicable level without blocking practical work.
3. The volume of policy versions and classification corrections will remain small enough that immutable append-only history is operationally acceptable.

None of these assumptions determines the ownership decision; each affects only how comfortably the chosen owner can operate.

## Architectural Dimensions

- **Ownership location** — which component may produce authoritative facts.
- **Policy ownership** — which component may author and resolve the governing policy.
- **Scope of policy authority** — whether the governed decision is retention alone or the full handling boundary of processing, retention, reconstruction, access, and deletion.
- **Consumer boundary** — what other components may do with facts and policy.
- **Failure posture** — what happens when authority is unresolved.
- **Historical semantics** — mutability, versioning, and cutoff selection.
- **Enforcement point** — whether the durability boundary trusts or rederives.

## Candidate Options

### Option 1 — Leave facts and policy consumer-supplied

No owner is assigned. Consumers continue to supply classification and policy at the point of use, with type discipline as the safeguard.

### Option 2 — Evidence Intelligence consumer-side Source Handling Authority

Exclusive canonical ownership of both authoritative source-handling facts and governed source-handling policy is assigned to an Evidence Intelligence consumer-side authority. All other components are consumers only. Unresolved authority fails closed; historical authority is immutable, versioned, and selected strictly by cutoff; persistence independently rederives and enforces.

### Option 3 — Extend ADR 0004 to cover data handling

The existing trust layer is widened so that source reliability and identity confidence also carry data-handling meaning.

### Option 4 — Repository-wide or project-neutral handling classification core

A generic classification service owns handling for every consumer, including any future project.

### Option 5 — Attach mandatory classification fields to `EvidenceSpan`

Handling values become required fields on the canonical span record, inherited wherever spans are used.

## Comparative Analysis

| Dimension | Option 1 | Option 2 | Option 3 | Option 4 | Option 5 |
|---|---|---|---|---|---|
| Removes consumer authority | No | Yes | Partly | Yes | Partly |
| Satisfies ADR 0031 obligation | No | Yes | Partly | Yes | Partly |
| Preserves ADR 0032 consumer-side ownership | Not applicable | Yes | Yes | No | Yes |
| Provenance for the fact | None | Yes | Partly | Yes | No |
| Historical/bitemporal semantics | None | Yes | Partly | Yes | No |
| Blast radius | None | Low | Medium | High | High |
| Introduces duplicate authority | No | No | Yes, trust and handling conflated | Yes, competes with consumer owner | Yes, span and owner both assert |
| Follows an accepted repository precedent | No | Yes | No | No | No |

## Falsification Results

Attempts were made to defeat the recommended option rather than to support it.

- *Claim: typed consumer inputs are sufficient.* Falsified. Independent review plus empirical reproduction showed a consumer supplying classification, provenance, and policy retains control of the outcome, and that a tampered decision was accepted at persistence.
- *Claim: the analytical authority registry forbids this without an ADR 0016 promotion.* Falsified as stated. The registry governs analytical and analytical-looking outputs; the equivalent accepted evidence-layer control fact carries no registry entry. An ADR is still required for the ownership assignment, but the analytical promotion checklist does not apply.
- *Claim: no ADR is needed because ADR 0031 already governs the rule.* Falsified. ADR 0031 governs the obligation but names no producer, and repository governance states that where authority is absent no component may claim it.
- *Claim: a separate preparation record is unnecessary because the decision is owner-directed.* Falsified. The preparation guide makes preparation mandatory for changes to canonical authority or ownership, persistence semantics, and strict-known replay, and for a new ADR. Owner direction authorizes the objective; it does not waive the governed process. This record exists because that claim failed.
- *Attempt to find an existing owner.* Searched accepted ADRs, the analytical authority registry, and the runtime. None found. The recommended option is therefore an assignment, not a re-labelling.

## Rejected Options

**Option 1 — consumer-supplied.** Rejected: it is the condition ADR 0031 forbids, and it was empirically shown to fail. Reconsider only if a mechanism is demonstrated by which a consumer cannot influence its own permissions while still supplying the inputs.

**Option 3 — extend ADR 0004.** Rejected: epistemic trust and data handling answer different questions, and merging them would let an authoritative source imply a retainable one. Reconsider if a future decision unifies trust and handling deliberately, with that inference explicitly prohibited.

**Option 4 — generic core.** Rejected: ADR 0032 requires consumer-side ownership and gates any shared contract behind two-consumer evidence, which does not exist. Reconsider when a second real consumer demonstrates common semantics under that admission rule.

**Option 5 — `EvidenceSpan` fields.** Rejected: wide blast radius, no provenance, no bitemporal history, and classification made mutable with the span. ADR 0031 explicitly permits a governed reference instead. Reconsider only if span-level granularity becomes mandatory and a provenance-bearing carrier is still provided.

## Risks

- The chosen owner must in practice have access to the evidence needed to establish facts; if intake evidence proves insufficient for some source classes, those sources will remain unresolved and therefore blocked.
- Fail-closed behaviour on unresolved authority will block work that previously proceeded, which is intended but will be visible as reduced throughput until coverage exists.
- Deferring all mechanics means this decision alone is not implementable; a subsequent design and implementation contract is required, adding a step before dependent work resumes.
- Document-level granularity resolves mixed-sensitivity sources to their most restrictive value, which may over-restrict some documents until span-level granularity is separately authorized.

## Open Questions

- Whether a future decision should authorize span-level granularity, and on what evidence.
- Whether policy may ever permit metadata or hash retention where exact bytes are prohibited, and under what justification.
- Whether reclassification should eventually support a governed deletion or purge path, which is deliberately excluded here.

These are recorded as open. None blocks the ownership assignment.

## Constitution Review

No conflict identified. The recommendation strengthens evidence-first and replay-first obligations: it prevents missing authority from being represented as permission and prevents current state from substituting for historical authority.

## Governance Review

No unresolved governance or ADR conflict identified. ADR 0031 is extended by supplying its missing producer and is not amended. ADR 0032 consumer-side ownership is preserved. ADR 0020 is relied upon without amendment. ADR 0009 separation is preserved. ADR 0016 is not engaged because no analytical output is created. This record exists to satisfy the mandatory Stage 1 preparation requirement that applies to changes in canonical authority or ownership, persistence semantics, strict-known replay, and to a new ADR.

## Quality Assessment

Applying `docs/ARCHITECTURE_DECISION_QUALITY_STANDARD.md`:

| Dimension | Rating | Basis |
|---|---|---|
| Problem clarity | Strong | Gap is stated by an accepted ADR and confirmed by repository absence |
| Evidence quality | Strong | Direct ADR text, direct repository observation, and reproduced behaviour |
| Assumption discipline | Strong | Three assumptions stated separately; none determines the decision |
| Option coverage | Adequate | Five materially distinct options spanning do-nothing, consumer-side, trust-merge, generic core, and record expansion |
| Comparative fairness | Adequate | Consistent dimensions applied to every option |
| Falsification | Strong | Four claims tested and falsified, including one against this record's own necessity |
| Governance alignment | Strong | Boundaries of ADRs 0031, 0032, 0020, 0016, 0009, 0004 checked individually |
| Scope discipline | Strong | Mechanics explicitly excluded and enumerated as out of scope |

## Architecture Readiness

- Outcome: `READY`
- Rationale: the decision required is a single ownership assignment; the option set is complete; the constraints and boundaries are identified; no unresolved conflict with accepted architecture remains.
- Missing evidence: none material to the ownership assignment.
- Unresolved conflicts: none.

## ADR Readiness

- Outcome: `READY_FOR_ADR`
- Proposed ADR title: Source Handling Classification Authority
- Proposed ADR scope: canonical ownership of source-handling facts and of governed source-handling policy; the consumer boundary; fail-closed behaviour; historical immutability, versioning, and strict-known selection; independent persistence rederivation and enforcement.
- Decisions the ADR must fix: who owns the facts; who owns the policy; that all other components are consumers only; that unresolved authority fails closed; that historical authority is immutable and cutoff-selected; that persistence rederives rather than trusts; that the governed policy decides processing, retention, reconstruction, access, and deletion rather than retention alone.
- Matters the ADR must leave open: every representational and mechanical concern listed under "Out of scope", deferred to a separate design and implementation contract and constrained by the ADR's invariants.

## Final Recommendation

Adopt Option 2. Assign exclusive canonical ownership of authoritative source-handling facts and of governed source-handling policy to the Evidence Intelligence consumer-side Source Handling Authority, with every other component a consumer only, fail-closed behaviour on unresolved authority, immutable and strict-known historical authority, and independent rederivation and enforcement at persistence.

Retention is one derived outcome of that policy, not the extent of the authority. The governed policy must decide whether source material may be processed, retained, reconstructed, what access restrictions apply, and what deletion or lifecycle restrictions apply, because ADR 0031's handling boundary covers all of those.

Option 2 is recommended because it is the only option that removes consumer authority, satisfies ADR 0031's obligation, preserves ADR 0032 consumer-side ownership, carries provenance and historical semantics, introduces no duplicate owner, and follows an accepted precedent already implemented in the owning package.

## Decision History

| Date | State | Change | Author or reviewer |
|---|---|---|---|
| 2026-08-14 | `READY_FOR_REVIEW` | Record created to satisfy mandatory Stage 1 preparation for ADR 0033 | Claude Code / owner-directed |

## Traceability

- Epic: not yet created
- Issue: not yet created; owner-directed under PR #262
- Preparation working document: this record
- Checklist review: `docs/checklists/ARCHITECTURE_DECISION_PREPARATION_CHECKLIST.md` applied by the author; independent audit not yet performed
- ADPR: ADPR-0008
- ADR: ADR 0033 (Proposed)
- Implementation plan: not authorized
- PR: #262
- Merge commit: not yet recorded
- Release: not yet assigned

## Immutability and Supersession

After `APPROVED`, this record is historical evidence. Corrections that change substantive reasoning require a new ADPR that explicitly supersedes this record. Non-substantive link completion and typographical corrections must remain auditable in version history.
