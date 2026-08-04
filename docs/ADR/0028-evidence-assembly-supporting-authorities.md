# ADR 0028: Canonical Evidence Assembly Supporting Authorities

## Status

Proposed.

Governing preparation record: [ADPR-0005 — Canonical Evidence Assembly Supporting Authorities](../architecture-records/ADPR-0005-evidence-assembly-supporting-authorities.md). Unlike ADR 0027's precedent, ADPR-0005 has **not yet been independently reviewed** — its self-assessment is `READY_FOR_ADR`, recorded under [Issue #191](https://github.com/fafa33/Project-Hunter/issues/191). This ADR is drafted alongside that preparation record, for both to be reviewed together, and carries no authority until both the preparation record and this ADR are independently reviewed and separately accepted.

This ADR does not authorize implementation of any of the three authorities it defines, does not modify `CanonicalEvidenceAssemblyService`'s existing validation logic, does not resume [Issue #190](https://github.com/fafa33/Project-Hunter/issues/190) (which remains `BLOCKED`), and does not activate any Market Validation input. It authorizes only the ownership, record-family, persistence, versioning, correction, provenance, strict-known replay, conflict, and amendment-governance decisions stated below, for a future, separately authorized implementation issue to execute without inventing architecture.

## Context

ADR 0025 (Accepted) establishes the Canonical Evidence Assembly Authority, exercised by `CanonicalEvidenceAssemblyService`, and fully specifies what three supporting collaborators must validate before `assemble()` may produce an `AssembledFundamentalEvidenceRecord`: a methodology's declared evidence-input contract (§"Methodology contract"), a versioned Evidence Shape Registry (§"Evidence Shape Registry"), and per-record authoritative evidence semantics (§"Assembly preconditions," invariants 1-7, enforced in code by `_validate_authoritative_semantics`). ADR 0025 names a governance owner for Evidence Shape Registry *content* amendments ("the Canonical Evidence Assembly Authority," under an amendment discipline modeled on ADR 0023) but is silent on who implements and persists any of the three, and silent entirely on who owns Evidence Semantics classification.

`docs/ARCHITECTURE_AUDITS/issue-190-evidence-assembly-authority-gap.md` and ADPR-0005 confirm, by direct repository-wide search, that `MethodologyEvidenceInputContract`, `EvidenceShapeRegistry`, and `AuthoritativeEvidenceSemantics` are constructed nowhere in `src/` outside test-only fakes in `tests/test_canonical_evidence_assembly.py`. `CanonicalEvidenceAssemblyService.__init__` requires concrete instances of all three to construct the service at all, so `assemble()` — the operation ADR 0025 exists to authorize — has been unreachable in production since ADR 0025's acceptance.

ADPR-0005 evaluated ownership, persistence, versioning, correction, provenance, replay, conflict, and amendment-governance options for all three authorities and recommends the architecture this ADR authorizes.

## Decision

Hunter establishes three new supporting authorities, none of which acquire any right over `AssembledFundamentalEvidenceRecord`, `assemble()`'s validation logic, or any downstream composition authority (ADR 0026, ADR 0027 remain wholly unaffected):

1. **Methodology Contract Authority**, owned by `hunter.valuation_methodology`, persisting `MethodologyEvidenceInputContract` as a new record family sibling to the existing `ValuationMethodologySnapshot`. `ValuationMethodologySnapshot` itself, and its ADR-0022-fixed Milestone-2 invariants, are unmodified by this ADR.
2. **Evidence Shape Registry Authority**, owned by `hunter.evidence_assembly` — exactly as ADR 0025 already states — implemented as a new mechanical repository persisting versioned `EvidenceShapeRegistry` snapshots.
3. **Evidence Semantics Authority**, owned by `hunter.evidence_assembly` (not `hunter.value_capture`), implemented as a new mechanical repository persisting `AuthoritativeEvidenceSemantics` classification records that reference an unmodified `FundamentalEvidenceRecord` by exact ID and version.

All three follow the standard immutable bitemporal envelope ADR 0021 §"Required new record families and minimum fields" already establishes, with the addition of `logical_id`, `supersedes_record_id`, and `correction_reason` fields on `MethodologyEvidenceInputContract` and `AuthoritativeEvidenceSemantics` (absent from both today) to reach that standard.

This ADR selects logical record families, ownership, and semantics only — not a database product, schema migration, file path, or store activation, consistent with ADR 0021's and ADR 0025's own precedent for the same kind of decision. A future implementation issue chooses the concrete persistence mechanism.

### Methodology Contract Authority

- **Owner**: `hunter.valuation_methodology`. A future implementation issue must decide, and this ADR does not fabricate, whether the existing `CanonicalValuationMethodologyAuthority` gains this responsibility directly or a new sibling service owns it within the same package (ADPR-0005 Open Question OQ-003) — either satisfies this ADR's ownership requirement, which is package-level, not service-level.
- **Record family**: `MethodologyEvidenceInputContract` (`src/hunter/evidence_assembly/models.py:98-144`), unmodified in field meaning, plus the three added correction-lineage fields above, plus a new mandatory field cross-referencing the exact `ValuationMethodologySnapshot` record ID and version the contract was declared under.
- **Scope**: this ADR does not resolve whether the contract remains per-target (entity/representation/window-scoped, its current shape) or becomes methodology-global (ADPR-0005 Open Question OQ-001). A future implementation issue or follow-on ADR amendment must state which scope it implements; this ADR authorizes the record family and ownership under either reading.
- **Relationship to ADR 0025's existing "future `ValuationMethodologySnapshot` version" wording**: ADR 0025's "Exact amendment to ADR 0022" states "a future `ValuationMethodologySnapshot` version may declare acceptance" of assembled evidence. This ADR's sibling-record design satisfies that sentence's underlying intent — every `MethodologyEvidenceInputContract` is published for, and cross-references, an exact identified `ValuationMethodologySnapshot` version — without requiring the acceptance flag to be a literal field on `ValuationMethodologySnapshot` itself. This reading is not textually certain (ADPR-0005 Open Question OQ-004) and independent review must explicitly confirm it; this ADR does not treat the question as silently settled by adopting Option 1.
- **Not a second evaluation of methodology-contract input eligibility**: this authority persists and versions contracts only. It performs no eligibility evaluation of any kind. ADR 0025's assignment of methodology-contract input-eligibility evaluation exclusively to `CanonicalValuationService` — "not a second, corroborating, or 'final defensive' check of a prior evaluation" — is unchanged and unaffected; this authority does not become a second evaluator.
- **Persistence, versioning, correction, provenance, strict-known replay, conflict**: exactly as specified in ADPR-0005 §"Recommended Authority Design" → "Methodology Contract Authority," incorporated here by reference and binding on implementation.
- **Amendment governance**: a methodology author publishes a new `contract_version` to change acceptance terms; no in-place mutation. Whether contract authorship is itself governed by a future methodology ADR or is implementation-level configuration is left open, consistent with ADPR-0005.

### Evidence Shape Registry Authority

- **Owner**: `hunter.evidence_assembly`, reaffirming ADR 0025 §"Evidence Shape Registry" without change to that section's governance-owner designation.
- **Record family**: `EvidenceShapeRegistry` (`src/hunter/evidence_assembly/registry.py:13-53`), unmodified. No correction-lineage fields are added — a Registry version is immutable reference data, amended by publishing a new `version`, never corrected in place, consistent with ADR 0023's precedent for `SUPPLY_COHERENCE_RELATIVE_TOLERANCE`.
- **Persistence, versioning, correction, provenance, strict-known replay, conflict**: exactly as specified in ADPR-0005 §"Recommended Authority Design" → "Evidence Shape Registry Authority," incorporated here by reference and binding on implementation.
- **Amendment governance**: unchanged from ADR 0025's existing text — a new or changed shape requires its own accepted ADR amendment under the ADR 0023 pattern. This ADR supplies only the missing persistence-implementation authorization; it does not loosen ADR 0025's existing amendment discipline.

### Evidence Semantics Authority

- **Owner**: `hunter.evidence_assembly`. `hunter.value_capture`'s existing evidence-validation authority (ADR 0021, ADR 0022) is unmodified and gains no new responsibility; `FundamentalEvidenceRecord`'s field contract is unchanged.
- **Record family**: `AuthoritativeEvidenceSemantics` (`src/hunter/evidence_assembly/models.py:64-95`), plus the three added correction-lineage fields above, plus a new mandatory `classification_rule_reference: str` field naming the governed rule or ADR-authorized criterion that produced the classification.
- **Persistence, versioning, correction, provenance, strict-known replay, conflict**: exactly as specified in ADPR-0005 §"Recommended Authority Design" → "Evidence Semantics Authority," incorporated here by reference and binding on implementation.
- **Amendment governance**: classification must be produced by a governed, deterministic, auditable rule set — never opaque, unexplained per-record judgment (Constitutional Rule 6). This ADR does not authorize the exact rule engine (ADPR-0005 Open Question OQ-002); a future implementation issue or follow-on ADR amendment must define it before Evidence Semantics can be populated for any real evidence record.
- **Independence of the authoritative-semantics cross-check**: `_validate_authoritative_semantics` (`src/hunter/evidence_assembly/service.py:271-310`) exists to catch a caller-supplied `AssemblyConstituent` that misdeclares a constituent's shape, currency, unit, accounting meaning, supply basis, or pathway. That check remains meaningful even though the Evidence Semantics Authority and `CanonicalEvidenceAssemblyService` share a package owner (`hunter.evidence_assembly`): the untrusted input is the caller's `AssemblyConstituent` payload, not the package boundary between the authority and the service. This authority does not therefore validate its own classification against itself in any circular sense — it validates a separately-persisted, governed classification against an independent caller's claim.

### Current availability decision

Adoption of this ADR does not itself implement any of the three authorities, populate any Evidence Shape Registry version, author any Methodology Contract, or classify any evidence record's semantics. `CanonicalEvidenceAssemblyService.assemble()` remains unreachable in production until a separately authorized implementation issue builds all three repositories and Issue #190 (or a successor) is separately unblocked and completed. This is deliberate fail-closed behavior, consistent with ADR 0025's own "Current availability decision," not an implementation defect to bypass.

### Exact amendment to ADR 0025

This ADR amends only ADR 0025's §"Evidence Shape Registry" → "Governance owner" paragraph, appending to it, and adds a new subsection immediately following §"Methodology contract," exactly as stated here. No other section, invariant, validation rule, or record-family definition in ADR 0025 is changed.

ADR 0025's "Governance owner" paragraph currently reads, in relevant part:

> **Governance owner.** The Evidence Shape Registry is governed by the Canonical Evidence Assembly Authority under this ADR's own amendment mechanism... The Registry is never amended by a code-only commit, and never scoped, conditioned, or waived for one named entity or provider.

It is amended to add the following sentence, appended to that same paragraph, with the original sentences otherwise unchanged:

> *(As amended by ADR 0028: implementation and persistence of Evidence Shape Registry snapshots is owned by `hunter.evidence_assembly`, exactly the same authority this paragraph already names as governance owner — this amendment assigns an implementation path to an ownership decision this ADR already made, and changes no invariant, amendment-governance rule, or content-authorization requirement stated above.)*

A new subsection, "Supporting authority ownership," is added to ADR 0025 immediately after §"Methodology contract":

> ### Supporting authority ownership *(added by ADR 0028)*
>
> The Methodology Contract Authority referenced throughout this ADR — the party that persists and versions each `MethodologyEvidenceInputContract` — is owned by `hunter.valuation_methodology`. The Evidence Semantics Authority referenced in this ADR's "Assembly preconditions" (invariants 1-7) and implemented in `_validate_authoritative_semantics` — the party that persists and versions each `AuthoritativeEvidenceSemantics` classification — is owned by `hunter.evidence_assembly`. Neither assignment changes any invariant, precondition, validation rule, or record-family field this ADR otherwise defines; both close an ownership silence this ADR originally left for a future ADR to resolve. See ADR 0028 for complete record-family, persistence, versioning, correction, provenance, replay, conflict, and amendment-governance semantics for both authorities.

No other part of ADR 0025 — including its lossless-only rule, assembly preconditions, `AssembledFundamentalEvidenceRecord` field definitions, temporal and replay semantics, conflict-resolution rules, or compatibility table — is changed by this ADR.

## Consequences

Positive:

- `CanonicalEvidenceAssemblyService` gains a production-constructible path for all five of its constructor collaborators, once all three authorities are implemented — closing the gap ADPR-0005 and `docs/ARCHITECTURE_AUDITS/issue-190-evidence-assembly-authority-gap.md` identified.
- Issue #190's orchestration module gains a governed path to eventually expose `assemble()`, not only `status`, once implementation of all three authorities and this ADR's acceptance are complete.
- No already-Accepted record family (`ValuationMethodologySnapshot`, `FundamentalEvidenceRecord`, `AssembledFundamentalEvidenceRecord`) is modified or reopened; blast radius is limited to three new sibling record families.
- Every new authority follows established, already-audited patterns (bitemporal envelope, single-predecessor correction, strict-known replay, ADR-governed reference-data amendment) rather than inventing new mechanics.

Costs and risks:

- Three new record families, repositories, and (for Methodology Contract and Evidence Semantics) services must be implemented, tested, and independently reviewed before Issue #190 can be unblocked — this ADR authorizes architecture, not implementation effort.
- Adding a mandatory `logical_id` field to `MethodologyEvidenceInputContract` and `AuthoritativeEvidenceSemantics` is not fully backward-compatible: unlike `supersedes_record_id`/`correction_reason` (defaultable), every existing construction site in `tests/test_canonical_evidence_assembly.py` must be updated to supply it explicitly, since no other record family in this repository defaults `logical_id`.
- Evidence Semantics' classification-authoring mechanism remains genuinely open (ADPR-0005 Open Question OQ-002); implementation cannot begin on that authority's amendment-governance process until a follow-on decision resolves it, even though this ADR authorizes its ownership, record family, and persistence/replay mechanics now.
- Methodology Contract's per-target-versus-global scope (Open Question OQ-001) is left to implementation or a follow-on amendment, which risks a future implementer choosing inconsistently with what a later ADR might have preferred; mitigated by requiring the implementation issue to state its chosen scope explicitly rather than leave it ambiguous.

## Alternatives Considered

### Fold Methodology Contract fields directly onto `ValuationMethodologySnapshot`

Rejected. Requires an ADR 0022 amendment to loosen `ValuationMethodologySnapshot`'s Milestone-2-fixed invariants (`src/hunter/valuation_methodology/models.py:96-117`) for a capability ADR 0025 itself already scoped as applying only to "a future `ValuationMethodologySnapshot` version," not a Milestone-2 concern. The sibling-record-family design in this ADR achieves the identical outcome without reopening an already-Accepted, already-implemented, already-relied-upon record family. See ADPR-0005 §"Rejected Options."

### Give `hunter.value_capture` ownership of Evidence Shape Registry or Evidence Semantics

Rejected. Evidence Shape Registry ownership by `hunter.value_capture` directly contradicts ADR 0025's own existing text. Evidence Semantics ownership by `hunter.value_capture` would require adding Evidence-Assembly-only classification vocabulary (`shape_id`, `accounting_meaning`) to the native evidence contract ADR 0021/ADR 0022 already fix, for a capability `hunter.value_capture` has no independent mandate to know about. See ADPR-0005 §"Rejected Options."

### One new consolidated package owning all three authorities

Rejected. Methodology-level policy already has a canonical owner (`hunter.valuation_methodology`); a fourth package would duplicate that ownership, violating Constitutional Rule 5 (Single Source of Truth). See ADPR-0005 §"Rejected Options."

### Derive Evidence Semantics at `assemble()` time with no persistence

Rejected. Removes an already-Accepted protocol (`evidence_semantics_authority`) from `CanonicalEvidenceAssemblyService`'s constructor contract without itself being authorized as an ADR 0025 amendment, and forecloses provenance for the classification decision, violating Constitutional Rule 6 (Explainability). See ADPR-0005 §"Rejected Options."

### Defer all three authorities indefinitely

Rejected as the status quo this ADR exists to resolve. Leaves Issue #190 permanently blocked and ADR 0025's own stated purpose — closing the disclosure-granularity gap — permanently unrealized.

## Compatibility With Accepted ADRs

| ADR | Compatibility effect |
|---|---|
| 0009 | Every new repository remains mechanical; no eligibility, correction, or composition decision moves into a repository. |
| 0020 | Every new authority's retrieval is strict-known, bounded by `(identity, known_by)`; no current/latest fallback is introduced. |
| 0021 | Reaffirmed. The five-layer evidence model is unchanged; no new record family collapses or relabels an existing layer. `FundamentalEvidenceRecord`'s field contract is unmodified. |
| 0022 | Reaffirmed, unmodified. `ValuationMethodologySnapshot`'s Milestone-2 invariants are untouched — the rejected "field extension" alternative, above, is the only option that would have required amending this ADR. |
| 0023 | Reaffirmed and relied upon as the direct precedent for Evidence Shape Registry's (and, by design choice, Evidence Semantics') amendment-governance discipline. Unmodified. |
| 0024 | Unaffected; `valuation`'s scalar-semantics boundary is orthogonal to this ADR's supporting-authority ownership decisions. |
| 0025 | Amended narrowly — see "Exact amendment to ADR 0025," above. Every invariant, precondition, validation rule, and record-family definition ADR 0025 fixes is otherwise unchanged and reaffirmed. |
| 0026 | Unaffected. Comparative Valuation's exclusion from Assembled Fundamental Evidence, reaffirmed in ADR 0026 §"Compatibility," is untouched by this ADR. |
| 0027 | Unaffected. Market Validation's exclusion from any direct right to Assembled Fundamental Evidence, reaffirmed in ADR 0027 §"Compatibility," is untouched by this ADR. |

No accepted ADR is superseded, weakened, or contradicted by this ADR.
