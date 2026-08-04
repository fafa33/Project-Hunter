# ADPR-0005 — Canonical Evidence Assembly Supporting Authorities

## Metadata

- ADPR ID: `ADPR-0005`
- Status: `READY_FOR_REVIEW`
- Version: 2.0
- Author: Claude, on behalf of Issue #191
- Reviewers: independent hostile architecture review against revision 1 (Draft PR #192), five `CHANGES REQUIRED` findings, all resolved in revision 2 below; a second independent review round has not yet occurred
- Created: 2026-08-04
- Revised: 2026-08-04 (revision 2, resolving all five review findings)
- Approved: not yet approved
- Related Epic: not yet created
- Related Issue: [Issue #191](https://github.com/fafa33/Project-Hunter/issues/191)
- Related blocked Issue: [Issue #190](https://github.com/fafa33/Project-Hunter/issues/190) (Canonical Evidence Assembly Orchestration Module, Undispatched) — remains `BLOCKED` until this record is accepted and a resulting ADR is accepted
- Planned or produced ADR: `ADR 0028` (drafted alongside this record, Status `Proposed`; not yet accepted — see `docs/ADR/0028-evidence-assembly-supporting-authorities.md`)
- Supersedes: not applicable (this is a revision in place, not a superseding record — see "Immutability and Supersession")
- Superseded by: not applicable

## Executive Summary

`CanonicalEvidenceAssemblyService` (ADR 0025, Accepted) requires five constructor collaborators to perform its sole authorized operation, `assemble()`. Two are production-backed (`repository`, `native_evidence_query`). Three — `methodology_contract_authority`, `evidence_shape_registry_authority`, `evidence_semantics_authority` — have no production implementation anywhere in `src/`; every construction of their backing record types (`MethodologyEvidenceInputContract`, `EvidenceShapeRegistry`, `AuthoritativeEvidenceSemantics`) is a test-only in-memory fake confined to `tests/test_canonical_evidence_assembly.py`. This was discovered while evaluating Issue #190 and is documented in full, with file:line evidence, in `docs/ARCHITECTURE_AUDITS/issue-190-evidence-assembly-authority-gap.md`.

**Revision 2 supersedes revision 1's four open questions with singular, fully resolved decisions**, produced in direct response to independent hostile architecture review of Draft PR #192 (five `CHANGES REQUIRED` findings). No material decision is deferred to implementation:

- **Methodology Contract Authority** — resolved as a two-record design, both owned exclusively by `hunter.valuation_methodology`: `ValuationMethodologySnapshot` gains one new field, `accepts_assembled_evidence: bool`, as the sole activation authority (conforming exactly to ADR 0025's literal text); a new per-target sibling record family, `MethodologyEvidenceInputContract`, is the declaration instance. Scope is resolved as **per-target** (per methodology-version × entity × representation × pathway × currency × unit × accounting-window) — not a preference, but a structural requirement of `CanonicalEvidenceAssemblyService._validate_methodology_contract`'s existing, unmodifiable field-by-field target comparison. Exact service owner: `CanonicalValuationMethodologyAuthority`. Exact repository owner: `ValuationMethodologyRepository`. Full canonical envelope, uniqueness constraint, and strict-known selection algorithm are specified without gaps.
- **Evidence Shape Registry Authority** — unchanged from revision 1: owned by `hunter.evidence_assembly`, exactly as ADR 0025 already states.
- **Evidence Semantics Authority** — resolved as a deterministic, governed-rule-driven classification authority owned by `hunter.evidence_assembly`, computed by a new service (`CanonicalEvidenceSemanticsAuthority`) from a new governed reference-data artifact (`EvidenceSemanticsClassificationRuleset`, structurally identical to the Evidence Shape Registry). Manual, per-record classification exceptions are **explicitly and permanently prohibited**. A separate, three-coordinate logical identity — `(evidence_record_id, evidence_record_version, ruleset_version)` — resolves revision 1's under-specified `(evidence_record_id, evidence_record_version)`-only identity.

Every one of revision 1's four open questions (OQ-001 through OQ-004) is resolved below, not carried forward. No production code is authorized by this record. Self-assessment: `READY_FOR_ADR`, unconditionally.

## Problem Statement

### Current condition

`CanonicalEvidenceAssemblyService.__init__` (`src/hunter/evidence_assembly/service.py:65-78`) requires `methodology_contract_authority`, `evidence_shape_registry_authority`, and `evidence_semantics_authority` as mandatory keyword arguments. No concrete class satisfying any of the three protocols (`service.py:41-54`) exists in `src/`. The service cannot be constructed with real collaborators, so `assemble()` — the operation ADR 0025 exists to authorize — is unreachable outside test fixtures.

### Desired condition

Each of the three missing authorities has:

- an explicit, single canonical owner (Constitutional Rule 5, Single Source of Truth);
- a defined, immutable, bitemporal record family, consistent with every other record family in this repository;
- defined persistence, versioning, correction, provenance, strict-known replay, and conflict semantics, with a fully specified strict-known selection algorithm that resolves to exactly one record, never ambiguously;
- a defined amendment-governance mechanism for changes to its content over time, including an explicit position on whether manual/human exceptions are ever permitted;
- a production-constructible implementation path that a future implementation issue can execute **without inventing any architecture** — every material decision is made in this record and its resulting ADR, not left for an implementer to choose.

### Decision required

The resulting ADR must fix, and this revision resolves, all of the following:

1. ownership of `MethodologyEvidenceInputContract`, `EvidenceShapeRegistry`, and `AuthoritativeEvidenceSemantics` — **resolved**: `hunter.valuation_methodology` (Methodology Contract, jointly with a new `ValuationMethodologySnapshot` field), `hunter.evidence_assembly` (Evidence Shape Registry, Evidence Semantics);
2. the record family/envelope for each, including every field ADR 0025's current dataclasses lack — **resolved**, full envelope specified for all three (see ADR 0028 §"Canonical envelope");
3. persistence, correction, provenance, and strict-known replay semantics for each, consistent with ADR 0020/ADR 0021 — **resolved**, including an exact strict-known selection algorithm per authority;
4. the conflict-handling and amendment-governance mechanism for each, consistent with ADR 0023's precedent — **resolved**, including an explicit, permanent prohibition on manual Evidence Semantics exceptions;
5. `MethodologyEvidenceInputContract`'s exact scope — **resolved**: per-target, required by already-accepted, unmodifiable code, not a preference.

### In scope

- architecture and ownership preparation only, for the three named authorities;
- persistence, versioning, correction, provenance, strict-known replay, conflict, and amendment semantics for each, fully resolved with no deferred choice;
- one narrow ADR 0025 amendment and one narrow ADR 0022 amendment (see "Governance Review").

### Out of scope

- implementation of any authority (repository, service, or persistence code);
- any modification to `src/hunter/evidence_assembly/service.py`'s existing `assemble()` logic, invariants, or validation order;
- any modification to `src/hunter/valuation_methodology/service.py` or `repository.py`'s existing methods (only new methods/tables are authorized, additively);
- any modification to `src/hunter/__main__.py`;
- resuming Issue #190's orchestration module;
- granting `hunter.comparative_valuation`, `hunter.mispricing`, `hunter.asymmetry`, or Canonical Market Validation (ADR 0027) any new right to Assembled Fundamental Evidence — ADR 0025, ADR 0026 §"Compatibility," and ADR 0027 §"Compatibility" already fix that boundary and this record does not reopen it;
- changing `ValuationMethodologySnapshot`'s existing ADR-0022-locked Milestone-2 invariants (permitted model identifier, horizon, correlation group, normalization-policy gate) — the one new field this revision adds is purely additive and does not touch them;
- publishing the first `EvidenceSemanticsClassificationRuleset` version's actual rule content, or the first target-specific `MethodologyEvidenceInputContract` — both are future, separately ADR/implementation-governed acts;
- assigning production weights, activating Market Validation input, or any runtime activation decision.

## Problem Validation

ADR 0025 §"Methodology contract" states every methodology "must explicitly declare an evidence-input contract" but did not, in revision 1's analysis, fully account for how that declaration interacts with `ValuationMethodologySnapshot`'s existing entity-agnostic design. §"Evidence Shape Registry" names a governance owner for Registry *content* amendments but not an implementation/persistence owner. No section of ADR 0025 addresses who classifies or persists `AuthoritativeEvidenceSemantics`. `docs/ARCHITECTURE_AUDITS/issue-190-evidence-assembly-authority-gap.md` independently confirms, by repository-wide search, that none of the three exist in `src/` outside test fixtures. Independent hostile review of revision 1 confirmed the problem was real but found revision 1's resolution incomplete on five specific points (see "Decision History"). The problem is real, unresolved by any accepted document prior to this record, and architectural under `docs/ARCHITECTURE_DECISION_PREPARATION_GUIDE.md` §"Scope."

## Motivation

Without this decision, any future attempt to implement these authorities would face the same choice Issue #190 correctly declined to make unilaterally: invent ownership and persistence semantics inside an implementation PR (violating ADR-before-implementation, Guiding Principle 8), or block indefinitely. Leaving Evidence Assembly's write path permanently unreachable also prevents ADR 0025's own purpose — closing the disclosure-granularity gap described in its Context section — from ever being realized.

## Existing Architecture

| Boundary | Existing authority | Binding consequence |
|---|---|---|
| Assembled Fundamental Evidence construction | `CanonicalEvidenceAssemblyService` (ADR 0025) | Sole authority; already implemented; fail-closed on any missing collaborator. |
| Native Fundamental Valuation Evidence | `hunter.value_capture` (ADR 0021) | Owns `FundamentalEvidenceRecord`; no `shape_id`, `accounting_meaning`, or `supply_basis_id` field exists on it (confirmed by direct read of `src/hunter/value_capture/models.py:80-112`). |
| Valuation methodology declaration | `hunter.valuation_methodology` (ADR 0022) via `CanonicalValuationMethodologyAuthority` (`service.py:36`) | Owns `ValuationMethodologySnapshot` via `ValuationMethodologyRepository` (`repository.py:23`); every field is hard-locked to ADR-0022 Milestone 2 constants in `__post_init__` (`models.py:96-117`) except the new `accepts_assembled_evidence` field this revision adds. `strict_known_methodology(*, effective_as_of, known_by)` (`service.py:148-154`) carries no `logical_id` parameter, confirming the existing single-methodology-lineage (singleton) design this revision's Methodology Contract design builds on top of, not against. |
| Methodology contract consumption | `CanonicalEvidenceAssemblyService._validate_methodology_contract` (`service.py:339-387`) | Existing, unmodifiable code compares a fetched contract's `entity_id`, `representation_id`, `currency`, `unit`, `accounting_window_start/end` against the specific target being assembled — the direct evidence that resolves this revision's per-target scope decision. |
| Evidence Shape Registry content governance | ADR 0025 §"Evidence Shape Registry" | Already names "the Canonical Evidence Assembly Authority" as governance owner, under an ADR-governed amendment mechanism explicitly modeled on ADR 0023's. No persistence owner is named. |
| Supply basis | `hunter.value_capture` (ADR 0021) via `SupplyBasisSnapshot` | Already an accepted, owned record family; not itself a candidate constituent for Evidence Semantics, but its identity (`supply_basis_id`) is a field `AuthoritativeEvidenceSemantics` must classify against. |
| Downstream composition | ADR 0026, ADR 0027 | Explicitly reaffirm Evidence Assembly's isolation; grant no downstream authority over Assembled Fundamental Evidence. Unaffected by this preparation. |

## Constraints

### Constitutional

- Rule 2 (Evidence Authority): "Unknown information remains unknown... Missing information remains missing" — a semantics or contract classification that cannot be produced must remain explicitly unavailable, never defaulted or inferred silently.
- Rule 4 (Architectural Integrity): "Architectural boundaries, responsibilities, and ownership must remain explicit... Architectural convenience is never sufficient justification for violating established boundaries" — directly governs every ownership decision below.
- Rule 5 (Single Source of Truth): "Every architectural concept, analytical authority, and governance rule must have one canonical owner... Competing authorities, duplicated ownership, and conflicting definitions are prohibited" — directly governs the resolution of former OQ-003 (one service, not two) and the package-ownership/dependency-direction resolution below.
- Rule 6 (Explainability): "Hunter must preserve the reasoning, evidence, methodology, uncertainty, and provenance supporting every meaningful analytical output... Opaque authority is prohibited" — directly governs the resolution of former OQ-002 (manual exceptions prohibited; deterministic, governed ruleset required).

### Governance and accepted ADRs

- ADR 0009 places decisions in services and keeps repositories mechanical; every new repository method is mechanical only; the cross-service strict-known check (contract vs. governing snapshot) is service-owned.
- ADR 0020 requires exact strict-known selection, explicit missingness, and forbids current/latest fallback — binding on every new authority's retrieval method, including the ruleset-version selection algorithm.
- ADR 0021 fixes the five-layer evidence model and the standard record envelope; a new record family must not collapse or relabel an existing layer.
- ADR 0022 fixes `ValuationMethodologySnapshot`'s Milestone-2 invariants; this revision's one new field is additive and does not touch them, but is a formal ADR 0022 amendment nonetheless (see "Governance Review").
- ADR 0023 establishes the precedent and precondition pattern for versioned, ADR-governed reference data, directly applicable to both the Evidence Shape Registry's and the new `EvidenceSemanticsClassificationRuleset`'s amendment mechanisms.
- ADR 0025 fixes what all three authorities must validate; the resulting ADR narrowly amends its ownership silence, not its invariants.
- ADR 0026, ADR 0027 reaffirm Evidence Assembly's isolation from downstream composition; unaffected.

### Technical and operational

- No authority may be constructed as a stub or fake inside production orchestration code (the exact failure mode Issue #190 was correctly blocked to avoid).
- Every new repository must reject a `save`/`apply`/`write` method of its own eligibility logic, consistent with the AST-based repository-purity regression already enforced by `tests/test_valuation_family_repository_purity.py`.
- No new authority may perform evidence acquisition, parsing, or valuation arithmetic.
- `hunter.valuation_methodology` (upstream owner of Methodology Contract) must not depend on `hunter.evidence_assembly.service` or any Evidence Assembly business logic (downstream consumer); the one narrow, stated, data-type-only exception (importing the `MethodologyEvidenceInputContract` dataclass from `hunter.evidence_assembly.models`, forced by ADR 0025's already-fixed Protocol return type) is documented, not silently permitted — see ADR 0028's "Package ownership and dependency direction."

### Persistence, replay, and provenance

- All new records are append-only, content-addressed, and bitemporal, matching every other record family in this repository.
- Corrections are single-predecessor successors; branching is prohibited, matching `_authorize_correction`'s existing pattern in `hunter.evidence_assembly.service`, `hunter.valuation.service`, and `hunter.valuation_methodology.service`.
- Every new authority's strict-known retrieval must be resolved by exact identity coordinates and `known_by` only, never a "latest" or "current" query path — including ruleset-version selection, which is defined to select the highest *strict-known-at-cutoff* version, never the highest wall-clock-current version.

## Evidence Inventory

| ID | Evidence | Authority/source | Finding | Quality and limitations | Supports or challenges |
|---|---|---|---|---|---|
| E-001 | Project Constitution, Rules 2/4/5/6 | Canonical constitutional authority | Requires explicit missingness, explicit ownership, single ownership, and explainable classification. | Highest authority; non-numeric. | Supports explicit, single-owner authorities for all three; rejects opaque classification and manual exceptions. |
| E-002 | ADR 0025 (Accepted) | Accepted ADR | Fully specifies what all three authorities must validate; requires "the `ValuationMethodologySnapshot` in force" to be the explicit declarer of assembled-evidence acceptance. | Binding; primary source for this preparation. | Directly resolves former OQ-004: the activation authority must be the snapshot itself, not only a cross-referencing sibling record. |
| E-003 | `src/hunter/evidence_assembly/service.py:65-78, 104-135, 271-310, 339-387` | Direct code read | Constructor requires all three collaborators unconditionally; `assemble()` gates on each with no fallback; `_validate_methodology_contract` compares the fetched contract's entity/representation/currency/unit/window fields against the specific assembly target. | Reproducible; read directly, not inferred. | Establishes the problem is real and structural; directly resolves former OQ-001 — the contract is per-target by construction of this already-accepted, unmodifiable code. |
| E-004 | `tests/test_canonical_evidence_assembly.py:151-227` | Direct code read | Every construction of the three missing types is a named, explicit test fake. | Reproducible. | Confirms zero production implementation exists. |
| E-005 | `src/hunter/valuation_methodology/models.py:44-120` | Direct code read | `ValuationMethodologySnapshot` is hard-locked to ADR-0022 Milestone 2 constants; carries no entity/representation/window scope; already carries `supersedes_record_id`/`correction_reason`. | Reproducible. | Confirms the contract's per-target fields cannot be folded onto this record; confirms the snapshot already has correction lineage the new `accepts_assembled_evidence` field can safely ride on. |
| E-006 | `src/hunter/value_capture/models.py:80-112` | Direct code read | `FundamentalEvidenceRecord` has no `shape_id`, `accounting_meaning`, `supply_basis_id`, or standalone `currency` field. | Reproducible. | Confirms Evidence Semantics is genuinely new classification data, not a read of existing native fields. |
| E-007 | ADR 0023 (Accepted) | Accepted ADR | Establishes the precedent that versioned reference-data content changes require ADR governance, and that future value changes must carry a persisted version evaluated against the version in force at record-creation time, never current. | Binding precedent. | Directly informs both the Evidence Shape Registry's and the new `EvidenceSemanticsClassificationRuleset`'s amendment-governance and version-ordering design. |
| E-008 | ADR 0026 §"Compatibility," ADR 0027 §"Compatibility" | Accepted ADRs | Both explicitly reaffirm Evidence Assembly's isolation; grant no downstream right to Assembled Fundamental Evidence. | Binding. | Confirms this preparation must not, and does not, reopen that boundary. |
| E-009 | `src/hunter/evidence_assembly/models.py:64-144` | Direct code read | `MethodologyEvidenceInputContract` and `AuthoritativeEvidenceSemantics` already carry `effective_at`/`recorded_at`/`known_at`/`quality_state`/`conflict_state`/`content_hash` but lack `logical_id`, `supersedes_record_id`, and `correction_reason`. | Reproducible. | Identifies the exact gap the resulting ADR's "Canonical envelope" section closes for both record families. |
| E-010 | `docs/ARCHITECTURE_AUDITS/issue-190-evidence-assembly-authority-gap.md` | This session's prior work product | Full original gap analysis, independently re-verified in this preparation. | Reproducible. | Establishes problem validation. |
| E-011 | `src/hunter/valuation_methodology/service.py:36, 63-157` | Direct code read | `CanonicalValuationMethodologyAuthority`'s exact public method set (`persist_methodology`, `get`, `methodology_history`, `strict_known_methodology`, `unresolved_conflicts`); `strict_known_methodology` takes no `logical_id`, confirming a singleton-lineage design. | Reproducible. | Directly resolves former OQ-003: this is the exact, named, existing service the Methodology Contract's new methods attach to; no new service is warranted. |
| E-012 | `src/hunter/valuation_methodology/repository.py:23` | Direct code read | `ValuationMethodologyRepository` is the exact, named existing repository class. | Reproducible. | Directly resolves the repository-ownership half of Finding 5/1: this class, not a new one, is extended. |
| E-013 | `src/hunter/evidence_assembly/repository.py:56, 76-112` | Direct code read | `AssembledEvidenceRepository` already owns two record families (`AssembledFundamentalEvidenceRecord`, `AssemblyConflictRecord`) in one class, and its `_insert_authorized` already implements the exact divergent-duplicate-rejection pattern this revision reuses for both new record families. | Reproducible; already-audited precedent. | Confirms "one repository class, multiple record families" and "reject divergent duplicates at persistence time" are established, not invented, patterns. |
| E-014 | Independent hostile architecture review of Draft PR #192 | External review input (five `CHANGES REQUIRED` findings; see "Decision History") | Identified all four of revision 1's open questions as unresolved material decisions, and identified split/ambiguous Methodology Contract ownership as a Rule-5 violation. | Authoritative for this revision's scope; not independently re-verified against a recorded GitHub review artifact — see the governing session's report for this caveat. | Directly drives every design change in revision 2. |

## Assumptions

| ID | Assumption | Rationale | Confidence | Falsification condition | Consequence if false |
|---|---|---|---|---|---|
| A-001 | `EvidenceSemanticsClassificationRuleset`'s deterministic function can, in practice, be expressed as a total or explicitly-partial mapping from `(evidence_type, source_methodology, attribution_rule_id, unit)` to the six classification outputs, without needing additional native-record fields this ADR does not add. | These are exactly the fields `FundamentalEvidenceRecord` already carries that plausibly correlate with disclosure shape/structure; no other candidate fields exist on the record today. | Medium | A future ruleset author finds these four fields insufficient to disambiguate two genuinely different shapes, requiring either a native-record field addition (an ADR 0021/0022 amendment) or acceptance of an irreducibly coarser classification. | The ruleset's first version (a future, separately governed act) may need to declare some inputs classifiable only to a coarser shape than ideal, or a future ADR may need to add a discriminating native field; ownership, envelope, and replay design in this record are unaffected either way. |
| A-002 | Ruleset-version total ordering by acceptance order (not lexicographic string order) is implementable without a dedicated integer sequence field, using each version's own governing-ADR acceptance date/order as the ordering key. | Every governed reference-data amendment in this repository is already an ADR-numbered, chronologically ordered act (ADR 0023's own numbering is itself such a sequence). | High | A future implementation finds ADR acceptance order insufficiently granular (e.g., two ruleset versions accepted by amendments to the same ADR). | A future ADR amendment would need to add an explicit integer ordering field to `EvidenceSemanticsClassificationRuleset`; the deterministic-selection *principle* (highest strict-known-at-cutoff version wins) is unaffected. |

## Architectural Dimensions

- **Authority and ownership**: fully resolved — see Executive Summary and ADR 0028 §"Decision."
- **Record family and envelope**: fully resolved — see ADR 0028 §"Canonical envelope."
- **Persistence**: mechanical repository design, consistent with ADR 0009; fully resolved (repository class named per authority).
- **Versioning**: fully resolved per authority, including the previously-unspecified ruleset-version total-ordering rule.
- **Correction**: append-only, single-predecessor successor discipline; fully resolved, including the correction-vs-conflict distinction Finding 4 required.
- **Provenance**: fully resolved — every classification/contract cites its exact governing snapshot/ruleset version.
- **Strict-known replay**: fully resolved — exact selection algorithm specified per authority, each proven unambiguous by construction.
- **Conflict**: fully resolved per authority, including the specific reasoning for why Evidence Semantics has no "competing classification" conflict class (pure function of governed inputs).
- **Amendment governance**: fully resolved per authority, including the explicit, permanent prohibition on Evidence Semantics manual exceptions.
- **Compatibility**: with ADR 0021, ADR 0022 (amended), ADR 0023, ADR 0024, ADR 0025 (amended), ADR 0026, ADR 0027 — see ADR 0028 §"Compatibility With Accepted ADRs."
- **Dependency direction**: new dimension, added in this revision in response to Finding 5 — `hunter.evidence_assembly` depends on `hunter.valuation_methodology`'s public contract; `hunter.valuation_methodology`'s service and repository logic has no reverse dependency, with one narrow, unmodifiable-code-forced exception at the data-type level only (one dataclass import, no business logic) — see ADR 0028 §"Package ownership and dependency direction" for the precise boundary.
- **Implementation impact**: fully bounded — no material decision is left for a future implementation issue.

## Candidate Options

Revision 1 enumerated four ownership-package options per authority and four combined architectures (see revision 1's history, preserved in "Decision History" below for traceability). Independent review's five findings collapsed that option space to the single design in ADR 0028 §"Decision" — no live alternative remains for Methodology Contract's scope, activation split, or service/repository ownership, or for Evidence Semantics' authoring model. The comparative analysis below reflects only the options that remain genuinely open after resolution: none for Methodology Contract or Evidence Semantics; the original three for Evidence Shape Registry ownership (unchanged from revision 1, since no finding targeted it).

### Evidence Shape Registry Authority — ownership options (unchanged from revision 1)

1. **`hunter.evidence_assembly`, new internal repository** (recommended and adopted — matches ADR 0025's explicit text).
2. **`hunter.value_capture`** — rejected, contradicts ADR 0025's explicit ownership text.
3. **A new standalone reference-data package** — rejected as unnecessary fragmentation.

## Recommended Authority Design

The complete, singular design for all three authorities — ownership, record family, persistence, versioning, correction, provenance, strict-known replay, conflict, and amendment governance — is specified in full in `docs/ADR/0028-evidence-assembly-supporting-authorities.md` §"Decision" and §"Canonical envelope," incorporated here by reference as this preparation's recommendation. It is not restated in full here to avoid the two documents silently drifting apart; where a reader needs the exact mechanism, ADR 0028 is the single source of truth for it, and this ADPR is the record of *why* that mechanism was chosen over the alternatives below.

## Comparative Analysis

| Criterion | Adopted design (ADR 0028) | Revision 1's design | Fold-onto-snapshot alternative | Consolidated-package alternative |
|---|---|---|---|---|
| Satisfies ADR 0025's literal "snapshot in force explicitly declares acceptance" text | Yes — `accepts_assembled_evidence` field is the sole activation authority | No — this was Finding 2/OQ-004, the defect this revision fixes | Yes, but at the cost below | N/A |
| Requires ADR 0022 amendment | Yes (additive `accepts_assembled_evidence` field only) | No | Yes (loosens Milestone-2 lock; redesigns contract as global) | Depends on sub-design |
| Preserves per-target precision `_validate_methodology_contract` already requires | Yes | Yes | No — would force one snapshot "correction" per target, corrupting correction lineage | Depends on sub-design |
| Single, named, non-ambiguous service/repository owner (Finding 1/5) | Yes — `CanonicalValuationMethodologyAuthority` / `ValuationMethodologyRepository`, explicitly | No — left as OQ-003 | Yes, trivially (one record, one owner) | No — a fourth package duplicates existing ownership |
| Evidence Semantics manual-exception policy stated | Yes — prohibited, explicitly | No — left as OQ-002 | N/A (different authority) | N/A |
| Unblocks Issue #190 | Yes, after implementation | Yes, after implementation, but with unresolved design risk | Yes, after implementation | Yes, after implementation |

## Falsification Results

- **Adopted design falsified if**: a future reviewer shows the per-target `MethodologyEvidenceInputContract` cannot in practice be authored without also needing entity-level context `ValuationMethodologySnapshot`'s owning service does not otherwise have access to. Not observed: `CanonicalValuationMethodologyAuthority` gains `persist_evidence_input_contract` as a new method accepting entity/representation/window parameters directly from its caller, exactly as `persist_methodology` already accepts its own parameters directly — no implicit context dependency is introduced.
- **Fold-onto-snapshot alternative falsified by**: E-003, E-005 — `_validate_methodology_contract`'s existing per-target field comparison and `ValuationMethodologySnapshot`'s existing entity-agnostic, singleton-lineage design (E-011) are structurally incompatible with a single global declaration.
- **Consolidated-package alternative falsified by**: Constitutional Rule 5 — methodology-level policy already has a canonical owner; Evidence-Assembly-domain classification already has a canonical owner (this same revision's Evidence Semantics/Registry design); a third, consolidating package would duplicate both.

## Rejected Options

- **Standalone sibling record with no reference to `ValuationMethodologySnapshot` (revision 1's design)** — rejected on independent review; superseded by the adopted two-record design. See ADR 0028 §"Alternatives Considered."
- **Fold the full per-target contract directly onto `ValuationMethodologySnapshot`** — rejected; corrupts correction lineage (one "correction" per new target) and is structurally incompatible with the snapshot's existing entity-agnostic design. See ADR 0028 §"Alternatives Considered."
- **New standalone package or service for Methodology Contract** — rejected; duplicates `hunter.valuation_methodology`'s existing single ownership of methodology-level policy (Constitutional Rule 5).
- **`hunter.value_capture` ownership of Evidence Shape Registry or Evidence Semantics** — rejected; contradicts ADR 0025's explicit text (Registry) or requires native-record vocabulary additions outside `hunter.value_capture`'s mandate (Semantics).
- **Permit manual exceptions for Evidence Semantics classification** — rejected; reintroduces the "ad hoc human manifest approval... without persisted authority" failure mode ADR 0025 already rejected, one layer upstream. See ADR 0028 §"Alternatives Considered."
- **Derive Evidence Semantics from a single global "current" ruleset** — rejected; violates ADR 0020 strict-known replay directly (a later correction would silently reclassify historical records on next read).
- **No persistence for Evidence Semantics; derive at `assemble()` time** — rejected; removes an already-accepted protocol from `CanonicalEvidenceAssemblyService`'s constructor contract and forecloses provenance (Constitutional Rule 6).
- **Defer all authorities indefinitely** — rejected as the status quo this preparation exists to resolve.

## Risks

- **Risk R-003 (carried forward, unresolved by design — a real implementation cost, not an architecture gap)**: Adding `logical_id`/correction-lineage fields to `MethodologyEvidenceInputContract` and `AuthoritativeEvidenceSemantics`, and `accepts_assembled_evidence` to `ValuationMethodologySnapshot`, is additive but not fully backward-compatible for `logical_id` specifically (no principled default exists). Implementation must update every existing construction site in `tests/test_canonical_evidence_assembly.py` and `hunter.valuation_methodology`'s own test fixtures. This is bounded, known implementation work, not an open architecture question.
- **Risk R-005 (new in revision 2)**: `EvidenceSemanticsClassificationRuleset`'s first version's actual rule content is not authored by this preparation or by ADR 0028 — only the mechanism is authorized. Until a future ADR amendment publishes real content, `strict_known_semantics` will always return `None` for every evidence record, which is correct fail-closed behavior but means Evidence Assembly's write path remains practically unreachable even after ADR 0028's acceptance and full implementation, pending that separate content-publishing act. This is stated explicitly in ADR 0028 §"Consequences," not left implicit.

Former risks R-001, R-002, and R-004 (revision 1) are **resolved, not carried forward**: R-001 (per-target scope ambiguity) is resolved by E-003's direct code evidence; R-002 (classification-authoring mechanism) is resolved by the deterministic-ruleset design with manual exceptions prohibited; R-004 (ADR 0025 wording tension) is resolved by the activation/declaration split, which satisfies the literal text directly rather than through an inferred reading.

## Open Questions

**None material.** Every question revision 1 carried forward (OQ-001 through OQ-004) is resolved:

- **OQ-001 (resolved)**: `MethodologyEvidenceInputContract` is per-target, exactly — required by `_validate_methodology_contract`'s existing code (E-003), not a preference.
- **OQ-002 (resolved)**: Evidence Semantics classification is authored exclusively by a deterministic, governed `EvidenceSemanticsClassificationRuleset`; manual exceptions are explicitly and permanently prohibited.
- **OQ-003 (resolved)**: `CanonicalValuationMethodologyAuthority` (existing service) gains the Methodology Contract responsibility directly; no new sibling service is created.
- **OQ-004 (resolved)**: The activation/declaration split — `accepts_assembled_evidence` on the snapshot as sole activation authority, `MethodologyEvidenceInputContract` as the per-target declaration instance — satisfies ADR 0025's literal "the `ValuationMethodologySnapshot` in force explicitly declares acceptance" text directly, not through an inferred or contested reading.

One narrow, explicitly-scoped item remains for future, separately-governed work (not an unresolved *architecture* question): `EvidenceSemanticsClassificationRuleset`'s first version's actual content (Risk R-005) and the first real `MethodologyEvidenceInputContract`/`ValuationMethodologySnapshot` correction that sets `accepts_assembled_evidence = True` are future ADR-governed and implementation acts, respectively — exactly as ADR 0025 itself authorized the Evidence Shape Registry's *mechanism* without publishing its first version's *content*.

## Constitution Check

| Rule | Compliance | Notes |
|---|---|---|
| Rule 1 (Purpose) | Compliant | No change to Hunter's evidence-to-decision purpose. |
| Rule 2 (Evidence Authority) | Compliant | Every design keeps unknown/missing classification explicit; no fallback or default is introduced anywhere, including the ruleset-selection algorithm. |
| Rule 3 (Deterministic Intelligence) | Compliant | All three authorities are strict-known, replay-deterministic record families; Evidence Semantics classification is additionally a pure deterministic function of governed inputs. |
| Rule 4 (Architectural Integrity) | Compliant | Ownership is assigned explicitly by evidence (E-003, E-005, E-006, E-011, E-012), not by convenience. |
| Rule 5 (Single Source of Truth) | Compliant | Each concept gets exactly one named owner (service and repository, not just package); the consolidated-package alternative was rejected specifically for violating this rule; the dependency-direction rule (`hunter.valuation_methodology`'s service/repository logic never imports `hunter.evidence_assembly`, with one narrow, stated, data-type-only exception — ADR 0028 §"Package ownership and dependency direction") prevents ownership from becoming ambiguous at the code level. |
| Rule 6 (Explainability) | Compliant, unconditionally | Evidence Semantics manual exceptions are explicitly prohibited; every classification is traceable to a named, governed ruleset version. No longer conditional on an open question, unlike revision 1. |
| Rule 7 (Long-Term Evolution) | Compliant | Amendment-governance mechanisms are fully defined for all three authorities, following existing precedent (ADR 0023), including version-ordering. |
| Rule 8 (Governance) | Compliant | This record itself follows `docs/ARCHITECTURE_DECISION_PREPARATION_GUIDE.md`. |
| Rule 9 (Constitutional Change) | Not applicable | No constitutional change proposed. |

## Governance Review

- `docs/DEVELOPMENT_GOVERNANCE.md` Stage 1 routing to this Guide: satisfied.
- `docs/ARCHITECTURE_DECISION_PREPARATION_GUIDE.md` Required Outputs: all present in this record.
- No accepted ADR is superseded, weakened, or contradicted. This revision resolves revision 1's ADR 0025-compatibility gap directly: ADR 0028 now amends both ADR 0025 (narrow ownership-naming, unchanged from revision 1) **and** ADR 0022 (new, additive `accepts_assembled_evidence` field on `ValuationMethodologySnapshot`) — the second amendment is new in this revision, required to make the activation authority conform to ADR 0025's literal text.
- ADR 0026 §"Compatibility" and ADR 0027 §"Compatibility" are unaffected — this preparation grants no new right to any downstream consumer.

## Quality Assessment

| Dimension | Rating | Rationale | Blocking limitation |
|---|---|---|---|
| Problem correctness | GOOD | Grounded in direct code reads, not assumption. | None |
| Scope completeness | GOOD | Ownership, persistence, versioning, correction, provenance, replay, conflict, and amendment fully resolved for all three authorities, with no deferred choice. | None |
| Canonical consistency | GOOD | Every recommendation cites the accepted ADR or code location it rests on. | None |
| Evidence integrity | GOOD | All evidence is reproducible direct code/document reads; no assumption is presented as evidence. | None |
| Assumption discipline | GOOD | Two remaining assumptions (A-001, A-002) are narrowly scoped to future ruleset-content mechanics, not to any ownership or envelope decision. | None |
| Option completeness | GOOD | Every option this revision's findings targeted was re-evaluated; only Evidence Shape Registry retains a live (already-resolved-in-revision-1) option set, since no finding targeted it. | None |
| Comparative fairness | GOOD | The adopted design is compared against every alternative a reviewer could plausibly have preferred, on the same criteria. | None |
| Falsifiability | GOOD | Each design element has a stated falsification condition or direct code evidence that falsifies its alternatives. | None |
| Authority and ownership | GOOD | Explicit, evidence-grounded, single named service and repository owner for every authority — no package-level-only ownership statement remains. | None |
| Persistence and replay | GOOD | Full envelope, uniqueness constraint, and strict-known selection algorithm specified per authority, each proven unambiguous by construction. | None |
| Evidence and provenance | GOOD | Evidence Semantics' authoring model is now fully specified (deterministic ruleset, manual exceptions prohibited) — no longer the open dimension revision 1 left at ACCEPTABLE. | None |
| Implementation impact | ACCEPTABLE | Bounded, real implementation cost remains (Risk R-003 test-fixture updates; Risk R-005 first-content publication) — correctly stated as cost, not architectural gap. | None |
| Governance compatibility | GOOD | No accepted ADR is superseded, weakened, or contradicted; two required amendments (ADR 0025, ADR 0022) are both narrow and explicitly scoped. | None |
| Traceability | GOOD | Issue #191, this ADPR, ADR 0028, and the independent review findings driving this revision are cross-linked. | Second independent review round pending |

## Architecture Readiness

- Outcome: `READY`
- Rationale: ownership, record-family shape, persistence, versioning, correction, provenance, strict-known replay, conflict, and amendment governance are fully resolved for all three authorities, with named single owners and unambiguous strict-known selection algorithms. No material decision remains open.
- Missing evidence: none material. `EvidenceSemanticsClassificationRuleset`'s first version's actual content (Risk R-005) is future, separately-governed work, not missing *architectural* evidence.
- Unresolved conflicts: none.

## ADR Readiness

- Outcome: `READY_FOR_ADR`
- Proposed ADR title: Canonical Evidence Assembly Supporting Authorities (unchanged).
- Proposed ADR scope: unchanged in kind, revised in content — see ADR 0028 in full.
- Decisions the ADR must fix: all fixed in ADR 0028 revision 2; none remain for independent review to send back as "still open."
- Matters the ADR must leave open, correctly: `EvidenceSemanticsClassificationRuleset`'s first version's content; the first real `MethodologyEvidenceInputContract`/`accepts_assembled_evidence` correction; any runtime/production activation; any change to Issue #190's blocked status.

## Final Recommendation

Advance the adopted design (ADR 0028, revision 2) to a second round of independent architecture review. This session's author has not approved its own work — ADR 0028 remains Status `Proposed` and this record remains `READY_FOR_REVIEW`, not `APPROVED`, pending that review.

## Decision History

| Date | State | Change | Author or reviewer |
|---|---|---|---|
| 2026-08-04 | READY_FOR_REVIEW | Initial complete preparation (revision 1) for Issue #191. Four open questions (OQ-001 through OQ-004) carried forward explicitly. | Claude |
| 2026-08-04 | READY_FOR_REVIEW | Independent hostile architecture review of Draft PR #192: five `CHANGES REQUIRED` findings — (1) Methodology Contract ownership/scope unresolved; (2) ADR 0025/0022 compatibility left as an unresolved normative tension (OQ-004); (3) Evidence Semantics authorship model unresolved (OQ-002); (4) incomplete identity/replay/correction model, including using only `(evidence_record_id, evidence_record_version)` as `AuthoritativeEvidenceSemantics`'s identity; (5) split Methodology Contract ownership across packages. | Independent review (see the governing session's report for a note on this review's recorded-artifact status) |
| 2026-08-04 | READY_FOR_REVIEW | Revision 2: all five findings resolved with singular, non-deferred designs — activation/declaration split for Methodology Contract (resolves 1, 2), deterministic-ruleset Evidence Semantics model with manual exceptions prohibited (resolves 3), full canonical envelope with three-coordinate Evidence Semantics identity (resolves 4), single-package/single-service/single-repository Methodology Contract ownership with explicit dependency-direction rule (resolves 5). | Claude |

## Traceability

- Epic: not yet created
- Issue: [#191](https://github.com/fafa33/Project-Hunter/issues/191)
- Blocked issue this preparation unblocks (upon ADR acceptance and implementation): [#190](https://github.com/fafa33/Project-Hunter/issues/190)
- Gap analysis this preparation is grounded in: `docs/ARCHITECTURE_AUDITS/issue-190-evidence-assembly-authority-gap.md`
- Preparation working document: this record
- ADPR: ADPR-0005
- ADR: `docs/ADR/0028-evidence-assembly-supporting-authorities.md`, Status `Proposed`, not yet accepted
- Implementation plan: not authorized
- Draft PR: [#192](https://github.com/fafa33/Project-Hunter/pull/192), Draft, not merged, not marked Ready for Review
- Release: not yet assigned

## Immutability and Supersession

This record is `READY_FOR_REVIEW`, not `APPROVED`. It is a working preparation artifact until independent architecture review completes. This is revision 2, corrected in place in response to independent review findings against revision 1 — the "Decision History" table above is the permanent record of what changed and why, consistent with `docs/ARCHITECTURE_DECISION_PREPARATION_GUIDE.md`'s allowance for in-place correction before approval. After approval, substantive corrections require a new ADPR that explicitly supersedes this one, consistent with ADPR-0004's precedent.

Nothing in this record authorizes implementation, runtime activation, or the resumption of Issue #190. Only an accepted ADR, followed by a separately authorized implementation issue, can do that.
