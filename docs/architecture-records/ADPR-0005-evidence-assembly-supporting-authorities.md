# ADPR-0005: Canonical Evidence Assembly Supporting Authorities

## Metadata

- ADPR ID: `ADPR-0005`
- Status: `READY_FOR_REVIEW`
- Version: 8 (preparation revision; governed ADR acceptance is revision 9)
- Author: Project Hunter Architecture Team
- Reviewers:
- Created: 2026-07-01
- Approved:
- Related Epic:
- Preparation Issue: [#191](https://github.com/fafa33/Project-Hunter/issues/191) (completed)
- Acceptance Issue: [#193](https://github.com/fafa33/Project-Hunter/issues/193)
- Planned or produced ADR: [ADR 0028](../ADR/0028-evidence-assembly-supporting-authorities.md)
- Supersedes:
- Superseded by:
- Self-assessment: `READY_FOR_ADR`
- Blocked implementation issue: [#190](https://github.com/fafa33/Project-Hunter/issues/190)
- Merged PR: [#192](https://github.com/fafa33/Project-Hunter/pull/192)
- Merge commit: `89a2c2c9c2714aa2391c1fb3f26de5ab3e36eb32`
- Branch: `claude/evidence-assembly-authority-preparation-issue-191`
- Merged revision: PR #192 documentation revision 8
- Validation status: documentation-only acceptance contribution; no `src/` or `tests/` changes; ADR 0025 ownership amendment is effective under accepted ADR 0028 revision 9; dependency direction and internal Markdown links validated

This record authorizes no implementation. Architecture is accepted through ADR 0028 revision 9; production implementation remains incomplete under Issues #194–#197. Issue #190 remains blocked until #197 concludes `IMPLEMENTABLE`.

## Problem

`CanonicalEvidenceAssemblyService` requires production collaborators for Methodology Contract, Evidence Shape Registry, and Evidence Semantics. Those collaborators do not exist in production.

Earlier preparation assumed Evidence Semantics could deterministically derive all classification dimensions from `(evidence_type, source_methodology, attribution_rule_id, unit)` on `FundamentalEvidenceRecord`. Independent review falsified architecture readiness: those observation fields do not authoritatively establish supply basis, pathway, representation continuity, or every future semantic dimension.

The problem cannot be solved by adding semantic fields to `FundamentalEvidenceRecord`. ADR 0021 requires native evidence to remain observation-only. The architecture therefore needs an upstream authority that owns semantic-classification inputs without changing native evidence.

## Desired condition

Provide a production-implementable, replay-safe architecture in which:

- one canonical upstream authority owns every semantic-classification input;
- native evidence remains unchanged and observation-only;
- Evidence Semantics consumes only that upstream authority;
- every output is immutable, provenance-complete, strict-known and correction-safe;
- dependency direction is acyclic;
- Issue #190 remains blocked until separate implementation and review.

## Scope

In scope:

- ownership and boundaries of the new upstream authority;
- its policy and output record families;
- persistence, identity, provenance, correction, conflict and replay;
- Evidence Semantics' revised input boundary;
- Methodology Contract and Evidence Shape Registry ownership needed by Issue #190;
- exact ADR 0021 and ADR 0025 amendments, now effective under accepted ADR 0028 revision 9;
- assembly semantic lineage.

Out of scope:

- production code or tests;
- first real policy content or classifications;
- CLI dispatch or activation;
- Issue #190 implementation or unblocking;
- downstream valuation-family or Market Validation activation.

## Governing constraints

- **Constitution Rule 2:** missing and conflicting information remain missing/conflicting.
- **Rules 4 and 5:** boundaries must be explicit and every concept must have one owner.
- **Rule 6:** authoritative semantics require reproducible reasoning and provenance.
- **ADR 0009:** repositories are mechanical; services own decisions.
- **ADR 0020:** canonical inputs require strict-known replay without current/latest fallback.
- **ADR 0021:** native Fundamental Evidence is observation-only; new canonical records require the full immutable bitemporal envelope.
- **ADR 0022:** methodology definition and final methodology-input eligibility remain with their existing valuation owners.
- **ADR 0023:** governed reference-data changes require versioned amendment discipline.
- **ADR 0025:** assembly remains lossless, strict-known, lineage-complete and fail-closed.
- **ADR 0026/0027:** no downstream authority receives assembled-evidence rights from this decision.

## Evidence inventory

| ID | Evidence | Verified fact | Architectural consequence |
|---|---|---|---|
| E-001 | `src/hunter/evidence_assembly/service.py:65-78,104-148,271-310` | Production construction and `assemble()` require all three collaborators; semantics are mandatory and fail closed. | The missing authorities are real runtime dependencies. |
| E-002 | `src/hunter/evidence_assembly/models.py:64-125` | Existing semantics and contract dataclasses lack production ownership/persistence and complete envelopes. | New canonical persistence decisions are required. |
| E-003 | `src/hunter/value_capture/models.py:80-112` | `FundamentalEvidenceRecord` does not carry the required semantic dimensions. | Those dimensions cannot be treated as observed facts. |
| E-004 | ADR 0021 | Native evidence and derived/canonical interpretation occupy distinct authority boundaries. | Native records must remain observation-only. |
| E-005 | ADR 0025 | Assembly requires compatible pathway, supply basis, accounting meaning, shape and continuity with exact replay. | A complete authoritative semantic-input source is mandatory. |
| E-006 | Repository-wide search | Only test fakes implement the three required protocols. | Issue #190 cannot lawfully supply them today. |
| E-007 | Independent review of revision 4 | The limited-native-field classification assumption was explicitly identified as unproven and architecture-changing if false. | The assumption must be removed, not deferred to first policy content. |

All evidence above is direct repository or accepted-governance evidence. No external source is required.

## Assumptions

None material. In particular, this revision does not assume that observation fields determine semantic classifications. The upstream policy is the authoritative source of those outputs, and absence or ambiguity is unavailable/conflict.

## Architectural dimensions

- **Owner:** `CanonicalEvidenceSemanticInputAuthority` in `hunter.evidence_semantic_inputs`.
- **Persistence:** mechanical `EvidenceSemanticInputRepository`.
- **Policy:** immutable, ADR-governed `EvidenceSemanticInputPolicySnapshot`.
- **Representation continuity proof:** the policy snapshot itself is the sole canonical proof; no separate proof record family exists.
- **Output:** immutable `EvidenceSemanticInputRecord`.
- **Identity:** exact native record/version plus policy lineage; content-addressed records.
- **Replay:** strict-known policy and output selection at explicit effective/known coordinates.
- **Correction:** append-only, single successor, no branching.
- **Conflict:** multiple non-equivalent matches or roots remain conflict/unavailable.
- **Provenance:** exact native record, policy, rule and hashes.
- **Future dimensions:** governed schema, never free-form caller extension.
- **Dependency:** value capture → semantic inputs → evidence assembly; no reverse import.
- **Missingness:** no match or ambiguous match produces no accepted output.
- **Activation:** none in this decision.

### Selected representation continuity proof

Design B is selected: `EvidenceSemanticInputPolicySnapshot` is itself the sole canonical representation-continuity proof. No separate proof family is required because the same governance-authored snapshot contains the deterministic rules that assert continuity and the full immutable ADR 0021 envelope, including its canonical content hash; creating another family would split proof ownership and add an unauthorized authority.

`CanonicalEvidenceSemanticInputAuthority` is the sole canonical owner and `EvidenceSemanticInputRepository` is mechanical only. The proof identity is the snapshot `record_id`, fixed `logical_id`, `schema_version`, `semantic_version`, and `content_hash`. Strict-known lookup uses the exact policy logical ID, `effective_as_of`, and `known_by` coordinates, with no `latest` or `current` fallback. Provenance is the exact snapshot identity/hash, native input references, ordered rules, authorizing ADR and configuration, methodology fingerprint, and `authorized_by`. Correction is append-only, with one successor carrying `supersedes_record_id` and `correction_reason`; prior proof is never mutated. Replay resolves the exact persisted snapshot ID/version/hash and fails closed on missing, mismatched, or cutoff-ineligible proof.

The upstream `EvidenceSemanticInputRecord` and downstream `AuthoritativeEvidenceSemantics` copy the exact policy snapshot ID/version/content hash as the continuity-proof reference. This preserves deterministic replay and provenance, keeps ownership upstream with no authority leakage, and leaves no semantic or proof decision for implementation to invent.

## Candidate options

### Option A — Add semantic fields to `FundamentalEvidenceRecord`

Rejected. It makes observation own interpretation and violates ADR 0021.

### Option B — Derive all semantics inside Evidence Semantics from existing native fields

Rejected. The fields do not authoritatively determine every required dimension. This was the invalid assumption in revision 4.

### Option C — Caller- or manifest-supplied semantic inputs

Rejected. It creates unaudited authority, fabricated provenance and non-replayable outcomes.

### Option D — New canonical upstream semantic-input authority

Selected. It preserves native evidence, establishes one owner, provides deterministic governed production, and gives Evidence Semantics a complete strict-known input.

### Option E — Put the upstream function inside Evidence Semantics

Rejected. The consumer would author the values it is supposed to validate, collapsing two authority boundaries and preventing independent provenance.

## Comparative evaluation

| Criterion | A | B | C | D | E |
|---|---:|---:|---:|---:|---:|
| Preserves observation-only evidence | No | Yes | Yes | Yes | Yes |
| Complete authoritative dimensions | Yes | No | Untrusted | Yes | Yes |
| One canonical owner | No | Ambiguous | No | Yes | Ambiguous |
| Strict-known replay | Possible | Incomplete | No | Yes | Possible |
| Independent provenance | No | No | No | Yes | No |
| Acyclic dependency | Yes | Yes | Yes | Yes | Yes |
| Constitution/ADR compliant | No | No | No | Yes | No |

## Selected design

Option D was selected exactly as specified in preparation revision 8 and is accepted by ADR 0028 revision 9:

- one new upstream package and canonical service;
- one mechanical repository;
- one governed policy record family;
- the `EvidenceSemanticInputPolicySnapshot` is the sole representation-continuity proof; no separate proof record family is introduced;
- one authoritative semantic-input output family;
- complete ADR 0021 envelopes;
- deterministic service-only production;
- strict-known selection and append-only correction;
- Evidence Semantics consumes only the upstream output;
- exact upstream lineage persists through `AuthoritativeEvidenceSemantics` and assembled evidence.

## Falsification

The selected design would be falsified if:

- an accepted ADR already assigned semantic-input authorship to another owner — none does;
- the new package required importing Evidence Assembly to function — it does not;
- callers could supply output dimensions — explicitly prohibited;
- policy selection depended on current/latest state — explicitly prohibited;
- replay could not resolve exact native and policy inputs — exact identities and hashes are mandatory;
- native evidence needed modification — explicitly prohibited.

No falsification condition is presently met.

## Ownership and dependency proof

`hunter.value_capture` owns observed native records only. `hunter.evidence_semantic_inputs` reads those records by exact reference and owns semantic-input policy and output. `hunter.valuation_methodology` owns the `MethodologyEvidenceInputContract` package, canonical record definition, production authority, persistence repository, and public read protocol. `hunter.evidence_assembly` consumes only the public upstream contracts and resulting immutable records; it owns downstream validation/assembly only.

There is no reverse dependency, no competing writer, and no duplicated canonical type. The semantic-input authority never imports Evidence Assembly, and the methodology-contract authority never imports Evidence Assembly. Evidence Assembly has no write path into either upstream owner, so both dependency paths remain acyclic.

## Persistence and replay proof

Both new families carry record/logical identity, schema/semantic versions, effective/recorded/known times, quality/conflict state, canonical hashes, provenance, authorization and correction lineage.

Services perform strict-known filtering and correction authorization. Repositories only persist and query. Historical reads resolve exact persisted identities and hashes; newer policy versions cannot reinterpret earlier records.

## Governance and amendment strategy

Accepted ADR 0028 amends ADR 0021 and ADR 0025 to register the new record families while expressly preserving `FundamentalEvidenceRecord` unchanged. The accepted ADR 0025 amendment assigns Representation Continuity Proof production and ownership exclusively upstream to `CanonicalEvidenceSemanticInputAuthority`, keeps Evidence Assembly consumption and validation-only, and requires constituent semantic lineage in assembled records. Methodology Contract ownership is accepted in `hunter.valuation_methodology`; no implementation or activation is established by this record.

First policy content requires a later accepted ADR amendment. That is governed data publication, not a deferred ownership or replay decision.

## Risks

- The additional authority increases implementation scope, but prevents permanent coupling of observation and interpretation.
- Policy content may initially classify few records; unclassified evidence remains unavailable rather than guessed.
- Future semantic dimensions require accepted schema/policy amendments, preventing uncontrolled extension.

## Open questions

None material. Concrete policy content and production activation are intentionally later governed acts.

## Constitution check

- Rule 2: unavailable/conflicting states fail closed.
- Rule 3: exact inputs and deterministic policy evaluation reproduce outputs.
- Rule 4: observation, semantic-input authorship, semantic validation and assembly are separate.
- Rule 5: each concept has one named owner.
- Rule 6: native, policy, rule, output and downstream lineage are persisted.
- Rule 7: policy and future dimensions evolve through immutable versions.
- Rule 8: ADR acceptance precedes implementation.

## Architecture readiness

- Outcome: `READY`
- Missing material evidence: none.
- Deferred architectural decisions: none.
- Deferred governed data: first policy content and first real contracts only.

## ADR readiness

- Outcome: `READY_FOR_ADR`
- Accepted decision: ADR 0028 revision 9, based on preparation revision 8.
- Architecture is accepted; production implementation remains incomplete and is governed separately by Issues #194–#197.

## Decision history

| Date | State | Change |
|---|---|---|
| 2026-08-04 | READY_FOR_REVIEW | Revisions 1–4 established and iteratively corrected Methodology Contract, Registry, Evidence Semantics and assembly-lineage architecture. |
| 2026-08-04 | READY_FOR_REVIEW | Independent review rejected the remaining assumption that limited native observation fields could authoritatively determine every semantic dimension. |
| 2026-08-04 | READY_FOR_REVIEW | Revision 5 selects a new canonical upstream Evidence Semantic Input Authority; native evidence remains unchanged and Evidence Semantics consumes only its strict-known output. |
| 2026-08-05 | READY_FOR_REVIEW | Preparation revision 8 moved the complete ADR 0025 Representation Continuity Proof ownership change into an acceptance-gated proposal, while preserving upstream-only production and making the MethodologyEvidenceInputContract package, record, persistence, production, public protocol, and dependency boundaries explicit. |

## Traceability

- Epic: not created.
- Preparation issue: #191 (completed).
- Acceptance issue: #193.
- Blocked issue: #190.
- ADPR: ADPR-0005 revision 8.
- ADR: ADR 0028 revision 9, Accepted.
- Merged PR: #192 (branch `claude/evidence-assembly-authority-preparation-issue-191`), merge commit `89a2c2c9c2714aa2391c1fb3f26de5ab3e36eb32`.
- Revision: 8.
- Validation: documentation-only acceptance correction; no `src/` or `tests/` changes; accepted ADR 0025 ownership amendment, dependency direction, and internal Markdown links validated.
- Architecture: Accepted.
- Implementation: Incomplete; Issues #194, #195, #196, and #197 remain outstanding. Issue #190 remains blocked until #197 concludes `IMPLEMENTABLE`.
- Historical Issue #193 acceptance contribution: `a71af2420ee0527ba7bf845068da675c7c3d0f82` (PR #198).
- Current substantive ADR-bearing commit: `1fdffad39e4beee0990d3680980ba31583813885`.
- Independent review remains required for the new exact source/target pair.
- Release: not assigned.
