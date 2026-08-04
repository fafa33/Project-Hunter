# ADPR-0005 — Canonical Evidence Assembly Supporting Authorities

## Metadata

- ADPR ID: `ADPR-0005`
- Status: `READY_FOR_REVIEW`
- Version: 1.0
- Author: Claude, on behalf of Issue #191
- Reviewers: independent architecture audit not yet performed
- Created: 2026-08-04
- Approved: not yet approved
- Related Epic: not yet created
- Related Issue: [Issue #191](https://github.com/fafa33/Project-Hunter/issues/191)
- Related blocked Issue: [Issue #190](https://github.com/fafa33/Project-Hunter/issues/190) (Canonical Evidence Assembly Orchestration Module, Undispatched) — remains `BLOCKED` until this record is accepted and a resulting ADR is accepted
- Planned or produced ADR: `ADR 0028` (drafted alongside this record, Status `Proposed`; not yet accepted — see `docs/ADR/0028-evidence-assembly-supporting-authorities.md`)
- Supersedes: not applicable
- Superseded by: not applicable

## Executive Summary

`CanonicalEvidenceAssemblyService` (ADR 0025, Accepted) requires five constructor collaborators to perform its sole authorized operation, `assemble()`. Two are production-backed (`repository`, `native_evidence_query`). Three — `methodology_contract_authority`, `evidence_shape_registry_authority`, `evidence_semantics_authority` — have no production implementation anywhere in `src/`; every construction of their backing record types (`MethodologyEvidenceInputContract`, `EvidenceShapeRegistry`, `AuthoritativeEvidenceSemantics`) is a test-only in-memory fake confined to `tests/test_canonical_evidence_assembly.py`. This was discovered while evaluating Issue #190 and is documented in full, with file:line evidence, in `docs/ARCHITECTURE_AUDITS/issue-190-evidence-assembly-authority-gap.md`.

ADR 0025 authorizes what these three authorities must do (§"Methodology contract," §"Evidence Shape Registry," §"Assembly preconditions") but does not fully assign who persists, versions, and serves them, or what their own correction/replay/conflict/amendment semantics are. It explicitly names the Evidence Shape Registry's governance owner ("the Canonical Evidence Assembly Authority") but is silent on the Methodology Contract's persistence owner and the Evidence Semantics Authority's owner entirely.

This preparation evaluates ownership, persistence, versioning, correction, provenance, strict-known replay, conflict, and amendment-governance options for all three authorities and recommends:

- **Methodology Contract Authority** — a new record family, `MethodologyEvidenceInputContract`, owned by `hunter.valuation_methodology` (the package that already owns `ValuationMethodologySnapshot`) as a sibling record family cross-referenced to an exact methodology-snapshot version, rather than a field extension of `ValuationMethodologySnapshot` itself.
- **Evidence Shape Registry Authority** — owned by `hunter.evidence_assembly` itself, exactly as ADR 0025 already states, implemented as a new repository inside the existing package, with Registry *content* changes gated by an ADR-governed amendment discipline mirroring ADR 0023's pattern.
- **Evidence Semantics Authority** — owned by `hunter.evidence_assembly` itself (not `hunter.value_capture`), as a new, immutable, strict-known "semantics assignment" record family that references a `FundamentalEvidenceRecord` by exact ID and version without mutating it.

All three follow the standard immutable bitemporal envelope ADR 0021 §"Required new record families and minimum fields" already establishes for every record family in this repository (record ID, logical ID, schema/semantic version, effective/recorded/known time, content hash, quality/conflict state, append-only correction lineage).

One open question is carried forward, unresolved, rather than papered over: `MethodologyEvidenceInputContract`'s current shape (entity/representation/window-scoped) versus a genuinely methodology-global contract is a real design tension this preparation surfaces but does not silently resolve by picking the more convenient reading (see "Open Questions").

No production code is authorized by this record. Self-assessment: `READY_FOR_ADR`, conditional on the open question above being carried into the ADR draft as an explicit, bounded scope note rather than resolved by assumption.

## Problem Statement

### Current condition

`CanonicalEvidenceAssemblyService.__init__` (`src/hunter/evidence_assembly/service.py:65-78`) requires `methodology_contract_authority`, `evidence_shape_registry_authority`, and `evidence_semantics_authority` as mandatory keyword arguments. No concrete class satisfying any of the three protocols (`service.py:41-54`) exists in `src/`. The service cannot be constructed with real collaborators, so `assemble()` — the operation ADR 0025 exists to authorize — is unreachable outside test fixtures.

### Desired condition

Each of the three missing authorities has:

- an explicit, single canonical owner (Constitutional Rule 5, Single Source of Truth);
- a defined, immutable, bitemporal record family, consistent with every other record family in this repository;
- defined persistence, versioning, correction, provenance, strict-known replay, and conflict semantics;
- a defined amendment-governance mechanism for changes to its content over time;
- a production-constructible implementation path that a future implementation issue can execute without inventing architecture.

### Decision required

A future ADR must fix:

1. ownership of `MethodologyEvidenceInputContract`, `EvidenceShapeRegistry`, and `AuthoritativeEvidenceSemantics`;
2. the record family/envelope for each, including any fields ADR 0025's current dataclasses lack (correction lineage in particular — see "Architectural Dimensions");
3. persistence, correction, provenance, and strict-known replay semantics for each, consistent with ADR 0020/ADR 0021;
4. the conflict-handling and amendment-governance mechanism for each, consistent with ADR 0023's precedent for versioned reference data;
5. whether `MethodologyEvidenceInputContract`'s scope is per-target (as currently coded) or methodology-global, or whether that question is deferred to implementation within fixed bounds this ADR sets.

### In scope

- architecture and ownership preparation only, for the three named authorities;
- persistence, versioning, correction, provenance, strict-known replay, conflict, and amendment semantics for each;
- whether a new ADR, an ADR 0025 amendment, or both are required.

### Out of scope

- implementation of any authority (repository, service, or persistence code);
- any modification to `src/hunter/evidence_assembly/service.py`'s existing `assemble()` logic, invariants, or validation order;
- any modification to `src/hunter/__main__.py`;
- resuming Issue #190's orchestration module;
- granting `hunter.comparative_valuation`, `hunter.mispricing`, `hunter.asymmetry`, or Canonical Market Validation (ADR 0027) any new right to Assembled Fundamental Evidence — ADR 0025, ADR 0026 §"Compatibility," and ADR 0027 §"Compatibility" already fix that boundary and this record does not reopen it;
- changing `ValuationMethodologySnapshot`'s existing ADR-0022-locked invariants (permitted model identifier, horizon, correlation group, normalization-policy gate) — see "Rejected Options";
- assigning production weights, activating Market Validation input, or any runtime activation decision.

## Problem Validation

ADR 0025 §"Methodology contract" states every methodology "must explicitly declare an evidence-input contract" but does not name a persistence owner. §"Evidence Shape Registry" names a governance owner for Registry *content* amendments but not an implementation/persistence owner. No section of ADR 0025 addresses who classifies or persists `AuthoritativeEvidenceSemantics`. `docs/ARCHITECTURE_AUDITS/issue-190-evidence-assembly-authority-gap.md` independently confirms, by repository-wide search, that none of the three exist in `src/` outside test fixtures. The problem is real, unresolved by any accepted document, and architectural under `docs/ARCHITECTURE_DECISION_PREPARATION_GUIDE.md` §"Scope" (it creates/materially changes canonical authority/ownership and persistence/versioning semantics).

## Motivation

Without this decision, any future attempt to implement these authorities would face the same choice Issue #190 correctly declined to make unilaterally: invent ownership and persistence semantics inside an implementation PR (violating ADR-before-implementation, Guiding Principle 8), or block indefinitely. Leaving Evidence Assembly's write path permanently unreachable also prevents ADR 0025's own purpose — closing the disclosure-granularity gap described in its Context section — from ever being realized, and blocks any future Market Validation methodology from declaring `accepts_assembled_evidence = True` (ADR 0025's amendment to ADR 0022, item 6) in practice, since no methodology contract could ever be authored or persisted.

## Existing Architecture

| Boundary | Existing authority | Binding consequence |
|---|---|---|
| Assembled Fundamental Evidence construction | `CanonicalEvidenceAssemblyService` (ADR 0025) | Sole authority; already implemented; fail-closed on any missing collaborator. |
| Native Fundamental Valuation Evidence | `hunter.value_capture` (ADR 0021) | Owns `FundamentalEvidenceRecord`; no `shape_id`, `accounting_meaning`, or `supply_basis_id` field exists on it (confirmed by direct read of `src/hunter/value_capture/models.py:80-112`). |
| Valuation methodology declaration | `hunter.valuation_methodology` (ADR 0022) via `CanonicalValuationMethodologyAuthority` | Owns `ValuationMethodologySnapshot`; every field is hard-locked to ADR-0022 Milestone 2 constants in `__post_init__` (`src/hunter/valuation_methodology/models.py:96-117`) — `permitted_model_identifier`, `horizon_days`, `correlation_group`, and `normalization_policy_id` cannot vary without a governing amendment. |
| Evidence Shape Registry content governance | ADR 0025 §"Evidence Shape Registry" | Already names "the Canonical Evidence Assembly Authority" as governance owner, under an ADR-governed amendment mechanism explicitly modeled on ADR 0023's. No persistence owner is named. |
| Supply basis | `hunter.value_capture` (ADR 0021) via `SupplyBasisSnapshot` | Already an accepted, owned record family; not itself a candidate constituent for Evidence Semantics, but its identity (`supply_basis_id`) is a field `AuthoritativeEvidenceSemantics` must classify against. |
| Downstream composition | ADR 0026, ADR 0027 | Explicitly reaffirm Evidence Assembly's isolation; grant no downstream authority over Assembled Fundamental Evidence. Unaffected by this preparation. |

## Constraints

### Constitutional

- Rule 2 (Evidence Authority): "Unknown information remains unknown... Missing information remains missing" — a semantics or contract classification that cannot be produced must remain explicitly unavailable, never defaulted or inferred silently.
- Rule 4 (Architectural Integrity): "Architectural boundaries, responsibilities, and ownership must remain explicit... Architectural convenience is never sufficient justification for violating established boundaries" — directly governs the ownership decision below; convenience (e.g., reusing `hunter.value_capture` because it is "nearby") is not itself a valid reason.
- Rule 5 (Single Source of Truth): "Every architectural concept, analytical authority, and governance rule must have one canonical owner... Competing authorities, duplicated ownership, and conflicting definitions are prohibited."
- Rule 6 (Explainability): "Hunter must preserve the reasoning, evidence, methodology, uncertainty, and provenance supporting every meaningful analytical output... Opaque authority is prohibited" — governs the rejection, below, of any classification mechanism that cannot show its own reasoning (see Evidence Semantics Authority's amendment-governance discussion).

### Governance and accepted ADRs

- ADR 0009 places decisions in services and keeps repositories mechanical; any new repository for these authorities must be mechanical only.
- ADR 0020 requires exact strict-known selection, explicit missingness, and forbids current/latest fallback for any input to a canonical service — binding on all three new authorities' retrieval methods.
- ADR 0021 fixes the five-layer evidence model and the standard record envelope; a new record family must not collapse or relabel an existing layer.
- ADR 0022 fixes `ValuationMethodologySnapshot`'s Milestone-2 invariants; any change to those invariants requires its own ADR 0022 amendment (this preparation recommends avoiding that need — see "Rejected Options").
- ADR 0023 establishes the precedent and precondition pattern for versioned, ADR-governed reference data and the requirement that any future value change carry a persisted, versioned policy identifier evaluated against the version in force *at record-creation time*, never the current value — directly applicable to the Evidence Shape Registry's amendment mechanism.
- ADR 0025 fixes what all three authorities must validate and is reaffirmed, not superseded, by this preparation; any resulting ADR may only narrowly amend ADR 0025's ownership silence, not its invariants.
- ADR 0026, ADR 0027 reaffirm Evidence Assembly's isolation from downstream composition; unaffected.

### Technical and operational

- No authority may be constructed as a stub or fake inside production orchestration code (the exact failure mode Issue #190 was correctly blocked to avoid).
- Every new repository must reject a `save`/`apply`/`write` method of its own eligibility logic, consistent with the AST-based repository-purity regression already enforced by `tests/test_valuation_family_repository_purity.py` for the existing four valuation-family repositories.
- No new authority may perform evidence acquisition, parsing, or valuation arithmetic.

### Persistence, replay, and provenance

- All new records are append-only, content-addressed, and bitemporal, matching every other record family in this repository.
- Corrections are single-predecessor successors; branching is prohibited, matching `_authorize_correction`'s existing pattern in `hunter.evidence_assembly.service`, `hunter.valuation.service`, and `hunter.valuation_methodology.service`.
- Every new authority's strict-known retrieval must be resolved by `(identity, known_by)` coordinates only, never a "latest" or "current" query path.

## Evidence Inventory

| ID | Evidence | Authority/source | Finding | Quality and limitations | Supports or challenges |
|---|---|---|---|---|---|
| E-001 | Project Constitution, Rules 2/4/5/6 | Canonical constitutional authority | Requires explicit missingness, explicit ownership, single ownership, and explainable classification. | Highest authority; non-numeric. | Supports explicit, single-owner authorities for all three; rejects opaque classification. |
| E-002 | ADR 0025 (Accepted) | Accepted ADR | Fully specifies what all three authorities must validate; partially specifies ownership (Registry only). | Binding; primary source for this preparation. | Defines the exact contract every option must satisfy. |
| E-003 | `src/hunter/evidence_assembly/service.py:65-78, 104-135, 271-310` | Direct code read | Constructor requires all three collaborators unconditionally; `assemble()` gates on each with no fallback. | Reproducible; read directly, not inferred. | Establishes the problem is real and structural, not a test-harness artifact. |
| E-004 | `tests/test_canonical_evidence_assembly.py:151-227` | Direct code read | Every construction of the three missing types is a named, explicit test fake. | Reproducible. | Confirms zero production implementation exists. |
| E-005 | `src/hunter/valuation_methodology/models.py:44-120` | Direct code read | `ValuationMethodologySnapshot` is hard-locked to ADR-0022 Milestone 2 constants; carries no entity/representation/window scope. | Reproducible. | Rejects folding the contract's fields directly onto this record without an ADR 0022 amendment; supports a sibling record family instead. |
| E-006 | `src/hunter/value_capture/models.py:80-112` | Direct code read | `FundamentalEvidenceRecord` has no `shape_id`, `accounting_meaning`, `supply_basis_id`, or standalone `currency` field. | Reproducible. | Rejects the assumption that Evidence Semantics is merely reading existing native fields; confirms it is genuinely new classification data. |
| E-007 | ADR 0023 (Accepted) | Accepted ADR | Establishes the precedent that versioned reference-data content changes require ADR governance, and that future value changes must carry a persisted version evaluated against the value in force at creation time. | Binding precedent. | Directly informs the Evidence Shape Registry's amendment-governance design. |
| E-008 | ADR 0026 §"Compatibility," ADR 0027 §"Compatibility" | Accepted ADRs | Both explicitly reaffirm Evidence Assembly's isolation; grant no downstream right to Assembled Fundamental Evidence. | Binding. | Confirms this preparation must not, and does not, reopen that boundary. |
| E-009 | `src/hunter/evidence_assembly/models.py:64-144` | Direct code read | `MethodologyEvidenceInputContract` and `AuthoritativeEvidenceSemantics` already carry `effective_at`/`recorded_at`/`known_at`/`quality_state`/`conflict_state`/`content_hash` but lack `logical_id`, `supersedes_record_id`, and `correction_reason`. | Reproducible. | Identifies a concrete gap the resulting ADR must close before either record family is correction-lineage-complete. |
| E-010 | `docs/ARCHITECTURE_AUDITS/issue-190-evidence-assembly-authority-gap.md` | This session's prior work product | Full original gap analysis, independently re-verified in this preparation. | Reproducible; produced by the same author, re-verified rather than merely re-cited. | Establishes problem validation. |

## Assumptions

| ID | Assumption | Rationale | Confidence | Falsification condition | Consequence if false |
|---|---|---|---|---|---|
| A-001 | `hunter.valuation_methodology` is the correct package to own a new sibling record family, not a wholly new top-level package. | ADR 0025's amendment to ADR 0022 frames the contract as intrinsic to "the `ValuationMethodologySnapshot` in force," and `hunter.valuation_methodology` already owns methodology-level governance. | Medium | A future ADR reviewer determines methodology-contract ownership must be independent of valuation methodology (e.g., because it must also serve a future non-valuation methodology family). | The recommended package location changes; the record family design and semantics below remain largely reusable. |
| A-002 | Evidence Shape Registry and Evidence Semantics Authority belong in the same package (`hunter.evidence_assembly`) as the Canonical Evidence Assembly Authority itself. | ADR 0025 already names this owner for the Registry; Evidence Semantics' classification vocabulary (`shape_id`, `accounting_meaning`) has no meaning outside the Evidence Assembly Authority's own domain. | High | A future ADR reviewer finds a reason `hunter.value_capture` must own semantics classification (e.g., because classification should happen at evidence-ingestion time, before Evidence Assembly ever sees the record). | Ownership moves to `hunter.value_capture`; correction/replay semantics below are largely reusable, but the classification-authoring question (Open Question OQ-002) becomes more, not less, load-bearing. |
| A-003 | Classification decisions (shape/semantics assignment) should be governed, deterministic, and auditable rather than ad hoc per-record human judgment. | ADR 0025 explicitly rejected "ad hoc human manifest approval of a composed value without persisted authority" for assembled *values*; Constitutional Rule 6 prohibits opaque authority. | Medium | A future ADR reviewer determines per-record classification requires case-by-case expert judgment that cannot be reduced to a governed rule set without losing accuracy. | The amendment-governance mechanism for Evidence Semantics needs a documented exception process rather than a pure rule table; ownership and record-family design are unaffected. |

## Architectural Dimensions

- **Authority and ownership**: which package/service owns construction, validation, and persistence-authorization for each of the three record families.
- **Record family and envelope**: whether each conforms to the standard bitemporal envelope (ADR 0021), and what fields the two already-drafted dataclasses (`MethodologyEvidenceInputContract`, `AuthoritativeEvidenceSemantics`) are missing to reach that standard.
- **Persistence**: mechanical repository design, consistent with ADR 0009.
- **Versioning**: how a "version" of a contract/registry/semantics assignment is identified and retrieved.
- **Correction**: append-only, single-predecessor successor discipline, consistent with every other authority in this repository.
- **Provenance**: what evidence each authority's decision must cite (e.g., which ADR-governed rule produced a given shape classification).
- **Strict-known replay**: retrieval bounded by `(identity, known_by)`, never "current."
- **Conflict**: what constitutes a conflict for each authority, and how it is surfaced (mirroring `AssemblyConflictRecord`'s existing pattern).
- **Amendment governance**: what process changes a given authority's *content* over time (ADR-governed, per ADR 0023's precedent, for Registry and Semantics; methodology-author-governed, cross-referenced to an exact methodology version, for the Contract).
- **Compatibility**: with ADR 0021, ADR 0022, ADR 0023, ADR 0024, ADR 0025, ADR 0026, ADR 0027.
- **Implementation impact**: what a future implementation issue must build, scoped narrowly enough not to require further architecture decisions.

## Exhaustive Option Inventory

### Methodology Contract Authority — ownership options

1. **New sibling record family under `hunter.valuation_methodology`** (recommended). `MethodologyEvidenceInputContract` becomes a new, separately persisted record family in the same package, cross-referencing an exact `ValuationMethodologySnapshot` record ID/version. `ValuationMethodologySnapshot` itself is not modified.
2. **Field extension of `ValuationMethodologySnapshot`**. Add the contract's fields directly onto the existing dataclass. Requires amending ADR 0022's Milestone-2-locked invariants (`models.py:96-117`) and redesigning `MethodologyEvidenceInputContract` to be methodology-global rather than entity/window-scoped.
3. **New standalone top-level package** (`hunter.methodology_contract` or similar), independent of `hunter.valuation_methodology`. Duplicates methodology-adjacent governance in a second location.
4. **Ownership by `hunter.evidence_assembly` itself**. The Evidence Assembly Authority persists the contracts it consumes, even though it does not author methodology policy.

### Evidence Shape Registry Authority — ownership options

1. **`hunter.evidence_assembly`, new internal repository** (recommended — matches ADR 0025's explicit text). A new `EvidenceShapeRegistryRepository` persists versioned `EvidenceShapeRegistry` snapshots inside the existing package, alongside `AssembledEvidenceRepository`.
2. **`hunter.value_capture`**. Registry lives with native evidence, since it classifies native evidence. Rejected in "Rejected Options" — contradicts ADR 0025's explicit ownership text.
3. **A new standalone reference-data package** shared by any future consumer of Evidence Shape classifications.

### Evidence Semantics Authority — ownership options

1. **`hunter.evidence_assembly`, new internal repository** (recommended). Semantics assignments are Evidence-Assembly-domain classifications of otherwise-unmodified native records.
2. **`hunter.value_capture`, as an extension of `FundamentalEvidenceRecord` validation at ingestion time**. Would require an ADR 0021/0022 amendment to add `shape_id`/`accounting_meaning`/`supply_basis_id` to the native evidence contract itself, and would make `hunter.value_capture` aware of Evidence-Assembly-only vocabulary it has no other reason to know.
3. **No separate authority — derive semantics deterministically from existing `FundamentalEvidenceRecord` fields at `assemble()` time, with no persistence at all.** Would remove the `evidence_semantics_authority` collaborator from `CanonicalEvidenceAssemblyService` entirely, which is itself an ADR 0025 amendment (the protocol is part of the accepted service's contract) and forecloses provenance/auditability of the classification decision (Constitutional Rule 6).

## Candidate Options

### Option 1 — Sibling authorities, narrow ADR 0025 amendment (recommended)

- Methodology Contract Authority: new sibling record family under `hunter.valuation_methodology` (ownership option 1 above).
- Evidence Shape Registry Authority: new repository under `hunter.evidence_assembly` (ownership option 1 above).
- Evidence Semantics Authority: new repository under `hunter.evidence_assembly` (ownership option 1 above).
- Resulting ADR: one new ADR that authorizes all three as new record families/authorities and narrowly amends ADR 0025 to name the two ownership assignments ADR 0025 left silent (Contract's persistence owner, Semantics' owner entirely) — ADR 0025's own invariants and validation logic in `service.py` are unchanged.
- No ADR 0021 or ADR 0022 amendment required.

### Option 2 — Fold Methodology Contract into `ValuationMethodologySnapshot`, everything else as Option 1

- Requires an ADR 0022 amendment (mirroring ADR 0023's/ADR 0024's pattern) to loosen `ValuationMethodologySnapshot`'s Milestone-2 lock and redesign the contract as methodology-global.
- Broader blast radius: touches an already-Accepted, already-implemented, already-relied-upon record family (`ValuationMethodologySnapshot` is consumed by `CanonicalValuationService`, per ADR 0021's authority matrix).

### Option 3 — Single new consolidated authority owning all three record families

- One new package or one new service owns Methodology Contract, Evidence Shape Registry, and Evidence Semantics together, rather than splitting Methodology Contract into `hunter.valuation_methodology`.
- Simpler to implement as one unit, but duplicates methodology-adjacent governance the existing `hunter.valuation_methodology` package already owns (Constitutional Rule 5, single canonical owner per concept — methodology-level policy already has an owner).

### Option 4 — Defer all three indefinitely; leave Issue #190 permanently blocked

- No architecture change. Issue #190 (and any future Evidence Assembly consumer) remains permanently blocked.
- Rejected as the status quo that motivated this preparation; ADR 0025's own purpose is never realized.

## Recommended Authority Design

### Methodology Contract Authority

- **Owner**: `hunter.valuation_methodology`, new service `CanonicalMethodologyEvidenceInputContractAuthority` (or an added responsibility on the existing `CanonicalValuationMethodologyAuthority` — the resulting ADR must pick one explicitly; this preparation does not fabricate that choice — see Open Question OQ-001).
- **Record family**: `MethodologyEvidenceInputContract`, extended with `logical_id: str`, `supersedes_record_id: str | None`, `correction_reason: str = ""` to reach the standard envelope (currently absent per E-009).
- **Persistence**: new mechanical repository, `MethodologyEvidenceInputContractRepository`, append-only, keyed by `record_id`; history queryable by `logical_id`.
- **Versioning**: `(contract_id, contract_version)` remains the retrieval key `strict_known_contract` already expects (`service.py:41-44`); unchanged from the current protocol.
- **Correction**: single-predecessor successor, mirroring `_authorize_correction` in `hunter.evidence_assembly.service` and `hunter.valuation_methodology.service`; branching prohibited.
- **Provenance**: every contract cross-references the exact `ValuationMethodologySnapshot` record ID/version it was declared under (new field), and the exact ADR reference authorizing that methodology's evidence-acceptance policy.
- **Strict-known replay**: `(contract_id, contract_version, known_by)`, exactly as `service.py:41-44` already requires; no change to the consuming protocol.
- **Conflict**: two divergent contracts sharing `(contract_id, contract_version)` is an unresolved conflict, surfaced identically to how `AssembledEvidenceRepository._insert_authorized` already rejects divergent duplicates (`repository.py:76-112`, pattern reused, not new).
- **Amendment governance**: a methodology author changes acceptance terms only by publishing a new `contract_version`; no in-place mutation. Whether authoring itself is governed by a future methodology ADR (per-methodology, like ADR 0022's own future amendments) or is implementation-level configuration is left to the resulting ADR, not fabricated here.

### Evidence Shape Registry Authority

- **Owner**: `hunter.evidence_assembly`, new repository `EvidenceShapeRegistryRepository`, alongside the existing `AssembledEvidenceRepository`.
- **Record family**: `EvidenceShapeRegistry` (`registry.py:13-53`), unchanged in shape; already carries the full standard envelope minus `logical_id`/correction fields, which — unlike the other two — this preparation recommends *not* adding: ADR 0025 already frames the Registry as versioned reference data amended by creating a new `version` string, not by a correction-successor chain (its precedent, ADR 0023's `SUPPLY_COHERENCE_RELATIVE_TOLERANCE`, has no correction lineage either — it is a fixed, ADR-governed constant with a version).
- **Persistence**: append-only by `version`; no two persisted registries may share a `version` string with different content (mirrors ADR 0023's precondition that a future value change must be evaluated against the version in force at record-creation time, never "current").
- **Versioning**: `version` is the sole identity key, matching `strict_known_registry`'s existing `(version, known_by)` signature (`service.py:47-48`); unchanged.
- **Correction**: none — a Registry version is immutable once published; a change is a new version, never a correction to an existing one, consistent with ADR 0023's precedent.
- **Provenance**: every `EvidenceShapeRegistry` version's content must cite the exact ADR (or ADR amendment) that authorized its shape definitions, mirroring how `SUPPLY_COHERENCE_RELATIVE_TOLERANCE`'s value is cited to ADR 0023 in code comments today.
- **Strict-known replay**: `(version, known_by)`, unchanged from the current protocol.
- **Conflict**: two different content payloads claiming the same `version` string is a hard rejection at persistence time (analogous to ADR 0023's "may never be changed by a code-only commit").
- **Amendment governance**: exactly ADR 0025's already-stated mechanism — a new or changed shape requires its own accepted ADR amendment, per the ADR 0023 pattern it explicitly cites. This preparation adds nothing new here; it only supplies the missing persistence implementation path.

### Evidence Semantics Authority

- **Owner**: `hunter.evidence_assembly`, new repository `EvidenceSemanticsRepository`.
- **Record family**: `AuthoritativeEvidenceSemantics`, extended with `logical_id: str`, `supersedes_record_id: str | None`, `correction_reason: str = ""` (currently absent per E-009), plus a new `classification_rule_reference: str` field recording which governed rule or ADR-authorized criterion produced this classification (Constitutional Rule 6, Explainability — the classification must show its reasoning, not merely assert a result).
- **Persistence**: append-only, keyed by `(evidence_record_id, evidence_record_version)`.
- **Versioning**: unchanged from the current protocol — `(evidence_record_id, evidence_record_version, known_by)` per `strict_known_semantics` (`service.py:51-54`).
- **Correction**: single-predecessor successor; a corrected classification (e.g., a fixed misclassified shape) produces a new `AuthoritativeEvidenceSemantics` record referencing its predecessor, never a mutation, mirroring every other correction chain in this repository.
- **Provenance**: `classification_rule_reference` above; every classification is traceable to a governed rule, not an unexplained assertion.
- **Strict-known replay**: `(evidence_record_id, evidence_record_version, known_by)`, unchanged.
- **Conflict**: two divergent classifications for the same `(evidence_record_id, evidence_record_version)` is an unresolved conflict, surfaced the same way `AssemblyConflictRecord` already surfaces omitted/overlapping-evidence conflicts.
- **Amendment governance**: this is the least settled dimension (see Open Question OQ-002). This preparation recommends classification be produced by a governed, deterministic rule set (Assumption A-003), authored and amended under the same ADR-governed discipline as the Evidence Shape Registry, rather than per-record human judgment — but does not fabricate the exact rule engine, since Guiding Principle 9 prohibits replacing missing evidence with convenience assumptions and no such rule engine currently exists to describe accurately.

## Comparative Analysis

| Criterion | Option 1 (recommended) | Option 2 | Option 3 | Option 4 |
|---|---|---|---|---|
| Touches an already-Accepted, already-implemented record family | No | Yes (`ValuationMethodologySnapshot`) | No | No |
| Requires ADR 0022 amendment | No | Yes | No | No |
| Requires ADR 0025 amendment | Yes (narrow: ownership only) | Yes (narrow: ownership only) | Yes (narrow: ownership only) | No |
| Preserves single canonical owner per concept (Rule 5) | Yes | Yes | No — new package duplicates methodology-adjacent governance | N/A |
| Unblocks Issue #190 | Yes, after implementation | Yes, after implementation | Yes, after implementation | No |
| Implementation blast radius | Three new sibling record families in two existing packages | Two new record families plus a change to a relied-upon existing one | Three new record families in one new package | None |

## Falsification Results

- **Option 1 falsified if**: a future reviewer shows `hunter.valuation_methodology` cannot cleanly own a per-target contract without itself becoming entity-aware in a way ADR 0022 forbids. Not observed in this preparation — `ValuationMethodologySnapshot` remains untouched under Option 1, and the sibling record family carries its own entity/representation/window scope independently, exactly as `MethodologyEvidenceInputContract` already does today.
- **Option 2 falsified by**: E-005 — reopening `ValuationMethodologySnapshot`'s Milestone-2 lock for a capability (assembled-evidence acceptance) that ADR 0025 itself already scoped as "a future `ValuationMethodologySnapshot` version," not a Milestone-2 concern, is disproportionate given Option 1 achieves the same outcome without touching it.
- **Option 3 falsified by**: Constitutional Rule 5 — methodology-level policy already has a canonical owner (`hunter.valuation_methodology`); a new package duplicating that concern is architecturally redundant, not merely stylistically different.
- **Option 4 falsified by**: E-002/E-003 — the problem is real and already blocking a filed issue; indefinite deferral is a decision to abandon ADR 0025's stated purpose, not a neutral non-decision.

## Rejected Options

- **Field extension of `ValuationMethodologySnapshot` (Methodology Contract option 2)** — rejected. Requires reopening ADR 0022's fixed Milestone-2 invariants for a capability ADR 0025 already scoped as future-version-only; Option 1 achieves the identical outcome with a narrower blast radius. Reconsideration condition: a future ADR that redesigns `ValuationMethodologySnapshot` for reasons independent of this preparation could revisit folding the contract in at that time.
- **`hunter.value_capture` ownership of Evidence Shape Registry** — rejected outright. Directly contradicts ADR 0025's explicit text naming "the Canonical Evidence Assembly Authority" as governance owner.
- **`hunter.value_capture` ownership of Evidence Semantics, via native-record field extension (Evidence Semantics option 2)** — rejected. E-006 shows the classification vocabulary (`shape_id`, `accounting_meaning`) does not exist in ADR 0021/ADR 0022's native evidence contract; adding it would be an ADR 0021/0022 amendment for a capability that is Evidence-Assembly-domain-specific, not native-evidence-domain-specific. Reconsideration condition: if a future ADR determines native evidence itself should carry assembly-shape metadata at ingestion time for reasons unrelated to this preparation.
- **No persistence for Evidence Semantics; derive at `assemble()` time (Evidence Semantics option 3)** — rejected. Removes the `evidence_semantics_authority` protocol from `CanonicalEvidenceAssemblyService`'s already-accepted contract (itself an ADR 0025 amendment, not preserved by this option's own premise) and forecloses provenance (Constitutional Rule 6).
- **Standalone top-level packages for any of the three (Methodology Contract option 3, Evidence Shape Registry option 3)** — rejected as unnecessary fragmentation with no evidenced benefit over placing each authority beside its closest existing conceptual owner.

## Risks

- **Risk R-001**: The Methodology Contract's per-target (entity/representation/window) scope, if carried forward unchanged into the ADR, may later prove awkward if a methodology needs to declare its acceptance policy once, globally, rather than per assembly target. Mitigated by carrying this forward as Open Question OQ-001 rather than silently deciding it.
- **Risk R-002**: The Evidence Semantics classification-authoring mechanism (rule-based vs. governed-manual) is the least-evidenced part of this preparation (Assumption A-003, Medium confidence). Mitigated by scoping the resulting ADR's authorization to ownership/record-family/persistence/replay only, and explicitly leaving the exact rule engine to a future implementation-preparation cycle rather than fabricating it now.
- **Risk R-003**: Adding correction-lineage fields to `MethodologyEvidenceInputContract` and `AuthoritativeEvidenceSemantics` changes their dataclass shape from what ADR 0025 (and the existing test fixtures in `tests/test_canonical_evidence_assembly.py`) currently model. `supersedes_record_id`/`correction_reason` can be added as defaulted fields (mirroring every other record family's `= None`/`= ""` pattern), but `logical_id` cannot — every other record family in this repository treats `logical_id` as a mandatory, non-defaulted field (see `AssembledFundamentalEvidenceRecord`, `ValuationMethodologySnapshot`, `FundamentalEvidenceRecord`), and there is no principled default for it. This is **not** a fully backward-compatible change: implementation must update every existing construction site in `tests/test_canonical_evidence_assembly.py` (`_contract()`, `_ContractAuthority`, `_SemanticsAuthority` usages) to supply `logical_id` explicitly. The resulting ADR must state this consequence rather than imply the change is transparently additive.
- **Risk R-004**: ADR 0025's own "Exact amendment to ADR 0022" text reads, "a future `ValuationMethodologySnapshot` version may declare acceptance" of assembled evidence — most naturally read as the acceptance flag living directly *on* a `ValuationMethodologySnapshot` (Methodology Contract option 2), not on a cross-referenced sibling record (Option 1, recommended here). Option 1 satisfies this sentence's underlying intent — a contract is still published *for* an exact, identified methodology-snapshot version — but this is an interpretation of ambiguous existing text, not a certain reading. See Open Question OQ-004. If ignored, a future implementer could build Option 1 believing it is unambiguously authorized by ADR 0025's existing wording, when independent review might instead read that sentence as requiring the field-extension design. Mitigated by surfacing this explicitly for reviewer confirmation rather than silently picking a reading.

## Open Questions

- **OQ-001**: Should `MethodologyEvidenceInputContract` remain per-target (entity/representation/window-scoped, as currently coded) or become genuinely methodology-global? This preparation recommends *not* deciding this now — Option 1 works under either answer, since the sibling-record-family ownership choice does not depend on it — but the resulting ADR must explicitly state which scope it authorizes, rather than leaving both readings simultaneously plausible.
- **OQ-002**: What governs authorship of an individual Evidence Semantics classification — a deterministic, ADR-governed rule table (recommended direction, Assumption A-003) or a documented exception/override process for cases a rule table cannot cleanly cover? Left open for the resulting ADR or a follow-on preparation cycle.
- **OQ-003**: Does `hunter.valuation_methodology`'s existing `CanonicalValuationMethodologyAuthority` gain the new Methodology Contract responsibility directly, or does a new sibling service own it within the same package? Both satisfy Rule 5 (one package, one concept-family); the resulting ADR must pick one.
- **OQ-004**: Does Option 1's sibling-record design for `MethodologyEvidenceInputContract`, cross-referenced to an exact `ValuationMethodologySnapshot` version, satisfy ADR 0025's existing "Exact amendment to ADR 0022" sentence — "a future `ValuationMethodologySnapshot` version may declare acceptance" — or does that sentence require the acceptance flag to live directly on `ValuationMethodologySnapshot` itself (Methodology Contract option 2)? This preparation recommends Option 1 as satisfying the sentence's underlying intent without reopening ADR 0022's fixed invariants (see Risk R-004), but flags this as requiring explicit independent-review confirmation rather than treating it as settled by this preparation alone.

## Constitution Check

| Rule | Compliance | Notes |
|---|---|---|
| Rule 1 (Purpose) | Compliant | No change to Hunter's evidence-to-decision purpose. |
| Rule 2 (Evidence Authority) | Compliant | Every recommended design keeps unknown/missing classification explicit; no fallback or default is introduced. |
| Rule 3 (Deterministic Intelligence) | Compliant | All three authorities are recommended as strict-known, replay-deterministic record families. |
| Rule 4 (Architectural Integrity) | Compliant | Ownership is assigned explicitly by evidence (E-005, E-006), not by convenience. |
| Rule 5 (Single Source of Truth) | Compliant | Each concept gets exactly one recommended owner; Option 3 (a fourth package) was rejected specifically for violating this rule. |
| Rule 6 (Explainability) | Compliant, conditionally | Satisfied for Methodology Contract and Evidence Shape Registry. For Evidence Semantics, compliance depends on OQ-002 being resolved toward a governed rule set rather than opaque per-record assertion — flagged, not silently assumed resolved. |
| Rule 7 (Long-Term Evolution) | Compliant | Amendment-governance mechanisms are defined for all three, following existing precedent (ADR 0023). |
| Rule 8 (Governance) | Compliant | This record itself follows `docs/ARCHITECTURE_DECISION_PREPARATION_GUIDE.md`. |
| Rule 9 (Constitutional Change) | Not applicable | No constitutional change proposed. |

## Governance Review

- `docs/DEVELOPMENT_GOVERNANCE.md` Stage 1 routing to this Guide: satisfied — this is an architecturally significant change per the Guide's own Scope section (canonical authority/ownership, persistence/versioning/correction semantics).
- `docs/ARCHITECTURE_DECISION_PREPARATION_GUIDE.md` Required Outputs: all seventeen items present in this record (see section list above).
- No accepted ADR is superseded, weakened, or contradicted. ADR 0025 is reaffirmed in every respect other than the narrow ownership-naming amendment recommended above (see `docs/ADR/0028-evidence-assembly-supporting-authorities.md`, drafted alongside this record, Status `Proposed`).
- ADR 0026 §"Compatibility" and ADR 0027 §"Compatibility," which reaffirm Evidence Assembly's isolation from downstream composition, are unaffected — this preparation grants no new right to any downstream consumer.

## Quality Assessment

| Dimension | Rating | Rationale | Blocking limitation |
|---|---|---|---|
| Problem correctness | GOOD | Grounded in direct code reads (E-003, E-004), not assumption. | None |
| Scope completeness | GOOD | Ownership, persistence, versioning, correction, provenance, replay, conflict, and amendment covered for all three authorities. | None |
| Canonical consistency | GOOD | Every recommendation cites the accepted ADR or code location it rests on. | None |
| Evidence integrity | GOOD | All evidence is reproducible direct code/document reads; no assumption is presented as evidence. | None |
| Assumption discipline | GOOD | Three assumptions stated with confidence, falsification condition, and consequence. | None |
| Option completeness | GOOD | 3-4 ownership options enumerated per authority; four combined architectures compared. | None |
| Comparative fairness | GOOD | Options compared against the same six criteria in one table. | None |
| Falsifiability | GOOD | Each option has a stated falsification condition or evidence that falsifies it. | None |
| Authority and ownership | GOOD | Explicit, evidence-grounded owner recommended for each of the three authorities. | None |
| Persistence and replay | GOOD | Full envelope, versioning, correction, and strict-known replay specified per authority, reusing established patterns rather than inventing new ones. | None |
| Evidence and provenance | ACCEPTABLE | Evidence Semantics' classification-authoring mechanism (OQ-002) is the one dimension left genuinely open. | Deferred to resulting ADR or follow-on preparation; does not block ADR readiness for the other two authorities or for Evidence Semantics' ownership/persistence/replay design. |
| Implementation impact | ACCEPTABLE | Three new sibling record families in two existing packages; no change to already-shipped `ValuationMethodologySnapshot`, `FundamentalEvidenceRecord`, or `CanonicalEvidenceAssemblyService` logic. Adding `logical_id` to two existing dataclasses requires updating existing test fixtures (Risk R-003) — a real, bounded implementation cost, not a blocking defect. | None |
| Governance compatibility | GOOD | No accepted ADR is superseded, weakened, or contradicted; the one required amendment is narrow and explicitly scoped. | None |
| Traceability | GOOD | Issue #191, this ADPR, and the drafted ADR 0028 are cross-linked; Issue #190 remains explicitly blocked pending acceptance. | Independent review pending |

## Architecture Readiness

- Outcome: `READY`
- Rationale: ownership, record-family shape, persistence, versioning, correction, provenance, strict-known replay, conflict, and amendment governance are bounded for all three authorities; the one genuinely open dimension (OQ-002) is scoped narrowly enough not to block the other two authorities or the ownership/persistence/replay design of the third.
- Missing evidence: a concrete classification-rule design for Evidence Semantics (OQ-002); real usage evidence for whether Methodology Contract should be per-target or global (OQ-001); explicit confirmation that Option 1's sibling-record design satisfies ADR 0025's existing "future `ValuationMethodologySnapshot` version may declare acceptance" wording (OQ-004).
- Unresolved conflicts: none. All four open questions are carried forward explicitly, not resolved by assumption.

## ADR Readiness

- Outcome: `READY_FOR_ADR`
- Proposed ADR title: Canonical Evidence Assembly Supporting Authorities.
- Proposed ADR scope: ownership, record-family definition (including the additive `logical_id`/correction-lineage fields), persistence, versioning, correction, provenance, strict-known replay, conflict, and amendment-governance mechanism for `MethodologyEvidenceInputContract`, `EvidenceShapeRegistry`, and `AuthoritativeEvidenceSemantics`; a narrow amendment to ADR 0025 naming the ownership this preparation resolves.
- Decisions the ADR must fix: Option 1 (or a materially different option, if independent review disagrees); the additive record-family fields, including the non-defaulted `logical_id` addition's compatibility consequence (Risk R-003); the amendment-governance mechanism per authority; explicit resolution of OQ-001, OQ-003, and OQ-004 (this preparation recommends against silently deferring these to implementation).
- Matters the ADR must leave open: OQ-002's exact classification-rule engine (recommended as a follow-on preparation or implementation-stage decision bounded by "governed and deterministic, never opaque per-record judgment," per Assumption A-003); any runtime/production activation; any change to Issue #190's blocked status (unblocking requires the ADR's acceptance plus implementation, not the ADR alone).

## Final Recommendation

Advance Option 1 (sibling authorities under `hunter.valuation_methodology` and `hunter.evidence_assembly`, with a narrow ADR 0025 amendment) to independent architecture review. The accompanying ADR draft (`docs/ADR/0028-evidence-assembly-supporting-authorities.md`, Status `Proposed`) is produced alongside this record for that review; it must not be treated as Accepted until independent review completes and, separately, until this session's user or a maintainer explicitly accepts it — this session author has not approved its own work (see Governance Proof in the accompanying report).

## Decision History

| Date | State | Change | Author or reviewer |
|---|---|---|---|
| 2026-08-04 | READY_FOR_REVIEW | Initial complete preparation for Issue #191. | Claude |

## Traceability

- Epic: not yet created
- Issue: [#191](https://github.com/fafa33/Project-Hunter/issues/191)
- Blocked issue this preparation unblocks (upon ADR acceptance and implementation): [#190](https://github.com/fafa33/Project-Hunter/issues/190)
- Gap analysis this preparation is grounded in: `docs/ARCHITECTURE_AUDITS/issue-190-evidence-assembly-authority-gap.md`
- Preparation working document: this record
- ADPR: ADPR-0005
- ADR: `docs/ADR/0028-evidence-assembly-supporting-authorities.md`, Status `Proposed`, not yet accepted
- Implementation plan: not authorized
- Draft PR: recorded once opened (see this record's governing session report)
- Release: not yet assigned

## Immutability and Supersession

This record is `READY_FOR_REVIEW`, not `APPROVED`. It is a working preparation artifact until independent architecture review completes. Substantive corrections before approval may be made in place with an updated Decision History entry; after approval, substantive corrections require a new ADPR that explicitly supersedes it, consistent with ADPR-0004's precedent.

Nothing in this record authorizes implementation, runtime activation, or the resumption of Issue #190. Only an accepted ADR, followed by a separately authorized implementation issue, can do that.
